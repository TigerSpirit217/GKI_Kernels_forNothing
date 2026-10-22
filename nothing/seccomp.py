"""Optional Nothing-only SukiSU seccomp filter-mode integration.

Port of LingLuo17/AnyKernel3 commit 841ab62e8af77ee73a1d1538bd3838e33df093cc.
The cache helper lives in the target kernel's seccomp.c translation unit so
vendor changes to private structure layouts cannot invalidate its offsets.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re


PATCHES = Path(__file__).with_name("patches")
ORIGIN = "https://github.com/LingLuo17/AnyKernel3/commit/841ab62e8af77ee73a1d1538bd3838e33df093cc"
CALL = "nothing_ksu_seccomp_allow_reboot();"
INCLUDE = '#include <linux/nothing_ksu_seccomp.h>\n'
TAIL = '\n#ifdef CONFIG_KSU\n#include "nothing_ksu_seccomp.inc"\n#endif\n'
RULES = (
    (r"if \(likely\(ksu_is_manager_appid_valid\(\)\) && unlikely\(is_uid_manager\(ruid\)\)\)", 2),
    (r"if \(ksu_is_allow_uid_for_current\(ruid\)\)", 2),
    (r"if \(unlikely\(is_uid_manager\(new_uid\)\)\)", 1),
    (r"if \(ksu_is_allow_uid_for_current\(new_uid\)\)", 1),
)
CONFIGS = ("CONFIG_SECCOMP", "CONFIG_SECCOMP_FILTER")
FILES = {"common/kernel/seccomp.c", "common/kernel/nothing_ksu_seccomp.inc",
         "common/include/linux/nothing_ksu_seccomp.h", "KernelSU/kernel/hook/lsm_hook.c",
         "KernelSU/kernel/hook/setuid_hook.c", "KernelSU/kernel/infra/seccomp_cache.c",
         "KernelSU/kernel/ksu.c", "KernelSU/kernel/ksu_main.c",
         "KernelSU/kernel/Makefile", "KernelSU/kernel/Kbuild"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code(text):
    """Ignore comments when checking source contracts, without executing C."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)


def patch_hooks(text):
    # Calculate every replacement first; an upstream change must not leave a
    # partially patched file or silently miss a manager/allowlist path.
    result = text
    for condition, expected in RULES:
        pattern = rf"({condition}\s*\{{\s*\n[ \t]*)disable_seccomp\(\);"
        result, count = re.subn(pattern, lambda match: match[1] + CALL, result)
        if count != expected:
            raise ValueError(f"SukiSU builtin seccomp hook changed: expected {expected}, found {count}; review Nothing adapter")
    if re.search(r"\bdisable_seccomp\s*\(\s*\)\s*;", code(result)):
        raise ValueError("Unexpected remaining disable_seccomp() call in SukiSU app-start hooks")
    return INCLUDE + result


def validate_cache(kernel):
    seccomp = code((kernel / "kernel/seccomp.c").read_text(encoding="utf-8"))
    arch = code((kernel / "arch/arm64/include/asm/seccomp.h").read_text(encoding="utf-8"))
    contracts = (r"struct action_cache\s*\{[^}]*DECLARE_BITMAP\(allow_native,\s*SECCOMP_ARCH_NATIVE_NR\)",
                 r"struct seccomp_filter\s*\{[^}]*struct action_cache\s+cache\s*;",
                 r"\bseccomp_cache_check_allow\s*\(")
    if not all(re.search(pattern, seccomp, re.S) for pattern in contracts):
        raise ValueError("Nothing kernel lacks the expected seccomp action cache; refusing a partial port")
    if not re.search(r"#\s*define\s+SECCOMP_ARCH_NATIVE\s+", arch):
        raise ValueError("ARM64 native seccomp action cache is unavailable")
    compat_reboot = 0
    if "SECCOMP_ARCH_COMPAT" in arch:
        table = kernel / "arch/arm64/include/asm/unistd32.h"
        compat = code(table.read_text(encoding="utf-8")) if table.is_file() else ""
        match = re.search(r"^#\s*define\s+__NR_reboot\s+(\d+)\s*$", compat, re.M)
        if not match or not re.search(r"DECLARE_BITMAP\(allow_compat,\s*SECCOMP_ARCH_COMPAT_NR\)", seccomp):
            raise ValueError("ARM64 compat seccomp action cache/reboot number is unavailable")
        compat_reboot = int(match[1])
    return compat_reboot


def native_filter_files(root):
    """Recognize main's existing implementation; never claim unknown trees work."""
    hook = root / "KernelSU/kernel/hook/setuid_hook.c"
    cache = root / "KernelSU/kernel/infra/seccomp_cache.c"
    kbuild = root / "KernelSU/kernel/Kbuild"
    if not all(path.is_file() for path in (hook, cache, kbuild)):
        raise ValueError("Unrecognized SukiSU seccomp hooks; review Nothing adapter")
    text = code(hook.read_text(encoding="utf-8"))
    if text.count("ksu_seccomp_allow_cache(") < 2 or re.search(r"\bdisable_seccomp\s*\(\s*\)\s*;", text):
        raise ValueError("SukiSU app-start hooks do not consistently retain seccomp filters")
    build = kbuild.read_text(encoding="utf-8")
    if "hook/setuid_hook.o" not in build or "infra/seccomp_cache.o" not in build:
        raise ValueError("SukiSU's native filter-mode code is not wired into Kbuild")
    return (hook, cache, kbuild)


