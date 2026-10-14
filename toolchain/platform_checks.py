"""ELF facts for every executable image in a toolchain tree, and the rules they must meet.

The report holds only facts derived from the bytes, so the build and a fresh runner
must produce identical reports for the same archive.
"""
from pathlib import Path
import re
import subprocess

from lock import digest, require, version_tuple

ALLOWED_LIBRARIES = {"libc.so.6", "libm.so.6", "libpthread.so.0", "libdl.so.2", "librt.so.1", "libresolv.so.2",
                     "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1"}
MACHINES = {"linux-amd64": "Advanced Micro Devices X86-64", "linux-arm64": "AArch64"}
# Binaries whose transport must be the statically linked curl and OpenSSL.
HTTP_HELPERS = ("libexec/git-core/git-remote-http",)


def readelf(*arguments):
    return subprocess.run(["readelf", "-W", *arguments], check=True, capture_output=True, text=True,
                          env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"}).stdout


def is_elf(path):
    with path.open("rb") as stream:
        return stream.read(4) == b"\x7fELF"


def image_facts(path):
    header = readelf("-h", str(path))
    machine = re.search(r"^\s*Machine:\s*(.+)$", header, re.M).group(1).strip()
    program = readelf("-l", str(path))
    interpreter = re.search(r"Requesting program interpreter: ([^\]]+)\]", program)
    dynamic = readelf("-d", str(path))
    needed = sorted(re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", dynamic))
    runpath = sorted(re.findall(r"\((?:RUNPATH|RPATH)\)\s+Library (?:runpath|rpath): \[([^\]]*)\]", dynamic))
    versions = sorted(set(re.findall(r"Name: GLIBC_([0-9.]+)", readelf("-V", str(path)))), key=version_tuple)
    return {"sha256": digest(path), "machine": machine,
            "linkage": "dynamic" if interpreter else "static",
            "interpreter": interpreter.group(1) if interpreter else None,
            "needed": needed, "runpath": runpath,
            "glibc_max": versions[-1] if versions else None}


def inspect_tree(root, platform, lock):
    """Facts for every regular ELF file, checked against the platform's rules."""
    root = Path(root)
    images = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink() and is_elf(path):
            images[path.relative_to(root).as_posix()] = image_facts(path)
    expected = lock["platforms"][platform]
    ceiling = version_tuple(lock["glibc_max"])
    require(images, "The tree has no ELF images")
    for name, facts in images.items():
        require(facts["machine"] == MACHINES[platform], name + " targets " + facts["machine"])
        require(not facts["runpath"], name + " carries a runpath")
        unexpected = sorted(set(facts["needed"]) - ALLOWED_LIBRARIES)
        require(not unexpected, name + " needs libraries beyond glibc: " + ", ".join(unexpected))
        if facts["linkage"] == "dynamic":
            require(facts["interpreter"] == expected["interpreter"], name + " uses loader " + str(facts["interpreter"]))
            require(facts["glibc_max"] is not None and version_tuple(facts["glibc_max"]) <= ceiling,
                    name + " requires glibc " + str(facts["glibc_max"]))
    for helper in HTTP_HELPERS:
        require(images.get(helper, {}).get("linkage") == "dynamic", helper + " is missing")
        raw = (root / helper).read_bytes()
        openssl = lock["sources"]["openssl"]["version"]
        require(("OpenSSL " + openssl + " ").encode() in raw, helper + " does not embed OpenSSL " + openssl)
    for name in ("bin/git", "libexec/git-core/git"):
        require(images.get(name, {}).get("linkage") == "dynamic", name + " is missing")
    require(images.get("libexec/git-core/git-lfs", {}).get("linkage") == "static",
            "git-lfs must be a static executable")
    glibc = [facts["glibc_max"] for facts in images.values() if facts["glibc_max"]]
    return {"schema_version": 1, "platform": platform, "glibc_ceiling": lock["glibc_max"],
            "glibc_max": max(glibc, key=version_tuple), "images": images}
