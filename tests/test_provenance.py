from pathlib import Path
import tempfile
import unittest
from unittest import mock

import support
import lock as LOCK
import release_provenance as PROVENANCE


class ProvenanceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock = support.lock()
        for platform in PROVENANCE.RUNNERS:
            for name in (LOCK.archive_name(self.lock, platform), LOCK.source_archive_name(self.lock),
                         f"platform-checks-{platform}.json"):
                (self.root / name).write_bytes(name.encode())
        self.sha = "a" * 40
        release_tag = LOCK.tag(self.lock)
        self.environment = {
            "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": LOCK.REPOSITORY,
            "GITHUB_REPOSITORY_ID": PROVENANCE.REPOSITORY_ID, "GITHUB_REPOSITORY_OWNER_ID": PROVENANCE.OWNER_ID,
            "GITHUB_SHA": self.sha, "GITHUB_WORKFLOW_SHA": self.sha, "GITHUB_REF_TYPE": "tag",
            "GITHUB_REF_NAME": release_tag, "GITHUB_REF": "refs/tags/" + release_tag,
            "GITHUB_WORKFLOW_REF": LOCK.REPOSITORY + "/" + PROVENANCE.WORKFLOW + "@refs/tags/" + release_tag,
            "GITHUB_EVENT_NAME": "workflow_dispatch", "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "compile",
            "ImageOS": "ubuntu24", "ImageVersion": "20261005.1", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64"}
        self.identity = {**PROVENANCE.workflow_identity(self.environment, self.lock), "source_lock_sha256": "c" * 64}
        self.tools = {"gcc": "gcc 11.4.0", "glibc": "ldd 2.35"}

    def test_workflow_identity_binds_reviewed_source_and_host(self):
        for key, value in (("GITHUB_WORKFLOW_SHA", "b" * 40), ("GITHUB_REPOSITORY_ID", "999"),
                           ("GITHUB_REF_NAME", "v2.53.0"), ("GITHUB_REF_TYPE", "branch"),
                           ("RUNNER_ENVIRONMENT", "self-hosted"), ("GITHUB_EVENT_NAME", "pull_request"),
                           ("GITHUB_RUN_ATTEMPT", "0")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                PROVENANCE.workflow_identity(dict(self.environment, **{key: value}), self.lock)

    def test_checkout_must_match_tag_and_be_clean(self):
        (self.root / "toolchain").mkdir()
        (self.root / "toolchain/source-lock.json").write_text((support.ROOT / "toolchain/source-lock.json").read_text())
        for outputs in (("b" * 40,), (self.sha, "b" * 40), (self.sha, self.sha, " M toolchain/compile.sh")):
            with self.subTest(outputs=outputs), mock.patch.object(PROVENANCE, "command", side_effect=outputs), \
                    mock.patch.object(PROVENANCE.subprocess, "run"), self.assertRaises(ValueError):
                PROVENANCE.checked_identity(self.root, self.environment)

    def test_evidence_round_trip_and_tampering(self):
        evidence = PROVENANCE.build_evidence(self.root, self.identity, self.environment, self.tools, "linux-amd64", self.lock)
        PROVENANCE.check_evidence(evidence, self.identity, "linux-amd64", self.lock, self.root)
        with self.assertRaises(ValueError):
            PROVENANCE.check_evidence(evidence, self.identity, "linux-arm64", self.lock)
        (self.root / LOCK.archive_name(self.lock, "linux-amd64")).write_bytes(b"replaced")
        with self.assertRaises(ValueError):
            PROVENANCE.check_evidence(evidence, self.identity, "linux-amd64", self.lock, self.root)
        with self.assertRaises(ValueError):
            PROVENANCE.build_evidence(self.root, self.identity, dict(self.environment, RUNNER_ARCH="ARM64"),
                                      self.tools, "linux-amd64", self.lock)

    def test_arm64_evidence_requires_arm_runner(self):
        environment = dict(self.environment, RUNNER_ARCH="ARM64")
        evidence = PROVENANCE.build_evidence(self.root, self.identity, environment, self.tools, "linux-arm64", self.lock)
        PROVENANCE.check_evidence(evidence, self.identity, "linux-arm64", self.lock, self.root)

    def test_attestation_must_match_hosted_identity(self):
        subject = self.root / LOCK.source_archive_name(self.lock)
        uri = "https://github.com/" + self.identity["workflow_ref"]
        certificate = {"subjectAlternativeName": uri, "buildSignerURI": uri,
                       "issuer": "https://token.actions.githubusercontent.com",
                       "buildSignerDigest": self.sha, "sourceRepositoryDigest": self.sha,
                       "sourceRepositoryRef": self.identity["ref"],
                       "sourceRepositoryURI": "https://github.com/" + LOCK.REPOSITORY,
                       "sourceRepositoryIdentifier": PROVENANCE.REPOSITORY_ID,
                       "sourceRepositoryOwnerIdentifier": PROVENANCE.OWNER_ID, "runnerEnvironment": "github-hosted",
                       "buildTrigger": "workflow_dispatch",
                       "runInvocationURI": "https://github.com/" + LOCK.REPOSITORY + "/actions/runs/123/attempts/1"}
        result = {"verificationResult": {"signature": {"certificate": certificate}, "verifiedTimestamps": [{}],
                  "statement": {"predicateType": "https://slsa.dev/provenance/v1",
                                "subject": [{"digest": {"sha256": LOCK.digest(subject)}}]}}}
        PROVENANCE.check_attestation([result], self.identity, subject)
        certificate["runnerEnvironment"] = "self-hosted"
        with self.assertRaises(ValueError):
            PROVENANCE.check_attestation([result], self.identity, subject)


if __name__ == "__main__":
    unittest.main()
