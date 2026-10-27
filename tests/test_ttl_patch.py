import copy
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

from scripts import ttl_patch


ROOT = Path(__file__).resolve().parents[1]
BASH = (str(Path(shutil.which("git")).with_name("bash.exe"))
        if os.name == "nt" and shutil.which("git") else shutil.which("bash"))


def function(name, statement):
    return f"int {name}(struct sk_buff *skb)\n{{\n\t{statement}\n\treturn 0;\n}}\n"


class TTLPatchTests(unittest.TestCase):
    def fixture(self, root, modern=False):
        files = {
            "Makefile": "VERSION = 5\nPATCHLEVEL = 10\nSUBLEVEL = 233\n",
            "net/ipv4/Kconfig": "config IP_MULTICAST\n\tbool\n\nconfig IP_ADVANCED_ROUTER\n\tbool\n",
            "arch/arm64/configs/gki_defconfig": "CONFIG_INET=y\n# CONFIG_IP_FORWARD_PRESERVE_TTL is not set\n",
            "net/ipv4/ip_forward.c": function("ip_forward", "ip_decrease_ttl(iph);")
                + function("unrelated_tunnel", "ip_decrease_ttl(iph);"),
            "net/ipv6/ip6_output.c": function("ip6_forward", "hdr->hop_limit--;")
                + function("unrelated_ipv6", "hdr->hop_limit--;"),
            "net/netfilter/nf_flow_table_ip.c": function(
                "nf_flow_offload_forward" if modern else "nf_flow_offload_ip_hook",
                "ip_decrease_ttl(iph);") + function(
                "nf_flow_offload_ipv6_forward" if modern else "nf_flow_offload_ipv6_hook",
                "ip6h->hop_limit--;"),
        }
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return files

    def test_legacy_and_modern_paths_are_idempotent_and_scoped(self):
        for modern in (False, True):
            with self.subTest(modern=modern), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                original = self.fixture(root, modern)
                ttl_patch.apply(root)
                first = {name: (root / name).read_bytes() for name in original}
                ttl_patch.apply(root)
                ttl_patch.apply(root, verify=True)
                self.assertEqual(first, {name: (root / name).read_bytes() for name in original})
                for name in ("net/ipv4/ip_forward.c", "net/ipv6/ip6_output.c"):
                    self.assertEqual(first[name].decode().count(ttl_patch.MARKER), 1)
                    self.assertTrue(first[name].decode().endswith(original[name].split("}\n", 1)[1]))
                self.assertEqual(first["net/netfilter/nf_flow_table_ip.c"].decode().count(ttl_patch.MARKER), 2)

    def test_unrecognized_source_fails_without_partial_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            path = root / "net/netfilter/nf_flow_table_ip.c"
            path.write_text(path.read_text().replace("ip6h->hop_limit--;", "different();"))
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with self.assertRaises(ValueError):
                ttl_patch.apply(root)
            self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_corrupted_guard_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            ttl_patch.apply(root)
            path = root / "net/ipv4/ip_forward.c"
            path.write_text(path.read_text().replace("!IS_ENABLED", "IS_ENABLED"))
            with self.assertRaisesRegex(ValueError, "incomplete or modified"):
                ttl_patch.apply(root, verify=True)

    def test_compiled_configuration_must_enable_the_option(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "out/android12-5.10/common/.config"
            config.parent.mkdir(parents=True)
            config.write_text(f"# {ttl_patch.OPTION} is not set\n")
            with self.assertRaises(ValueError):
                ttl_patch.verify_build(root)
            config.write_text(f"{ttl_patch.OPTION}=y\n")
            ttl_patch.verify_build(root)

    def test_kleaf_restored_defconfig_is_checked_via_compiled_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            ttl_patch.apply(root)
            (root / "arch/arm64/configs/gki_defconfig").write_text("CONFIG_INET=y\n")
            ttl_patch.apply(root, verify=True, compiled=True)
            with self.assertRaises(ValueError):
                ttl_patch.apply(root, verify=True)

    def test_kleaf_configuration_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "bazel-bin/common/kernel_aarch64_config/out_dir/.config"
            config.parent.mkdir(parents=True)
            config.write_text(f"{ttl_patch.OPTION}=y\n")
            ttl_patch.verify_build(root)


class WorkflowTests(unittest.TestCase):
    def workflow(self, filename):
        return yaml.safe_load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"))

    def test_thin_entry_point_preserves_the_original_menu(self):
        custom = self.workflow("kernel-custom.yml")
        ttl = self.workflow("kernel-ttl.yml")
        self.assertEqual(ttl["name"], "Kernel+TTL Patch")
        old = custom[True]["workflow_dispatch"]["inputs"]
        new = copy.deepcopy(ttl["on"]["workflow_dispatch"]["inputs"])
        thin = new.pop("force_thin_lto")
        self.assertEqual(thin["type"], "boolean")
        self.assertIs(thin["default"], False)
        defaults = {"os_patch_level": "2025-02", "kernelsu_variant": "SukiSU",
                    "use_kpm": "enabled (开启)", "use_zram": True, "use_bbg": True}
        for name, value in defaults.items():
            self.assertEqual(new[name]["default"], value)
            new[name]["default"] = old[name]["default"]
        self.assertEqual(new, old)
        job = ttl["jobs"]["build"]
        self.assertEqual(job["uses"], "./.github/workflows/kernel-custom.yml")
        self.assertEqual(set(job["with"]), set(old) | {"use_ttl_patch", "force_thin_lto"})
        self.assertIs(job["with"]["use_ttl_patch"], True)
        self.assertEqual(job["with"]["force_thin_lto"], "${{ inputs.force_thin_lto }}")
        call = custom[True]["workflow_call"]["inputs"]
        self.assertEqual(set(call), set(old) | {"use_ttl_patch", "force_thin_lto"})
        self.assertIs(call["force_thin_lto"]["default"], False)
        build_inputs = self.workflow("build.yml")[True]["workflow_call"]["inputs"]
        self.assertIs(build_inputs["force_thin_lto"]["default"], False)
        self.assertEqual(custom["jobs"]["build-custom-kernel"]["with"]["force_thin_lto"],
                         "${{ inputs.force_thin_lto || false }}")
        for name, schema in old.items():
            self.assertEqual(call[name]["type"], "string" if schema["type"] == "choice" else schema["type"])
            self.assertEqual(call[name].get("default"),
                             None if schema.get("required") else schema.get("default"))

    def test_original_builds_leave_ttl_disabled_and_patch_precedes_compile(self):
        build = self.workflow("build.yml")
        self.assertIs(build[True]["workflow_call"]["inputs"]["use_ttl_patch"]["default"], False)
        steps = build["jobs"]["build-kernel"]["steps"]
        names = [step["name"] for step in steps]
        patch = names.index("应用 TTL / Hop Limit 保持补丁")
        self.assertLess(patch, names.index("配置内核名称"))
        self.assertLess(names.index("编译内核"), names.index("验证 TTL 补丁已编入内核"))
        self.assertEqual(steps[patch]["if"], "inputs.use_ttl_patch")


@unittest.skipUnless(BASH and Path(BASH).is_file(), "Bash is required for build dispatch tests")
class ThinLTOBuildTests(unittest.TestCase):
    def run_build(self, root, modern, force, simulate="normal"):
        kernel = root / "kernel"
        config = kernel / "common/arch/arm64/configs/gki_defconfig"
        config.parent.mkdir(parents=True)
        config.write_text("CONFIG_INET=y\n")
        config.with_name("gki_defconfig.orig").write_text("CONFIG_INET=y\n")
        (kernel / "common/build.config.gki.aarch64").write_text("BUILD_SYSTEM_DLKM=1\n")
        builder = kernel / ("tools/bazel" if modern else "build/build.sh")
        builder.parent.mkdir(parents=True)
        code = """#!/usr/bin/env bash
set -e
mode=NONE
"""
        if modern:
            code += """printf '%s\\n' "$@" > "$GITHUB_WORKSPACE/bazel-args.txt"
for arg in "$@"; do
  if [ "$arg" = --lto=thin ]; then mode=CLANG_THIN; fi
done
config=bazel-bin/common/kernel_aarch64_config/out_dir/.config
image=bazel-bin/common/kernel_aarch64/Image
"""
        else:
            code += """printf '%s\\n' "${LTO-unset}" > "$GITHUB_WORKSPACE/lto-env.txt"
if [ "${LTO-unset}" = thin ]; then mode=CLANG_THIN; fi
config=out/android12-5.10/common/.config
image=out/android12-5.10/dist/Image
"""
        code += """mkdir -p "$(dirname "$config")" "$(dirname "$image")"
if [ "$SIMULATE" = ignored ]; then mode=NONE; fi
if [ "$SIMULATE" != missing ]; then printf 'CONFIG_LTO_%s=y\\n' "$mode" > "$config"; fi
printf 'Linux version test\\n' > "$image"
"""
        builder.write_text(code, encoding="utf-8", newline="\n")
        builder.chmod(0o755)
        workflow = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8"))
        compile_step = next(s for s in workflow["jobs"]["build-kernel"]["steps"]
                            if s.get("id") == "compile_kernel")
        self.assertEqual(compile_step["env"]["FORCE_THIN_LTO"], "${{ inputs.force_thin_lto }}")
        command = compile_step["with"]["command"].replace("${{ inputs.android_version }}", "android12")
        command = command.replace("${{ inputs.kernel_version }}", "5.10")
        self.assertNotIn("${{", command)
        prefix = """cd "$1"
export GITHUB_WORKSPACE="$PWD"
export KERNEL_ROOT="$PWD/kernel"
export DEFCONFIG="$KERNEL_ROOT/common/arch/arm64/configs/gki_defconfig"
strings() { cat "$1"; }
"""
        environment = dict(os.environ, FORCE_THIN_LTO="true" if force else "false", SIMULATE=simulate)
        environment.pop("LTO", None)
        return subprocess.run([BASH, "-c", prefix + command, "_", root.as_posix()],
                              env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace")

    def test_forced_and_upstream_modes_reach_both_build_systems(self):
        for modern in (False, True):
            for force in (False, True):
                with self.subTest(modern=modern, force=force), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    result = self.run_build(root, modern, force)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    if modern:
                        args = (root / "bazel-args.txt").read_text().splitlines()
                        self.assertEqual([arg for arg in args if arg.startswith("--lto=")],
                                         ["--lto=thin"] if force else [])
                    else:
                        self.assertEqual((root / "lto-env.txt").read_text().strip(), "thin" if force else "unset")

    def test_forced_mode_rejects_a_builder_that_drops_thin_lto(self):
        for modern in (False, True):
            with self.subTest(modern=modern), tempfile.TemporaryDirectory() as directory:
                result = self.run_build(Path(directory), modern, True, "ignored")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("CONFIG_LTO_CLANG_THIN=y", result.stdout)

    def test_forced_mode_rejects_missing_compiled_configuration(self):
        for modern in (False, True):
            with self.subTest(modern=modern), tempfile.TemporaryDirectory() as directory:
                result = self.run_build(Path(directory), modern, True, "missing")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("::error::", result.stdout)


if __name__ == "__main__":
    unittest.main()
