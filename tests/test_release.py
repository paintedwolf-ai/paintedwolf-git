import json
from pathlib import Path
import shutil
import tempfile
import unittest

import support
import lock as LOCK
import release as RELEASE


def build_directory(root, lock, platform, source_bytes=b"source"):
    directory = root / ("build-" + platform)
    directory.mkdir()
    archive = directory / LOCK.archive_name(lock, platform)
    archive.write_bytes(b"toolchain " + platform.encode())
    source = directory / LOCK.source_archive_name(lock)
    source.write_bytes(source_bytes)
    checks = directory / f"platform-checks-{platform}.json"
    checks.write_text("{}\n")
    families = [{"family": family["name"]} for family in lock["qualification"]["families"] if platform in family["platforms"]]
    record = {"version": LOCK.version(lock), "platform": platform,
              "archive": {"name": archive.name, "sha256": LOCK.digest(archive), "bytes": archive.stat().st_size},
              "platform_checks_sha256": LOCK.digest(checks),
              "source_archive": {"name": source.name, "sha256": LOCK.digest(source), "bytes": source.stat().st_size},
              "families": families}
    (directory / f"qualification-{platform}.json").write_text(json.dumps(record))
    return directory


def pack(root, lock, platform, directory=None):
    directory = directory or build_directory(root, lock, platform)
    return RELEASE.pack(platform, directory / LOCK.archive_name(lock, platform), directory / LOCK.source_archive_name(lock),
                        directory / f"platform-checks-{platform}.json", directory / f"qualification-{platform}.json",
                        LOCK.tag(lock), root / ("release-" + platform), lock)


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock = support.lock()

    def test_pack_and_merge_describe_every_platform(self):
        for platform in RELEASE.TARGETS:
            pack(self.root, self.lock, platform)
        metadata = RELEASE.merge([self.root / "release-linux-amd64", self.root / "release-linux-arm64"],
                                 self.root / "release", self.lock)["paintedwolf_git"]
        self.assertEqual([row["platform"] for row in metadata["artifacts"]], ["linux-amd64", "linux-arm64"])
        self.assertEqual(metadata["artifacts"][1]["url"], LOCK.download_url(self.lock, LOCK.archive_name(self.lock, "linux-arm64")))
        self.assertEqual(metadata["artifacts"][0]["reported_version"], self.lock["git_version"])
        self.assertEqual(metadata["source"]["name"], LOCK.source_archive_name(self.lock))
        self.assertEqual(len(list((self.root / "release").iterdir())), 8)

    def test_pack_refuses_unqualified_bytes(self):
        directory = build_directory(self.root, self.lock, "linux-amd64")
        (directory / LOCK.archive_name(self.lock, "linux-amd64")).write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            pack(self.root, self.lock, "linux-amd64", directory)

    def test_pack_refuses_missing_family_and_wrong_tag(self):
        directory = build_directory(self.root, self.lock, "linux-amd64")
        path = directory / "qualification-linux-amd64.json"
        record = json.loads(path.read_text())
        record["families"].pop()
        path.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            pack(self.root, self.lock, "linux-amd64", directory)
        with self.assertRaises(ValueError):
            RELEASE.pack("linux-amd64", directory / "a", directory / "b", directory / "c", path, "v0", self.root / "x", self.lock)

    def test_merge_refuses_different_sources(self):
        pack(self.root, self.lock, "linux-amd64")
        pack(self.root, self.lock, "linux-arm64", build_directory(self.root, self.lock, "linux-arm64", b"other source"))
        with self.assertRaises(ValueError):
            RELEASE.merge([self.root / "release-linux-amd64", self.root / "release-linux-arm64"], self.root / "release", self.lock)

    def test_merge_refuses_extra_files(self):
        for platform in RELEASE.TARGETS:
            pack(self.root, self.lock, platform)
        shutil.copyfile(self.root / "release-linux-amd64/release.json", self.root / "release-linux-amd64/extra.json")
        with self.assertRaises(ValueError):
            RELEASE.merge([self.root / "release-linux-amd64", self.root / "release-linux-arm64"], self.root / "release", self.lock)


if __name__ == "__main__":
    unittest.main()
