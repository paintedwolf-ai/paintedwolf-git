import unittest

import support
import lock as LOCK


class LockTest(unittest.TestCase):
    def test_checked_in_lock_is_valid(self):
        lock = LOCK.read_lock()
        self.assertEqual(LOCK.version(lock), lock["git_version"] + "+paintedwolf." + str(lock["revision"]))
        self.assertEqual(LOCK.tag(lock), "v" + LOCK.version(lock))
        self.assertEqual(LOCK.archive_name(lock, "linux-arm64"),
                         "paintedwolf-git-" + LOCK.version(lock) + "-linux-arm64.tar.gz")

    def test_mutations_are_refused(self):
        def unpinned_image(lock):
            lock["build_image"] = "ubuntu:22.04"

        def short_digest(lock):
            lock["sources"]["curl"]["sha256"] = "abc"

        def http_source(lock):
            lock["sources"]["zlib"]["url"] = lock["sources"]["zlib"]["url"].replace("https://", "http://")

        def git_version_drift(lock):
            lock["git_version"] = "2.54.0"

        def unpinned_vendor(lock):
            lock["sources"]["git-lfs"]["vendor_modules_sha256"] = "PENDING"

        def extra_source(lock):
            lock["sources"]["expat"] = dict(lock["sources"]["zlib"])

        def unknown_family_platform(lock):
            lock["qualification"]["families"][0]["platforms"] = ["linux-riscv64"]

        def unqualified_platform(lock):
            for family in lock["qualification"]["families"]:
                family["platforms"] = ["linux-amd64"]

        def go_archive_mismatch(lock):
            lock["tools"]["go"]["archives"]["linux-arm64"]["url"] = "https://go.dev/dl/go1.0.linux-arm64.tar.gz"

        for mutate in (unpinned_image, short_digest, http_source, git_version_drift, unpinned_vendor,
                       extra_source, unknown_family_platform, unqualified_platform, go_archive_mismatch):
            lock = support.lock()
            mutate(lock)
            with self.subTest(mutation=mutate.__name__), self.assertRaises(ValueError):
                LOCK.check_lock(lock)


if __name__ == "__main__":
    unittest.main()
