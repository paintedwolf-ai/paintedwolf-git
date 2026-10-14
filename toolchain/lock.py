"""Source lock access and validation shared by the build, qualification and release tools."""
import hashlib
import json
from pathlib import Path
import re

PACKAGE = Path(__file__).resolve().parent
LOCK = PACKAGE / "source-lock.json"
REPOSITORY = "paintedwolf-ai/paintedwolf-git"
SOURCE_NAMES = ("git", "git-lfs", "curl", "openssl", "zlib")
SHA256 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"[0-9]+(\.[0-9]+)+")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "Expected a regular file: " + str(path))
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            result.update(block)
    return result.hexdigest()


def read_json(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "Expected a regular JSON file: " + str(path))
    return json.loads(path.read_text())


def read_lock(path=LOCK):
    return check_lock(read_json(path))


def version(lock):
    return f'{lock["git_version"]}+paintedwolf.{lock["revision"]}'


def tag(lock):
    return "v" + version(lock)


def archive_name(lock, platform):
    return f"paintedwolf-git-{version(lock)}-{platform}.tar.gz"


def source_archive_name(lock):
    return f"paintedwolf-git-{version(lock)}-source.tar.gz"


def download_url(lock, name):
    return f"https://github.com/{REPOSITORY}/releases/download/{tag(lock)}/{name}"


def source_file_name(entry):
    return entry["url"].rsplit("/", 1)[1]


def version_tuple(text):
    require(VERSION.fullmatch(text) is not None, "Invalid version: " + str(text))
    return tuple(int(part) for part in text.split("."))


def check_lock(lock):
    require(lock.get("schema_version") == 1 and lock.get("name") == "paintedwolf-git", "Unknown source lock schema")
    require(type(lock.get("revision")) is int and lock["revision"] > 0, "Revision must be a positive integer")
    version_tuple(lock.get("git_version", ""))
    version_tuple(lock.get("lfs_version", ""))
    version_tuple(lock.get("glibc_max", ""))
    require(re.fullmatch(r"[a-z0-9.:/_-]+@sha256:[0-9a-f]{64}", lock.get("build_image", "")) is not None,
            "The build image must be pinned by digest")
    platforms = lock.get("platforms", {})
    require(sorted(platforms) == ["linux-amd64", "linux-arm64"], "Released platforms are linux-amd64 and linux-arm64")
    for name, platform in platforms.items():
        require(set(platform) == {"machine", "openssl_target", "interpreter"}, "Incomplete platform: " + name)
    sources = lock.get("sources", {})
    require(sorted(sources) == sorted(SOURCE_NAMES), "Sources must be exactly " + ", ".join(SOURCE_NAMES))
    for name, entry in sources.items():
        for key in ("version", "url", "sha256", "checksum_source", "root", "license", "license_files"):
            require(entry.get(key), name + " source is missing " + key)
        require(entry["url"].startswith("https://"), name + " source must use HTTPS")
        require(SHA256.fullmatch(entry["sha256"]) is not None, name + " source digest is not a SHA-256")
        require(entry["version"] in source_file_name(entry), name + " source URL does not name its version")
    require(sources["git"]["version"] == lock["git_version"], "Git source differs from git_version")
    require(sources["git-lfs"]["version"] == lock["lfs_version"], "Git LFS source differs from lfs_version")
    require(re.fullmatch(r"[0-9a-f]{40}", sources["git-lfs"].get("commit", "")) is not None,
            "Git LFS needs its full release commit")
    require(SHA256.fullmatch(sources["git-lfs"].get("vendor_modules_sha256", "")) is not None,
            "Git LFS vendored modules must be pinned")
    go = lock.get("tools", {}).get("go", {})
    version_tuple(go.get("version", ""))
    require(sorted(go.get("archives", {})) == sorted(platforms), "Go needs an archive for every platform")
    for name, archive in go["archives"].items():
        require(archive["url"].startswith("https://go.dev/dl/go" + go["version"] + "."), "Go archive URL differs: " + name)
        require(SHA256.fullmatch(archive["sha256"]) is not None, "Go archive digest is not a SHA-256: " + name)
    require(go.get("proxy", "").startswith("https://") and go.get("sumdb"), "Go module proxy and checksum database are required")
    qualification = lock.get("qualification", {})
    families = qualification.get("families", [])
    require(families and len({family["name"] for family in families}) == len(families), "Families need unique names")
    for family in families:
        require(set(family["platforms"]) <= set(platforms) and family["platforms"], "Unknown family platform")
        require(family.get("prepare") in (None, "apt"), "Unknown family preparation: " + family["name"])
    for platform in platforms:
        require(any(platform in family["platforms"] for family in families), "No family qualifies " + platform)
    for key in ("https_remote", "clone_remote"):
        require(qualification.get(key, "").startswith("https://"), "Qualification remotes must use HTTPS")
    require(qualification.get("https_ref", "").startswith("refs/"), "Qualification needs a full remote ref")
    return lock
