#!/usr/bin/env bash
# Exercises a read-only toolchain tree inside one distribution container and prints
# a RESULT line of JSON. qualify.py supplies every variable.
set -euo pipefail

: "${TOOLCHAIN:?}" "${HTTPS_REMOTE:?}" "${HTTPS_REF:?}" "${CLONE_REMOTE:?}" "${LFS_URL:?}"
git="${TOOLCHAIN}/bin/git"
work="$(mktemp -d)"
export HOME="${work}/home" GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0
mkdir -p "${HOME}"
unset GIT_SSL_CAINFO SSL_CERT_FILE SSL_CERT_DIR
identity=(-c user.name=Qualification -c user.email=qualification@paintedwolf.invalid)

fail() {
  echo "smoke: $*" >&2
  exit 1
}

# Go's crypto/x509 order on Linux: the first readable file wins.
bundle=""
for candidate in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/ca-bundle.pem \
  /etc/pki/tls/cacert.pem /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem /etc/ssl/cert.pem; do
  if [[ -r "${candidate}" ]]; then
    bundle="${candidate}"
    break
  fi
done
[[ -n "${bundle}" ]] || fail "no host CA bundle"

git_version="$("${git}" --version)"
lfs_version="$("${git}" lfs version)"

loader="unavailable"
if command -v ldd >/dev/null 2>&1; then
  for image in "${TOOLCHAIN}/bin/git" "${TOOLCHAIN}/libexec/git-core/git-remote-http"; do
    ! ldd "${image}" | grep -q 'not found' || fail "unresolved libraries in ${image}"
  done
  loader="resolved"
fi

if "${git}" ls-remote "${HTTPS_REMOTE}" HEAD >/dev/null 2>"${work}/implicit.err"; then
  fail "HTTPS succeeded without a CA bundle; trust must come from GIT_SSL_CAINFO"
fi
grep -qi 'certificate' "${work}/implicit.err" || fail "unexpected failure without a CA bundle: $(head -c 300 "${work}/implicit.err")"

remote_line="$(GIT_SSL_CAINFO="${bundle}" "${git}" ls-remote "${HTTPS_REMOTE}" "${HTTPS_REF}")"
[[ "${remote_line}" == *"${HTTPS_REF}" ]] || fail "ls-remote did not list ${HTTPS_REF}"
remote_oid="${remote_line%%[[:space:]]*}"

GIT_SSL_CAINFO="${bundle}" "${git}" clone -q --depth 1 "${CLONE_REMOTE}" "${work}/clone"
clone_head="$("${git}" -C "${work}/clone" rev-parse HEAD)"

"${git}" lfs install --skip-repo >/dev/null
"${git}" init -q --bare -b main "${work}/remote.git"
"${git}" init -q -b main "${work}/source"
cd "${work}/source"
"${git}" lfs install >/dev/null
"${git}" config lfs.url "${LFS_URL}"
"${git}" config lfs.locksverify false
"${git}" lfs track '*.bin' >/dev/null
head -c 1048576 /dev/urandom > payload.bin
oid="$(sha256sum payload.bin | cut -d' ' -f1)"
"${git}" add .gitattributes payload.bin
"${git}" "${identity[@]}" commit -q -m "LFS round trip"
"${git}" cat-file -p HEAD:payload.bin | grep -q "^oid sha256:${oid}$" || fail "payload was not stored as an LFS pointer"
"${git}" push -q "${work}/remote.git" main
cd "${work}"
"${git}" clone -q -c lfs.url="${LFS_URL}" "${work}/remote.git" "${work}/fetched"
[[ "$(sha256sum "${work}/fetched/payload.bin" | cut -d' ' -f1)" == "${oid}" ]] || fail "LFS smudge returned other bytes"

printf 'RESULT {"git_version":"%s","lfs_version":"%s","ca_bundle":"%s","loader":"%s","implicit_trust":false,"ls_remote_oid":"%s","clone_head":"%s","lfs_oid":"%s"}\n' \
  "${git_version}" "${lfs_version%% *}" "${bundle}" "${loader}" "${remote_oid}" "${clone_head}" "${oid}"
