#!/usr/bin/env python3
"""Bind release files to the reviewed hosted workflow execution."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "toolchain"))
from lock import (REPOSITORY, archive_name, digest, read_json, read_lock, require,  # noqa: E402
                  source_archive_name, tag, version)

REPOSITORY_ID = "1419092891"
OWNER_ID = "289211278"
WORKFLOW = ".github/workflows/native-release.yml"
COMPILE_JOB = "compile"
RUNNERS = {"linux-amd64": ("Linux", "X64"), "linux-arm64": ("Linux", "ARM64")}


def evidence_name(platform):
    return "build-evidence-" + platform + ".json"


def command(argv, root=ROOT):
    return subprocess.check_output(argv, cwd=root, text=True).strip()


def workflow_identity(environment, lock):
    release_tag = tag(lock)
    sha = environment.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "Invalid workflow source commit")
    expected = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": REPOSITORY,
                "GITHUB_REPOSITORY_ID": REPOSITORY_ID, "GITHUB_REPOSITORY_OWNER_ID": OWNER_ID,
                "GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": release_tag, "GITHUB_REF": "refs/tags/" + release_tag,
                "GITHUB_WORKFLOW_REF": REPOSITORY + "/" + WORKFLOW + "@refs/tags/" + release_tag,
                "GITHUB_WORKFLOW_SHA": sha, "GITHUB_EVENT_NAME": "workflow_dispatch",
                "RUNNER_ENVIRONMENT": "github-hosted"}
    for key, value in expected.items():
        require(environment.get(key) == value, "Unexpected release context: " + key)
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(re.fullmatch(r"[1-9][0-9]*", environment.get(key, "")) is not None, "Invalid release run identity: " + key)
    return {"repository": REPOSITORY, "repository_id": REPOSITORY_ID, "owner_id": OWNER_ID, "commit": sha,
            "ref": expected["GITHUB_REF"], "version": version(lock), "workflow_ref": expected["GITHUB_WORKFLOW_REF"],
            "workflow_sha": sha, "run_id": environment["GITHUB_RUN_ID"], "run_attempt": environment["GITHUB_RUN_ATTEMPT"]}


def checked_identity(root=ROOT, environment=None):
    environment = os.environ if environment is None else environment
    lock_path = root / "toolchain/source-lock.json"
    identity = workflow_identity(environment, read_lock(lock_path))
    require(command(["git", "rev-parse", "HEAD"], root) == identity["commit"], "Checkout differs from workflow source")
    require(command(["git", "rev-parse", identity["ref"] + "^{commit}"], root) == identity["commit"],
            "Release tag differs from workflow source")
    subprocess.run(["git", "merge-base", "--is-ancestor", identity["commit"], "refs/remotes/origin/main"],
                   cwd=root, check=True)
    require(not command(["git", "status", "--porcelain", "--untracked-files=no"], root),
            "Release checkout has modified tracked inputs")
    return {**identity, "source_lock_sha256": digest(lock_path)}


def require_unpublished(identity):
    pages = json.loads(command(["gh", "api", "--paginate", "--slurp", "repos/" + REPOSITORY + "/releases?per_page=100"]))
    release_tag = identity["ref"].removeprefix("refs/tags/")
    require(not any(release["tag_name"] == release_tag for page in pages for release in page),
            "This version already has a release or draft; existing assets will not be replaced")


def build_files(directory, lock, platform):
    names = (archive_name(lock, platform), source_archive_name(lock), f"platform-checks-{platform}.json")
    return {name: {"sha256": digest(directory / name), "bytes": (directory / name).stat().st_size} for name in names}


def build_evidence(directory, identity, environment, tools, platform, lock):
    require(environment.get("GITHUB_JOB") == COMPILE_JOB, "Build evidence must originate in the compile job")
    for key in ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH"):
        require(bool(environment.get(key)), "Missing hosted image identity: " + key)
    require((environment["RUNNER_OS"], environment["RUNNER_ARCH"]) == RUNNERS[platform],
            "Compile runner differs from the " + platform + " hosted runner")
    require(tools and all(isinstance(value, str) and value.strip() for value in tools.values()),
            "Build tool versions are incomplete")
    return {"schema_version": 1, "build": identity, "job": COMPILE_JOB, "platform": platform, "reused_artifact": False,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "runner": {key: environment[key] for key in ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH")},
            "tools": tools, "files": build_files(directory, lock, platform)}


def check_evidence(evidence, identity, platform, lock, directory=None):
    require(type(evidence.get("schema_version")) is int and evidence["schema_version"] == 1
            and evidence.get("build") == identity, "Build evidence differs from this source and workflow execution")
    require(evidence.get("job") == COMPILE_JOB and evidence.get("platform") == platform
            and evidence.get("reused_artifact") is False, "Release requires a fresh native compile")
    runner = evidence.get("runner", {})
    require((runner.get("RUNNER_OS"), runner.get("RUNNER_ARCH")) == RUNNERS[platform],
            "Build runner differs from the " + platform + " hosted runner")
    tools = evidence.get("tools")
    require(all(isinstance(runner.get(key), str) and runner[key] for key in ("ImageOS", "ImageVersion"))
            and isinstance(tools, dict) and tools
            and all(isinstance(value, str) and value.strip() for value in tools.values()),
            "Build environment evidence is incomplete")
    files = evidence.get("files", {})
    expected = (archive_name(lock, platform), source_archive_name(lock), f"platform-checks-{platform}.json")
    require(sorted(files) == sorted(expected) and all(
        re.fullmatch(r"[0-9a-f]{64}", record.get("sha256", "")) and type(record.get("bytes")) is int and record["bytes"] > 0
        for record in files.values()), "Build file identities are incomplete")
    if directory is not None:
        require(files == build_files(directory, lock, platform), "Build files differ from build evidence")


def check_attestation(results, identity, subject):
    uri = "https://github.com/" + identity["workflow_ref"]
    expected = {"subjectAlternativeName": uri, "buildSignerURI": uri,
                "issuer": "https://token.actions.githubusercontent.com",
                "buildSignerDigest": identity["commit"], "sourceRepositoryDigest": identity["commit"],
                "sourceRepositoryRef": identity["ref"], "sourceRepositoryURI": "https://github.com/" + REPOSITORY,
                "sourceRepositoryIdentifier": REPOSITORY_ID, "sourceRepositoryOwnerIdentifier": OWNER_ID,
                "runnerEnvironment": "github-hosted", "buildTrigger": "workflow_dispatch",
                "runInvocationURI": "https://github.com/" + REPOSITORY + "/actions/runs/"
                + identity["run_id"] + "/attempts/" + identity["run_attempt"]}
    for result in results:
        verified = result.get("verificationResult", {})
        certificate = verified.get("signature", {}).get("certificate", {})
        statement = verified.get("statement", {})
        if (all(certificate.get(key) == value for key, value in expected.items())
                and verified.get("verifiedTimestamps")
                and statement.get("predicateType") == "https://slsa.dev/provenance/v1"
                and any(item.get("digest", {}).get("sha256") == digest(subject) for item in statement.get("subject", []))):
            return
    raise ValueError("No verified attestation matches the exact hosted release identity")


def verify_attestation(subject, bundle, identity):
    output = command(["gh", "attestation", "verify", str(subject), "--bundle", str(bundle),
                      "--repo", REPOSITORY, "--signer-workflow", REPOSITORY + "/" + WORKFLOW,
                      "--source-ref", identity["ref"], "--source-digest", identity["commit"],
                      "--signer-digest", identity["commit"], "--deny-self-hosted-runners", "--format", "json"])
    check_attestation(json.loads(output), identity, subject)


def build_tools(directory, platform, image):
    tools = read_json(directory / f"build-tools-{platform}.json")
    tools["compile_image"] = command(["docker", "image", "inspect", "--format", "{{.Id}}", image])
    tools["driver_python"] = command([sys.executable, "--version"])
    return tools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("identity")
    commands.add_parser("preflight")
    build = commands.add_parser("build-evidence")
    build.add_argument("--platform", required=True, choices=sorted(RUNNERS))
    build.add_argument("--build-directory", required=True, type=Path)
    build.add_argument("--image", required=True)
    build.add_argument("--output", required=True, type=Path)
    check = commands.add_parser("check-evidence")
    check.add_argument("--platform", required=True, choices=sorted(RUNNERS))
    check.add_argument("--evidence", required=True, type=Path)
    check.add_argument("--build-directory", type=Path)
    args = parser.parse_args()
    lock = read_lock()
    identity = checked_identity()
    if args.operation in ("identity", "preflight"):
        if args.operation == "preflight":
            require_unpublished(identity)
        result = identity
    elif args.operation == "build-evidence":
        directory = args.build_directory.resolve(strict=True)
        result = build_evidence(directory, identity, os.environ, build_tools(directory, args.platform, args.image),
                                args.platform, lock)
        with args.output.open("x") as output:
            output.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
        return
    else:
        check_evidence(read_json(args.evidence), identity, args.platform, lock,
                       args.build_directory.resolve(strict=True) if args.build_directory else None)
        result = {"verified": True}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
