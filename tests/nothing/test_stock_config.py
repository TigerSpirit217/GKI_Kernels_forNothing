import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from nothing import stock_config
from nothing.package import package


class StockConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kernel = self.root / "common"
        (self.kernel / "kernel").mkdir(parents=True)
        self.makefile = self.kernel / "kernel/Makefile"
        self.original = "# kernel rules\n" + stock_config.ORIGINAL_RULE + "\n\t$(call filechk,cat)\n"
        self.makefile.write_text(self.original, encoding="utf-8")
        (self.kernel / "arch/arm64/configs").mkdir(parents=True)
        self.defconfig = self.kernel / "arch/arm64/configs/gki_defconfig"
        self.defconfig.write_text("CONFIG_KSU=y\nCONFIG_KSU_SUSFS=y\n", encoding="utf-8")
        profile = json.loads((stock_config.PROFILES / "manifest.json").read_text(encoding="utf-8"))["profiles"][0]
        self.plan = {key: profile[key] for key in stock_config.MATCH_FIELDS}
        self.plan["device"] = {"id": profile["device_id"]}
        self.factory = (stock_config.PROFILES / profile["file"]).read_bytes()

    def prepare(self):
        return stock_config.prepare(self.kernel, self.plan)

    def image(self, config):
        path = self.root / "Image"
        path.write_bytes(b"ARM64 Image fixture\0IKCFG_ST" + gzip.compress(config, mtime=0) + b"IKCFG_ED")
        return path

    def test_known_device_uses_recorded_factory_config_without_changing_build_options(self):
        record = self.prepare()
        self.assertEqual(record["mode"], "factory")
        self.assertEqual((self.kernel / stock_config.DESTINATION).read_bytes(), self.factory)
        self.assertIn(b"# Linux/arm64 5.10.252 Kernel Configuration", self.factory)
        self.assertIn(b"CONFIG_CLANG_VERSION=120005", self.factory)
        self.assertNotIn(b"CONFIG_KSU=", self.factory)
        self.assertEqual(self.defconfig.read_text(), "CONFIG_KSU=y\nCONFIG_KSU_SUSFS=y\n")
        stock_config.verify(self.kernel, record, self.image(self.factory))
        self.assertEqual(self.prepare(), record)  # A retry uses the same profile.

    def test_different_device_or_source_never_reuses_the_phone2_profile(self):
        for field, value in (("device", {"id": "phone-3a"}), ("source_sha", "a" * 40),
                             ("android_version", "android13"), ("sub_level", "253"),
                             ("kmi_generation", "10")):
            with self.subTest(field=field):
                plan = {**self.plan, field: value}
                record = stock_config.prepare(self.kernel, plan)
                self.assertEqual(record["mode"], "actual")
                self.assertEqual(self.makefile.read_text(), self.original)
                self.assertFalse((self.kernel / stock_config.DESTINATION).exists())
                stock_config.verify(self.kernel, record)

    def test_switching_sources_removes_a_stale_override(self):
        self.prepare()
        record = stock_config.prepare(self.kernel, {**self.plan, "source_sha": "a" * 40})
        self.assertEqual(record["mode"], "actual")
        self.assertEqual(self.makefile.read_text(), self.original)
        stock_config.verify(self.kernel, record)

    def test_corrupt_profile_is_rejected_before_modifying_the_kernel(self):
        profiles = self.root / "profiles"
        profiles.mkdir()
        manifest = json.loads((stock_config.PROFILES / "manifest.json").read_text(encoding="utf-8"))
        manifest["profiles"][0]["file"] = "factory.config"
        (profiles / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (profiles / "factory.config").write_bytes(self.factory + b"CONFIG_KSU=y\n")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            stock_config.prepare(self.kernel, self.plan, profiles)
        self.assertEqual(self.makefile.read_text(), self.original)
        self.assertFalse((self.kernel / stock_config.DESTINATION).exists())

    def test_upstream_rule_changes_require_adapter_review(self):
        self.makefile.write_text("new config_data generation rule\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "rule changed"):
            self.prepare()
        self.assertFalse((self.kernel / stock_config.DESTINATION).exists())

    def test_post_build_checks_reject_stale_embedded_config_and_modified_override(self):
        record = self.prepare()
        with self.assertRaisesRegex(ValueError, "Embedded IKCONFIG differs"):
            stock_config.verify(self.kernel, record, self.image(b"# Linux/arm64 5.10.107 Kernel Configuration\n"))
        destination = self.kernel / stock_config.DESTINATION
        destination.write_bytes(self.factory + b"# changed after preparation\n")
        with self.assertRaisesRegex(ValueError, "changed after"):
            stock_config.verify(self.kernel, record)

    def test_packaging_rejects_the_wrong_ikconfig_even_with_a_matching_image_hash(self):
        record = self.prepare()
        subprocess.run(["git", "init", "--quiet", str(self.kernel)], check=True)
        out = self.root / "out"
        (out / "arch/arm64/boot").mkdir(parents=True)
        image = out / "arch/arm64/boot/Image"
        image.write_bytes(self.image(b"old generic display config\n").read_bytes())
        (out / ".config").write_text("CONFIG_TMPFS_XATTR=y\nCONFIG_TMPFS_POSIX_ACL=y\n", encoding="utf-8")
        plan = {**self.plan, "stock_config": record,
                "patch_inputs": {"ksu_mode": "禁用KSU"},
                "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest()}
        plan_path = self.root / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Embedded IKCONFIG differs"):
            package(plan_path, self.kernel, out, self.root / "AnyKernel3", self.root / "dist")
        self.assertFalse((self.root / "dist").exists())


if __name__ == "__main__":
    unittest.main()
