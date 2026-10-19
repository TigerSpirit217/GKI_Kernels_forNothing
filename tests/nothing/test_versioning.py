import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nothing import versioning


class VersioningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kernel, self.out = self.root / "kernel", self.root / "out"
        self.kernel.mkdir()
        self.out.mkdir()
        self.config = self.out / ".config"
        self.config.write_text('CONFIG_LOCALVERSION="-stock"\nCONFIG_LOCALVERSION_AUTO=y\nCONFIG_KPM=y\n')
        self.plan = dict(kernel_version="5.10", sub_level="252", android_version="android12",
                         kmi_generation="9", build_settings={"version_name": ""})

    def test_empty_name_produces_only_fixed_base_and_kmi(self):
        suffix = versioning.prepare_version(self.plan, self.kernel, self.out)
        self.assertEqual(suffix, "-android12-9")
        self.assertEqual(versioning.expected_release(self.plan), "5.10.252-android12-9")
        config = self.config.read_text()
        self.assertNotIn("CONFIG_LOCALVERSION_AUTO=y", config)
        self.assertIn('CONFIG_LOCALVERSION=""\n', config)
        self.assertIn("# CONFIG_LOCALVERSION_AUTO is not set", config)
        self.assertIn("CONFIG_KPM=y", config)

    def test_custom_name_is_last_after_kmi(self):
        self.plan["build_settings"]["version_name"] = "Tig"
        self.assertEqual(versioning.prepare_version(self.plan, self.kernel, self.out), "-android12-9-Tig")
        self.assertEqual(versioning.expected_release(self.plan), "5.10.252-android12-9-Tig")

    def test_fixed_part_uses_selected_source(self):
        self.plan.update(kernel_version="6.6", sub_level="57", android_version="android15", kmi_generation="8")
        self.assertEqual(versioning.expected_release(self.plan), "6.6.57-android15-8")

    def test_source_and_output_localversion_files_cannot_add_extra_labels(self):
        for directory in (self.kernel, self.out):
            (directory / "localversion").write_text("-android12-9")
            (directory / "localversion-extra").write_text("-stock")
            (directory / "localversion~backup").write_text("-backup")
            (directory / "localversion-directory").mkdir()
        self.assertEqual(versioning.prepare_version(self.plan, self.kernel, self.out), "-android12-9")
        for directory in (self.kernel, self.out):
            self.assertEqual((directory / "localversion").read_text(), "")
            self.assertEqual((directory / "localversion-extra").read_text(), "")
            self.assertEqual((directory / "localversion~backup").read_text(), "-backup")

    def test_prepare_is_idempotent_with_auto_already_disabled(self):
        self.config.write_text('CONFIG_KPM=y\n# CONFIG_LOCALVERSION_AUTO is not set\n')
        versioning.prepare_version(self.plan, self.kernel, self.out)
        before = self.config.read_bytes()
        versioning.prepare_version(self.plan, self.kernel, self.out)
        self.assertEqual(self.config.read_bytes(), before)

    def test_invalid_name_does_not_mutate_config(self):
        self.plan["build_settings"]["version_name"] = "bad name"
        before = self.config.read_bytes()
        with self.assertRaisesRegex(ValueError, "Invalid custom"):
            versioning.prepare_version(self.plan, self.kernel, self.out)
        self.assertEqual(self.config.read_bytes(), before)

    def test_diagnostic_65_byte_failure_is_rejected_and_corrected(self):
        failed = "5.10.252-Ciallo-MadeByTigerSpirit-android12-9-ged888387f1aa-dirty"
        self.assertEqual(len(failed.encode()), 65)
        with self.assertRaisesRegex(ValueError, "65.*64"):
            versioning.validate_release(self.plan, failed)
        self.plan["build_settings"]["version_name"] = "Ciallo-MadeByTigerSpirit"
        corrected = "5.10.252-android12-9-Ciallo-MadeByTigerSpirit"
        self.assertEqual(versioning.validate_release(self.plan, corrected), corrected)

    def test_unexpected_suffix_order_hash_or_whitespace_is_rejected(self):
        self.plan["build_settings"]["version_name"] = "Tig"
        for value in ["5.10.252-Tig-android12-9", "5.10.252-android12-9-Tig-dirty",
                      "5.10.252-android12-9-Tig+", "5.10.252-android12-9-Tig-g123",
                      "5.10.252-android12-9-Tig\n", "5.10.2520-android12-9-Tig"]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "预期不符"):
                versioning.validate_release(self.plan, value)

    def test_release_limit_counts_bytes_and_accepts_64(self):
        self.plan["build_settings"]["version_name"] = "T" * 32
        padding = 64 - len(versioning.expected_release(self.plan).encode())
        self.plan["sub_level"] += "0" * padding
        release = versioning.expected_release(self.plan)
        self.assertEqual(len(release.encode()), 64)
        self.assertEqual(versioning.validate_release(self.plan, release), release)
        with self.assertRaisesRegex(ValueError, "65.*64"):
            versioning.validate_release(self.plan, release + "a")
        with self.assertRaisesRegex(ValueError, "66.*64"):
            versioning.validate_release(self.plan, "中" * 22)

    def test_preflight_records_the_verified_release_in_plan(self):
        path = self.root / "plan.json"
        path.write_text(json.dumps(self.plan))
        with patch("sys.argv", ["nothing.versioning", "check", "--plan", str(path),
                                "--release", "5.10.252-android12-9"]):
            versioning.main()
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["expected_kernel_release"], "5.10.252-android12-9")


if __name__ == "__main__":
    unittest.main()
