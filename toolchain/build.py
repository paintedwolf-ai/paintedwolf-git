#!/usr/bin/env python3
"""Fetch verified sources and build a portable Linux Git toolchain in the pinned image."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive as ARCHIVE  # noqa: E402
import licensing  # noqa: E402
from lock import (PACKAGE, archive_name, digest, read_lock, require, source_archive_name,  # noqa: E402
                  source_file_name, version)
import platform_checks  # noqa: E402

ROOT = PACKAGE.parent
OPENSSL_DIR = "/nonexistent/paintedwolf-git/openssl"
RECIPE = ("README.md", "toolchain")


def downloads(lock, platform):
    items = [(source_file_name(entry), entry["url"], entry["sha256"]) for _, entry in sorted(lock["sources"].items())]
    go = lock["tools"]["go"]["archives"][platform]
    items.append((source_file_name(go), go["url"], go["sha256"]))
    return items


def fetch(cache, platform, lock):
    """Download each pinned input once; a cached file is used only when its digest matches."""
    cache.mkdir(parents=True, exist_ok=True)
    for name, url, expected in downloads(lock, platform):
        target = cache / name
        if target.is_file() and digest(target) == expected:
            continue
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as partial:
            with urllib.request.urlopen(url, timeout=120) as response:
                shutil.copyfileobj(response, partial)
        actual = digest(Path(partial.name))
        if actual != expected:
            os.unlink(partial.name)
            raise ValueError(f"{name}: SHA-256 {actual} differs from the lock's {expected}")
        os.replace(partial.name, target)
        print(f"fetched {name}", file=sys.stderr)


def extract(tarball, root, destination):
    """Unpack one verified upstream archive whose members all sit under root/."""
    with tarfile.open(tarball) as source:
        for member in source.getmembers():
            require(member.name == root or member.name.startswith(root + "/"), "Unexpected member: " + member.name)
    destination.mkdir(parents=True)
    subprocess.run(["tar", "-xf", str(tarball), "-C", str(destination), "--strip-components=1",
                    "--no-same-owner", "--no-same-permissions"], check=True)


def shape_stage(stage):
    """Keep the dugite-native layout: bin/git, libexec/git-core, share/git-core/templates."""
    for path in (stage / "bin").iterdir():
        if path.name != "git":
            path.unlink()
    for path in (stage / "share").iterdir():
        if path.name != "git-core":
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
    require(sorted(p.name for p in (stage / "share/git-core").iterdir()) == ["templates"], "Unexpected share/git-core")
    require(sorted(p.name for p in stage.iterdir()) == ["bin", "libexec", "share"], "Unexpected install layout")
    for name in ("bin/git", "libexec/git-core/git", "libexec/git-core/git-remote-http", "libexec/git-core/git-lfs"):
        require((stage / name).is_file() and not (stage / name).is_symlink(), "Missing " + name)


def write_source_archive(destination, lock, cache, platform, lfs_vendor):
    with tempfile.TemporaryDirectory(prefix="source-") as temporary:
        root = Path(temporary) / "source"
        (root / "upstream").mkdir(parents=True)
        for name, url, expected in downloads(lock, platform)[:-1]:
            shutil.copyfile(cache / name, root / "upstream" / name)
        shutil.copytree(lfs_vendor, root / "git-lfs-vendor")
        for name in RECIPE:
            source = ROOT / name
            if source.is_dir():
                shutil.copytree(source, root / "recipe" / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                (root / "recipe").mkdir(exist_ok=True)
                shutil.copyfile(source, root / "recipe" / name)
        manifest = {"schema_version": 1, "version": version(lock),
                    "upstream": {name: {"file": source_file_name(entry), "url": entry["url"], "sha256": entry["sha256"]}
                                 for name, entry in sorted(lock["sources"].items())},
                    "git_lfs_vendor": "git-lfs-vendor/ is `go mod vendor` output for git-lfs, checked by vendor/modules.txt"}
        (root / "SOURCE-MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        ARCHIVE.write_tree(destination, root, prefix=source_archive_name(lock).removesuffix(".tar.gz") + "/")


def first_line(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip().splitlines()[0]


def build_tools(goroot):
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    return {"os_release": release.get("PRETTY_NAME", "").strip('"'), "gcc": first_line(["gcc", "--version"]),
            "glibc": first_line(["ldd", "--version"]), "binutils": first_line(["ld", "--version"]),
            "make": first_line(["make", "--version"]), "perl": first_line(["perl", "-e", "print $^V"]),
            "go": first_line([str(goroot / "bin/go"), "version"])}


def compile_toolchain(cache, work, platform, jobs, lock):
    """Runs inside the build image."""
    require(os.uname().machine == lock["platforms"][platform]["machine"], "Build natively for " + platform)
    require(work.is_dir() and not any(work.iterdir()), "Work directory must be empty")
    for name, url, expected in downloads(lock, platform):
        require(digest(cache / name) == expected, "Cached input differs from the lock: " + name)
    sources, deps, stage, gowork, out = (work / name for name in ("src", "deps", "stage", "gowork", "out"))
    for name, entry in sorted(lock["sources"].items()):
        extract(cache / source_file_name(entry), entry["root"], sources / name)
    go = lock["tools"]["go"]
    extract(cache / source_file_name(go["archives"][platform]), go["root"], work / "go")
    lfs = lock["sources"]["git-lfs"]
    environment = dict(os.environ, SRC=str(sources), DEPS=str(deps), STAGE=str(stage), JOBS=str(jobs),
                       OPENSSL_TARGET=lock["platforms"][platform]["openssl_target"], OPENSSL_DIR=OPENSSL_DIR,
                       LFS_COMMIT=lfs["commit"], LFS_VENDOR_SHA256=lfs["vendor_modules_sha256"],
                       GOROOT=str(work / "go"), GOWORK_DIR=str(gowork), GO_PROXY=go["proxy"], GO_SUMDB=go["sumdb"],
                       SOURCE_DATE_EPOCH="0", LC_ALL="C")
    subprocess.run(["bash", str(PACKAGE / "compile.sh")], check=True, env=environment)
    shape_stage(stage)
    out.mkdir()
    source_archive = out / source_archive_name(lock)
    write_source_archive(source_archive, lock, cache, platform, sources / "git-lfs/vendor")
    source_identity = (source_archive.name, digest(source_archive))
    licensing.write(stage, lock, platform, sources, work / "go", gowork / "gomodcache",
                    sources / "git-lfs/vendor/modules.txt", source_identity)
    manifest = {"schema_version": 1, "name": "paintedwolf-git", "version": version(lock), "platform": platform,
                "git_version": lock["git_version"], "lfs_version": lock["lfs_version"],
                "components": {name: {"version": entry["version"], "license": entry["license"], "sha256": entry["sha256"]}
                               for name, entry in sorted(lock["sources"].items())},
                "go_version": go["version"], "source_lock_sha256": digest(PACKAGE / "source-lock.json"),
                "source_archive": {"name": source_identity[0], "sha256": source_identity[1],
                                   "bytes": source_archive.stat().st_size},
                "trust": {"ca_bundle": None, "openssl_dir": OPENSSL_DIR,
                          "contract": "set GIT_SSL_CAINFO or http.sslCAInfo to the host CA bundle"}}
    (stage / "toolchain.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    report = platform_checks.inspect_tree(stage, platform, lock)
    (out / f"platform-checks-{platform}.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (out / f"build-tools-{platform}.json").write_text(json.dumps(build_tools(work / "go"), indent=2, sort_keys=True) + "\n")
    ARCHIVE.write_tree(out / archive_name(lock, platform), stage)
    print(json.dumps({"archive": archive_name(lock, platform), "sha256": digest(out / archive_name(lock, platform)),
                      "source_archive": source_identity[0], "source_sha256": source_identity[1]}, sort_keys=True))


def image_tag(lock):
    return "paintedwolf-git-build:" + digest(PACKAGE / "Dockerfile")[:12] + "-" + lock["build_image"].rsplit(":", 1)[1][:12]


def build(platform, output, cache, jobs, lock):
    """Fetch on the host, then compile in the pinned image with the checkout mounted read-only."""
    require(not output.exists(), "Output directory must be new")
    fetch(cache, platform, lock)
    tag = image_tag(lock)
    subprocess.run(["docker", "build", "--pull", "--build-arg", "BASE=" + lock["build_image"], "-t", tag,
                    "-f", str(PACKAGE / "Dockerfile"), str(PACKAGE)], check=True)
    output.mkdir(parents=True)
    subprocess.run(["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
                    "-v", f"{ROOT}:/recipe:ro", "-v", f"{cache}:/cache:ro", "-v", f"{output}:/work", tag,
                    "python3", "/recipe/toolchain/build.py", "compile", "--cache", "/cache", "--work", "/work",
                    "--platform", platform, "--jobs", str(jobs)], check=True)
    print(output / "out")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Validate the source lock offline")
    commands.add_parser("identity", help="Print the version, release tag and build image tag")
    for name in ("fetch", "compile", "build"):
        command = commands.add_parser(name)
        command.add_argument("--platform", required=True, choices=("linux-amd64", "linux-arm64"))
        command.add_argument("--cache", type=Path, default=ROOT / ".cache/sources")
        if name != "fetch":
            command.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
        if name == "compile":
            command.add_argument("--work", type=Path, required=True)
        if name == "build":
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        lock = read_lock()
        if args.command == "check":
            print(f"paintedwolf-git {version(lock)} source lock verified")
        elif args.command == "identity":
            print(json.dumps({"version": version(lock), "tag": "v" + version(lock), "image": image_tag(lock)}, sort_keys=True))
        elif args.command == "fetch":
            fetch(args.cache.resolve(), args.platform, lock)
        elif args.command == "compile":
            compile_toolchain(args.cache.resolve(), args.work.resolve(), args.platform, args.jobs, lock)
        else:
            build(args.platform, args.output.resolve(), args.cache.resolve(), args.jobs, lock)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, "build: " + str(error) + "\n")


if __name__ == "__main__":
    main()
