# Painted Wolf Git agent policy

Use `./task` from the repository root. Run `./task check` and `./task test` before
handing off any change. Builds run natively on Linux amd64 or arm64 and need Docker;
they are heavy, so run one at a time and coordinate on shared hosts.

`toolchain/source-lock.json` binds every build input: upstream archives by SHA-256,
the digest-pinned build image, the Go toolchain and the Git LFS module set. A changed
input is a new `revision` with a fresh build and qualification; never relabel an
existing archive. Tooling outside the lock may change without rebuilding.

The toolchain must stay portable: the only runtime dependencies are glibc (symbol
versions at or below `glibc_max`) and its loader, with curl, OpenSSL and zlib linked
statically. It carries no CA bundle and no compiled-in trust paths; consumers name
the host bundle through `GIT_SSL_CAINFO` (see the README's trust contract). Keep the
archive layout compatible with the dugite-native layout Painted Wolf Code stages.

Keep the repository independent of Painted Wolf Code. Releases carry linux-amd64
and linux-arm64 only; do not invent other platform assets.

Every commit carries `Signed-off-by: Chris Beckman <chris@paintedwolf.ai>`. Commit,
tag or publish only when authorized; releases come only from the `Native release`
workflow at a version tag. Never remove another agent's work, stash the shared tree,
or stop a process you did not start. Comments state non-obvious logic briefly; no
history or product comparisons.
