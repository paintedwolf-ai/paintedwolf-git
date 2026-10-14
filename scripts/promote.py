#!/usr/bin/env python3
"""Verify attested release files and create a complete draft without overwriting a version."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release as RELEASE  # noqa: E402
import release_provenance as PROVENANCE  # noqa: E402
from lock import archive_name, digest, download_url, read_json, read_lock, require, source_archive_name  # noqa: E402


def release_files(directory, identity, lock):
    record = read_json(directory / "release.json")["paintedwolf_git"]
    require(record.get("version") == identity["version"] and record.get("source_lock_sha256") == identity["source_lock_sha256"],
            "Release descriptor identity differs")
    names = [source_archive_name(lock)]
    expected = []
    for platform in sorted(RELEASE.TARGETS):
        archive = directory / archive_name(lock, platform)
        expected.append({"platform": platform, "goos": RELEASE.TARGETS[platform][0], "goarch": RELEASE.TARGETS[platform][1],
                         "reported_version": lock["git_version"], "name": archive.name,
                         "url": download_url(lock, archive.name), "sha256": digest(archive), "bytes": archive.stat().st_size})
        PROVENANCE.check_evidence(read_json(directory / PROVENANCE.evidence_name(platform)), identity, platform, lock, directory)
        names += [archive.name, PROVENANCE.evidence_name(platform), f"platform-checks-{platform}.json",
                  f"qualification-{platform}.json"]
    require(record.get("artifacts") == expected, "Release archives differ from descriptor")
    source = directory / source_archive_name(lock)
    require(record["source"] == {"name": source.name, "url": download_url(lock, source.name),
                                 "sha256": digest(source), "bytes": source.stat().st_size}, "Source archive differs from descriptor")
    files = [directory / name for name in sorted(names)] + [directory / "release.json", directory / "provenance.sigstore.json"]
    require({path.name for path in directory.iterdir()} == {path.name for path in files},
            "Release directory must contain exactly the archives, evidence, descriptor and attestation")
    for path in files:
        digest(path)
    return files


def validate_uploaded_assets(release, files, identity):
    require(release.get("draft") is True and release.get("tag_name") == identity["ref"].removeprefix("refs/tags/"),
            "Uploaded release is not the expected draft")
    assets = release.get("assets", [])
    expected = {path.name: (path.stat().st_size, "sha256:" + digest(path)) for path in files}
    require(len(assets) == len(expected) and {asset["name"] for asset in assets} == set(expected),
            "Draft release does not contain the complete exact asset set")
    for asset in assets:
        require(asset.get("state") == "uploaded" and (asset.get("size"), asset.get("digest")) == expected[asset["name"]],
                "Uploaded asset differs: " + asset["name"])


def draft(directory, identity, lock):
    files = release_files(directory, identity, lock)
    for subject in files[:-1]:
        PROVENANCE.verify_attestation(subject, files[-1], identity)
    PROVENANCE.require_unpublished(identity)
    release_tag = identity["ref"].removeprefix("refs/tags/")
    with tempfile.TemporaryDirectory(prefix="paintedwolf-git-draft-") as temporary:
        notes = Path(temporary) / "notes.md"
        notes.write_text(
            f"Portable Git {lock['git_version']} and Git LFS {lock['lfs_version']} for Linux amd64 and arm64, "
            f"glibc {lock['glibc_max']} and later. Point GIT_SSL_CAINFO at the host CA bundle.\n\n"
            "Source commit: " + identity["commit"] + "\nBuild run: https://github.com/" + PROVENANCE.REPOSITORY
            + "/actions/runs/" + identity["run_id"] + "/attempts/" + identity["run_attempt"]
            + "\n\nArchives, the source archive, descriptor and evidence have GitHub-hosted provenance attestations."
            " Confirm that immutable releases are enabled before publishing this complete draft.\n")
        subprocess.run(["gh", "release", "create", release_tag, "--repo", PROVENANCE.REPOSITORY, "--verify-tag", "--draft",
                        "--target", identity["commit"], "--title", release_tag, "--notes-file", str(notes),
                        *[str(path) for path in files]], check=True)
    pages = json.loads(PROVENANCE.command(["gh", "api", "--paginate", "--slurp",
                                          "repos/" + PROVENANCE.REPOSITORY + "/releases?per_page=100"]))
    matches = [release for page in pages for release in page if release["tag_name"] == release_tag]
    require(len(matches) == 1, "Cannot locate the unique uploaded draft")
    validate_uploaded_assets(matches[0], files, identity)
    return matches[0]["html_url"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(draft(args.directory.resolve(), PROVENANCE.checked_identity(), read_lock()))


if __name__ == "__main__":
    main()
