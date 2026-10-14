# Painted Wolf Git

Portable Linux Git toolchains for Painted Wolf Code: Git 2.53.0 and Git LFS 3.7.1
for linux-amd64 and linux-arm64, built from verified upstream source. One archive per
architecture runs on every supported family (Debian/Ubuntu, Fedora/RHEL-compatible,
Arch) with glibc 2.35 or later.

Git's HTTP transport links curl, OpenSSL 3 and zlib statically, so the only runtime
dependencies are glibc and its loader. Git LFS is a static Go executable.

## Archive layout

Each `paintedwolf-git-<version>-<platform>.tar.gz` follows the dugite-native layout
that Painted Wolf Code stages:

| Path | Contents |
|---|---|
| `bin/git` | Git |
| `libexec/git-core/` | `git`, built-in links to `git`, `git-remote-http` with `git-remote-https` linked to it, script helpers, and `git-lfs` |
| `share/git-core/templates/` | repository templates |
| `licenses/` | `NOTICES.md`, `SOURCE.md`, and every component's license texts |
| `toolchain.json` | version, component versions and source digests, source archive identity, and the trust contract |

Git is built with `RUNTIME_PREFIX`, so the tree works from any location. Unlike
dugite-native, the archive has no `ssl/cacert.pem`, no `etc/gitconfig`, no Git
Credential Manager, and no Perl, Python or Tcl tools (`git-svn`, `git-p4` and the
CVS tools are upstream's "not built" stubs).

## Certificate trust contract

The toolchain carries no CA bundle and compiles in no trust location: curl is built
without a CA bundle, CA path or OpenSSL fallback, and OpenSSL's directory is
`/nonexistent/paintedwolf-git/openssl`. Distributions disagree on OpenSSL's default
paths, so relying on them would trust different stores, or none, per family.

The consumer selects the host bundle and passes it explicitly, through
`GIT_SSL_CAINFO` or `http.sslCAInfo`. Use the first readable file, in the order Go's
`crypto/x509` uses on Linux:

1. `/etc/ssl/certs/ca-certificates.crt` (Debian, Ubuntu, Arch, current Fedora)
2. `/etc/pki/tls/certs/ca-bundle.crt` (Fedora, RHEL-compatible)
3. `/etc/ssl/ca-bundle.pem` (openSUSE)
4. `/etc/pki/tls/cacert.pem` (OpenELEC)
5. `/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem` (CentOS, RHEL 7)
6. `/etc/ssl/cert.pem` (Alpine)

Without one, HTTPS fails certificate verification; qualification checks that it does.
Git LFS uses Go's own selection of the same list and also honors `GIT_SSL_CAINFO`.

## Build and test

Tooling needs Python 3.9 or newer, and Go for the pinned Task bootstrap.

```sh
./task check   # validate the source lock and release inputs offline
./task test    # tooling regression tests
```

Builds run natively on Linux amd64 or arm64 with Docker:

```sh
./task build -- --platform linux-amd64 --output /absolute/new/directory --jobs 4
```

The build downloads each input once into `.cache/sources` and verifies it against the
lock, then compiles zlib, OpenSSL, curl, Git and Git LFS inside the digest-pinned
Ubuntu 22.04 image (glibc 2.35) with the checkout mounted read-only. Git LFS modules
come from the Go module proxy, checked by upstream's `go.sum`, and the vendored module
set is pinned in the lock. `<output>/out` receives the archive, the source archive,
`platform-checks-<platform>.json` (the ELF facts of every image) and the build tool
versions.

`platform-checks` requires every ELF image to target the platform, need nothing but
glibc libraries, carry no runpath, use the standard loader, and require glibc no newer
than `glibc_max`; `git-remote-http` must embed the pinned OpenSSL and `git-lfs` must
be static.

## Qualification

```sh
./task qualify -- verify --platform linux-amd64 \
  --archive /path/out/paintedwolf-git-<version>-linux-amd64.tar.gz \
  --platform-checks /path/out/platform-checks-linux-amd64.json \
  --output /path/out/qualification-linux-amd64.json
```

Qualification extracts the archive on a fresh host, re-derives the ELF report and
requires it to equal the build's, then runs `scripts/smoke.sh` inside each family
container in the lock (Ubuntu 22.04 and 24.04, Debian 13, Fedora 44, AlmaLinux 10,
and Arch on amd64). Each run checks the versions and that every library resolves,
requires HTTPS to fail without a CA bundle, runs `git ls-remote` and a shallow clone
over HTTPS with `GIT_SSL_CAINFO` set to the family's bundle, and pushes and clones a
Git LFS object through `scripts/lfs_server.py`, a local batch server. The record names
each image's digest.

The `Native release` workflow's `qualify` operation builds and qualifies both
architectures on GitHub-hosted runners (`ubuntu-24.04`, `ubuntu-24.04-arm`) from any
branch without releasing; pull requests that touch build inputs run it too:

```sh
gh workflow run native-release.yml --ref <branch> -f operation=qualify
```

## Publish a release

1. Change `toolchain/source-lock.json` (new inputs and a higher `revision`) through a
   reviewed pull request, qualified on both architectures, and merge it to `main`.
2. Tag the merged commit with the exact version, `v<git_version>+paintedwolf.<revision>`
   (`./task check` prints it), and push the tag.
3. Run the build operation at that tag:

   ```sh
   gh workflow run native-release.yml --ref "$RELEASE_TAG" -f operation=build
   ```

   It verifies the tag, source commit, workflow commit and repository identity and
   refuses an existing release or draft. Each architecture compiles on a fresh runner
   and records build evidence; a second fresh runner per architecture qualifies the
   archive. In the protected `release` environment the workflow rechecks the evidence,
   merges both platforms (their source archives must be byte-identical), attests every
   archive, descriptor and evidence file, and creates a complete draft.
4. Review the draft and publish it. Immutable releases must already be enabled:

   ```sh
   gh api repos/paintedwolf-ai/paintedwolf-git/immutable-releases --jq '.enabled'
   ```

A release holds both architecture archives, `paintedwolf-git-<version>-source.tar.gz`,
`release.json`, and per architecture `build-evidence`, `platform-checks` and
`qualification` records, plus `provenance.sigstore.json`. `release.json` gives each
archive's URL, SHA-256, size and the `git --version` it reports.

A failed upload can leave a draft. Delete an unpublished failed draft before rerunning
all jobs; published releases and their tags never change. The attestation binds the
reviewed source and workflow commit; it is provenance, not a claim of byte-for-byte
reproducibility.

### GitHub release controls

In Settings → Environments, create `release` with **Required reviewers** set to the
release operator, administrator bypass disabled, and a tag rule for `v*`. In Settings
→ Rules, protect `main` (pull requests, the `check` status, no deletion or force push)
and add tag rulesets that restrict updates and deletion of `v*` with no bypass, and
creation to the release operator.

## Consuming a release

Painted Wolf Code pins each archive in `lycaon/config/gitengine/pin.yaml`:

```yaml
linux-amd64:
  url: "https://github.com/paintedwolf-ai/paintedwolf-git/releases/download/v{git_version}+{build}/paintedwolf-git-{git_version}+{build}-linux-amd64.tar.gz"
  build: "paintedwolf.1"
  reported_version: "2.53.0"
  sha256: "<sha256 from release.json>"
```

## Licensing

Git is GPL-2.0-only; curl uses the curl license, OpenSSL Apache-2.0, zlib the zlib
license, Git LFS MIT, and Go and the Git LFS modules their own permissive licenses.
Every archive carries the license texts and notices under `licenses/`, and each
release publishes the complete corresponding source: the verified upstream archives,
the vendored Git LFS modules, and this repository's build recipe.
