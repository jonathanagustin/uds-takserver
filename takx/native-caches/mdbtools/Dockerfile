# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG MDBTOOLS_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/mdbtools.tar.gz"
ARG MDBTOOLS_SHA256="3446e1d71abdeb98d41e252777e67e1909b186496fda59f98f67032f7fbcd955"
ARG LICENSE_SHA256="b7993225104d90ddd8024fd838faf300bea5e83d91203eab98e29512acebd69c"
ARG PATCH_URL="https://raw.githubusercontent.com/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/mdbtools-pgsc.patch"
ARG PATCH_SHA256="8c5e6faf6c95b026d834b7094d4f7a4bafc0f55222312a3bc7aac691309c3839"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY mdbtools_control.c /build/mdbtools_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/mdbtools.tar.gz")
urllib.request.urlretrieve(os.environ["MDBTOOLS_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["MDBTOOLS_SHA256"]:
    raise SystemExit(f"mdbtools SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")

lic = hashlib.sha256(Path("/build/mdbtools-1.0.0/COPYING.LIB").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"mdbtools COPYING.LIB SHA-256 mismatch: {lic}")

pdest = Path("/build/mdbtools-pgsc.patch")
urllib.request.urlretrieve(os.environ["PATCH_URL"], pdest)
gotp = hashlib.sha256(pdest.read_bytes()).hexdigest()
if gotp != os.environ["PATCH_SHA256"]:
    raise SystemExit(f"mdbtools patch SHA-256 mismatch: {gotp}")
PY

RUN set -x; \
    patch -d /build/mdbtools-1.0.0 --fuzz=0 --batch --no-backup-if-mismatch -p1 -i /build/mdbtools-pgsc.patch; \
    mkdir -p /build/out; \
    cd /build/out; \
    CONFIG_SITE=/dev/null \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
    /build/mdbtools-1.0.0/configure \
      --prefix=/mdbtools \
      --enable-static \
      --disable-shared \
      --with-pic \
      --disable-maintainer-mode \
      --disable-glib \
      --disable-man \
      --disable-sql \
      --enable-iconv \
      --disable-nls; \
    make -j4; \
    make install; \
    cp /build/mdbtools-1.0.0/COPYING /mdbtools/COPYING; \
    cp /build/mdbtools-1.0.0/COPYING.LIB /mdbtools/COPYING.LIB; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/mdbtools/include /build/mdbtools_control.c /mdbtools/lib/libmdb.a -lm \
      -o /build/mdbtools_control; \
    /build/mdbtools_control; \
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
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "mdbtools",\n  "version": "1.0.0",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libmdb.a": "%s",\n    "include/mdbtools.h": "%s",\n    "COPYING.LIB": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${MDBTOOLS_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /mdbtools/cache-receipt.json

FROM scratch
COPY --from=builder /mdbtools /mdbtools
