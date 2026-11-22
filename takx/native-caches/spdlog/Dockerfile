# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-fmt:10.2.1-amd64 AS fmt_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG SPDLOG_URL="https://github.com/gabime/spdlog/archive/v1.14.1.tar.gz"
ARG SPDLOG_SHA256="1586508029a7d0670dfcb2d97575dcdc242d3868a259742b69f100801ab4e16b"
ARG SPDLOG_SHA512="d8f36a3d65a43d8c64900e46137827aadb05559948b2f5a389bea16ed1bfac07d113ee11cf47970913298d6c37400355fe6895cda8fa6dcf6abd9da0d8f199e9"
ARG LICENSE_SHA256="4ccecab18d1ff0b61174fe3d6c430541625d3ddb865b0d5887db296f883c76e7"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY --from=fmt_cache /fmt /opt/fmt
COPY spdlog_control.cpp /build/spdlog_control.cpp
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/spdlog.tar.gz")
urllib.request.urlretrieve(os.environ["SPDLOG_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["SPDLOG_SHA256"]:
    raise SystemExit(f"spdlog SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["SPDLOG_SHA512"]:
    raise SystemExit(f"spdlog SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/spdlog-1.14.1/LICENSE").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"spdlog LICENSE SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/spdlog-1.14.1 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/spdlog \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_PREFIX_PATH=/opt/fmt \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DSPDLOG_BUILD_SHARED=OFF \
      -DSPDLOG_BUILD_EXAMPLE=OFF \
      -DSPDLOG_BUILD_TESTS=OFF \
      -DSPDLOG_FMT_EXTERNAL=ON \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/spdlog-1.14.1/LICENSE /spdlog/LICENSE; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -I/spdlog/include -I/opt/fmt/include \
      /build/spdlog_control.cpp \
      /spdlog/lib/libspdlog.a /opt/fmt/lib/libfmt.a -lpthread \
      -o /build/spdlog_control; \
    /build/spdlog_control; \
    g++ -shared -o /build/libspdlog_whole.so \
      -Wl,--whole-archive /spdlog/lib/libspdlog.a -Wl,--no-whole-archive \
      /opt/fmt/lib/libfmt.a \
      -Wl,--no-undefined -lpthread; \
    if readelf -d /build/libspdlog_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libspdlog_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /spdlog/lib/libspdlog.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /spdlog/include/spdlog/spdlog.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "spdlog",\n  "version": "1.14.1",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libspdlog.a": "%s",\n    "include/spdlog/spdlog.h": "%s",\n    "LICENSE": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${SPDLOG_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /spdlog/cache-receipt.json

FROM scratch
COPY --from=builder /spdlog /spdlog
