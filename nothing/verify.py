"""Reject patch failures and verify that selected features survive Kconfig."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def rejects(kernel):
    failures = []
    for path in Path(kernel).rglob("*.rej"):
        relative = path.relative_to(kernel).as_posix()
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative],
                                 cwd=kernel, capture_output=True).returncode == 0
        unchanged = tracked and subprocess.run(["git", "diff", "--quiet", "--", relative],
                                               cwd=kernel).returncode == 0
        if not unchanged:
            failures.append(relative)
    if failures:
        raise ValueError("Patch rejects remain; refusing to build/package a partially patched kernel:\n" + "\n".join(failures))


def verify_config(kernel, out, inputs):
    actual = set((Path(out) / ".config").read_text(encoding="utf-8").splitlines())
    required = {"CONFIG_TMPFS_XATTR=y", "CONFIG_TMPFS_POSIX_ACL=y"}
    if inputs["ksu_mode"] != "禁用KSU":
        required.add("CONFIG_KSU=y")
    elif "CONFIG_KSU=y" in actual:
        raise ValueError("KernelSU was disabled but remains enabled in the effective config")
    switches = {"enable_susfs": "CONFIG_KSU_SUSFS=y", "use_zram": "CONFIG_ZRAM=y",
                "use_bbg": "CONFIG_BBG=y", "use_nomount": "CONFIG_NOMOUNT=y",
                "use_rekernel": "CONFIG_REKERNEL=y", "use_networking": "CONFIG_TCP_CONG_BBR=y",
                "droidspaces_ntsync": "CONFIG_NTSYNC=y"}
    for option, config in switches.items():
        if inputs.get(option):
            required.add(config)
    if inputs.get("use_kpm", "disabled").startswith("enabled"):
        required.add("CONFIG_KPM=y")
    if inputs.get("droidspaces", "off") != "off":
        required.update({"CONFIG_SYSVIPC=y", "CONFIG_POSIX_MQUEUE=y", "CONFIG_IPC_NS=y",
                         "CONFIG_PID_NS=y", "CONFIG_DEVTMPFS=y", "CONFIG_USER_NS=y"})
    missing = sorted(required - actual)
    if missing:
        raise ValueError("Requested options were dropped by Kconfig: " + ", ".join(missing))
    rejects(kernel)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["rejects", "config", "record"])
    parser.add_argument("--kernel", default="nothing-kernel/common")
    parser.add_argument("--out", default="nothing-kernel/out")
    parser.add_argument("--plan", default="nothing-plan.json")
    parser.add_argument("--clang")
    args = parser.parse_args()
    if args.command == "rejects":
        rejects(args.kernel)
        return
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    verify_config(args.kernel, args.out, plan["patch_inputs"])
    if args.command == "record":
        out = Path(args.out)
        plan["kernel_release"] = (out / "include/config/kernel.release").read_text().strip()
        plan["build_timestamp"] = os.environ.get("KBUILD_BUILD_TIMESTAMP", "")
        kmi_suffix = "-" + plan["android_version"] + "-" + plan["kmi_generation"]
        if kmi_suffix not in plan["kernel_release"]:
            raise ValueError("Built kernel release is missing its declared Android KMI generation: " + kmi_suffix)
        plan["config_sha256"] = hashlib.sha256((out / ".config").read_bytes()).hexdigest()
        plan["image_sha256"] = hashlib.sha256((out / "arch/arm64/boot/Image").read_bytes()).hexdigest()
        plan["compiler"] = subprocess.check_output([args.clang, "--version"], text=True).strip()
        plan["compiler_source"] = (Path(args.clang).parent.parent / "nothing-source.txt").read_text().strip()
        dependencies = {}
        for path in ["nothing-kernel/KernelSU", "susfs4ksu", "kernel_patches", "SukiSU_patch", "Action-Build"]:
            if (Path(path) / ".git").exists():
                dependencies[path] = subprocess.check_output(["git", "-C", path, "rev-parse", "HEAD"], text=True).strip()
        plan["dependencies"] = dependencies
        Path(args.plan).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
