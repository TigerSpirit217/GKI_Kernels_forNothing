import copy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

from nothing import seccomp, source
from nothing.verify import verify_config


# Minimized app-start paths from SukiSU builtin@7f1c299a (GPL-2.0).
# Keep all six independent manager/allowlist cases, including zygote_next.
LSM = """
static int handle_zygote_setresuid(uid_t ruid) {
    if (likely(ksu_is_manager_appid_valid()) && unlikely(is_uid_manager(ruid))) {
        disable_seccomp();
    }
    if (ksu_is_allow_uid_for_current(ruid)) {
        disable_seccomp();
    }
}
static int handle_zygote_next_setresuid(uid_t ruid) {
    if (likely(ksu_is_manager_appid_valid()) && unlikely(is_uid_manager(ruid))) {
        disable_seccomp();
    }
    if (ksu_is_allow_uid_for_current(ruid)) {
        disable_seccomp();
    }
}
static int ksu_task_fix_setuid(uid_t new_uid) {
    if (unlikely(is_uid_manager(new_uid))) {
        disable_seccomp();
    }
    if (ksu_is_allow_uid_for_current(new_uid)) {
        disable_seccomp();
    }
}
"""
CACHE = """
struct action_cache {
    DECLARE_BITMAP(allow_native, SECCOMP_ARCH_NATIVE_NR);
    DECLARE_BITMAP(allow_compat, SECCOMP_ARCH_COMPAT_NR);
};
struct seccomp_filter {
    /* Vendor-specific fields before the cache must be preserved. */
    unsigned long vendor_field;
    struct action_cache cache;
};
bool seccomp_cache_check_allow(struct seccomp_filter *filter, int nr) {
    return test_bit(nr, filter->cache.allow_native);
}
"""


class SeccompTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {
            "common/kernel/seccomp.c": CACHE,
            "common/arch/arm64/include/asm/seccomp.h": "#define SECCOMP_ARCH_NATIVE 1\n#define SECCOMP_ARCH_COMPAT 2\n",
            "common/arch/arm64/include/asm/unistd32.h": "#define __NR_reboot 88\n",
            "common/arch/arm64/configs/gki_defconfig": "# CONFIG_SECCOMP is not set\nCONFIG_SECCOMP_FILTER=n\nCONFIG_KSU=y\n",
            "KernelSU/kernel/hook/lsm_hook.c": LSM,
            "KernelSU/kernel/ksu.c": '#include "hook/lsm_hook.c"\n',
            "KernelSU/kernel/policy/app_profile.c": "void escalate() { disable_seccomp(); }\n",
        }
        for name, text in self.files.items():
            self.write(name, text)
        self.plan = {"nothing_options": {"enable_seccomp": True},
                     "patch_inputs": {"ksu_variant": "SukiSU", "ksu_mode": "关闭"}}

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_builtin_retains_filters_and_leaves_root_escalation_untouched(self):
        record = seccomp.apply(self.root, self.plan)
        self.assertEqual(record["mode"], "patched-builtin")
        hook = (self.root / "KernelSU/kernel/hook/lsm_hook.c").read_text()
        self.assertEqual(hook.count(seccomp.CALL), 6)
        self.assertNotIn("disable_seccomp();", hook)
        self.assertEqual((self.root / "KernelSU/kernel/policy/app_profile.c").read_text(),
                         self.files["KernelSU/kernel/policy/app_profile.c"])
        # No cloned private struct: the helper compiles inside this kernel's TU.
        self.assertTrue((self.root / "common/kernel/seccomp.c").read_text().startswith(CACHE))
        helper = (self.root / "common/kernel/nothing_ksu_seccomp.inc").read_text()
        self.assertNotIn("struct seccomp_filter {", helper)
        self.assertIn("NOTHING_KSU_COMPAT_NR_REBOOT, filter->cache.allow_compat", helper)
        config = (self.root / "common/arch/arm64/configs/gki_defconfig").read_text()
        self.assertEqual(config.count("CONFIG_SECCOMP=y"), 1)
        self.assertIn("CONFIG_SECCOMP_FILTER=y", config)
        seccomp.verify_applied(self.root, record)
        before = self.snapshot()
        self.assertEqual(seccomp.apply(self.root, self.plan), record)
        self.assertEqual(before, self.snapshot())

    def test_disabled_option_performs_no_source_reads_or_writes(self):
        plan = copy.deepcopy(self.plan)
        plan["nothing_options"]["enable_seccomp"] = False
        before = self.snapshot()
        self.assertIsNone(seccomp.apply(self.root / "nonexistent", plan))
        self.assertEqual(before, self.snapshot())

    def test_changed_hooks_fail_without_partial_writes(self):
        for changed in (LSM.replace("is_uid_manager(ruid)", "manager_changed(ruid)", 1),
                        LSM + "void unknown_path() { disable_seccomp(); }\n"):
            with self.subTest(changed=changed):
                self.write("KernelSU/kernel/hook/lsm_hook.c", changed)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    seccomp.apply(self.root, self.plan)
                self.assertEqual(before, self.snapshot())
                self.assertNotIn("seccomp_filter", self.plan)

    def test_missing_vendor_cache_or_compat_number_fails_before_writes(self):
        for name, changed in (("common/kernel/seccomp.c", CACHE.replace("struct action_cache cache;", "unsigned long cache;")),
                              ("common/arch/arm64/include/asm/unistd32.h", "")):
            with self.subTest(name=name):
                self.write(name, changed)
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "cache|compat"):
                    seccomp.apply(self.root, self.plan)
                self.assertEqual(before, self.snapshot())
                self.write(name, self.files[name])

    def test_source_drift_is_detected_before_compilation_and_packaging(self):
        record = seccomp.apply(self.root, self.plan)
        self.write("KernelSU/kernel/hook/lsm_hook.c", LSM)
        with self.assertRaisesRegex(ValueError, "changed after integration"):
            seccomp.verify_applied(self.root, record)
        with self.assertRaisesRegex(ValueError, "no verified source integration"):
            seccomp.verify_applied(self.root, None)

    def test_main_already_supports_filter_mode_without_builtin_patch(self):
        self.write("KernelSU/kernel/hook/lsm_hook.c", "/* main uses setuid_hook.c */\n")
        self.write("KernelSU/kernel/hook/setuid_hook.c", "ksu_seccomp_allow_cache(filter, nr);\n" * 2)
        self.write("KernelSU/kernel/infra/seccomp_cache.c", "/* upstream implementation */\n")
        self.write("KernelSU/kernel/Kbuild", "kernelsu-objs += hook/setuid_hook.o infra/seccomp_cache.o\n")
        before = {name: data for name, data in self.snapshot().items() if name.startswith("KernelSU/")}
        record = seccomp.apply(self.root, self.plan)
        self.assertEqual(record["mode"], "upstream")
        self.assertEqual(before, {name: data for name, data in self.snapshot().items() if name.startswith("KernelSU/")})
        self.assertFalse((self.root / "common/kernel/nothing_ksu_seccomp.inc").exists())

    def test_kconfig_cannot_silently_drop_requested_filter_support(self):
        out = self.root / "out"
        out.mkdir()
        base = "CONFIG_TMPFS_XATTR=y\nCONFIG_TMPFS_POSIX_ACL=y\nCONFIG_KSU=y\n"
        self.write("out/.config", base + "CONFIG_SECCOMP=y\n")
        with self.assertRaisesRegex(ValueError, "CONFIG_SECCOMP_FILTER"):
            verify_config(self.root / "common", out, self.plan["patch_inputs"], self.plan["nothing_options"])

    def test_workflow_applies_nothing_patch_after_upstream_before_compile(self):
        workflow = yaml.safe_load((source.ROOT / ".github/workflows/nothing-build.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["nothing"]["steps"]
        inherited = next(i for i, s in enumerate(steps) if s.get("uses") == "./.nothing-actions")
        integration = next(i for i, s in enumerate(steps) if s.get("run") == "python -m nothing.seccomp")
        compile_step = next(i for i, s in enumerate(steps) if "nothing.run" in s.get("run", ""))
        self.assertLess(inherited, integration)
        self.assertLess(integration, compile_step)
        self.assertIn("inputs.enable_seccomp", steps[integration]["if"])


@unittest.skipUnless(shutil.which("cc"), "A C compiler is required for the cache-helper behavior test")
class CacheHelperTests(unittest.TestCase):
    def test_filter_mode_null_filter_and_native_compat_syscall_bits(self):
        # Execute the actual shipped helper with a tiny kernel API shim. Check
        # that filtering remains active and ARM/AArch64 use distinct syscall bits.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("linux/nothing_ksu_seccomp.h", "linux/sched/signal.h", "asm/unistd.h"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("")
            helper = (seccomp.PATCHES / "seccomp_filter.inc").as_posix()
            program = root / "test.c"
            program.write_text('''
#include <assert.h>
#define CONFIG_SECCOMP_FILTER 1
#define SECCOMP_ARCH_NATIVE 1
#define SECCOMP_ARCH_COMPAT 2
#define SECCOMP_MODE_FILTER 2
#define __NR_reboot 142
#define NOTHING_KSU_COMPAT_NR_REBOOT 88
#define spin_lock_irqsave(lock, flags) ((flags) = 0)
#define spin_unlock_irqrestore(lock, flags) ((void)0)
#define set_bit(nr, bits) ((bits)[nr] = 1)
struct seccomp_filter { struct { int allow_native[512]; int allow_compat[512]; } cache; };
struct task { struct { int mode; struct seccomp_filter *filter; } seccomp; } task;
#define current (&task)
''' + '#include "' + helper + '"\n' + '''
int main(void) {
    struct seccomp_filter filter = {0};
    task.seccomp.mode = 2;
    nothing_ksu_seccomp_allow_reboot(); /* null filter is safe */
    task.seccomp.filter = &filter;
    nothing_ksu_seccomp_allow_reboot();
    assert(task.seccomp.mode == 2 && task.seccomp.filter == &filter);
    for (int i = 0; i < 512; i++) {
        assert(filter.cache.allow_native[i] == (i == 142));
        assert(filter.cache.allow_compat[i] == (i == 88));
    }
    filter.cache.allow_native[142] = 0;
    task.seccomp.mode = 0;
    nothing_ksu_seccomp_allow_reboot();
    assert(filter.cache.allow_native[142] == 0);
    return 0;
}
''')
            executable = root / "test"
            subprocess.run([shutil.which("cc"), "-std=c99", "-I", str(root), str(program), "-o", str(executable)],
                           check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
