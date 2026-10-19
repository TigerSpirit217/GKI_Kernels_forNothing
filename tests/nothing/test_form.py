from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from nothing import catalog, form, source, upstream


class FormTests(unittest.TestCase):
    def test_defaults_do_not_enable_optional_features(self):
        options = form.options_from_form({})
        self.assertEqual(options["ksu_variant"], "None")
        self.assertEqual(options["droidspaces"], "off")
        self.assertEqual(options["use_kpm"], "disabled (关闭)")
        self.assertFalse(any(options[key] for key in form.SWITCHES))
        self.assertEqual(form.build_settings({}), {"version_name": "", "build_time": ""})

    def test_kpm_and_containers_translate_to_upstream_options(self):
        options = form.options_from_form({"ksu_variant": "SukiSU", "enable_susfs": True,
                                          "use_kpm": True, "droidspaces": True, "droidspaces_slot": "123",
                                          "droidspaces_ntsync": True, "export_susfs_patches": True})
        self.assertEqual(options["use_kpm"], "enabled (开启)")
        self.assertEqual(options["ksu_variant"], "SukiSU(41013)")
        self.assertEqual(options["droidspaces"], "123")
        self.assertTrue(options["droidspaces_ntsync"])
        self.assertTrue(options["export_susfs_patches"])

    def test_ntsync_automatically_enables_droidspaces(self):
        self.assertEqual(form.options_from_form({"droidspaces_ntsync": True})["droidspaces"], "678")

    def test_incompatible_selections_fail_before_source_download(self):
        for inputs in ({"use_kpm": True}, {"use_kpm": True, "ksu_variant": "Official"},
                       {"enable_susfs": True}, {"export_susfs_patches": True},
                       {"use_zram": "false"}, {"droidspaces_slot": "999"}):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                form.options_from_form(inputs)

    def test_custom_name_and_iso_utc_time(self):
        settings = form.build_settings({"version_name": "NothingCustom", "build_time": "2026-10-19 12:30:00 UTC"})
        self.assertEqual(settings, {"version_name": "NothingCustom", "build_time": "Mon Oct 19 12:30:00 UTC 2026"})

    def test_legacy_time_and_current_time(self):
        value = "Sun Dec 01 08:10:00 UTC 2024"
        self.assertEqual(form.build_settings({"build_time": value})["build_time"], value)
        for value in ("", " ", "N", "n"):
            self.assertEqual(form.build_settings({"build_time": value})["build_time"], "")

    def test_invalid_time_dates_and_weekdays(self):
        for value in ("2026-02-30 10:00:00 UTC", "Sun Oct 19 12:30:00 UTC 2026", "2026-10-19 25:00:00 UTC", "$(id)"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "构建时间无效"):
                form.build_settings({"build_time": value})

    def test_invalid_custom_name(self):
        for value in ("$(whoami)", "bad'name", "x" * 33, "-my-kernel", "a/b", "two names"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "自定义版本名"):
                form.build_settings({"version_name": value})

    def test_all_current_upstream_features_have_form_controls(self):
        _, schema = upstream.load_upstream()
        configurable = set(schema) - upstream.RESERVED
        self.assertEqual(configurable, (set(form.SWITCHES) | {"droidspaces", "use_kpm"}) - upstream.RESERVED)
        workflow = yaml.safe_load(catalog.WORKFLOW.read_text(encoding="utf-8"))
        controls = workflow[True]["workflow_dispatch"]["inputs"]
        for key in (*form.SWITCHES, "use_kpm", "droidspaces"):
            self.assertEqual(controls[key]["type"], "boolean")
        self.assertEqual(controls["droidspaces_slot"]["type"], "choice")
        self.assertEqual(controls["version_name"]["type"], "string")
        self.assertEqual(controls["build_time"]["type"], "string")
        self.assertNotIn("patch_options", controls)
        self.assertEqual(controls["ksu_variant"]["options"], ["None", "Official", "SukiSU(41013)", "BakaSU"])

    def test_removed_fixed_versions_are_rejected(self):
        for variant in ("SukiSU(40726)", "SukiSU(40548)"):
            with self.subTest(variant=variant), self.assertRaisesRegex(ValueError, "Unsupported KernelSU"):
                form.options_from_form({"ksu_variant": variant})


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads(catalog.CATALOG.read_text(encoding="utf-8"))

    def release(self, version, sha, branch="sm8475/b/mr", date="2026-10-19T00:00:00Z"):
        return {"repository": source.device_for("phone-2")["repository"], "branch": branch,
                "sha": sha * 40, "versions": ["OS 4.1(" + version + ")"], "committed_at": date,
                "readme": "# NOTHING Phone(2) Release Note\nOS 4.1(" + version + ")"}

    def test_committed_menu_covers_every_device_and_is_in_sync(self):
        catalog.validate_catalog(self.snapshot)
        text = catalog.WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(catalog.render_workflow(text, self.snapshot), text)
        workflow = yaml.safe_load(text)
        controls = workflow[True]["workflow_dispatch"]["inputs"]
        self.assertEqual(controls["model"]["options"], [d["name"] for d in catalog.devices().values()])
        self.assertEqual(controls["version"]["options"], [catalog.LATEST, *[e["label"] for e in self.snapshot["entries"]]])
        self.assertLessEqual(len(controls), 25)

    def test_historical_menu_selection_uses_pinned_commit(self):
        row = self.snapshot["entries"][-1]
        selected = catalog.select_entry(catalog.devices()[row["model"]]["name"], row["label"])
        self.assertEqual(selected["sha"], row["sha"])
        self.assertEqual(selected["branch"], row["branch"])

    def test_another_models_version_is_rejected(self):
        row = next(e for e in self.snapshot["entries"] if e["model"] == "phone-4a")
        with self.assertRaisesRegex(ValueError, "机型和版本不匹配"):
            catalog.select_entry("phone-3a", row["label"])

    def test_unknown_selection_does_not_fall_back(self):
        with self.assertRaisesRegex(ValueError, "不在当前下拉目录"):
            catalog.select_entry("phone-2", "nonexistent")

    def test_latest_queries_live_history_instead_of_the_snapshot(self):
        history = [self.release("Pong-B4.1-260101-1111", "a", date="2026-10-19T00:00:00Z"),
                   self.release("Pong-B4.1-260818-1726", "b", date="2026-09-01T00:00:00Z")]
        with patch.object(catalog, "fetch_history", return_value=history) as fetch:
            row = catalog.select_entry("phone-2", catalog.LATEST, Path("does-not-exist.json"))
        self.assertEqual(row["sha"], "b" * 40)
        self.assertEqual(set(fetch.call_args.args[0]), {"phone-2"})

    def test_latest_ambiguity_is_not_silently_resolved(self):
        history = [self.release("Pong-B4.1-260818-1726", "a"), self.release("Pong-B4.1-260818-1726", "b")]
        with patch.object(catalog, "fetch_history", return_value=history), self.assertRaisesRegex(ValueError, "存在歧义"):
            catalog.select_entry("phone-2", catalog.LATEST)

    def test_shared_branch_ancestors_do_not_leak_another_models_builds(self):
        device = source.device_for("cmf-phone-1")
        history = [{"repository": device["repository"], "branch": "mt6878/Tetris/16b", "sha": "a" * 40,
                    "versions": ["OS 4.1(Galaga-B4.1-260101-1111)"], "readme": "# CMF by NOTHING Phone 1 Release Note"}]
        self.assertEqual(catalog.make_entries(device["id"], device, history), [])

    def test_same_version_and_commit_on_two_branches_is_deduplicated(self):
        device = source.device_for("phone-2")
        history = [self.release("Pong-B4.1-260101-1111", "a", branch="sm8475/b/mr"),
                   self.release("Pong-B4.1-260101-1111", "a", branch="sm8475/v/mr")]
        self.assertEqual(len(catalog.make_entries(device["id"], device, history)), 1)

    def test_tampered_catalog_device_branch_fails(self):
        invalid = copy.deepcopy(self.snapshot)
        invalid["entries"][0]["branch"] = "mt6878/Galaxian/v"
        with self.assertRaisesRegex(ValueError, "another device"):
            catalog.validate_catalog(invalid)

    def test_menu_source_cli_preserves_build_name_and_custom_settings(self):
        row = self.snapshot["entries"][0]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            settings_path.write_text('{"version_name":"Custom","build_time":"2026-10-19 12:30:00 UTC"}')
            output = root / "plan.json"
            args = ["nothing.source", "--model", row["model"], "--version", row["label"], "--menu",
                    "--settings", str(settings_path), "--output", str(output)]
            metadata = dict(android_version="android12", kernel_version="5.10", sub_level="252", kmi_generation="9", clang_version="r416183b")
            with patch("sys.argv", args), patch.object(source, "resolve", return_value=(row["sha"], row["branch"])) as resolve, \
                 patch.object(source, "checkout", return_value=metadata), \
                 patch.dict(os.environ, {"GITHUB_OUTPUT": str(root / "outputs")}), redirect_stdout(io.StringIO()):
                source.main()
            plan = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(resolve.call_args.args[1:3], (row["sha"], row["branch"]))
            self.assertEqual(plan["requested_version"], row["version"])
            self.assertEqual(plan["requested_label"], row["label"])
            self.assertEqual(plan["build_settings"], {"version_name": "Custom", "build_time": "Mon Oct 19 12:30:00 UTC 2026"})


if __name__ == "__main__":
    unittest.main()
