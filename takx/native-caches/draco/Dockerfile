# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG DRACO_URL="https://github.com/google/draco/archive/1.5.7.tar.gz"
ARG DRACO_SHA256="bf6b105b79223eab2b86795363dfe5e5356050006a96521477973aba8f036fe1"
ARG DRACO_SHA512="8b444744cdf12fb9d276916eb2ff0735cd1a6497b65b88813ec457fe2169db987db62e3db253a7d0f3ae7d45ae6502e8a9f8c0b81abde73e07b3bec69f9dc170"
ARG LICENSE_SHA256="d3709b0fb4b8a94bbb1d02b8a2e484f258b0d9c5c5a01f940391f3fe662cd1a4"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY draco_control.cpp /build/draco_control.cpp
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/draco.tar.gz")
urllib.request.urlretrieve(os.environ["DRACO_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["DRACO_SHA256"]:
    raise SystemExit(f"draco SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["DRACO_SHA512"]:
    raise SystemExit(f"draco SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/draco-1.5.7/LICENSE").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"draco LICENSE SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/draco-1.5.7 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/draco \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DDRACO_TESTS=OFF \
      -DDRACO_JS_GLUE=OFF \
      -DDRACO_WASM=OFF \
      -DDRACO_UNITY_PLUGIN=OFF \
      -DDRACO_MAYA_PLUGIN=OFF \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/draco-1.5.7/LICENSE /draco/LICENSE; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -isystem /draco/include /build/draco_control.cpp /draco/lib/libdraco.a \
      -o /build/draco_control; \
    /build/draco_control; \
    g++ -shared -o /build/libdraco_whole.so \
      -Wl,--whole-archive /draco/lib/libdraco.a -Wl,--no-whole-archive \
      -Wl,--no-undefined; \
    if readelf -d /build/libdraco_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libdraco_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /draco/lib/libdraco.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /draco/include/draco/compression/decode.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "draco",\n  "version": "1.5.7",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libdraco.a": "%s",\n    "include/draco/compression/decode.h": "%s",\n    "LICENSE": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${DRACO_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /draco/cache-receipt.json

FROM scratch
COPY --from=builder /draco /draco
