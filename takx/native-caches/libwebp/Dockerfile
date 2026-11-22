# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG LIBWEBP_URL="https://github.com/webmproject/libwebp/archive/v1.4.0.tar.gz"
ARG LIBWEBP_SHA256="12af50c45530f0a292d39a88d952637e43fb2d4ab1883c44ae729840f7273381"
ARG LIBWEBP_SHA512="666b53fb33fa7895bf9dc83ca24e09865cf7fba7ae88b77f15a2ce11d94e84d5a6b8a084f0d8cad6c2a6eb8868ea7268f7efd2f6886148bbb58b7a3522957cde"
ARG LICENSE_SHA256="5aec868f669e384a22372a4e8a1a6cd7d44c64cd451f960ca69cc170d1e13acf"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY libwebp_control.c /build/libwebp_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/libwebp.tar.gz")
urllib.request.urlretrieve(os.environ["LIBWEBP_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["LIBWEBP_SHA256"]:
    raise SystemExit(f"libwebp SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["LIBWEBP_SHA512"]:
    raise SystemExit(f"libwebp SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/libwebp-1.4.0/COPYING").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"libwebp COPYING SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/libwebp-1.4.0 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/libwebp \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DWEBP_BUILD_LIBWEBPMUX=ON \
      -DWEBP_NEAR_LOSSLESS=ON \
      -DWEBP_ENABLE_SIMD=ON \
      -DCMAKE_DISABLE_FIND_PACKAGE_SDL=ON \
      -DWEBP_BUILD_ANIM_UTILS=OFF \
      -DWEBP_BUILD_CWEBP=OFF \
      -DWEBP_BUILD_DWEBP=OFF \
      -DWEBP_BUILD_EXTRAS=OFF \
      -DWEBP_BUILD_GIF2WEBP=OFF \
      -DWEBP_BUILD_IMG2WEBP=OFF \
      -DWEBP_BUILD_WEBPINFO=OFF \
      -DWEBP_BUILD_WEBPMUX=OFF \
      -DWEBP_ENABLE_SWAP_16BIT_CSP=OFF \
      -DWEBP_UNICODE=OFF \
      -DWEBP_BUILD_VWEBP=OFF \
      -DCMAKE_C_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/libwebp-1.4.0/COPYING /libwebp/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/libwebp/include /build/libwebp_control.c \
      /libwebp/lib/libwebp.a /libwebp/lib/libsharpyuv.a -lm -lpthread \
      -o /build/libwebp_control; \
    /build/libwebp_control; \
    gcc -shared -o /build/libwebp_whole.so \
      -Wl,--whole-archive /libwebp/lib/libwebp.a /libwebp/lib/libsharpyuv.a -Wl,--no-whole-archive \
      -Wl,--no-undefined -lm -lpthread; \
    if readelf -d /build/libwebp_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libwebp_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /libwebp/lib/libwebp.a | awk '{print $1}')"; \
    syuv_sha="$(sha256sum /libwebp/lib/libsharpyuv.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /libwebp/include/webp/encode.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "libwebp",\n  "version": "1.4.0",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libwebp.a": "%s",\n    "lib/libsharpyuv.a": "%s",\n    "include/webp/encode.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${LIBWEBP_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${syuv_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /libwebp/cache-receipt.json

FROM scratch
COPY --from=builder /libwebp /libwebp