def builtin_filter_files(root):
    """Resolve the unity source actually selected by Kbuild/Makefile.

    builtin renamed ksu.c to ksu_main.c when it made ksu.o a composite object
    containing the preinit runner. Retain support for fixed 41013 as well.
    Kbuild takes precedence over Makefile, as in the kernel build system.
    """
    kernel = root / "KernelSU/kernel"
    build = kernel / "Kbuild"
    if not build.is_file():
        build = kernel / "Makefile"
    if not build.is_file():
        raise ValueError("SukiSU builtin has no Kbuild/Makefile; review Nothing adapter")
    text = re.sub(r"#[^\n]*", "", build.read_text(encoding="utf-8")).replace("\\\n", " ")
    target = re.search(r"^\s*obj-\$\(CONFIG_KSU\)\s*(?::=|\+=|=)\s*(\w+)\.o\s*$", text, re.M)
    if not target:
        raise ValueError("SukiSU builtin CONFIG_KSU build target changed; review Nothing adapter")
    obj = target[1]
    components = re.findall(rf"^\s*{re.escape(obj)}-(?:y|objs)\s*(?::=|\+=|=)\s*([^\n]*)", text, re.M)
    objects = " ".join(components).split() if components else [obj + ".o"]
    for name in ("ksu.c", "ksu_main.c"):
        unity = kernel / name
        if unity.with_suffix(".o").name in objects and unity.is_file():
            if '#include "hook/lsm_hook.c"' in code(unity.read_text(encoding="utf-8")):
                return (unity, build)
    raise ValueError("SukiSU builtin build does not compile lsm_hook.c via ksu.c/ksu_main.c; review Nothing adapter")


def enable_defconfig(text):
    pattern = r"^(?:CONFIG_SECCOMP(?:_FILTER)?=.*|# CONFIG_SECCOMP(?:_FILTER)? is not set)\n?"
    text = re.sub(pattern, "", text, flags=re.M)
    return text.rstrip() + "\n" + "".join(name + "=y\n" for name in CONFIGS)


def verify_applied(root, record):
    if not record or record.get("mode") not in ("patched-builtin", "upstream") or not record.get("files"):
        raise ValueError("Requested seccomp filter mode has no verified source integration")
    for name, expected in record["files"].items():
        if name not in FILES or not (Path(root) / name).is_file() or digest(Path(root) / name) != expected:
            raise ValueError("seccomp filter-mode source changed after integration: " + name)


def apply(root, plan):
    if not plan.get("nothing_options", {}).get("enable_seccomp", False):
        return None
    inputs = plan["patch_inputs"]
    if inputs["ksu_variant"] != "SukiSU" or inputs["ksu_mode"] == "禁用KSU":
        raise ValueError("seccomp filter mode requires SukiSU")
    root = Path(root)
    kernel = root / "common"
    compat_reboot = validate_cache(kernel)
    defconfig = kernel / "arch/arm64/configs/gki_defconfig"
    updated_config = enable_defconfig(defconfig.read_text(encoding="utf-8"))
    if plan.get("seccomp_filter"):
        verify_applied(root, plan["seccomp_filter"])
        defconfig.write_text(updated_config, encoding="utf-8", newline="\n")
        return plan["seccomp_filter"]
    hook = root / "KernelSU/kernel/hook/lsm_hook.c"
    text = hook.read_text(encoding="utf-8") if hook.is_file() else ""
    writes = {}
    if "handle_zygote_setresuid" in code(text):
        patched = patch_hooks(text)
        seccomp_path = kernel / "kernel/seccomp.c"
        seccomp_text = seccomp_path.read_text(encoding="utf-8")
        if "nothing_ksu_seccomp" in seccomp_text or "nothing_ksu_seccomp" in text:
            raise ValueError("Unrecorded Nothing seccomp changes; use a clean source checkout")
        writes = {hook: patched, seccomp_path: seccomp_text.rstrip() + "\n" + TAIL,
                  kernel / "kernel/nothing_ksu_seccomp.inc": (PATCHES / "seccomp_filter.inc").read_text(encoding="utf-8"),
                  kernel / "include/linux/nothing_ksu_seccomp.h": (PATCHES / "seccomp_filter.h").read_text(encoding="utf-8").replace("@COMPAT_REBOOT@", str(compat_reboot))}
        # Record both the unity source and its build wiring: the source rename
        # must not bypass the check that these hooks really get compiled.
        mode, files = "patched-builtin", (*writes, *builtin_filter_files(root))
    else:
        mode, files = "upstream", native_filter_files(root)
    # All source contracts have passed before writing any files.
    for path, content in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    defconfig.write_text(updated_config, encoding="utf-8", newline="\n")
    record = {"mode": mode, "origin": ORIGIN,
              "files": {path.relative_to(root).as_posix(): digest(path) for path in files}}
    plan["seccomp_filter"] = record
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", default="nothing-plan.json")
    parser.add_argument("--root", default="nothing-kernel")
    args = parser.parse_args()
    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    record = apply(Path(args.root), plan)
    if record:
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Nothing seccomp filter mode: " + record["mode"] + "; CONFIG_SECCOMP/FILTER=y requested")


if __name__ == "__main__":
    main()
