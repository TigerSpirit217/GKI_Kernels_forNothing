"""Create a flashable device-checked AnyKernel3 ZIP, not an artifact directory."""

import argparse
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from nothing.source import ROOT
from nothing.verify import verify_config
from nothing.seccomp import verify_applied
from nothing.stock_config import verify as verify_stock_config


def package(plan_path, kernel, out, anykernel, destination):
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if plan.get("nothing_options", {}).get("enable_seccomp"):
        verify_applied(Path(kernel).parent, plan.get("seccomp_filter"))
    verify_config(kernel, out, plan["patch_inputs"], plan.get("nothing_options"))
    image = Path(out) / "arch/arm64/boot/Image"
    if not image.is_file() or image.stat().st_size == 0:
        raise ValueError("Missing compiled Image")
    if hashlib.sha256(image.read_bytes()).hexdigest() != plan.get("image_sha256"):
        raise ValueError("Image differs from the verified build manifest")
    verify_stock_config(kernel, plan.get("stock_config"), image)
    anykernel = Path(anykernel)
    for required in ("tools/ak3-core.sh", "META-INF/com/google/android/update-binary"):
        if not (anykernel / required).is_file():
            raise ValueError("Incomplete upstream AnyKernel3: " + required)
    device = plan["device"]
    custom_name = plan.get("build_settings", {}).get("version_name", "")
    if custom_name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", custom_name):
        raise ValueError("Invalid custom version name in manifest")
    template = (ROOT / "nothing/anykernel.sh.in").read_text(encoding="utf-8")
    title = device["name"] + " " + plan["requested_version"] + (" " + custom_name if custom_name else "")
    script = template.replace("@KERNEL_STRING@", title)
    script = script.replace("@SOURCE_SHA@", plan["source_sha"])
    script = script.replace("@DEVICE_NAMES@", "\n".join(f"device.name{i}={name}" for i, name in enumerate(device["device_names"], 1)))
    # Only safe identifier characters reach the shell template / archive name.
    if not re.fullmatch(r"[A-Za-z0-9._/+\-]+", plan["requested_version"]):
        raise ValueError("Invalid version in manifest")
    if any(not re.fullmatch(r"[A-Za-z0-9_\-]+", name) for name in device["device_names"]):
        raise ValueError("Invalid device identifier in manifest")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    version = re.sub(r"[^A-Za-z0-9._-]", "-", plan["requested_version"])
    variant = "NoKSU" if plan["patch_inputs"]["ksu_mode"] == "禁用KSU" else plan["patch_inputs"]["ksu_variant"]
    if variant != "NoKSU" and plan.get("ksu_profile"):
        variant = plan["ksu_profile"]["name"]
    if custom_name:
        version += "-" + custom_name
    filename = f"Nothing-{device['id']}-{version}-{variant}-{plan['source_sha'][:12]}-AnyKernel3.zip"
    archive_path = destination / filename
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(anykernel.rglob("*")):
            relative = path.relative_to(anykernel).as_posix()
            if not path.is_file() or any(part.startswith(".") for part in path.relative_to(anykernel).parts):
                continue
            if path.name in ("anykernel.sh", "Image", "Image.gz", "Image.lz4", "README.md"):
                continue
            if relative.startswith(("modules/", "ramdisk/", "patch/")):
                continue
            info = ZipInfo(relative)
            info.create_system = 3
            info.external_attr = (0o100755 if relative.startswith(("tools/", "META-INF/")) else 0o100644) << 16
            archive.writestr(info, path.read_bytes(), compress_type=ZIP_DEFLATED)
        for name, data, mode in [("anykernel.sh", script.encode(), 0o100755),
                                 ("Image", image.read_bytes(), 0o100644),
                                 ("nothing-build.json", Path(plan_path).read_bytes(), 0o100644)]:
            info = ZipInfo(name)
            info.create_system = 3
            info.external_attr = mode << 16
            archive.writestr(info, data, compress_type=ZIP_DEFLATED)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (destination / (filename + ".sha256")).write_text(f"{digest}  {filename}\n", encoding="utf-8")
    print(archive_path)
    return archive_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", default="nothing-plan.json")
    parser.add_argument("--kernel", default="nothing-kernel/common")
    parser.add_argument("--out", default="nothing-kernel/out")
    parser.add_argument("--anykernel", default="AnyKernel3")
    parser.add_argument("--destination", default="out/nothing")
    args = parser.parse_args()
    package(args.plan, args.kernel, args.out, args.anykernel, args.destination)


if __name__ == "__main__":
    main()
