from pathlib import Path
import tempfile
import unittest
from unittest import mock

import support
import platform_checks as CHECKS

DYNAMIC = {"interpreter": "/lib64/ld-linux-x86-64.so.2", "needed": ["libc.so.6"], "runpath": [], "glibc": "2.34"}


def readelf_for(images):
    def readelf(option, path):
        facts = images[Path(path).relative_to(readelf.root).as_posix()]
        if option == "-h":
            return "  Machine:                           " + facts.get("machine", "Advanced Micro Devices X86-64") + "\n"
        if option == "-l":
            return f"      [Requesting program interpreter: {facts['interpreter']}]\n" if facts.get("interpreter") else ""
        if option == "-d":
            lines = [f" 0x1 (NEEDED)             Shared library: [{name}]" for name in facts.get("needed", [])]
            lines += [f" 0x1d (RUNPATH)            Library runpath: [{path}]" for path in facts.get("runpath", [])]
            return "\n".join(lines) + "\n"
        return "".join(f"  0x0010: Name: GLIBC_{version}  Flags: none  Version: 2\n"
                       for version in (["2.2.5", facts["glibc"]] if facts.get("glibc") else []))
    return readelf


class PlatformChecksTest(unittest.TestCase):
    def inspect(self, changes=None, remove=()):
        images = {"bin/git": dict(DYNAMIC), "libexec/git-core/git": dict(DYNAMIC),
                  "libexec/git-core/git-remote-http": dict(DYNAMIC), "libexec/git-core/git-lfs": {}}
        for name, facts in (changes or {}).items():
            images[name] = {**images.get(name, DYNAMIC), **facts}
        for name in remove:
            images.pop(name)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in images:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                body = b"\x7fELF" + (b" OpenSSL 3.5.9 30 Sep 2026" if name.endswith("git-remote-http") else b"")
                (root / name).write_bytes(body)
            (root / "libexec/git-core/git-sh-setup").write_text("#!/bin/sh\n")
            readelf = readelf_for(images)
            readelf.root = root
            with mock.patch.object(CHECKS, "readelf", readelf):
                return CHECKS.inspect_tree(root, "linux-amd64", support.lock())

    def test_glibc_only_images_pass(self):
        report = self.inspect()
        self.assertEqual(report["glibc_max"], "2.34")
        self.assertEqual(report["images"]["libexec/git-core/git-lfs"]["linkage"], "static")
        self.assertNotIn("libexec/git-core/git-sh-setup", report["images"])

    def test_portability_violations_are_refused(self):
        cases = {
            "gnutls curl": {"libexec/git-core/git-remote-http": {"needed": ["libc.so.6", "libcurl-gnutls.so.4"]}},
            "newer glibc": {"bin/git": {"glibc": "2.38"}},
            "runpath": {"bin/git": {"runpath": ["/work/deps/lib"]}},
            "loader": {"bin/git": {"interpreter": "/lib/ld-musl-x86_64.so.1"}},
            "architecture": {"bin/git": {"machine": "AArch64"}},
            "dynamic lfs": {"libexec/git-core/git-lfs": dict(DYNAMIC)},
        }
        for name, change in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                self.inspect(change)
        with self.assertRaises(ValueError):
            self.inspect(remove=("libexec/git-core/git-remote-http",))


if __name__ == "__main__":
    unittest.main()
