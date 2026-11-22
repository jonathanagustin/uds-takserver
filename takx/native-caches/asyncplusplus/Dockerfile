# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG ASYNCPP_URL="https://github.com/Amanieu/asyncplusplus/archive/172ca3f8e0df1b3f7f5ee8b8244e4ac67258b0d8.tar.gz"
ARG ASYNCPP_SHA256="abca8bcdd423a0bccd677fe5e21d368a0e2310d466e208e147a0f033eab95c1e"
ARG ASYNCPP_SHA512="fd95b3349ceed4cab5cb0e146d2ccfe77c85318303015cf513037fc69c1ade7cfdb3dc81de8a90846c4387c5e31f5a70a64da770547c201dfe24d2c181be1933"
ARG LICENSE_SHA256="cfd9b9cfc85de17b05900d2870413ff80e7427bc9c156cd5d189b1ce9158b9ef"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY asyncplusplus_control.cpp /build/asyncplusplus_control.cpp
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/asyncplusplus.tar.gz")
urllib.request.urlretrieve(os.environ["ASYNCPP_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["ASYNCPP_SHA256"]:
    raise SystemExit(f"asyncplusplus SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["ASYNCPP_SHA512"]:
    raise SystemExit(f"asyncplusplus SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
src = Path("/build/asyncplusplus-172ca3f8e0df1b3f7f5ee8b8244e4ac67258b0d8")
lic = hashlib.sha256((src / "LICENSE").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"asyncplusplus LICENSE SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/asyncplusplus-172ca3f8e0df1b3f7f5ee8b8244e4ac67258b0d8 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/asyncplusplus \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/asyncplusplus-172ca3f8e0df1b3f7f5ee8b8244e4ac67258b0d8/LICENSE /asyncplusplus/LICENSE; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -I/asyncplusplus/include /build/asyncplusplus_control.cpp /asyncplusplus/lib/libasync++.a -lpthread \
      -o /build/asyncplusplus_control; \
    /build/asyncplusplus_control; \
    g++ -shared -o /build/libasyncplusplus_whole.so \
      -Wl,--whole-archive /asyncplusplus/lib/libasync++.a -Wl,--no-whole-archive \
      -Wl,--no-undefined -lpthread; \
    if readelf -d /build/libasyncplusplus_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libasyncplusplus_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /asyncplusplus/lib/libasync++.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /asyncplusplus/include/async++.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "asyncplusplus",\n  "version": "1.1",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libasync++.a": "%s",\n    "include/async++.h": "%s",\n    "LICENSE": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${ASYNCPP_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /asyncplusplus/cache-receipt.json

FROM scratch
COPY --from=builder /asyncplusplus /asyncplusplus
