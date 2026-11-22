# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG ZLIB_NG_URL="https://github.com/zlib-ng/zlib-ng/archive/2.1.5.tar.gz"
ARG ZLIB_NG_SHA256="3f6576971397b379d4205ae5451ff5a68edf6c103b2f03c4188ed7075fbb5f04"
ARG ZLIB_NG_SHA512="9212d87c63a2da4e5355a7a1c75380aeba40fbd0ea3d71d3784cb3eac94237f9bea2a1b7993a08f39d4197725c4c133087d3a9d213d3944aa48a7559de2be920"
ARG LICENSE_SHA256="d3c80be055d94d798eaa786116e84fa0b010bc11420b5d2060d978ea77845436"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY zlib_ng_control.c /build/zlib_ng_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/zlib-ng.tar.gz")
urllib.request.urlretrieve(os.environ["ZLIB_NG_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["ZLIB_NG_SHA256"]:
    raise SystemExit(f"zlib-ng SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["ZLIB_NG_SHA512"]:
    raise SystemExit(f"zlib-ng SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/zlib-ng-2.1.5/LICENSE.md").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"zlib-ng LICENSE.md SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/zlib-ng-2.1.5 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/zlib-ng \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DZLIB_ENABLE_TESTS=OFF \
      -DWITH_NEW_STRATEGIES=ON \
      -DWITH_OPTIM=ON \
      -DCMAKE_C_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/zlib-ng-2.1.5/LICENSE.md /zlib-ng/LICENSE.md; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/zlib-ng/include /build/zlib_ng_control.c /zlib-ng/lib/libz-ng.a \
      -o /build/zlib_ng_control; \
    /build/zlib_ng_control; \
    gcc -shared -o /build/libz_ng_whole.so \
      -Wl,--whole-archive /zlib-ng/lib/libz-ng.a -Wl,--no-whole-archive \
      -Wl,--no-undefined; \
    if readelf -d /build/libz_ng_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libz_ng_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /zlib-ng/lib/libz-ng.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /zlib-ng/include/zlib-ng.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "zlib-ng",\n  "version": "2.1.5",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libz-ng.a": "%s",\n    "include/zlib-ng.h": "%s",\n    "LICENSE.md": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${ZLIB_NG_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /zlib-ng/cache-receipt.json

FROM scratch
COPY --from=builder /zlib-ng /zlib-ng
