"""Deterministic tree archives: sorted members, zero times and owners, normalized modes."""
import gzip
import io
import os
from pathlib import Path, PurePosixPath
import tarfile

from lock import require

MAX_ARCHIVE_BYTES = 256 << 20
MAX_EXPANDED_BYTES = 1 << 30


def tree_entries(root):
    """Every path under root in a stable order, with symlinks kept as links inside the tree."""
    root = Path(root)
    entries = []
    for directory, names, files in os.walk(root):
        names.sort()
        for name in sorted(names + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                target = os.readlink(path)
                resolved = PurePosixPath(relative).parent / target
                require(not target.startswith("/") and ".." not in PurePosixPath(os.path.normpath(resolved)).parts,
                        "Symlink leaves the tree: " + relative)
                entries.append((relative, "symlink", target))
            elif path.is_dir():
                entries.append((relative, "dir", None))
            else:
                require(path.is_file(), "Unsupported file type: " + relative)
                entries.append((relative, "file", os.access(path, os.X_OK)))
    return sorted(entries)


def _info(name, kind, detail):
    info = tarfile.TarInfo(name)
    info.mtime = info.uid = info.gid = 0
    info.uname = info.gname = ""
    if kind == "dir":
        info.type, info.mode = tarfile.DIRTYPE, 0o755
    elif kind == "symlink":
        info.type, info.mode, info.linkname = tarfile.SYMTYPE, 0o777, detail
    else:
        info.mode = 0o755 if detail else 0o644
    return info


def write_tree(destination, root, prefix=""):
    """Write root as a gzip tar; returns the expanded tar size."""
    root = Path(root)
    destination = Path(destination)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, kind, detail in tree_entries(root):
            info = _info(prefix + name, kind, detail)
            if kind == "file":
                raw = (root / name).read_bytes()
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            else:
                archive.addfile(info)
    expanded = buffer.getvalue()
    require(len(expanded) <= MAX_EXPANDED_BYTES, "Archive exceeds the expanded size limit")
    with destination.open("xb") as stream:
        with gzip.GzipFile(fileobj=stream, filename="", mode="wb", mtime=0, compresslevel=9) as compressed:
            compressed.write(expanded)
    require(destination.stat().st_size <= MAX_ARCHIVE_BYTES, "Archive exceeds the compressed size limit")
    return len(expanded)


def extract_tree(archive, destination):
    """Extract an archive written by write_tree, refusing anything write_tree would not produce."""
    destination = Path(destination)
    require(not destination.exists(), "Extraction target must be new")
    destination.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        names = [member.name for member in members]
        require(names == sorted(names) and len(set(names)) == len(names), "Archive members are not canonical")
        total = 0
        for member in members:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts and member.name == path.as_posix(),
                    "Unsafe archive member: " + member.name)
            require(member.isdir() or member.isfile() or member.issym(), "Unsupported member: " + member.name)
            require(member.uid == 0 and member.gid == 0 and member.mtime == 0, "Archive member is not normalized")
            if member.issym():
                resolved = os.path.normpath(str(path.parent / member.linkname))
                require(not member.linkname.startswith("/") and not resolved.startswith(".."),
                        "Symlink leaves the archive: " + member.name)
            total += member.size
            require(total <= MAX_EXPANDED_BYTES, "Archive exceeds the expanded size limit")
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir()
            elif member.issym():
                os.symlink(member.linkname, target)
            else:
                with source.extractfile(member) as stream:
                    target.write_bytes(stream.read())
                os.chmod(target, member.mode & 0o755)
    return destination
