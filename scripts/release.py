#!/usr/bin/env python3
"""Package qualified toolchain archives into a release directory without rebuilding them."""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "toolchain"))
from lock import (archive_name, digest, download_url, read_json, read_lock, require,  # noqa: E402
                  source_archive_name, tag, version)

TARGETS = {"linux-amd64": ("linux", "amd64"), "linux-arm64": ("linux", "arm64")}


def descriptor(lock):
    return {"version": version(lock), "git_version": lock["git_version"], "lfs_version": lock["lfs_version"],
            "revision": lock["revision"], "license": "GPL-2.0-only", "glibc_max": lock["glibc_max"],
            "source_lock_sha256": digest(ROOT / "toolchain/source-lock.json"),
            "components": {name: entry["version"] for name, entry in sorted(lock["sources"].items())}}


def file_record(lock, path):
    return {"name": path.name, "url": download_url(lock, path.name), "sha256": digest(path), "bytes": path.stat().st_size}


def pack(platform, archive, source, checks, qualification, release_tag, output, lock=None):
    """One platform's release files, checked against its qualification record."""
    lock = lock or read_lock()
    require(release_tag == tag(lock), "Release tag must be " + tag(lock))
    require(not output.exists(), "Release output directory must be new")
    require(archive.name == archive_name(lock, platform) and source.name == source_archive_name(lock),
            "Unexpected archive names")
    record = read_json(qualification)
    require(record.get("version") == version(lock) and record.get("platform") == platform,
            "Qualification describes another toolchain")
    require(record["archive"] == {"name": archive.name, "sha256": digest(archive), "bytes": archive.stat().st_size},
            "Archive differs from its qualification")
    require(record["platform_checks_sha256"] == digest(checks), "Platform report differs from its qualification")
    require(record["source_archive"] == {"name": source.name, "sha256": digest(source), "bytes": source.stat().st_size},
            "Source archive differs from the one the toolchain names")
    families = [family["family"] for family in record["families"]]
    expected = [family["name"] for family in lock["qualification"]["families"] if platform in family["platforms"]]
    require(families == expected, "Qualification did not cover every family for " + platform)
    output.mkdir(parents=True)
    names = (archive, source, checks, qualification)
    require([path.name for path in names[2:]] == [f"platform-checks-{platform}.json", f"qualification-{platform}.json"],
            "Unexpected evidence names")
    for path in names:
        shutil.copyfile(path, output / path.name)
    artifact = {"platform": platform, "goos": TARGETS[platform][0], "goarch": TARGETS[platform][1],
                "reported_version": lock["git_version"], **file_record(lock, output / archive.name)}
    metadata = {"paintedwolf_git": {**descriptor(lock), "source": file_record(lock, output / source.name),
                                    "artifacts": [artifact]}}
    (output / "release.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def merge(sources, output, lock=None):
    """One release from per-platform outputs: every file checked against the descriptor that named it."""
    lock = lock or read_lock()
    require(not output.exists(), "Release output directory must be new")
    require(len(sources) == len(TARGETS), "A release carries every platform")
    shared, artifacts, files = None, [], {}
    for directory in sources:
        record = read_json(directory / "release.json")["paintedwolf_git"]
        rows = record.pop("artifacts")
        require(len(rows) == 1, "Each platform output names exactly one archive")
        require(shared is None or record == shared, "Platform outputs describe different toolchains or sources")
        shared = record
        row = rows[0]
        platform = row["platform"]
        expected = {"release.json", row["name"], record["source"]["name"],
                    f"platform-checks-{platform}.json", f"qualification-{platform}.json"}
        require({path.name for path in directory.iterdir()} == expected, "Unexpected files in " + str(directory))
        for item in (row, record["source"]):
            path = directory / item["name"]
            require(digest(path) == item["sha256"] and path.stat().st_size == item["bytes"],
                    "File differs from its descriptor: " + item["name"])
        for name in expected - {"release.json"}:
            files.setdefault(name, directory / name)
        artifacts.append(row)
    require(sorted(row["platform"] for row in artifacts) == sorted(TARGETS), "A release carries each platform once")
    require(shared == {**descriptor(lock), "source": shared["source"]}, "Descriptor differs from the source lock")
    output.mkdir(parents=True)
    for name, path in sorted(files.items()):
        shutil.copyfile(path, output / name)
    metadata = {"paintedwolf_git": {**shared, "artifacts": sorted(artifacts, key=lambda row: row["platform"])}}
    (output / "release.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-inputs", help="Validate release metadata inputs offline")
    command = commands.add_parser("pack")
    command.add_argument("--platform", required=True, choices=sorted(TARGETS))
    command.add_argument("--build", type=Path, required=True, help="Directory with the build and qualification files")
    command.add_argument("--tag", required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("merge")
    command.add_argument("sources", type=Path, nargs="+")
    command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        lock = read_lock()
        if args.command == "check-inputs":
            require(re.fullmatch(r"v[0-9.]+\+paintedwolf\.[0-9]+", tag(lock)) is not None, "Invalid release tag")
            print("paintedwolf-git release inputs verified for " + tag(lock))
            return
        if args.command == "pack":
            build = args.build.resolve(strict=True)
            metadata = pack(args.platform, build / archive_name(lock, args.platform), build / source_archive_name(lock),
                            build / f"platform-checks-{args.platform}.json",
                            build / f"qualification-{args.platform}.json", args.tag, args.output.resolve(), lock)
        else:
            metadata = merge([path.resolve(strict=True) for path in args.sources], args.output.resolve(), lock)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, "release: " + str(error) + "\n")
    print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
