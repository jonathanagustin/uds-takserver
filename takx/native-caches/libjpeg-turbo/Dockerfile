# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG JPEGTURBO_URL="https://github.com/libjpeg-turbo/libjpeg-turbo/archive/3.0.3.tar.gz"
ARG JPEGTURBO_SHA256="a649205a90e39a548863a3614a9576a3fb4465f8e8e66d54999f127957c25b21"
ARG JPEGTURBO_SHA512="46c44be837654e201d11bbf8d9fbb35b775a7d4bf653e9e709279437b10d5c8b0825ece4c8ee33f66689c263234fa2b08240fb5f5ba80e76e03891da8f64eda8"
ARG LICENSE_SHA256="e548c96c0c09397c94b5c5531836c02ff2c403f1f2b130cad8e3ec9439b5a7cc"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY libjpeg_turbo_control.c /build/libjpeg_turbo_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/libjpeg-turbo.tar.gz")
urllib.request.urlretrieve(os.environ["JPEGTURBO_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["JPEGTURBO_SHA256"]:
    raise SystemExit(f"libjpeg-turbo SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["JPEGTURBO_SHA512"]:
    raise SystemExit(f"libjpeg-turbo SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/libjpeg-turbo-3.0.3/LICENSE.md").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"libjpeg-turbo LICENSE.md SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/libjpeg-turbo-3.0.3 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/libjpeg-turbo \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DENABLE_STATIC=ON \
      -DENABLE_SHARED=OFF \
      -DENABLE_EXECUTABLES=OFF \
      -DINSTALL_DOCS=OFF \
      -DWITH_SIMD=OFF \
      -DCMAKE_C_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/libjpeg-turbo-3.0.3/LICENSE.md /libjpeg-turbo/LICENSE.md; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/libjpeg-turbo/include /build/libjpeg_turbo_control.c /libjpeg-turbo/lib/libjpeg.a -lm \
      -o /build/libjpeg_turbo_control; \
    /build/libjpeg_turbo_control; \
    gcc -shared -o /build/libjpeg_whole.so \
      -Wl,--whole-archive /libjpeg-turbo/lib/libjpeg.a -Wl,--no-whole-archive \
      -Wl,--no-undefined -lm; \
    if readelf -d /build/libjpeg_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libjpeg_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /libjpeg-turbo/lib/libjpeg.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /libjpeg-turbo/include/jpeglib.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "libjpeg-turbo",\n  "version": "3.0.3",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libjpeg.a": "%s",\n    "include/jpeglib.h": "%s",\n    "LICENSE.md": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${JPEGTURBO_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /libjpeg-turbo/cache-receipt.json

FROM scratch
COPY --from=builder /libjpeg-turbo /libjpeg-turbo
