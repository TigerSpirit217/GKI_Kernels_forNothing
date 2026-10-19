"""Pin a compatible SukiSU kernel/manager pair without changing upstream files."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


PROFILE = Path(__file__).with_name("sukisu.json")
VARIANTS = {"SukiSU(41013)"}


def profile_for(plan, variant, susfs):
    if variant not in VARIANTS:
        return None
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    branch = "builtin" if susfs else "main"
    selected = {key: profile[key] for key in ("name", "version", "uapi_version", "repository",
                                             "manager_commit", "manager_run")}
    selected.update(branch=branch, source_sha=profile["kernel_refs"][branch])
    if susfs:
        susfs_branch = "gki-" + plan["android_version"] + "-" + plan["kernel_version"]
        if susfs_branch not in profile["susfs_refs"]:
            raise ValueError("No pinned SukiSU 41013 SUSFS source for " + susfs_branch)
        selected.update(susfs_branch=susfs_branch, susfs_sha=profile["susfs_refs"][susfs_branch],
                        susfs_repository=profile["susfs_repository"])
    return selected


def git(directory, *args):
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()


def verify_source(directory, expected):
    actual = git(directory, "rev-parse", "HEAD")
    if actual != expected:
        raise ValueError(f"Pinned source mismatch in {directory}: expected {expected}, got {actual}")


def pin_susfs(profile, directory):
    sha = profile["susfs_sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid pinned SUSFS SHA")
    exists = subprocess.run(["git", "-C", str(directory), "cat-file", "-e", sha + "^{commit}"],
                            capture_output=True).returncode == 0
    if not exists:
        git(directory, "fetch", "--depth=1", profile["susfs_repository"], sha)
    git(directory, "checkout", "--detach", sha)
    verify_source(directory, sha)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["select", "pin-susfs", "verify"])
    parser.add_argument("--plan", default="nothing-plan.json")
    parser.add_argument("--root", default="nothing-kernel")
    parser.add_argument("--susfs", default="susfs4ksu")
    args = parser.parse_args()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    profile = plan.get("ksu_profile")
    if not profile:
        return
    if args.command == "select":
        sha = profile["source_sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid pinned SukiSU SHA")
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env:
            env.write("BRANCH=-s " + sha + "\n")
        print(f"SukiSU 41013: {profile['branch']}@{sha}; use the matching 41013-6 manager")
    elif args.command == "pin-susfs":
        pin_susfs(profile, args.susfs)
    else:
        verify_source(Path(args.root) / "KernelSU", profile["source_sha"])
        if plan["patch_inputs"]["enable_susfs"]:
            verify_source(args.susfs, profile["susfs_sha"])


if __name__ == "__main__":
    main()
