#!/usr/bin/env python3
"""Apply a checked TTL/Hop Limit patch to Android GKI 5.10 through 6.12."""

import argparse
from pathlib import Path
import re


OPTION = "CONFIG_IP_FORWARD_PRESERVE_TTL"
MARKER = "/* TTL_PATCH: preserve the forwarded packet's hop count. */"
KCONFIG = """config IP_FORWARD_PRESERVE_TTL
	bool "Preserve TTL / Hop Limit when forwarding unicast packets"
	default n
	help
	  Preserve IPv4 TTL and IPv6 Hop Limit in the kernel unicast and
	  software flowtable forwarding paths. Packets with a hop count of
	  one or less still expire normally. Locally generated packets and
	  multicast forwarding are unchanged. Android tethering BPF and
	  hardware offload must be disabled separately to use these paths.

"""


def guard_function(text, name, statement):
    """Match one top-level C function, never another forwarding/tunnel path."""
    function = re.compile(
        r"^[^\n;{}]*\b" + re.escape(name) + r"\([^;{}]*?\n\{\n.*?^\}",
        re.MULTILINE | re.DOTALL,
    )
    matches = list(function.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"Expected one definition of {name}, found {len(matches)}")
    match = matches[0]
    body = match.group()
    if MARKER in body:
        existing = re.compile(r"^(\t+)" + re.escape(MARKER) + r"\n\1"
                              + re.escape(f"if (!IS_ENABLED({OPTION}))")
                              + r"\n\1\t" + re.escape(statement) + r"$", re.MULTILINE)
        if body.count(MARKER) != 1 or len(existing.findall(body)) != 1 or body.count(statement) != 1:
            raise ValueError(f"{name}: incomplete or modified TTL patch")
        return text
    line = re.compile(r"^(\t+)" + re.escape(statement) + r"$", re.MULTILINE)
    occurrences = list(line.finditer(body))
    if len(occurrences) != 1:
        raise ValueError(f"{name}: expected one {statement!r}")
    occurrence = occurrences[0]
    indent = occurrence[1]
    guarded = (indent + MARKER + "\n" + indent + f"if (!IS_ENABLED({OPTION}))\n"
               + indent + "\t" + statement)
    if OPTION in body:
        raise ValueError(f"{name}: unexpected existing TTL configuration")
    body = body[:occurrence.start()] + guarded + body[occurrence.end():]
    return text[:match.start()] + body + text[match.end():]


def patched_files(kernel):
    kernel = Path(kernel)
    makefile = (kernel / "Makefile").read_text(encoding="utf-8")
    version = tuple(int(re.search(rf"^{key}\s*=\s*(\d+)", makefile, re.MULTILINE)[1])
                    for key in ("VERSION", "PATCHLEVEL"))
    if version not in {(5, 10), (5, 15), (6, 1), (6, 6), (6, 12)}:
        raise ValueError(f"Unsupported GKI version: {version}")

    targets = {
        "net/ipv4/ip_forward.c": [("ip_forward", "ip_decrease_ttl(iph);")],
        "net/ipv6/ip6_output.c": [("ip6_forward", "hdr->hop_limit--;")],
        "net/netfilter/nf_flow_table_ip.c": [
            ("nf_flow_offload_ip_hook", "ip_decrease_ttl(iph);"),
            ("nf_flow_offload_ipv6_hook", "ip6h->hop_limit--;"),
        ],
    }
    # Newer kernels moved the updates from hooks into these helper functions.
    flow = (kernel / "net/netfilter/nf_flow_table_ip.c").read_text(encoding="utf-8")
    for index, helper in enumerate(("nf_flow_offload_forward", "nf_flow_offload_ipv6_forward")):
        if re.search(r"\b" + helper + r"\s*\(", flow):
            targets["net/netfilter/nf_flow_table_ip.c"][index] = (
                helper, targets["net/netfilter/nf_flow_table_ip.c"][index][1])
    changes = {}
    for relative, functions in targets.items():
        path = kernel / relative
        text = path.read_text(encoding="utf-8")
        for name, statement in functions:
            text = guard_function(text, name, statement)
        changes[path] = text

    path = kernel / "net/ipv4/Kconfig"
    text = path.read_text(encoding="utf-8")
    if "config IP_FORWARD_PRESERVE_TTL\n" in text:
        if KCONFIG not in text:
            raise ValueError("Existing TTL Kconfig differs from this patch")
    else:
        anchor = "config IP_ADVANCED_ROUTER\n"
        if text.count(anchor) != 1:
            raise ValueError("Cannot locate IPv4 Kconfig insertion point")
        text = text.replace(anchor, KCONFIG + anchor)
    changes[path] = text
    path = kernel / "arch/arm64/configs/gki_defconfig"
    text = path.read_text(encoding="utf-8")
    text = re.sub(rf"^(?:{OPTION}=.*|# {OPTION} is not set)\n?", "", text,
                  flags=re.MULTILINE)
    changes[path] = text.rstrip("\n") + f"\n{OPTION}=y\n"
    return changes


def apply(kernel, verify=False, compiled=False):
    # Validate every target before modifying any source file.
    changes = patched_files(kernel)
    for path, text in changes.items():
        # Kleaf restores gki_defconfig after moving additions to ksu.fragment.
        if verify and compiled and path.name == "gki_defconfig":
            continue
        if verify:
            if path.read_text(encoding="utf-8") != text:
                raise ValueError(f"TTL patch is missing or incomplete: {path}")
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
    print(f"TTL patch {'verified' if verify else 'applied'}: {len(changes)} files; {OPTION}=y")


def verify_build(root):
    root = Path(root)
    candidates = list((root / "out").glob("**/common/.config"))
    candidates += [root / relative for relative in (
        "bazel-bin/common/kernel_aarch64/.config",
        "bazel-bin/common/kernel_aarch64_config/.config",
        "bazel-bin/common/kernel_aarch64_config/out_dir/.config",
    )]
    configs = [path for path in candidates if path.is_file()]
    if not configs or not any(f"{OPTION}=y" in path.read_text(encoding="utf-8").splitlines()
                              for path in configs):
        raise ValueError(f"Compiled kernel configuration does not enable {OPTION}")
    print(f"Compiled configuration verified: {OPTION}=y")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--build-root", type=Path, help="Also check the compiled .config")
    args = parser.parse_args()
    apply(args.kernel, verify=args.verify, compiled=bool(args.build_root))
    if args.build_root:
        verify_build(args.build_root)


if __name__ == "__main__":
    main()
