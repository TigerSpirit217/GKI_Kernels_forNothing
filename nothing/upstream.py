"""Materialize a composite action from the *current* upstream patch workflow.

No copied patch implementations or generated workflow need to be committed.
GitHub evaluates the inherited steps/conditions, including typed boolean inputs.
"""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re

import yaml

from nothing.source import ROOT
from nothing.sukisu import profile_for

UPSTREAM = ROOT / ".github/workflows/build.yml"
START = "提取实际子版本号"
END = "配置内核名称"
EXCLUDED = {"添加一加 8E 处理器支持", "修复 6.6 WiFi/蓝牙兼容性（三星 + 小米）"}
VARIANTS = {"None", "Official", "SukiSU", "SukiSU(41013)", "BakaSU"}
RESERVED = {"android_version", "kernel_version", "sub_level", "os_patch_level", "ksu_variant",
            "ksu_mode", "enable_susfs", "version", "revision", "build_time", "supp_op", "artifact_upload_mode"}


def load_upstream(path=UPSTREAM):
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    # PyYAML's YAML 1.1 loader reads the Actions key `on` as True.
    trigger = data.get("on", data.get(True))
    return data["jobs"]["build-kernel"]["steps"], trigger["workflow_call"]["inputs"]


def patch_context(plan, options, schema):
    if not isinstance(options, dict):
        raise ValueError("Patch options must be a JSON object")
    unknown = set(options) - set(schema) - {"ksu_variant"}
    if unknown:
        raise ValueError("Unknown upstream patch options: " + ", ".join(sorted(unknown)))
    if set(options) & (RESERVED - {"ksu_variant", "enable_susfs"}):
        raise ValueError("Patch options cannot override source metadata or reserved build inputs")
    variant = options.get("ksu_variant", "None")
    if variant not in VARIANTS:
        raise ValueError("Unsupported KernelSU variant: " + str(variant))
    context = {}
    for key, definition in schema.items():
        if key in RESERVED:
            continue
        if key in options:
            value = options[key]
        elif "default" in definition:
            value = definition["default"]
        elif definition.get("required"):
            raise ValueError("Upstream added a required input without a default: " + key)
        else:
            value = False if definition["type"] == "boolean" else ""
        if definition["type"] == "boolean" and type(value) is not bool:
            raise ValueError(key + " must be a JSON boolean")
        if definition["type"] == "string":
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_. ()\-\u4e00-\u9fff]*", value):
                raise ValueError(key + " must be a simple option string")
        if definition["type"] == "number" and type(value) not in (int, float):
            raise ValueError(key + " must be a JSON number")
        context[key] = value
    if options.get("enable_susfs", False) and variant == "None":
        raise ValueError("SUSFS requires a KernelSU variant")
    if type(options.get("enable_susfs", False)) is not bool:
        raise ValueError("enable_susfs must be a JSON boolean")
    if context.get("droidspaces") not in ("off", "678", "123", "345", "on"):
        raise ValueError("Invalid Droidspaces mode")
    if context.get("droidspaces_ntsync") and context.get("droidspaces") == "off":
        raise ValueError("NTSync requires Droidspaces to be enabled")
    if context.get("droidspaces") == "on" and plan["kernel_version"] != "6.12":
        raise ValueError("For kernels below 6.12, choose a Droidspaces slot: 678, 123 or 345")
    if context.get("use_kpm") not in ("disabled (关闭)", "enabled (开启)"):
        raise ValueError("Only disabled/enabled KPM is supported; binary-patched KPM is not supported upstream")
    if context.get("use_kpm", "disabled").startswith("enabled") and variant in ("None", "Official"):
        raise ValueError("KPM requires a compatible SukiSU/BakaSU variant")
    context.update({key: plan[key] for key in ("android_version", "kernel_version", "sub_level")})
    # The fixed Nothing profile uses the original SukiSU patch paths, including
    # KPM and SUSFS export. Its immutable refs are supplied in extra steps.
    upstream_variant = "SukiSU" if variant == "SukiSU(41013)" else variant
    context.update(ksu_variant="Official" if variant == "None" else upstream_variant,
                   ksu_mode="禁用KSU" if variant == "None" else "关闭",
                   enable_susfs=options.get("enable_susfs", False),
                   os_patch_level="nothing", version="", revision="r1", build_time="",
                   supp_op=False, artifact_upload_mode="仅上传 AnyKernel3.zip")
    # Fail clearly when an upstream update changes the input contract.
    if set(context) != set(schema):
        raise ValueError("Upstream input schema changed; review Nothing's input adapter")
    return context


