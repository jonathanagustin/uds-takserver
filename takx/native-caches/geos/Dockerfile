# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG GEOS_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/geos.tar.bz2"
ARG GEOS_SHA256="cfbf68079117c1c2b76411636444ff41d73c31093c4cab9dcc9a8c1bbe7e3897"
ARG LICENSE_SHA256="dc626520dcd53a22f727af3ee42c770e56c97a64fe3adb063799d8ab032fe551"
ARG PATCH_URL="https://raw.githubusercontent.com/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/geos-pgsc.patch"
ARG PATCH_SHA256="8b29c9170e5cdad979658a72ba914dd9b7508ed6be4c8414add246defb080995"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY geos_control.c /build/geos_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/geos.tar.bz2")
urllib.request.urlretrieve(os.environ["GEOS_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["GEOS_SHA256"]:
    raise SystemExit(f"geos SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:bz2") as tf:
    tf.extractall("/build")

lic = hashlib.sha256(Path("/build/geos-3.4.3/COPYING").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"geos COPYING SHA-256 mismatch: {lic}")

pdest = Path("/build/geos-pgsc.patch")
urllib.request.urlretrieve(os.environ["PATCH_URL"], pdest)
gotp = hashlib.sha256(pdest.read_bytes()).hexdigest()
if gotp != os.environ["PATCH_SHA256"]:
    raise SystemExit(f"geos patch SHA-256 mismatch: {gotp}")
PY

RUN set -x; \
    patch -d /build/geos-3.4.3 --fuzz=0 --batch --no-backup-if-mismatch -p1 -i /build/geos-pgsc.patch; \
    mkdir -p /build/out; \
    cd /build/out; \
    CONFIG_SITE=/dev/null \
    CFLAGS="-O2 -fPIC -std=gnu89 -march=x86-64 -mtune=generic" \
    CXXFLAGS="-O2 -fPIC -std=c++11 -march=x86-64 -mtune=generic" \
    /build/geos-3.4.3/configure \
      --prefix=/geos \
      --enable-static \
      --disable-shared \
      --with-pic \
      --disable-python \
      --disable-ruby \
      --disable-php; \
    make -j4; \
    make install; \
    cp /build/geos-3.4.3/COPYING /geos/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/geos/include /build/geos_control.c /geos/lib/libgeos_c.a /geos/lib/libgeos.a -lstdc++ -lm \
      -o /build/geos_control; \
    /build/geos_control; \
    g++ -shared -o /build/libgeos_whole.so \
      -Wl,--whole-archive /geos/lib/libgeos_c.a /geos/lib/libgeos.a -Wl,--no-whole-archive \
      -Wl,--no-undefined -lm; \
    if readelf -d /build/libgeos_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libgeos_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /geos/lib/libgeos.a | awk '{print $1}')"; \
    capi_sha="$(sha256sum /geos/lib/libgeos_c.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /geos/include/geos_c.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "geos",\n  "version": "3.4.3",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libgeos.a": "%s",\n    "lib/libgeos_c.a": "%s",\n    "include/geos_c.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${GEOS_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${capi_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /geos/cache-receipt.json

FROM scratch
COPY --from=builder /geos /geos
