import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nothing import sukisu, upstream


class SukiSUPinTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"android_version": "android12", "kernel_version": "5.10", "sub_level": "252"}

    def test_susfs_uses_compatible_builtin_and_matching_manager(self):
        profile = sukisu.profile_for(self.plan, "SukiSU(41013)", True)
        self.assertEqual(profile["version"], 41013)
        self.assertEqual(profile["uapi_version"], 6)
        self.assertEqual(profile["branch"], "builtin")
        self.assertEqual(profile["source_sha"], "7f1c299af463d008e66e714077eb6a8079ff722b")
        self.assertEqual(profile["manager_commit"], "ffa00c0700604216de8a86ad9c74226af1add0d5")
        self.assertEqual(profile["susfs_sha"], "c06d5f8ef2b53d04c5e1c6b86dc3076f1e0e9f33")
        self.assertEqual(sukisu.profile_for(self.plan, "SukiSU", True), profile)

    def test_without_susfs_uses_main_before_breaking_extension_change(self):
        profile = sukisu.profile_for(self.plan, "SukiSU(41013)", False)
        self.assertEqual(profile["branch"], "main")
        self.assertEqual(profile["source_sha"], profile["manager_commit"])
        self.assertNotIn("susfs_sha", profile)

    def test_other_variants_are_not_pinned_and_unknown_susfs_base_fails(self):
        for variant in ("None", "Official", "BakaSU"):
            self.assertIsNone(sukisu.profile_for(self.plan, variant, True))
        with self.assertRaisesRegex(ValueError, "No pinned"):
            sukisu.profile_for({"android_version": "android16", "kernel_version": "6.12"}, "SukiSU", True)

    def test_pinned_variant_inherits_kpm_and_susfs_export_paths(self):
        _, schema = upstream.load_upstream()
        context = upstream.patch_context(self.plan, {"ksu_variant": "SukiSU(41013)", "enable_susfs": True,
                                                     "export_susfs_patches": True, "use_kpm": "enabled (开启)"}, schema)
        self.assertEqual(context["ksu_variant"], "SukiSU")
        self.assertTrue(context["export_susfs_patches"])
        self.assertEqual(context["use_kpm"], "enabled (开启)")

    def test_selection_overrides_floating_upstream_branch_with_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = root / "plan.json"
            profile = sukisu.profile_for(self.plan, "SukiSU(41013)", True)
            plan.write_text(json.dumps({"ksu_profile": profile}))
            env = root / "env"
            with patch.dict(os.environ, {"GITHUB_ENV": str(env)}), \
                 patch("sys.argv", ["nothing.sukisu", "select", "--plan", str(plan)]):
                sukisu.main()
            self.assertEqual(env.read_text(), "BRANCH=-s " + profile["source_sha"] + "\n")

    def test_susfs_is_checked_out_to_exact_commit_and_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            for value in ("old", "new"):
                (root / "source").write_text(value)
                subprocess.run(["git", "-C", str(root), "add", "source"], check=True)
                subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                "commit", "--quiet", "-m", value], check=True)
                if value == "old":
                    fixed = sukisu.git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(ValueError, "source mismatch"):
                sukisu.verify_source(root, fixed)
            sukisu.pin_susfs({"susfs_sha": fixed}, root)
            sukisu.verify_source(root, fixed)


if __name__ == "__main__":
    unittest.main()
