"""Resolve an explicit NothingOSS release/ref to an immutable source commit."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent


def api(path):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "nothing-kernel-builder"}
    if os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    for attempt in range(3):
        try:
            with urlopen(Request("https://api.github.com/" + path, headers=headers), timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def git(*args, cwd=None):
    attempts = 3 if args[0] in ("fetch", "ls-remote") else 1
    for attempt in range(attempts):
        try:
            return subprocess.check_output(["git", *args], cwd=cwd, text=True, encoding="utf-8").strip()
        except subprocess.CalledProcessError:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def device_for(model):
    normalize = lambda value: re.sub(r"[^a-z0-9]", "", value.lower().replace("nothing", ""))
    if normalize(model) in ("phone1", "spacewar", "a063"):
        raise ValueError("Phone (1) uses a non-GKI 5.4 kernel; the upstream GKI patch stack does not support it.")
    devices = json.loads((ROOT / "nothing/devices.json").read_text(encoding="utf-8"))["devices"]
    for key, item in devices.items():
        if normalize(model) in {normalize(v) for v in [key, item["name"], *item["aliases"]]}:
            return {"id": key, **item}
    raise ValueError("Unknown/unsupported model. Supported: " + ", ".join(devices))


def remote_refs(repository):
    refs = {}
    output = git("ls-remote", "--heads", "--tags", "https://github.com/" + repository + ".git")
    for line in output.splitlines():
        sha, name = line.split()
        peeled = name.endswith("^{}")
        name = name.removesuffix("^{}")
        if name not in refs or peeled:
            refs[name] = sha
    return refs


def release_history(repository, branches):
    """Follow each branch's README release commits, including 'No update' releases.

    README introduction commits identify releases even when their commit titles
    omit the OS version. Old Android/Linux README history is not a device release.
    """
    releases = []
    details = {}
    for branch, head in branches.items():
        for page in range(1, 11):
            query = urlencode({"sha": head, "path": "README.md", "per_page": 100, "page": page})
            commits = api(f"repos/{repository}/commits?{query}")
            for commit in commits:
                sha = commit["sha"]
                if sha not in details:
                    details[sha] = api(f"repos/{repository}/commits/{sha}?per_page=100")
                detail = details[sha]
                files = detail.get("files", [])
                # Source import commits can change thousands of files. GitHub
                # paginates the file list separately from the commit history.
                for file_page in range(2, 31):
                    if any(file["filename"] == "README.md" for file in files) or len(files) % 100:
                        break
                    extra = api(f"repos/{repository}/commits/{sha}?per_page=100&page={file_page}")["files"]
                    if not extra:
                        break
                    files.extend(extra)
                if not any(file["filename"] == "README.md" for file in files):
                    raise ValueError(f"Cannot inspect README changes for {sha}; use an explicit source SHA.")
                added = []
                for file in files:
                    if file["filename"] != "README.md":
                        continue
                    if "patch" not in file:
                        raise ValueError(f"GitHub omitted README diff for {sha}; use an explicit source SHA.")
                    added = [line[1:] for line in file["patch"].splitlines()
                             if line.startswith("+") and not line.startswith("+++")]
                version_lines = [line for line in added if re.search(r"\b(?:NOS|OS)\b", line, re.I)
                                 and re.search(r"\d+\.\d+", line)]
                if version_lines:
                    releases.append({"branch": branch, "sha": sha, "versions": version_lines,
                                     "committed_at": commit["commit"].get("committer", {}).get("date", ""),
                                     "subject": commit["commit"]["message"].splitlines()[0]})
            if len(commits) < 100:
                break
        else:
            raise ValueError(f"Release history for {branch} exceeded 1000 README commits; choose a branch/SHA.")
    return releases


def select_release(releases, version):
    boundary = re.compile(r"(?<![A-Za-z0-9.])" + re.escape(version) + r"(?![A-Za-z0-9.])", re.I)
    matches = {}
    for release in releases:
        # Match only newly added release entries, never an old version still
        # mentioned in a newer cumulative README.
        if any(boundary.search(re.sub(r"\b(?:NOS|OS)[.\s]*", " ", line, flags=re.I))
               for line in release["versions"]):
            matches.setdefault(release["sha"], []).append(release)
    if len(matches) != 1:
        choices = "\n".join(f"  {row['branch']} {sha}: {'; '.join(row['versions'])}"
                            for sha, rows in matches.items() for row in rows)
        raise ValueError(f"Version {version!r} has {len(matches)} release commit matches. "
                         "Use --branch, a full build identifier or an official Git ref/SHA.\n" + choices)
    sha, rows = next(iter(matches.items()))
    return sha, rows[0]["branch"] + ":release:" + version


def device_branches(refs, pattern, requested=""):
    branches = {name.removeprefix("refs/heads/"): sha for name, sha in refs.items()
                if name.startswith("refs/heads/") and re.search(pattern, name.removeprefix("refs/heads/"), re.I)}
    if requested:
        requested = requested.removeprefix("refs/heads/")
        if requested not in branches:
            raise ValueError("Unknown official branch or branch belongs to another model. Available: " + ", ".join(branches))
        branches = {requested: branches[requested]}
    if not branches:
        raise ValueError("No official branches match this device profile; review the device branch mapping.")
    return branches


def belongs_to_branches(repository, sha, branches):
    if sha in branches.values():
        return True
    for head in set(branches.values()):
        try:
            comparison = api(f"repos/{repository}/compare/{sha}...{head}")
        except HTTPError as error:
            if error.code in (404, 409):
                continue
            raise
        if comparison["status"] in ("ahead", "identical"):
            return True
    return False


def resolve(repository, version, branch="", branch_pattern=".*"):
    """Resolve refs or release entries across every official branch."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+\-]{0,180}", version):
        raise ValueError("Version must be an official Git branch/tag/SHA or an OS/build identifier (no spaces).")
    repo = "repos/" + repository
    refs = remote_refs(repository)
    branches = device_branches(refs, branch_pattern, branch)
    if re.fullmatch(r"[0-9a-fA-F]{40}", version):
        commit = api(f"{repo}/commits/{version}")
        if not belongs_to_branches(repository, commit["sha"], branches):
            raise ValueError("Requested source SHA is not an ancestor of a selected device branch.")
        return commit["sha"], version

    candidates = {}
    for name, sha in refs.items():
        short = re.sub(r"^refs/(heads|tags)/", "", name)
        if version in (name, short):
            candidates[name] = sha
    if len(set(candidates.values())) == 1:
        sha = next(iter(candidates.values()))
        selected_heads = [name.removeprefix("refs/heads/") for name in candidates if name.startswith("refs/heads/")]
        if selected_heads and not any(name in branches for name in selected_heads):
            raise ValueError("Requested branch belongs to another model or differs from source_branch.")
        if not belongs_to_branches(repository, sha, branches):
            raise ValueError("Requested ref does not belong to a selected device branch.")
        return sha, next(iter(candidates))
    if candidates:
        raise ValueError("Ambiguous branch/tag; use refs/heads/... or refs/tags/... explicitly.")

    return select_release(release_history(repository, branches), version)


