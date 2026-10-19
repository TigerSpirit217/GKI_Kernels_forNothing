from pathlib import Path
import tempfile
import unittest

from nothing.compile_config import prepare, verify


class CompileConfigTests(unittest.TestCase):
    def test_full_lto_changes_to_thin_and_cfi_and_patches_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "arch").mkdir()
            (root / "arch/Kconfig").write_text("config LTO_CLANG_THIN\n\tbool\n")
            path = root / ".config"
            path.write_text("CONFIG_LTO_CLANG_FULL=y\n# CONFIG_LTO_CLANG_THIN is not set\n"
                            "CONFIG_CFI_CLANG=y\nCONFIG_KPM=y\n")
            settings = prepare(root, root)
            verify(root, settings)
            config = path.read_text()
            self.assertNotIn("CONFIG_LTO_CLANG_FULL=y", config)
            self.assertIn("CONFIG_KPM=y", config)
            self.assertIn("CONFIG_CFI_CLANG=y", config)
            path.write_text(config.replace("CONFIG_LTO_CLANG_THIN=y", "CONFIG_LTO_CLANG_FULL=y"))
            with self.assertRaisesRegex(ValueError, "ThinLTO"):
                verify(root, settings)
            path.write_text(config.replace("CONFIG_CFI_CLANG=y", "# CONFIG_CFI_CLANG is not set"))
            with self.assertRaisesRegex(ValueError, "CFI_CLANG"):
                verify(root, settings)

    def test_unknown_kconfig_is_rejected_before_modifying_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "arch").mkdir()
            (root / "arch/Kconfig").write_text("config DIFFERENT_LTO\n\tbool\n")
            (root / ".config").write_text("CONFIG_KPM=y\n")
            with self.assertRaisesRegex(ValueError, "no LTO_CLANG_THIN"):
                prepare(root, root)
            self.assertEqual((root / ".config").read_text(), "CONFIG_KPM=y\n")
