import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

import support  # noqa: F401
import archive as ARCHIVE


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        tree = self.root / "tree"
        (tree / "bin").mkdir(parents=True)
        (tree / "libexec/git-core").mkdir(parents=True)
        (tree / "libexec/git-core/git").write_bytes(b"\x7fELF git")
        os.chmod(tree / "libexec/git-core/git", 0o700)
        (tree / "libexec/git-core/git-sh-setup").write_text("shell library\n")
        os.symlink("git", tree / "libexec/git-core/git-add")
        (tree / "bin/git").write_bytes(b"\x7fELF git")
        os.chmod(tree / "bin/git", 0o750)
        self.tree = tree

    def test_archives_are_byte_identical_and_normalized(self):
        first, second = self.root / "a.tar.gz", self.root / "b.tar.gz"
        ARCHIVE.write_tree(first, self.tree)
        os.utime(self.tree / "bin/git", (1, 1))
        ARCHIVE.write_tree(second, self.tree)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as archive:
            members = {member.name: member for member in archive.getmembers()}
        self.assertEqual(sorted(members), sorted(members.keys()))
        self.assertEqual(members["bin/git"].mode, 0o755)
        self.assertEqual(members["libexec/git-core/git-sh-setup"].mode, 0o644)
        self.assertEqual(members["libexec/git-core/git-add"].linkname, "git")
        self.assertTrue(all(member.mtime == 0 and member.uid == 0 for member in members.values()))

    def test_round_trip_preserves_links_and_modes(self):
        archive = self.root / "tree.tar.gz"
        ARCHIVE.write_tree(archive, self.tree)
        extracted = ARCHIVE.extract_tree(archive, self.root / "out")
        self.assertEqual(os.readlink(extracted / "libexec/git-core/git-add"), "git")
        self.assertTrue(os.access(extracted / "bin/git", os.X_OK))
        self.assertEqual(ARCHIVE.tree_entries(extracted), ARCHIVE.tree_entries(self.tree))

    def test_escaping_symlink_is_refused(self):
        os.symlink("../../../etc/passwd", self.tree / "libexec/git-core/escape")
        with self.assertRaises(ValueError):
            ARCHIVE.write_tree(self.root / "bad.tar.gz", self.tree)

    def test_unsafe_members_are_refused(self):
        for name, kind, link in (("../evil", tarfile.REGTYPE, ""), ("a/link", tarfile.SYMTYPE, "../../x"),
                                 ("dev", tarfile.CHRTYPE, "")):
            path = self.root / (kind.decode() + ".tar.gz")
            with tarfile.open(path, "w:gz") as archive:
                info = tarfile.TarInfo(name)
                info.type, info.linkname, info.mtime = kind, link, 0
                archive.addfile(info, io.BytesIO(b"") if kind == tarfile.REGTYPE else None)
            with self.subTest(name=name), self.assertRaises(ValueError):
                ARCHIVE.extract_tree(path, self.root / ("out-" + kind.decode()))


if __name__ == "__main__":
    unittest.main()