def parse_build_metadata(kernel):
    makefile = (kernel / "Makefile").read_text(encoding="utf-8")
    numbers = []
    for key in ("VERSION", "PATCHLEVEL", "SUBLEVEL"):
        match = re.search(r"^" + key + r"\s*=\s*(\d+)\s*$", makefile, re.M)
        if not match:
            raise ValueError("Missing kernel version field: " + key)
        numbers.append(match[1])
    configs = "\n".join(p.read_text(encoding="utf-8") for p in
                        [kernel / "build.config.constants", kernel / "build.config.common"] if p.is_file())
    branches = set(re.findall(r"^BRANCH=['\"]?(android\d+)-(\d+\.\d+)", configs, re.M))
    if len(branches) != 1:
        raise ValueError("Cannot uniquely determine Android GKI base from Nothing build.config files.")
    android, base = branches.pop()
    if base != ".".join(numbers[:2]):
        raise ValueError("Kernel Makefile and build.config BRANCH disagree.")
    clang = re.search(r"^CLANG_VERSION=['\"]?(r[0-9]+[a-z0-9]*)", configs, re.M)
    if not clang:
        clang = re.search(r"/clang-(r[0-9]+[a-z0-9]*)/bin", configs)
    if not clang:
        raise ValueError("Nothing source does not declare a supported Clang version.")
    generations = set(re.findall(r"^KMI_GENERATION=['\"]?(\d+)", configs, re.M))
    if len(generations) != 1:
        raise ValueError("Cannot uniquely determine KMI_GENERATION from Nothing build.config files.")
    return {"android_version": android, "kernel_version": base,
            "sub_level": numbers[2], "clang_version": clang[1], "kmi_generation": generations.pop()}


