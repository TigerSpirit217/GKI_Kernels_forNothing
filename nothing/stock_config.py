"""Select a recorded factory IKCONFIG for an exact Nothing device/source pair.

This only changes the configuration embedded for /proc/config.gz. The patched
gki_defconfig and the effective compiler .config remain independent.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import zlib

from nothing.source import ROOT

PROFILES = ROOT / "nothing/stock_configs"
ORIGINAL_RULE = "$(obj)/config_data: $(KCONFIG_CONFIG) FORCE"
FACTORY_RULE = "$(obj)/config_data: arch/arm64/configs/stock_defconfig FORCE"
DESTINATION = "arch/arm64/configs/stock_defconfig"
MATCH_FIELDS = ("source_sha", "android_version", "kernel_version", "sub_level", "kmi_generation")


def prepare(kernel, plan, profiles=PROFILES):
    kernel = Path(kernel)
    manifest = json.loads((Path(profiles) / "manifest.json").read_text(encoding="utf-8"))
    matches = [entry for entry in manifest["profiles"]
               if entry["device_id"] == plan["device"]["id"]
               and all(entry[key] == plan[key] for key in MATCH_FIELDS)]
    if len(matches) > 1:
        raise ValueError("Multiple factory configs match the selected Nothing source")
    makefile = kernel / "kernel/Makefile"
    text = makefile.read_text(encoding="utf-8")
    if not matches:
        # A reused tree must not retain a previous device's display config.
        if FACTORY_RULE in text:
            if text.count(FACTORY_RULE) != 1:
                raise ValueError("Ambiguous stock config_data rule; review Nothing adapter")
            makefile.write_text(text.replace(FACTORY_RULE, ORIGINAL_RULE), encoding="utf-8", newline="\n")
        return {"mode": "actual", "reason": "No recorded factory config for this device/source; generic templates are not used"}

    entry = matches[0]
    base = Path(profiles).resolve()
    path = (base / entry["file"]).resolve()
    if not path.is_relative_to(base):
        raise ValueError("Factory config path escapes Nothing's profile directory")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError("Factory config SHA256 differs from its recorded provenance")
    version = plan["kernel_version"] + "." + plan["sub_level"]
    if f"# Linux/arm64 {version} Kernel Configuration" not in data.decode("utf-8").splitlines():
        raise ValueError("Factory config kernel version differs from the selected source")
    if re.search(rb"^CONFIG_(?:KSU(?:_\w+)?|KPM)=", data, re.M):
        raise ValueError("Factory display config contains root implementation options")
    if text.count(ORIGINAL_RULE) + text.count(FACTORY_RULE) != 1:
        raise ValueError("Kernel config_data rule changed; review Nothing adapter")
    destination = kernel / DESTINATION
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    makefile.write_text(text.replace(ORIGINAL_RULE, FACTORY_RULE), encoding="utf-8", newline="\n")
    return {"mode": "factory", "profile": entry}


def verify(kernel, record, image=None):
    if record is None:
        return  # Older manifests and standalone callers have no display override.
    kernel = Path(kernel)
    text = (kernel / "kernel/Makefile").read_text(encoding="utf-8")
    if record["mode"] == "actual":
        if FACTORY_RULE in text:
            raise ValueError("Unexpected factory config override on an unmatched Nothing source")
        return
    if record["mode"] != "factory":
        raise ValueError("Unknown Nothing stock config mode")
    if text.count(FACTORY_RULE) != 1 or ORIGINAL_RULE in text:
        raise ValueError("Factory config_data rule changed after Nothing integration")
    expected = (kernel / DESTINATION).read_bytes()
    if hashlib.sha256(expected).hexdigest() != record["profile"]["sha256"]:
        raise ValueError("Factory display config changed after Nothing integration")
    if image is not None:
        raw = Path(image).read_bytes()
        marker = raw.find(b"IKCFG_ST")
        if marker < 0:
            raise ValueError("Built Image has no embedded IKCONFIG for the factory display config")
        try:
            embedded = zlib.decompress(raw[marker + 8:], 31)
        except zlib.error as exc:
            raise ValueError("Built Image has an invalid embedded IKCONFIG") from exc
        if embedded != expected:
            raise ValueError("Embedded IKCONFIG differs from the selected Nothing factory config")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare"])
    parser.add_argument("--kernel", default="nothing-kernel/common")
    parser.add_argument("--plan", default="nothing-plan.json")
    args = parser.parse_args()
    path = Path(args.plan)
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["stock_config"] = prepare(args.kernel, plan)
    verify(args.kernel, plan["stock_config"])
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = plan["stock_config"]
    detail = record["profile"]["file"] if record["mode"] == "factory" else record["reason"]
    print("Nothing IKCONFIG: " + record["mode"] + " — " + detail)


if __name__ == "__main__":
    main()
