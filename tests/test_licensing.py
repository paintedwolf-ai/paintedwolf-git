from pathlib import Path
import tempfile
import unittest

import support
import licensing as LICENSING


class LicensingTest(unittest.TestCase):
    def test_module_paths_use_cache_case_encoding(self):
        self.assertEqual(LICENSING.escape_module("github.com/BurntSushi/toml"), "github.com/!burnt!sushi/toml")

    def test_notices_cover_every_component_and_module(self):
        lock = support.lock()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, entry in lock["sources"].items():
                (root / "src" / name).mkdir(parents=True)
                for text in entry["license_files"]:
                    (root / "src" / name / text).write_text(name + " license\n")
            (root / "go").mkdir()
            for text in lock["tools"]["go"]["license_files"]:
                (root / "go" / text).write_text("go license\n")
            module = root / "modcache/github.com/!burnt!sushi/toml@v1.0.0"
            module.mkdir(parents=True)
            (module / "COPYING").write_text("MIT\n")
            (module / "toml.go").write_text("package toml\n")
            modules = root / "modules.txt"
            modules.write_text("# github.com/BurntSushi/toml v1.0.0\n## explicit\ngithub.com/BurntSushi/toml\n")
            (root / "stage").mkdir()
            LICENSING.write(root / "stage", lock, "linux-amd64", root / "src", root / "go", root / "modcache",
                            modules, ("source.tar.gz", "f" * 64))
            licenses = root / "stage/licenses"
            notices = (licenses / "NOTICES.md").read_text()
            for name in lock["sources"]:
                self.assertIn("| " + name + " |", notices)
            self.assertIn("github.com/BurntSushi/toml | v1.0.0 | `COPYING`", notices)
            self.assertTrue((licenses / "go-modules/github.com/BurntSushi/toml/COPYING").is_file())
            self.assertFalse((licenses / "go-modules/github.com/BurntSushi/toml/toml.go").exists())
            self.assertIn("f" * 64, (licenses / "SOURCE.md").read_text())

    def test_module_without_license_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "modcache/example.com/m@v1.0.0").mkdir(parents=True)
            modules = root / "modules.txt"
            modules.write_text("# example.com/m v1.0.0\n")
            lock = support.lock()
            lock["sources"] = {}
            lock["tools"]["go"]["license_files"] = []
            (root / "stage").mkdir()
            with self.assertRaises(ValueError):
                LICENSING.write(root / "stage", lock, "linux-amd64", root, root, root / "modcache", modules, ("s", "0" * 64))


if __name__ == "__main__":
    unittest.main()
