# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG MESHOPT_URL="https://github.com/zeux/meshoptimizer/archive/v0.20.tar.gz"
ARG MESHOPT_SHA256="cf1077a83958bed3d8da28a841ca53a6a42d871e49023edce64e37002a0f5a48"
ARG MESHOPT_SHA512="29f9987325b1e00e2b4bf5cb7745323456f5f766f6bdc189aeb192c802e7c73d6ec4484cd7c91d070ee940b423a680bee31c79e4db4a49da99346d21f70c58d6"
ARG LICENSE_SHA256="af0399f6fe106205872fd58c25f5a33fdefd34754b6d96d3f6b4307eb17d05ed"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY meshoptimizer_control.cpp /build/meshoptimizer_control.cpp
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/meshoptimizer.tar.gz")
urllib.request.urlretrieve(os.environ["MESHOPT_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["MESHOPT_SHA256"]:
    raise SystemExit(f"meshoptimizer SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["MESHOPT_SHA512"]:
    raise SystemExit(f"meshoptimizer SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/meshoptimizer-0.20/LICENSE.md").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"meshoptimizer LICENSE.md SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/meshoptimizer-0.20 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/meshoptimizer \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DMESHOPT_BUILD_DEMO=OFF \
      -DMESHOPT_BUILD_GLTFPACK=OFF \
      -DMESHOPT_BUILD_SHARED_LIBS=OFF \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/meshoptimizer-0.20/LICENSE.md /meshoptimizer/LICENSE.md; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -I/meshoptimizer/include /build/meshoptimizer_control.cpp /meshoptimizer/lib/libmeshoptimizer.a \
      -o /build/meshoptimizer_control; \
    /build/meshoptimizer_control; \
    g++ -shared -o /build/libmeshoptimizer_whole.so \
      -Wl,--whole-archive /meshoptimizer/lib/libmeshoptimizer.a -Wl,--no-whole-archive \
      -Wl,--no-undefined; \
    if readelf -d /build/libmeshoptimizer_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libmeshoptimizer_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /meshoptimizer/lib/libmeshoptimizer.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /meshoptimizer/include/meshoptimizer.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "meshoptimizer",\n  "version": "0.20",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libmeshoptimizer.a": "%s",\n    "include/meshoptimizer.h": "%s",\n    "LICENSE.md": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${MESHOPT_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /meshoptimizer/cache-receipt.json

FROM scratch
COPY --from=builder /meshoptimizer /meshoptimizer
