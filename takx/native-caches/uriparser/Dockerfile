# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG URIPARSER_URL="https://github.com/uriparser/uriparser/archive/uriparser-0.9.8.tar.gz"
ARG URIPARSER_SHA256="d6289387eaf2495e38ed80d709ad673fe04d63fad707badfee96f3d2dabc7c35"
ARG URIPARSER_SHA512="4222e6d7dcdb525514ca8f42e5a9b314e0f09102ef2e72131702b6297d7cb9f509f757b9bec64c4ef88146667a60b992ff71e3c1fea50a82e45c3a14b8992326"
ARG LICENSE_SHA256="287f09e6546a9610f949f89e8fb937cacfeabd7bfaa8c8a0c18312193bf04ad3"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY uriparser_control.c /build/uriparser_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/uriparser.tar.gz")
urllib.request.urlretrieve(os.environ["URIPARSER_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["URIPARSER_SHA256"]:
    raise SystemExit(f"uriparser SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["URIPARSER_SHA512"]:
    raise SystemExit(f"uriparser SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/uriparser-uriparser-0.9.8/COPYING").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"uriparser COPYING SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/uriparser-uriparser-0.9.8 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/uriparser \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DURIPARSER_BUILD_DOCS=OFF \
      -DURIPARSER_BUILD_TESTS=OFF \
      -DURIPARSER_BUILD_TOOLS=OFF \
      -DCMAKE_C_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/uriparser-uriparser-0.9.8/COPYING /uriparser/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/uriparser/include /build/uriparser_control.c /uriparser/lib/liburiparser.a \
      -o /build/uriparser_control; \
    /build/uriparser_control; \
    gcc -shared -o /build/liburiparser_whole.so \
      -Wl,--whole-archive /uriparser/lib/liburiparser.a -Wl,--no-whole-archive \
      -Wl,--no-undefined; \
    if readelf -d /build/liburiparser_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in liburiparser_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /uriparser/lib/liburiparser.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /uriparser/include/uriparser/Uri.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "uriparser",\n  "version": "0.9.8",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/liburiparser.a": "%s",\n    "include/uriparser/Uri.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${URIPARSER_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /uriparser/cache-receipt.json

FROM scratch
COPY --from=builder /uriparser /uriparser
