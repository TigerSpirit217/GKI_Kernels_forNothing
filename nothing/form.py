"""Translate the Actions form's controls to the inherited upstream inputs."""

import json
import os
from pathlib import Path
from datetime import datetime, timezone
import re

from nothing.upstream import load_upstream, patch_context

SWITCHES = ("enable_susfs", "use_zram", "use_bbg", "use_rekernel", "use_nomount",
            "use_networking", "cve_2026_43499_patch", "droidspaces_ntsync", "export_susfs_patches")


def options_from_form(inputs):
    for key in (*SWITCHES, "use_kpm", "droidspaces"):
        if type(inputs.get(key, False)) is not bool:
            raise ValueError(f"{key} 必须是勾选框的布尔值")
    options = {key: inputs.get(key, False) for key in SWITCHES}
    options["ksu_variant"] = inputs.get("ksu_variant", "None")
    options["use_kpm"] = "enabled (开启)" if inputs.get("use_kpm", False) else "disabled (关闭)"
    enabled = inputs.get("droidspaces", False) or options["droidspaces_ntsync"]
    slot = inputs.get("droidspaces_slot", "678")
    if slot not in ("678", "123", "345"):
        raise ValueError("Droidspaces 兼容槽位无效")
    options["droidspaces"] = slot if enabled else "off"
    if options["ksu_variant"] == "None" and (options["enable_susfs"] or inputs.get("use_kpm", False)):
        raise ValueError("启用 SUSFS / KPM 时，请先从 KernelSU 下拉框选择 SukiSU 或 BakaSU")
    if inputs.get("use_kpm", False) and options["ksu_variant"] == "Official":
        raise ValueError("KPM 需要 SukiSU / BakaSU，不能选择 Official")
    if options["export_susfs_patches"] and (not options["enable_susfs"] or options["ksu_variant"] not in ("SukiSU", "SukiSU(41013)", "BakaSU")):
        raise ValueError("导出 SUSFS 补丁需要启用 SUSFS，并选择 SukiSU 或 BakaSU")
    _, schema = load_upstream()
    patch_context(dict(android_version="android14", kernel_version="6.1", sub_level="0"), options, schema)
    return options


def build_settings(inputs):
    name = inputs.get("version_name", "").strip()
    if name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", name):
        raise ValueError("自定义版本名请使用 1–32 位英文字母、数字、点、下划线或连字符，并以字母或数字开头")
    value = inputs.get("build_time", "").strip()
    if value in ("", "N", "n"):
        return {"version_name": name, "build_time": ""}
    formats = ("%Y-%m-%d %H:%M:%S UTC", "%a %b %d %H:%M:%S UTC %Y")
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if parsed.strftime(fmt) != value:
            continue
        return {"version_name": name, "build_time": parsed.strftime("%a %b %d %H:%M:%S UTC %Y")}
    raise ValueError("构建时间无效，请使用 YYYY-MM-DD HH:MM:SS UTC，例如 2026-10-19 12:30:00 UTC")


def main():
    inputs = json.loads(os.environ["BUILD_INPUTS"])
    options = options_from_form(inputs)
    settings = build_settings(inputs)
    Path("nothing-options.json").write_text(json.dumps(options, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path("nothing-settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
