# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG ZSTD_URL="https://github.com/facebook/zstd/archive/v1.5.6.tar.gz"
ARG ZSTD_SHA256="30f35f71c1203369dc979ecde0400ffea93c27391bfd2ac5a9715d2173d92ff7"
ARG ZSTD_SHA512="ca12dffd86618ca008e1ecc79056c1129cb4e61668bf13a3cd5b2fa5c93bc9c92c80f64c1870c68b9c20009d9b3a834eac70db72242d5106125a1c53cccf8de8"
ARG LICENSE_SHA256="7055266497633c9025b777c78eb7235af13922117480ed5c674677adc381c9d8"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY zstd_control.c /build/zstd_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/zstd.tar.gz")
urllib.request.urlretrieve(os.environ["ZSTD_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["ZSTD_SHA256"]:
    raise SystemExit(f"zstd SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["ZSTD_SHA512"]:
    raise SystemExit(f"zstd SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/zstd-1.5.6/LICENSE").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"zstd LICENSE SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/zstd-1.5.6/build/cmake -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/zstd \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DZSTD_BUILD_SHARED=OFF \
      -DZSTD_BUILD_STATIC=ON \
      -DZSTD_LEGACY_SUPPORT=ON \
      -DZSTD_MULTITHREAD_SUPPORT=ON \
      -DZSTD_BUILD_PROGRAMS=OFF \
      -DZSTD_BUILD_TESTS=OFF \
      -DZSTD_BUILD_CONTRIB=OFF \
      -DCMAKE_C_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/zstd-1.5.6/LICENSE /zstd/LICENSE; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/zstd/include /build/zstd_control.c /zstd/lib/libzstd.a -lpthread \
      -o /build/zstd_control; \
    /build/zstd_control; \
    gcc -shared -o /build/libzstd_whole.so \
      -Wl,--whole-archive /zstd/lib/libzstd.a -Wl,--no-whole-archive \
      -Wl,--no-undefined -lpthread; \
    if readelf -d /build/libzstd_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libzstd_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /zstd/lib/libzstd.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /zstd/include/zstd.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "zstd",\n  "version": "1.5.6",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libzstd.a": "%s",\n    "include/zstd.h": "%s",\n    "LICENSE": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${ZSTD_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /zstd/cache-receipt.json

FROM scratch
COPY --from=builder /zstd /zstd
