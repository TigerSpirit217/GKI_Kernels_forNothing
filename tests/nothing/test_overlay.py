import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

import yaml

from nothing import source, upstream
from nothing.package import package
from nothing.verify import rejects, verify_config


class SourceTests(unittest.TestCase):
    def test_model_aliases_and_non_gki(self):
        self.assertEqual(source.device_for("Nothing Phone (2)")["id"], "phone-2")
        self.assertEqual(source.device_for("A059P")["id"], "phone-3a-pro")
        self.assertEqual(source.device_for("CMF Phone (1)")["id"], "cmf-phone-1")
        with self.assertRaisesRegex(ValueError, "non-GKI"):
            source.device_for("Phone (1)")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            source.device_for("unknown-device")

    def test_annotated_tags_are_peeled(self):
        with patch.object(source, "git", return_value="a refs/tags/v1\nb refs/tags/v1^{}\nc refs/heads/main"):
            self.assertEqual(source.remote_refs("NothingOSS/test")["refs/tags/v1"], "b")

    def history_fixture(self, path):
        # Two official branches with several releases each, plus an unrelated
        # README edit which still mentions an old version in unchanged context.
        branches = {
            "sm7635/v/mr": [("a" * 40, "+- NOS 3.2(Asteroids-V3.2-250717-1803)"),
                              ("b" * 40, "+- NOS 3.1(Asteroids-V3.1-250302-1856)")],
            "sm7635/b/mr": [("c" * 40, "+- NOS 3.2(Asteroids-B3.2-251201-1111)"),
                              ("d" * 40, " - NOS 3.1(Asteroids-V3.1-250302-1856)\n+fix documentation")],
        }
        url = urlsplit(path)
        if url.path.endswith("/commits"):
            head = parse_qs(url.query)["sha"][0]
            return [{"sha": sha, "commit": {"message": "Update release notes"}} for sha, _ in branches[head]]
        sha = url.path.split("/")[-1]
        for rows in branches.values():
            for candidate, diff in rows:
                if candidate == sha:
                    return {"files": [{"filename": "README.md", "patch": diff}]}
        self.fail("Unexpected API path: " + path)

    def test_all_branches_and_versions_within_a_branch(self):
        with patch.object(source, "api", side_effect=self.history_fixture):
            releases = source.release_history("NothingOSS/test", {"sm7635/v/mr": "sm7635/v/mr", "sm7635/b/mr": "sm7635/b/mr"})
        self.assertEqual(len(releases), 3)
        self.assertEqual(source.select_release(releases, "Asteroids-V3.1-250302-1856")[0], "b" * 40)
        self.assertEqual(source.select_release(releases, "Asteroids-V3.2-250717-1803")[0], "a" * 40)
        self.assertEqual(source.select_release(releases, "3.1")[0], "b" * 40)
        with self.assertRaisesRegex(ValueError, "2 release commit matches"):
            source.select_release(releases, "3.2")
        with self.assertRaisesRegex(ValueError, "0 release commit matches"):
            source.select_release(releases, "3.10")

    def test_branch_filter_disambiguates_versions(self):
        refs = {"refs/heads/sm7635/v/mr": "sm7635/v/mr", "refs/heads/sm7635/b/mr": "sm7635/b/mr"}
        with patch.object(source, "remote_refs", return_value=refs), patch.object(source, "api", side_effect=self.history_fixture):
            sha, ref = source.resolve("NothingOSS/test", "3.2", "sm7635/v/mr")
            self.assertEqual(sha, "a" * 40)
            self.assertIn("sm7635/v/mr", ref)
            with self.assertRaisesRegex(ValueError, "Unknown official branch"):
                source.resolve("NothingOSS/test", "3.2", "nonexistent")

    def test_shared_repository_branches_are_filtered_by_model(self):
        refs = {"refs/heads/mt6878/Tetris/v": "a" * 40,
                "refs/heads/mt6878/Galaga/v": "b" * 40,
                "refs/heads/mt6878/Galaxian/v": "c" * 40}
        cmf = source.device_for("cmf-phone-1")
        self.assertEqual(source.device_branches(refs, cmf["branch_pattern"]), {"mt6878/Tetris/v": "a" * 40})
        with self.assertRaisesRegex(ValueError, "another model"):
            source.device_branches(refs, cmf["branch_pattern"], "mt6878/Galaga/v")
        with patch.object(source, "remote_refs", return_value=refs):
            with self.assertRaisesRegex(ValueError, "another model"):
                source.resolve(cmf["repository"], "mt6878/Galaxian/v", branch_pattern=cmf["branch_pattern"])

    def test_phone_3a_and_4a_use_separate_branches(self):
        refs = {"refs/heads/sm7635/v/mr": "a" * 40,
                "refs/heads/sm7635/b/mr": "b" * 40,
                "refs/heads/sm7635/b/mr_Frogger": "c" * 40}
        three = source.device_branches(refs, source.device_for("phone-3a")["branch_pattern"])
        four = source.device_branches(refs, source.device_for("phone-4a")["branch_pattern"])
        self.assertEqual(len(three), 2)
        self.assertEqual(four, {"sm7635/b/mr_Frogger": "c" * 40})

    def test_same_commit_on_multiple_branches_is_not_ambiguous(self):
        releases = [{"sha": "a" * 40, "branch": name, "versions": ["OS 2.0.1"]} for name in ["old", "new"]]
        self.assertEqual(source.select_release(releases, "2.0.1")[0], "a" * 40)

    def test_os_prefix_with_dot_is_a_valid_release(self):
        releases = [{"sha": "a" * 40, "branch": "old", "versions": ["4. OS.2.5.5"]}]
        self.assertEqual(source.select_release(releases, "2.5.5")[0], "a" * 40)

    def test_history_and_large_commit_file_lists_are_paginated(self):
        calls = []
        def fake_api(path):
            calls.append(path)
            url = urlsplit(path)
            query = parse_qs(url.query)
            page = query.get("page", ["1"])[0]
            if url.path.endswith("/commits"):
                if page == "1":
                    return [{"sha": "a" * 40, "commit": {"message": "Release"}}] * 100
                return [{"sha": "b" * 40, "commit": {"message": "Earlier release"}}]
            if url.path.endswith("a" * 40):
                if page == "1":
                    return {"files": [{"filename": f"file{i}"} for i in range(100)]}
                return {"files": [{"filename": "README.md", "patch": "+- NOS 3.2(Build-3.2-1)"}]}
            return {"files": [{"filename": "README.md", "patch": "+- NOS 3.1(Build-3.1-1)"}]}
        with patch.object(source, "api", side_effect=fake_api):
            releases = source.release_history("NothingOSS/test", {"branch": "head"})
        self.assertEqual(source.select_release(releases, "3.1")[0], "b" * 40)
        self.assertTrue(any("page=2" in path and "/commits?" in path for path in calls))
        self.assertEqual(sum("/commits/" + "a" * 40 in path for path in calls), 2)

    def test_exact_ref_does_not_use_default_branch(self):
        with patch.object(source, "remote_refs", return_value={"refs/heads/old": "a" * 40, "refs/heads/new": "b" * 40}):
            self.assertEqual(source.resolve("NothingOSS/test", "old"), ("a" * 40, "refs/heads/old"))

    def test_sha_must_belong_to_selected_branch(self):
        with patch.object(source, "remote_refs", return_value={"refs/heads/old": "b" * 40}), \
             patch.object(source, "api", side_effect=[{"sha": "a" * 40}, {"status": "diverged"}]):
            with self.assertRaisesRegex(ValueError, "not an ancestor"):
                source.resolve("NothingOSS/test", "a" * 40, "old")

    def test_invalid_versions_rejected_before_network(self):
        for value in ["", "latest;echo bad", '$(whoami)', 'branch\nother', "-b"]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Version must"):
                source.resolve("NothingOSS/test", value)

    def test_metadata_uses_kernel_base_not_phone_android_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Makefile").write_text("VERSION = 6\nPATCHLEVEL = 1\nSUBLEVEL = 75\n")
            (root / "build.config.constants").write_text("BRANCH=android14-6.1\nCLANG_VERSION=r487747c\nKMI_GENERATION=11\n")
            data = source.parse_build_metadata(root)
            self.assertEqual(data["android_version"], "android14")
            self.assertEqual(data["sub_level"], "75")
            self.assertEqual(data["kmi_generation"], "11")
            (root / "build.config.constants").write_text("BRANCH=android14-6.6\nCLANG_VERSION=r487747c\n")
            with self.assertRaisesRegex(ValueError, "disagree"):
                source.parse_build_metadata(root)

    def test_old_clang_declared_as_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Makefile").write_text("VERSION = 5\nPATCHLEVEL = 10\nSUBLEVEL = 209\n")
            (root / "build.config.common").write_text("BRANCH=android12-5.10\nKMI_GENERATION=9\nCLANG_PREBUILT_BIN=prebuilts/clang-r416183b/bin\n")
            self.assertEqual(source.parse_build_metadata(root)["clang_version"], "r416183b")


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.steps, self.schema = upstream.load_upstream()
        self.plan = dict(android_version="android14", kernel_version="6.1", sub_level="75")

    def test_disabled_booleans_remain_booleans(self):
        context = upstream.patch_context(self.plan, {"use_zram": False, "use_bbg": False}, self.schema)
        self.assertIs(context["use_zram"], False)
        self.assertIs(context["enable_susfs"], False)
        self.assertEqual(context["ksu_mode"], "禁用KSU")
        action = upstream.generate_action(self.steps)
        zram = next(step for step in action["runs"]["steps"] if step["name"] == "配置 ZRAM 选项")
        self.assertEqual(zram["if"], "${{ fromJSON(inputs.context).use_zram }}")

    def test_missing_and_dangerous_options_fail(self):
        for options in [{"use_zram": "false"}, {"use_bbg": 1}, {"does_not_exist": True},
                        {"enable_susfs": True}, {"android_version": "android15"},
                        {"droidspaces": "$(id)"}, {"use_kpm": "enabled (开启)"}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                upstream.patch_context(self.plan, options, self.schema)

    def test_original_patch_bodies_are_inherited(self):
        action = upstream.generate_action(self.steps)
        for converted in action["runs"]["steps"]:
            if converted["name"].startswith("Nothing 固定"):
                continue
            original = next(step for step in self.steps if step.get("name") == converted["name"])
            self.assertEqual(converted["run"], upstream.rewrite(original["run"]))
        names = [step["name"] for step in action["runs"]["steps"]]
        self.assertNotIn("初始化并同步内核源码", names)
        self.assertNotIn("配置内核名称", names)
        self.assertNotIn("修复 6.6 WiFi/蓝牙兼容性（三星 + 小米）", names)
        self.assertIn("应用 SUSFS 补丁", names)
        self.assertLess(names.index("确定 KernelSU 分支"), names.index("Nothing 固定 SukiSU 41013 提交"))
        self.assertLess(names.index("Nothing 固定 SukiSU 41013 提交"), names.index("添加 KernelSU"))
        self.assertLess(names.index("克隆 SUSFS 源码"), names.index("Nothing 固定 SukiSU 配套 SUSFS 提交"))
        self.assertLess(names.index("Nothing 固定 SukiSU 配套 SUSFS 提交"), names.index("记录 SUSFS 基线快照"))

    def test_new_upstream_feature_is_inherited(self):
        steps = copy.deepcopy(self.steps)
        index = next(i for i, step in enumerate(steps) if step.get("name") == upstream.END)
        steps.insert(index, {"name": "Future upstream patch", "if": "inputs.use_future", "run": "echo future"})
        schema = copy.deepcopy(self.schema)
        schema["use_future"] = {"type": "boolean", "default": False}
        self.assertIs(upstream.patch_context(self.plan, {"use_future": True}, schema)["use_future"], True)
        self.assertIn("Future upstream patch", [step["name"] for step in upstream.generate_action(steps)["runs"]["steps"]])

    def test_anchor_changes_fail_instead_of_skipping_patches(self):
        steps = [step for step in self.steps if step.get("name") != upstream.START]
        with self.assertRaisesRegex(ValueError, "anchor changed"):
            upstream.generate_action(steps)

    def test_no_unadapted_input_or_external_step_references(self):
        rendered = yaml.safe_dump(upstream.generate_action(self.steps), allow_unicode=True)
        self.assertEqual(set(re.findall(r"\binputs\.(\w+)", rendered)), {"context"})
        self.assertNotIn("vars.", rendered)
        action = upstream.generate_action(self.steps)
        ids = {step["id"] for step in action["runs"]["steps"] if "id" in step}
        self.assertTrue(set(re.findall(r"\bsteps\.([\w-]+)", rendered)) <= ids)

    def test_generator_exports_context_environment_and_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = {**self.plan, "device": source.device_for("A065"), "source_sha": "a" * 40}
            plan_path, options_path = root / "plan.json", root / "options.json"
            plan_path.write_text(json.dumps(plan))
            options_path.write_text('{"ksu_variant":"SukiSU(41013)","enable_susfs":true,"use_zram":false}')
            env_path, output_path = root / "env", root / "output"
            with patch.dict(os.environ, {"GITHUB_ENV": str(env_path), "GITHUB_OUTPUT": str(output_path),
                                         "GITHUB_WORKSPACE": str(root)}), \
                 patch("sys.argv", ["nothing.upstream", "--plan", str(plan_path), "--options", str(options_path),
                                    "--output", str(root / "action")]):
                upstream.main()
            saved = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["upstream_workflow_sha256"], hashlib.sha256(upstream.UPSTREAM.read_bytes()).hexdigest())
            self.assertEqual(saved["patch_inputs"]["ksu_variant"], "SukiSU")
            self.assertEqual(saved["ksu_profile"]["version"], 41013)
            self.assertEqual(saved["ksu_profile"]["source_sha"], "7f1c299af463d008e66e714077eb6a8079ff722b")
            self.assertIn("LEGACY_SUKISU_CONFIG=\n", env_path.read_text(encoding="utf-8"))
            self.assertIn("context=", output_path.read_text(encoding="utf-8"))
            action = yaml.safe_load((root / "action/action.yml").read_text(encoding="utf-8"))
            self.assertEqual(action["runs"]["using"], "composite")


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kernel = self.root / "kernel"
        self.kernel.mkdir()
        subprocess.run(["git", "init", "--quiet", str(self.kernel)], check=True)
        self.out = self.root / "build"
        (self.out / "arch/arm64/boot").mkdir(parents=True)
        (self.out / "arch/arm64/boot/Image").write_bytes(b"compiled test Image")
        (self.out / ".config").write_text("CONFIG_TMPFS_XATTR=y\nCONFIG_TMPFS_POSIX_ACL=y\n")
        self.inputs = {"ksu_mode": "禁用KSU", "ksu_variant": "Official"}
        self.plan = {"device": source.device_for("phone-2"), "source_sha": "a" * 40,
                     "requested_version": "sm8475/t", "patch_inputs": self.inputs,
                     "image_sha256": hashlib.sha256(b"compiled test Image").hexdigest()}
        self.plan_path = self.root / "plan.json"
        self.plan_path.write_text(json.dumps(self.plan))
        self.ak = self.root / "AnyKernel3"
        for name in ["tools/ak3-core.sh", "tools/magiskboot", "META-INF/com/google/android/update-binary"]:
            file = self.ak / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("upstream tool")
        (self.ak / "anykernel.sh").write_text("do.devicecheck=0")

    def test_real_zip_has_root_payload_model_check_and_executable_tools(self):
        archive_path = package(self.plan_path, self.kernel, self.out, self.ak, self.root / "dist")
        with ZipFile(archive_path) as archive:
            self.assertEqual(archive.testzip(), None)
            self.assertEqual(archive.read("Image"), b"compiled test Image")
            script = archive.read("anykernel.sh").decode()
            self.assertIn("do.devicecheck=1", script)
            self.assertIn("device.name1=Pong", script)
            self.assertIn("block=boot", script)
            self.assertNotIn("@", script)
            self.assertIn("nothing-build.json", archive.namelist())
            self.assertEqual(archive.getinfo("tools/magiskboot").external_attr >> 16, 0o100755)
        self.assertTrue(Path(str(archive_path) + ".sha256").is_file())

    def test_dropped_feature_fails_before_packaging(self):
        with self.assertRaisesRegex(ValueError, "CONFIG_KSU_SUSFS"):
            verify_config(self.kernel, self.out, {**self.inputs, "enable_susfs": True})

    def test_patch_rejects_fail(self):
        (self.kernel / "conflict.rej").write_text("rejected hunk")
        with self.assertRaisesRegex(ValueError, "Patch rejects remain"):
            package(self.plan_path, self.kernel, self.out, self.ak, self.root / "dist")

    def test_modified_image_fails(self):
        (self.out / "arch/arm64/boot/Image").write_bytes(b"stale Image")
        with self.assertRaisesRegex(ValueError, "differs"):
            package(self.plan_path, self.kernel, self.out, self.ak, self.root / "dist")

    def test_custom_version_name_is_in_zip_and_install_title(self):
        self.plan["build_settings"] = {"version_name": "NothingCustom", "build_time": ""}
        self.plan_path.write_text(json.dumps(self.plan))
        archive_path = package(self.plan_path, self.kernel, self.out, self.ak, self.root / "dist")
        self.assertIn("NothingCustom", archive_path.name)
        with ZipFile(archive_path) as archive:
            self.assertIn("NothingCustom", archive.read("anykernel.sh").decode())

    def test_fixed_sukisu_version_is_in_filename_and_manifest(self):
        self.plan["patch_inputs"] = {"ksu_mode": "关闭", "ksu_variant": "SukiSU"}
        self.plan["ksu_profile"] = {"name": "SukiSU(41013)", "version": 41013}
        with (self.out / ".config").open("a") as config:
            config.write("CONFIG_KSU=y\n")
        self.plan_path.write_text(json.dumps(self.plan))
        archive_path = package(self.plan_path, self.kernel, self.out, self.ak, self.root / "dist")
        self.assertIn("SukiSU(41013)", archive_path.name)
        with ZipFile(archive_path) as archive:
            self.assertEqual(json.loads(archive.read("nothing-build.json"))["ksu_profile"]["version"], 41013)


if __name__ == "__main__":
    unittest.main()
