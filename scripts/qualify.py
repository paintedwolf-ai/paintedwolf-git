#!/usr/bin/env python3
"""Qualify a built toolchain archive on a fresh Linux host.

Re-derives the ELF report from the archive and requires it to equal the build's,
then exercises the toolchain inside each supported distribution family: HTTPS with
the host CA bundle, refusal without one, a shallow clone and a Git LFS round trip.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform as host
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "toolchain"))
sys.path.insert(0, str(ROOT / "scripts"))
import archive as ARCHIVE  # noqa: E402
import lfs_server  # noqa: E402
from lock import archive_name, digest, read_json, read_lock, require, version  # noqa: E402
import platform_checks  # noqa: E402

MOUNT = "/opt/paintedwolf-git"
APT_PREPARE = "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends ca-certificates >/dev/null 2>&1"


def check_manifest(tree, lock, platform):
    manifest = read_json(tree / "toolchain.json")
    for key, expected in (("version", version(lock)), ("platform", platform), ("git_version", lock["git_version"]),
                          ("lfs_version", lock["lfs_version"]),
                          ("source_lock_sha256", digest(ROOT / "toolchain/source-lock.json"))):
        require(manifest.get(key) == expected, "toolchain.json differs from the source lock: " + key)
    require(manifest["trust"]["ca_bundle"] is None, "The toolchain must not carry a CA bundle")
    for name in ("NOTICES.md", "SOURCE.md"):
        require((tree / "licenses" / name).is_file(), "Missing licenses/" + name)
    return manifest


def parse_result(output):
    lines = [line for line in output.splitlines() if line.startswith("RESULT ")]
    require(len(lines) == 1, "Smoke run printed no result")
    return json.loads(lines[0][len("RESULT "):])


def image_identity(image):
    subprocess.run(["docker", "pull", "-q", image], check=True, stdout=subprocess.DEVNULL)
    output = subprocess.run(["docker", "image", "inspect", image], check=True, capture_output=True, text=True).stdout
    facts = json.loads(output)[0]
    return {"reference": image, "id": facts["Id"], "repo_digests": sorted(facts.get("RepoDigests") or [])}


def run_family(family, tree, lock, port):
    qualification = lock["qualification"]
    prepare = APT_PREPARE + " && " if family["prepare"] == "apt" else ""
    url = f"http://127.0.0.1:{port}/{family['name']}"
    resolved = image_identity(family["image"])
    command = ["docker", "run", "--rm", "--network", "host",
               "-v", f"{tree}:{MOUNT}:ro", "-v", f"{ROOT / 'scripts/smoke.sh'}:/smoke.sh:ro",
               "-e", "TOOLCHAIN=" + MOUNT, "-e", "HTTPS_REMOTE=" + qualification["https_remote"],
               "-e", "HTTPS_REF=" + qualification["https_ref"], "-e", "CLONE_REMOTE=" + qualification["clone_remote"],
               "-e", "LFS_URL=" + url, family["image"], "bash", "-c", prepare + "bash /smoke.sh"]
    process = subprocess.run(command, capture_output=True, text=True, timeout=900)
    sys.stderr.write(process.stderr)
    require(process.returncode == 0, family["name"] + " smoke failed: " + process.stderr.strip()[-600:])
    result = parse_result(process.stdout)
    with urllib.request.urlopen(url + "/stats", timeout=30) as response:
        stats = json.load(response)
    require(result["git_version"] == "git version " + lock["git_version"], family["name"] + ": " + result["git_version"])
    require(result["lfs_version"] == "git-lfs/" + lock["lfs_version"], family["name"] + ": " + result["lfs_version"])
    require(stats["uploads"] >= 1 and stats["downloads"] >= 1, family["name"] + ": LFS transfers missing")
    return {"family": family["name"], "image": resolved, **result, "lfs_transfers": stats}


def qualify(archive, checks, platform, output):
    lock = read_lock()
    require(host.machine() == lock["platforms"][platform]["machine"], "Qualify natively on " + platform)
    require(archive.name == archive_name(lock, platform), "Unexpected archive name: " + archive.name)
    require(not output.exists(), "Qualification output must be new")
    with tempfile.TemporaryDirectory(prefix="paintedwolf-git-qualify-") as temporary:
        scratch = Path(temporary)
        tree = ARCHIVE.extract_tree(archive, scratch / "tree")
        report = platform_checks.inspect_tree(tree, platform, lock)
        require(report == read_json(checks), "Archive images differ from the build's platform report")
        manifest = check_manifest(tree, lock, platform)
        server = lfs_server.serve(scratch / "lfs")
        try:
            families = [run_family(family, tree, lock, server.server_address[1])
                        for family in lock["qualification"]["families"] if platform in family["platforms"]]
        finally:
            server.shutdown()
            server.server_close()
    result = {"schema_version": 1, "version": version(lock), "platform": platform,
              "archive": {"name": archive.name, "sha256": digest(archive), "bytes": archive.stat().st_size},
              "platform_checks_sha256": digest(checks), "source_archive": manifest["source_archive"],
              "glibc_max": report["glibc_max"], "families": families,
              "host": {"kernel": host.release(), "machine": host.machine()},
              "completed_at": datetime.now(timezone.utc).isoformat()}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Print the ELF report for an extracted tree")
    inspect.add_argument("--tree", type=Path, required=True)
    inspect.add_argument("--platform", required=True, choices=("linux-amd64", "linux-arm64"))
    verify = commands.add_parser("verify", help="Qualify an archive in every distribution family")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--platform-checks", type=Path, required=True)
    verify.add_argument("--platform", required=True, choices=("linux-amd64", "linux-arm64"))
    verify.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            result = platform_checks.inspect_tree(args.tree.resolve(strict=True), args.platform, read_lock())
        else:
            result = qualify(args.archive.resolve(strict=True), args.platform_checks.resolve(strict=True),
                             args.platform, args.output.resolve())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, "qualify: " + str(error) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    os.umask(0o022)
    main()
