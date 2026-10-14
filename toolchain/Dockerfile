# The glibc of this image is the oldest the toolchain supports; build.py passes the
# digest-pinned base from source-lock.json.
ARG BASE
FROM ${BASE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      binutils ca-certificates file gcc libc6-dev make perl pkg-config python3 xz-utils \
 && rm -rf /var/lib/apt/lists/*
