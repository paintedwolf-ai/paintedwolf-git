"""License texts, notices and the source statement that travel inside every toolchain archive."""
from pathlib import Path
import re
import shutil

from lock import require, source_file_name, version

LICENSE_NAME = re.compile(r"(licen[cs]e|copying|notice|patents)([._-].*)?", re.I)


def escape_module(path):
    """The module cache's case-encoding of a module path."""
    return re.sub(r"[A-Z]", lambda match: "!" + match.group(0).lower(), path)


def vendored_modules(modules_txt):
    modules = []
    for line in Path(modules_txt).read_text().splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0] == "#" and not fields[1].startswith("explicit"):
            require("=>" not in fields, "Replaced modules are not supported: " + line)
            modules.append((fields[1], fields[2]))
    require(modules, "No vendored modules listed")
    return sorted(set(modules))


def license_files(directory):
    return sorted(path for path in Path(directory).iterdir()
                  if path.is_file() and not path.is_symlink() and LICENSE_NAME.fullmatch(path.name))


def copy_texts(names, source, destination):
    destination.mkdir(parents=True)
    copied = []
    for name in names:
        path = Path(source) / name
        require(path.is_file() and not path.is_symlink(), "Missing license file: " + str(path))
        shutil.copyfile(path, destination / path.name)
        copied.append(path.name)
    return copied


def write(stage, lock, platform, sources, goroot, modcache, modules_txt, source_archive):
    """Populate stage/licenses from the exact sources the build consumed."""
    directory = Path(stage) / "licenses"
    directory.mkdir()
    rows = []
    for name, entry in sorted(lock["sources"].items()):
        texts = copy_texts(entry["license_files"], Path(sources) / name, directory / name)
        rows.append((name, entry["version"], entry["license"], entry["url"], name, texts))
    go = lock["tools"]["go"]
    texts = copy_texts(go["license_files"], goroot, directory / "go")
    rows.append(("go (runtime and standard library in git-lfs)", go["version"], go["license"],
                 "https://go.dev/dl/", "go", texts))
    modules = []
    for module, module_version in vendored_modules(modules_txt):
        root = Path(modcache) / (escape_module(module) + "@" + module_version)
        found = license_files(root)
        require(found, "No license text for Go module " + module + "@" + module_version)
        target = directory / "go-modules" / module
        target.mkdir(parents=True)
        for path in found:
            shutil.copyfile(path, target / path.name)
        modules.append((module, module_version, [path.name for path in found]))
    lines = [f"# Third-party notices — Painted Wolf Git {version(lock)} ({platform})", "",
             "This toolchain combines the works below. Each one's license texts are in the",
             "named directory beside this file. Git is distributed under GPL-2.0-only.", "",
             "| Component | Version | License | Upstream source | Texts |", "|---|---|---|---|---|"]
    for name, component_version, license_id, url, folder, texts in rows:
        lines.append(f"| {name} | {component_version} | {license_id} | {url} | "
                     + ", ".join(f"`{folder}/{text}`" for text in texts) + " |")
    lines += ["", "## Go modules in git-lfs", "",
              "Each module's license texts are under `go-modules/<module path>/`.", "",
              "| Module | Version | Texts |", "|---|---|---|"]
    for module, module_version, texts in modules:
        lines.append(f"| {module} | {module_version} | " + ", ".join(f"`{text}`" for text in texts) + " |")
    (directory / "NOTICES.md").write_text("\n".join(lines) + "\n")
    archive_name, archive_sha256 = source_archive
    tarballs = ", ".join(f"`{source_file_name(entry)}`" for _, entry in sorted(lock["sources"].items()))
    (directory / "SOURCE.md").write_text("\n".join([
        f"# Corresponding source — Painted Wolf Git {version(lock)}", "",
        "Git is licensed under the GNU General Public License, version 2. The complete",
        "corresponding source for every binary in this archive is published as an asset",
        "of the same immutable GitHub release:", "",
        f"- `{archive_name}`",
        f"- SHA-256 `{archive_sha256}`", "",
        f"It contains the verified upstream archives ({tarballs}), the Go module",
        "sources vendored for git-lfs, and the scripts and source lock that build",
        "this toolchain, which together reproduce these binaries.", "",
        f"Release: https://github.com/paintedwolf-ai/paintedwolf-git/releases/tag/v{version(lock)}",
    ]) + "\n")
