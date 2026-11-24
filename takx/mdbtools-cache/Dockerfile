# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Multi-stage builder for the prebuilt TAKX MDBTools 1.0.0 static PIC OCI cache:
# - Uses the exact digest-pinned GCC 14.2.0 compiler image shared with geos-cache/proj-cache.
# - Verifies exact SHA-256 hashes of mdbtools.tar.gz, mdbtools-pgsc.patch, COPYING, and COPYING.LIB.
# - Builds static PIC libmdb.a (--disable-glib --disable-sql --disable-man --disable-nls --enable-iconv).
# - Runs mdb_control.c and whole-archive --no-undefined / no-TEXTREL shared link check.
# - Exports only /mdbtools into a minimal scratch OCI artifact.

FROM docker.io/library/gcc@sha256:9af0ba1a5281cac2a79aa718b6d21af8670322cf4378198ee306efd5acc1955f AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG MDB_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/mdbtools.tar.gz"
ARG MDB_SHA256="3446e1d71abdeb98d41e252777e67e1909b186496fda59f98f67032f7fbcd955"
ARG PATCH_URL="https://raw.githubusercontent.com/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/mdbtools-pgsc.patch"
ARG PATCH_SHA256="8c5e6faf6c95b026d834b7094d4f7a4bafc0f55222312a3bc7aac691309c3839"
ARG COPYING_SHA256="8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643"
ARG COPYING_LIB_SHA256="b7993225104d90ddd8024fd838faf300bea5e83d91203eab98e29512acebd69c"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200 \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
    CXXFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"

COPY mdb_control.c /build/mdb_control.c
WORKDIR /build/work

RUN set -x; \
    curl -fsSL "${MDB_URL}" -o /build/mdbtools.tar.gz; \
    echo "${MDB_SHA256}  /build/mdbtools.tar.gz" | sha256sum -c -; \
    curl -fsSL "${PATCH_URL}" -o /build/mdbtools-pgsc.patch; \
    echo "${PATCH_SHA256}  /build/mdbtools-pgsc.patch" | sha256sum -c -; \
    tar -xzf /build/mdbtools.tar.gz -C /build; \
    echo "${COPYING_SHA256}  /build/mdbtools-1.0.0/COPYING" | sha256sum -c -; \
    echo "${COPYING_LIB_SHA256}  /build/mdbtools-1.0.0/COPYING.LIB" | sha256sum -c -; \
    patch -d /build/mdbtools-1.0.0 --fuzz=0 --batch --no-backup-if-mismatch -p1 -i /build/mdbtools-pgsc.patch; \
    mkdir -p /mdbtools; \
    /build/mdbtools-1.0.0/configure \
      --disable-shared \
      --enable-static \
      --with-pic \
      --disable-maintainer-mode \
      --disable-glib \
      --disable-man \
      --disable-sql \
      --enable-iconv \
      --disable-nls \
      --prefix=/mdbtools; \
    make -j2; \
    make install; \
    cp /build/mdbtools-1.0.0/COPYING /mdbtools/COPYING; \
    cp /build/mdbtools-1.0.0/COPYING.LIB /mdbtools/COPYING.LIB; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/mdbtools/include /build/mdb_control.c /mdbtools/lib/libmdb.a -lm \
      -o /build/mdb_control; \
    /build/mdb_control; \
    gcc -shared -o /build/libmdb_whole.so \
      -Wl,--whole-archive /mdbtools/lib/libmdb.a -Wl,--no-whole-archive \
      -Wl,--no-undefined -lm; \
    if readelf -d /build/libmdb_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libmdb_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /mdbtools/lib/libmdb.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /mdbtools/include/mdbtools.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-mdbtools-cache-v1",\n  "component": "mdbtools",\n  "version": "1.0.0",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "patch_sha256": "%s",\n  "copying_sha256": "%s",\n  "copying_lib_sha256": "%s",\n  "libmdb_a_sha256": "%s",\n  "mdbtools_h_sha256": "%s",\n  "controls": {\n    "mdb_static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${MDB_SHA256}" "${PATCH_SHA256}" "${COPYING_SHA256}" "${COPYING_LIB_SHA256}" "${lib_sha}" "${hdr_sha}" \
      > /mdbtools/mdbtools-receipt.json

FROM scratch
COPY --from=builder /mdbtools /mdbtools
