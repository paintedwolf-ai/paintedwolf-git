#!/usr/bin/env bash
# Compiles extracted, verified sources inside the build image. build.py sets every input.
set -euo pipefail

: "${SRC:?}" "${DEPS:?}" "${STAGE:?}" "${JOBS:?}" "${OPENSSL_TARGET:?}" "${OPENSSL_DIR:?}"
: "${LFS_COMMIT:?}" "${GOROOT:?}" "${GOWORK_DIR:?}"

common="-O2 -fPIC -ffunction-sections -fdata-sections -fstack-protector-strong"

cd "${SRC}/zlib"
CFLAGS="${common}" ./configure --static --prefix="${DEPS}"
make -j"${JOBS}" libz.a
make install

cd "${SRC}/openssl"
# OPENSSL_DIR names a directory that does not exist, so no host's OpenSSL
# configuration or certificate directory is read implicitly.
./Configure "${OPENSSL_TARGET}" no-shared no-module no-dso no-engine no-tests no-docs no-comp \
  --prefix="${DEPS}" --libdir=lib --openssldir="${OPENSSL_DIR}" ${common}
make -j"${JOBS}" build_libs
make install_dev

cd "${SRC}/curl"
# No CA bundle, CA path, or OpenSSL fallback is compiled in: callers name the
# host bundle through GIT_SSL_CAINFO or http.sslCAInfo.
CFLAGS="${common}" PKG_CONFIG_PATH="${DEPS}/lib/pkgconfig" ./configure \
  --prefix="${DEPS}" --disable-shared --enable-static --with-pic \
  --with-openssl="${DEPS}" --with-zlib="${DEPS}" \
  --without-ca-bundle --without-ca-path --without-ca-fallback \
  --without-libpsl --without-libidn2 --without-nghttp2 --without-nghttp3 --without-ngtcp2 \
  --without-brotli --without-zstd --without-librtmp --without-libssh2 --without-libssh --without-gssapi \
  --disable-ldap --disable-ldaps --disable-rtsp --disable-dict --disable-telnet --disable-tftp \
  --disable-pop3 --disable-imap --disable-smb --disable-smtp --disable-gopher --disable-mqtt \
  --disable-ftp --disable-file --disable-manual --disable-docs
make -j"${JOBS}"
make install

cd "${SRC}/git"
# CURLDIR and ZLIB_PATH would add a runpath to the build prefix; pass flags directly.
git_make=(
  prefix=/ DESTDIR="${STAGE}"
  CFLAGS="${common} -I${DEPS}/include"
  LDFLAGS="-L${DEPS}/lib -Wl,--gc-sections -Wl,-z,relro,-z,now"
  CURL_CONFIG="${DEPS}/bin/curl-config"
  CURL_CFLAGS="-I${DEPS}/include"
  CURL_LDFLAGS="$("${DEPS}/bin/curl-config" --static-libs)"
  NO_OPENSSL=YesPlease NO_EXPAT=YesPlease NO_GETTEXT=YesPlease NO_TCLTK=YesPlease
  NO_PERL=YesPlease NO_PYTHON=YesPlease NO_INSTALL_HARDLINKS=YesPlease RUNTIME_PREFIX=YesPlease
)
make -j"${JOBS}" "${git_make[@]}" all
make "${git_make[@]}" strip install

cd "${SRC}/git-lfs"
export PATH="${GOROOT}/bin:${PATH}" GOTOOLCHAIN=local CGO_ENABLED=0
export GOPATH="${GOWORK_DIR}/gopath" GOCACHE="${GOWORK_DIR}/gocache" GOMODCACHE="${GOWORK_DIR}/gomodcache"
export GOFLAGS="-mod=mod -modcacherw" GOPROXY="${GO_PROXY:?}" GOSUMDB="${GO_SUMDB:?}"
go mod vendor
# go.sum in the pinned tarball fixes module contents; this pins the module set.
vendored="$(sha256sum vendor/modules.txt | cut -d' ' -f1)"
if [[ "${vendored}" != "${LFS_VENDOR_SHA256:?}" ]]; then
  echo "error: vendor/modules.txt is ${vendored}; the source lock pins ${LFS_VENDOR_SHA256}" >&2
  exit 1
fi
export GOFLAGS="-mod=vendor -modcacherw" GOPROXY=off
go generate ./commands
mkdir -p po/build
go generate ./tr
go build -trimpath -buildvcs=false \
  -ldflags "-s -w -X github.com/git-lfs/git-lfs/v3/config.GitCommit=${LFS_COMMIT:0:8}" \
  -o "${STAGE}/libexec/git-core/git-lfs" .
