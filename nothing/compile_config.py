"""Select the upstream builder's ThinLTO mode and verify it after Kconfig."""

import argparse
import json
from pathlib import Path
import re


def prepare(kernel, out):
    kconfig = (Path(kernel) / "arch/Kconfig").read_text(encoding="utf-8")
    if not re.search(r"^config LTO_CLANG_THIN$", kconfig, re.M):
        raise ValueError("This source has no LTO_CLANG_THIN option; review its build recipe")
    path = Path(out) / ".config"
    config = path.read_text(encoding="utf-8")
    settings = {"lto": "thin", "preserve_cfi_clang": "CONFIG_CFI_CLANG=y" in config.splitlines()}
    for symbol, enabled in (("LTO_NONE", False), ("LTO_CLANG_FULL", False), ("LTO_CLANG_THIN", True)):
        config = re.sub(rf"^(?:CONFIG_{symbol}=.*|# CONFIG_{symbol} is not set)\n?", "", config, flags=re.M)
        config = config.rstrip() + "\n" + (f"CONFIG_{symbol}=y" if enabled else f"# CONFIG_{symbol} is not set") + "\n"
    path.write_text(config, encoding="utf-8")
    return settings


def verify(out, settings):
    actual = set((Path(out) / ".config").read_text(encoding="utf-8").splitlines())
    if "CONFIG_LTO_CLANG_THIN=y" not in actual or "CONFIG_LTO_CLANG_FULL=y" in actual:
        raise ValueError("Kconfig did not retain ThinLTO; refusing a Full LTO build on this runner")
    if settings["preserve_cfi_clang"] and "CONFIG_CFI_CLANG=y" not in actual:
        raise ValueError("Kconfig dropped the source's CFI_CLANG setting")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "check"])
    parser.add_argument("--kernel", default="nothing-kernel/common")
    parser.add_argument("--out", default="nothing-kernel/out")
    parser.add_argument("--plan", default="nothing-plan.json")
    args = parser.parse_args()
    path = Path(args.plan)
    plan = json.loads(path.read_text(encoding="utf-8"))
    if args.command == "prepare":
        plan["compiler_settings"] = prepare(args.kernel, args.out)
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        verify(args.out, plan["compiler_settings"])
        print("LTO: thin (source CFI setting preserved)")


if __name__ == "__main__":
    main()