def checkout(device, sha, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Source destination already exists: " + str(destination))
    destination.mkdir(parents=True)
    git("init", str(destination))
    git("remote", "add", "origin", "https://github.com/" + device["repository"] + ".git", cwd=destination)
    git("fetch", "--depth=1", "origin", sha, cwd=destination)
    git("checkout", "--detach", "FETCH_HEAD", cwd=destination)
    if git("rev-parse", "HEAD", cwd=destination) != sha:
        raise ValueError("Checked-out commit differs from resolved NothingOSS commit.")
    metadata = parse_build_metadata(destination)
    release_notes = (destination / "README.md").read_text(encoding="utf-8")
    if not re.search(device["readme_pattern"], release_notes, re.I):
        raise ValueError("This source commit's release notes do not identify the selected device.")
    if metadata["kernel_version"] != device["kernel"]:
        raise ValueError("This official ref uses a different kernel base; update the device profile first.")
    if not (destination / "arch/arm64/configs/gki_defconfig").is_file():
        raise ValueError("This source release has no gki_defconfig; a device-specific build recipe is required.")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--version")
    parser.add_argument("--branch", default="", help="Restrict OS version lookup to this official branch")
    parser.add_argument("--menu", action="store_true", help="Resolve an Actions dropdown choice")
    parser.add_argument("--settings", help="Validated custom version name and build time JSON")
    parser.add_argument("--list-refs", action="store_true")
    parser.add_argument("--list-versions", action="store_true")
    parser.add_argument("--directory", default="nothing-kernel/common")
    parser.add_argument("--output", default="nothing-plan.json")
    args = parser.parse_args()
    device = device_for(args.model)
    if args.list_refs:
        print(json.dumps(remote_refs(device["repository"]), indent=2))
        return
    if args.list_versions:
        refs = remote_refs(device["repository"])
        branches = device_branches(refs, device["branch_pattern"], args.branch)
        print(json.dumps(release_history(device["repository"], branches), ensure_ascii=False, indent=2))
        return
    if not args.version:
        parser.error("--version is required unless --list-refs is used")
    requested_label = args.version
    if args.menu:
        from nothing.catalog import select_entry
        entry = select_entry(args.model, args.version)
        args.version, args.branch = entry["version"], entry["branch"]
        sha, ref = resolve(device["repository"], entry["sha"], args.branch, device["branch_pattern"])
    else:
        sha, ref = resolve(device["repository"], args.version, args.branch, device["branch_pattern"])
    metadata = checkout(device, sha, args.directory)
    plan = {"device": device, "requested_version": args.version, "requested_branch": args.branch, "source_ref": ref,
            "source_sha": sha, "requested_label": requested_label, **metadata}
    if args.settings:
        from nothing.form import build_settings
        settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
        plan["build_settings"] = build_settings(settings)
    Path(args.output).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"device_id={device['id']}\nsource_sha={sha}\nclang_version={metadata['clang_version']}\n")
    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