def rewrite(value):
    if isinstance(value, dict):
        return {key: rewrite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite(item) for item in value]
    if isinstance(value, str):
        value = value.replace("vars.MANAGER_STR", "env.MANAGER_STR")
        return re.sub(r"\binputs\.(\w+)", r"fromJSON(inputs.context).\1", value)
    return value


def generate_action(steps):
    names = [step.get("name") for step in steps]
    for anchor in (START, END, "克隆依赖仓库", "应用 Stock Config 伪装", "确定 KernelSU 分支", "克隆 SUSFS 源码"):
        if names.count(anchor) != 1:
            raise ValueError("Upstream step anchor changed; review adapter: " + anchor)
    begin, end = names.index(START), names.index(END)
    if begin >= end:
        raise ValueError("Upstream patch step order changed")
    selected = [steps[names.index("克隆依赖仓库")], steps[names.index("应用 Stock Config 伪装")]]
    selected += [step for step in steps[begin:end] if step.get("name") not in EXCLUDED]
    converted = []
    for step in copy.deepcopy(selected):
        if "run" not in step:
            raise ValueError("Upstream added a non-shell patch step; review its composite compatibility")
        step.setdefault("shell", "bash")
        step = rewrite(step)
        if "if" in step and not step["if"].startswith("${{"):
            step["if"] = "${{ " + step["if"] + " }}"
        converted.append(step)
        if step["name"] == "确定 KernelSU 分支":
            converted.append({"name": "Nothing 固定 SukiSU 41013 提交", "shell": "bash",
                              "if": "${{ env.NOTHING_SUKISU_FIXED == 'true' && fromJSON(inputs.context).ksu_variant == 'SukiSU' && fromJSON(inputs.context).ksu_mode != '禁用KSU' }}",
                              "run": "python -m nothing.sukisu select"})
        elif step["name"] == "克隆 SUSFS 源码":
            converted.append({"name": "Nothing 固定 SukiSU 配套 SUSFS 提交", "shell": "bash",
                              "if": "${{ env.NOTHING_SUKISU_FIXED == 'true' && fromJSON(inputs.context).ksu_variant == 'SukiSU' && fromJSON(inputs.context).enable_susfs }}",
                              "run": "python -m nothing.sukisu pin-susfs"})
    return {"name": "Nothing: inherited upstream patches",
            "description": "Generated at runtime from .github/workflows/build.yml; edit the upstream or adapter, not this file.",
            "inputs": {"context": {"description": "Typed upstream inputs as JSON", "required": True}},
            "runs": {"using": "composite", "steps": converted}}


def export_environment(plan, workspace):
    kernel_root = workspace / "nothing-kernel"
    env = {"CONFIG": f"Nothing-{plan['device']['id']}-{plan['source_sha'][:12]}",
           "KERNEL_ROOT": str(kernel_root),
           "DEFCONFIG": str(kernel_root / "common/arch/arm64/configs/gki_defconfig"),
           "SUSFS4KSU": str(workspace / "susfs4ksu"), "KERNEL_PATCHES": str(workspace / "kernel_patches"),
           "SUKISU_PATCHES": str(workspace / "SukiSU_patch"), "ZZH_PATCHES": str(workspace),
           "ANYKERNEL3": str(workspace / "AnyKernel3"), "ACTION_BUILD": str(workspace / "Action-Build"),
           "LEGACY_SUKISU_CONFIG": "", "SUSFS_PATCH_EXPORT": "false", "NOTHING_SUKISU_FIXED": "false"}
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", default="nothing-plan.json")
    parser.add_argument("--options", required=True, help="JSON file containing upstream feature options")
    parser.add_argument("--output", default=".nothing-actions")
    args = parser.parse_args()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    options = json.loads(Path(args.options).read_text(encoding="utf-8"))
    steps, schema = load_upstream()
    context = patch_context(plan, options, schema)
    action = generate_action(steps)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "action.yml").write_text(yaml.safe_dump(action, allow_unicode=True, sort_keys=False), encoding="utf-8")
    env = export_environment(plan, Path(os.environ.get("GITHUB_WORKSPACE", ROOT)))
    profile = profile_for(plan, options.get("ksu_variant", "None"), context["enable_susfs"])
    if profile:
        plan["ksu_profile"] = profile
        env["NOTHING_SUKISU_FIXED"] = "true"
    else:
        plan.pop("ksu_profile", None)
    plan["patch_inputs"] = context
    plan["upstream_workflow_sha256"] = hashlib.sha256(UPSTREAM.read_bytes()).hexdigest()
    Path(args.plan).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as handle:
            for key, value in env.items():
                handle.write(f"{key}={value}\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write("context=" + json.dumps(context, ensure_ascii=False) + "\n")
    print(f"Inherited {len(action['runs']['steps'])} steps from {UPSTREAM}")


if __name__ == "__main__":
    main()
