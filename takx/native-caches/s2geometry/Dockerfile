# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-abseil:20240116.2-amd64 AS abseil_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-openssl:3.5.9-amd64 AS openssl_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG S2_URL="https://github.com/google/s2geometry/archive/v0.11.1.tar.gz"
ARG S2_SHA256="bdbeb8ebdb88fa934257caf81bb44b55711617a3ab4fdec2c3cfd6cc31b61734"
ARG S2_SHA512="c500029c6e9cc412a29a8a74961688b0a504f60b1a7698ef84c0d0ae760e3c3f05e7068fb1154c9755d907f82e3bc09f8bf1d0ff629cbd3bad6e70169187dd37"
ARG LICENSE_SHA256="cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY --from=abseil_cache /abseil /opt/abseil
COPY --from=openssl_cache /openssl /opt/openssl
COPY s2geometry_control.cpp /build/s2geometry_control.cpp
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/s2geometry.tar.gz")
urllib.request.urlretrieve(os.environ["S2_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["S2_SHA256"]:
    raise SystemExit(f"s2geometry SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["S2_SHA512"]:
    raise SystemExit(f"s2geometry SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")

lic = hashlib.sha256(Path("/build/s2geometry-0.11.1/LICENSE").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"s2geometry LICENSE SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/s2geometry-0.11.1 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/s2geometry \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_PREFIX_PATH="/opt/abseil;/opt/openssl" \
      -DOPENSSL_ROOT_DIR=/opt/openssl \
      -DOPENSSL_USE_STATIC_LIBS=TRUE \
      -DCMAKE_CXX_STANDARD=17 \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DBUILD_EXAMPLES=OFF \
      -DBUILD_TESTS=OFF \
      -DWITH_PYTHON=OFF \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/s2geometry-0.11.1/LICENSE /s2geometry/LICENSE; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -I/s2geometry/include -I/opt/abseil/include -I/opt/openssl/include \
      /build/s2geometry_control.cpp \
      /s2geometry/lib/libs2.a \
      -Wl,--start-group /opt/abseil/lib/libabsl_*.a -Wl,--end-group \
      /opt/openssl/lib/libcrypto.a -lpthread -ldl -lm \
      -o /build/s2geometry_control; \
    /build/s2geometry_control; \
    g++ -shared -o /build/libs2_whole.so \
      -Wl,--whole-archive /s2geometry/lib/libs2.a -Wl,--no-whole-archive \
      -Wl,--start-group /opt/abseil/lib/libabsl_*.a -Wl,--end-group \
      /opt/openssl/lib/libcrypto.a \
      -Wl,--no-undefined -lpthread -ldl -lm; \
    if readelf -d /build/libs2_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libs2_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /s2geometry/lib/libs2.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /s2geometry/include/s2/s2cell_id.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "s2geometry",\n  "version": "0.11.1",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libs2.a": "%s",\n    "include/s2/s2cell_id.h": "%s",\n    "LICENSE": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${S2_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /s2geometry/cache-receipt.json

FROM scratch
COPY --from=builder /s2geometry /s2geometry
