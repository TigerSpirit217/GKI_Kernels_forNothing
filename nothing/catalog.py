"""Build the Actions device/version menus from NothingOSS release commits."""

import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlencode

import yaml

from nothing import source

LATEST = "所选机型最新官方发布（运行时查询）"
CATALOG = source.ROOT / "nothing/releases.json"
WORKFLOW = source.ROOT / ".github/workflows/nothing-build.yml"


def devices():
    return json.loads((source.ROOT / "nothing/devices.json").read_text(encoding="utf-8"))["devices"]


def version_parts(line):
    os_version = re.search(r"\b(?:NOS|OS)[.\s]*(\d+(?:\.[0-9a-z]+)+)", line, re.I)
    if not os_version:
        raise ValueError("Unrecognized official release entry: " + line)
    build = re.search(r"\(([A-Za-z0-9][A-Za-z0-9._/+\-]+)\)", line)
    return os_version[1], build[1] if build else os_version[1]


def release_rank(entry):
    os_version = tuple(map(int, re.findall(r"\d+", entry["os_version"])))
    dates = re.findall(r"[-_](\d{8}|\d{6})[-_]\d{4}", entry["version"])
    build_date = ("20" + dates[-1] if len(dates[-1]) == 6 else dates[-1]) if dates else ""
    timestamp = datetime.fromisoformat(entry["committed_at"].replace("Z", "+00:00")).timestamp() if entry.get("committed_at") else 0
    return os_version, build_date, timestamp


def make_entries(device_id, device, history):
    entries = {}
    for release in history:
        if release["repository"] != device["repository"] or not re.search(device["branch_pattern"], release["branch"], re.I):
            continue
        # A shared branch's ancestors can still contain another device's notes.
        if not re.search(device["readme_pattern"], release["readme"], re.I):
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", release["sha"]):
            raise ValueError("Invalid official source SHA")
        for line in release["versions"]:
            os_version, version = version_parts(line)
            if version != os_version and re.split(r"[-_]", version)[0].lower() not in {name.lower() for name in device["device_names"]}:
                continue
            entry = {"model": device_id, "repository": device["repository"], "branch": release["branch"],
                     "sha": release["sha"], "version": version, "os_version": os_version,
                     "committed_at": release.get("committed_at", ""),
                     "label": f"{device['name']} | OS {os_version} | {version} | {release['sha'][:12]}"}
            key = version, release["sha"]
            if key not in entries or release["branch"] < entries[key]["branch"]:
                entries[key] = entry
    return sorted(entries.values(), key=lambda row: (release_rank(row), row["label"]), reverse=True)


def fetch_history(selected_devices):
    history = []
    for repository in sorted({item["repository"] for item in selected_devices.values()}):
        profiles = [item for item in selected_devices.values() if item["repository"] == repository]
        refs = source.remote_refs(repository)
        branches = {}
        for profile in profiles:
            branches.update(source.device_branches(refs, profile["branch_pattern"]))
        releases = source.release_history(repository, branches)
        notes = {}
        for release in releases:
            sha = release["sha"]
            if sha not in notes:
                data = source.api(f"repos/{repository}/contents/README.md?" + urlencode({"ref": sha}))
                if data.get("encoding") != "base64":
                    raise ValueError("GitHub did not return the official README content for " + sha)
                notes[sha] = base64.b64decode(data["content"]).decode("utf-8")
            history.append({**release, "repository": repository, "readme": notes[sha]})
    return history


def make_catalog(history):
    entries = []
    for device_id, device in devices().items():
        rows = make_entries(device_id, device, history)
        if not rows:
            raise ValueError("No verified official release entries for " + device_id)
        entries.extend(rows)
    return {"schema_version": 1, "entries": entries}


