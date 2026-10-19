"""Compose device kernel names and check Linux's 64-byte release limit."""

import argparse
import json
from pathlib import Path
import re


def kmi_suffix(plan):
    return "-" + plan["android_version"] + "-" + plan["kmi_generation"]


def release_suffix(plan):
    name = plan.get("build_settings", {}).get("version_name", "")
    if name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", name):
        raise ValueError("Invalid custom version name")
    return kmi_suffix(plan) + ("-" + name if name else "")


def expected_release(plan):
    return plan["kernel_version"] + "." + plan["sub_level"] + release_suffix(plan)


def prepare_version(plan, kernel, out):
    suffix = release_suffix(plan)
    kernel, out = Path(kernel), Path(out)
    config_path = out / ".config"
    config = config_path.read_text(encoding="utf-8")
    # Use one explicit LOCALVERSION for both old and new Nothing kernel trees.
    # The complete source SHA remains in the manifest rather than in uname -r.
    config = re.sub(r"^(?:CONFIG_LOCALVERSION(?:_AUTO)?=.*|# CONFIG_LOCALVERSION_AUTO is not set)\n?",
                    "", config, flags=re.M)
    config = config.rstrip() + '\nCONFIG_LOCALVERSION=""\n# CONFIG_LOCALVERSION_AUTO is not set\n'
    config_path.write_text(config, encoding="utf-8")
    # These source/build metadata files otherwise precede LOCALVERSION, even
    # when automatic SCM naming is disabled. Only clear their naming content.
    for folder in (kernel, out):
        for path in sorted(folder.glob("localversion*")):
            if path.is_file() and "~" not in path.name:
                path.write_text("", encoding="utf-8")
    return suffix


def validate_release(plan, release):
    length = len(release.encode("utf-8"))
    if length > 64:
        raise ValueError(f"完整内核版本名为 {length} 字节，超过 64 字节限制：{release}。"
                         "请缩短自定义名称后重试。")
    expected = expected_release(plan)
    if release != expected:
        raise ValueError(f"内核版本名与预期不符：{release!r}，预期为 {expected!r}")
    return release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "check"])
    parser.add_argument("--plan", default="nothing-plan.json")
    parser.add_argument("--kernel", default="nothing-kernel/common")
    parser.add_argument("--out", default="nothing-kernel/out")
    parser.add_argument("--release")
    args = parser.parse_args()
    path = Path(args.plan)
    plan = json.loads(path.read_text(encoding="utf-8"))
    if args.command == "prepare":
        print(prepare_version(plan, args.kernel, args.out))
    else:
        release = validate_release(plan, args.release or "")
        plan["expected_kernel_release"] = release
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"最终内核版本：{release}（{len(release.encode('utf-8'))}/64 字节）")


if __name__ == "__main__":
    main()
