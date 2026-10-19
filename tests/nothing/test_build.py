"""Exercise the build script with Kconfig's stale generated-config behavior."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
if os.name == "nt":
    git = shutil.which("git")
    BASH = Path(git).resolve().parents[1] / "bin/bash.exe" if git else None
else:
    BASH = shutil.which("bash")


# Only the compiler/make boundary is replaced. The real build.sh, Python naming
# helper, preflight validation, config validation and diagnostic trap all run.
MAKE_FIXTURE = '''
import json
import os
from pathlib import Path
import sys

out = Path(next(arg[2:] for arg in sys.argv if arg.startswith("O=")))
targets = {"gki_defconfig", "olddefconfig", "syncconfig", "kernelrelease", "Image"}
target = next(arg for arg in sys.argv if arg in targets)
if os.environ.get("NOTHING_TEST_EXPECT_KSU_VERSION"):
    assert "KSU_VERSION=" + os.environ["NOTHING_TEST_EXPECT_KSU_VERSION"] in sys.argv
with open(os.environ["NOTHING_TEST_MAKE_LOG"], "a") as log:
    log.write(target + "\\n")
auto = out / "include/config/auto.conf"
if target == "gki_defconfig":
    auto.parent.mkdir(parents=True)
    config = ('CONFIG_LTO_CLANG_FULL=y\\n# CONFIG_LTO_CLANG_THIN is not set\\nCONFIG_CFI_CLANG=y\\n'
              'CONFIG_LOCALVERSION=""\\nCONFIG_LOCALVERSION_AUTO=y\\n'
              'CONFIG_TMPFS_XATTR=y\\nCONFIG_TMPFS_POSIX_ACL=y\\n')
    (out / ".config").write_text(config)
    auto.write_text(config.replace('"', ''))
elif target == "olddefconfig":
    # Like the real Kconfig frontend, leave an existing auto.conf untouched.
    pass
elif target == "syncconfig":
    auto.write_text("\\n".join(line.replace('"', '') for line in
                    (out / ".config").read_text().splitlines() if line.startswith("CONFIG_")) + "\\n")
elif target == "kernelrelease":
    suffix = next(arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("LOCALVERSION="))
    release = "5.10.252" + suffix
    if "CONFIG_LOCALVERSION_AUTO=y" in auto.read_text().splitlines():
        release += "-android12-9-ged888387f1aa-dirty"
    (out / "include/config/kernel.release").write_text(release + "\\n")
    print(release)
elif target == "Image":
    # Stop after reaching compilation; no fake Image is packaged as a success.
    sys.exit(23)
'''


@unittest.skipUnless(BASH and Path(BASH).is_file(), "Bash is required to test build.sh")
class BuildConfigTests(unittest.TestCase):
    def test_preflight_refreshes_stale_generated_config_before_reading_release(self):
        for name in ("", "Tig", "Ciallo-MadeByTigerSpirit"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                arch = root / "nothing-kernel/common/arch"
                arch.mkdir(parents=True)
                (arch / "Kconfig").write_text("config LTO_CLANG_THIN\n\tbool\n")
                clang = root / "nothing-clang/bin/clang"
                clang.parent.mkdir(parents=True)
                clang.write_text("#!/usr/bin/env bash\necho 'test clang'\n", newline="\n")
                clang.chmod(0o755)
                fakebin = root / "fakebin"
                fakebin.mkdir()
                make = fakebin / "make"
                make.write_text('#!/usr/bin/env bash\nexec "$NOTHING_TEST_PYTHON" "$NOTHING_TEST_MAKE" "$@"\n', newline="\n")
                make.chmod(0o755)
                fixture = root / "make_fixture.py"
                fixture.write_text(MAKE_FIXTURE, newline="\n")
                plan = dict(kernel_version="5.10", sub_level="252", android_version="android12",
                            kmi_generation="9", clang_version="r416183b",
                            patch_inputs={"ksu_mode": "禁用KSU"}, build_settings={"version_name": name})
                plan["ksu_profile"] = {"version": 41013}
                (root / "nothing-plan.json").write_text(json.dumps(plan), encoding="utf-8")
                log = root / "make.log"
                # Git Bash needs /c/... in values later appended to shell PATH.
                workspace = root.as_posix()
                if os.name == "nt":
                    workspace = "/" + workspace[0].lower() + workspace[2:]
                env = dict(os.environ, GITHUB_WORKSPACE=workspace,
                           KERNEL_ROOT=workspace + "/nothing-kernel",
                           PYTHONPATH=str(ROOT), PYTHONUTF8="1", RUNNER_TEMP=root.as_posix(),
                           NOTHING_TEST_PYTHON=Path(sys.executable).as_posix(),
                           NOTHING_TEST_EXPECT_KSU_VERSION="41013",
                           NOTHING_TEST_MAKE=fixture.as_posix(), NOTHING_TEST_MAKE_LOG=str(log),
                           PATH=str(fakebin) + os.pathsep + os.environ["PATH"])
                result = subprocess.run([str(BASH), (ROOT / "nothing/build.sh").as_posix()],
                                        env=env, capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
                stages = log.read_text().splitlines()
                self.assertEqual(stages, ["gki_defconfig", "olddefconfig", "syncconfig", "kernelrelease", "Image"])
                expected = "5.10.252-android12-9" + ("-" + name if name else "")
                release = (root / "build-logs/kernel.release").read_text().strip()
                self.assertEqual(release, expected)
                saved = json.loads((root / "nothing-plan.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["expected_kernel_release"], expected)
                self.assertNotIn("CONFIG_LOCALVERSION_AUTO=y", (root / "build-logs/kernel.auto.conf").read_text())
                config = (root / "build-logs/kernel.config").read_text()
                self.assertIn("CONFIG_LTO_CLANG_THIN=y", config)
                self.assertNotIn("CONFIG_LTO_CLANG_FULL=y", config)
                self.assertIn("CONFIG_CFI_CLANG=y", config)


if __name__ == "__main__":
    unittest.main()