def validate_catalog(catalog):
    if catalog.get("schema_version") != 1:
        raise ValueError("Unsupported release catalog schema")
    profiles = devices()
    labels = set()
    for row in catalog["entries"]:
        device = profiles[row["model"]]
        if row["repository"] != device["repository"] or not re.search(device["branch_pattern"], row["branch"], re.I):
            raise ValueError("Catalog release belongs to another device")
        if not re.fullmatch(r"[0-9a-f]{40}", row["sha"]) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+\-]{0,180}", row["version"]):
            raise ValueError("Invalid source identity in catalog")
        expected = f"{device['name']} | OS {row['os_version']} | {row['version']} | {row['sha'][:12]}"
        if row["label"] != expected or row["label"] in labels:
            raise ValueError("Invalid or duplicate release menu label")
        labels.add(row["label"])
    if {entry["model"] for entry in catalog["entries"]} != set(profiles):
        raise ValueError("Release catalog must cover every configured model")


def render_workflow(text, catalog):
    validate_catalog(catalog)
    menus = {
        "MODEL": {"model": {"description": "选择你的手机型号", "required": True, "type": "choice",
                            "options": [item["name"] for item in devices().values()]}},
        "VERSION": {"version": {"description": "默认自动查询所选机型的最新发布；旧版本请选择同机型的条目",
                                "required": True, "type": "choice", "default": LATEST,
                                "options": [LATEST, *[entry["label"] for entry in catalog["entries"]]]}},
    }
    for name, menu in menus.items():
        begin = f"      # BEGIN NOTHING {name} MENU\n"
        end = f"      # END NOTHING {name} MENU"
        if text.count(begin) != 1 or text.count(end) != 1:
            raise ValueError("Workflow menu markers changed: " + name)
        body = yaml.safe_dump(menu, allow_unicode=True, sort_keys=False, width=200)
        body = "".join("      " + line + "\n" for line in body.splitlines())
        start, rest = text.split(begin)
        _, tail = rest.split(end)
        text = start + begin + body + end + tail
    return text


def select_entry(model, label, catalog_path=CATALOG):
    device = source.device_for(model)
    if label == LATEST:
        history = fetch_history({device["id"]: device})
        entries = make_entries(device["id"], device, history)
        if not entries:
            raise ValueError("所选机型没有可确认的官方发布，请更新版本目录后重试")
        best = entries[0]
        if len({row["sha"] for row in entries if release_rank(row) == release_rank(best)}) > 1:
            raise ValueError("最新发布存在歧义，请从版本下拉框选择完整 build 条目")
        return best
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    validate_catalog(catalog)
    entries = [entry for entry in catalog["entries"] if entry["label"] == label]
    if len(entries) != 1:
        raise ValueError("版本不在当前下拉目录中，请刷新页面后重新选择")
    entry = entries[0]
    if entry["model"] != device["id"]:
        raise ValueError(f"机型和版本不匹配：请选择 {device['name']} 开头的版本条目")
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Refresh official releases via the GitHub API")
    parser.add_argument("--history-file", help="Import previously fetched official release history JSON")
    parser.add_argument("--output-dir", default=str(source.ROOT))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        actual = WORKFLOW.read_text(encoding="utf-8")
        if render_workflow(actual, catalog) != actual:
            raise ValueError("Actions model/version menus are out of date; run python -m nothing.catalog")
        print(f"Verified {len(devices())} models and {len(catalog['entries'])} pinned version choices")
        return
    if args.refresh or args.history_file:
        history = json.loads(Path(args.history_file).read_text(encoding="utf-8")) if args.history_file else fetch_history(devices())
        catalog = make_catalog(history)
        catalog["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    output = Path(args.output_dir)
    catalog_file = output / "nothing/releases.json"
    workflow_file = output / ".github/workflows/nothing-build.yml"
    rendered = render_workflow(WORKFLOW.read_text(encoding="utf-8"), catalog)
    catalog_file.parent.mkdir(parents=True, exist_ok=True)
    workflow_file.parent.mkdir(parents=True, exist_ok=True)
    catalog_file.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    workflow_file.write_text(rendered, encoding="utf-8")
    print(f"Generated {len(catalog['entries'])} official version choices")


if __name__ == "__main__":
    main()
