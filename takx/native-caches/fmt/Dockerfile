# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG FMT_URL="https://github.com/fmtlib/fmt/archive/10.2.1.tar.gz"
ARG FMT_SHA256="1250e4cc58bf06ee631567523f48848dc4596133e163f02615c97f78bab6c811"
ARG FMT_SHA512="27df90c681ec37e55625062a79e3b83589b6d7e94eff37a3b412bb8c1473f757a8adb727603acc9185c3490628269216843b7d7bd5a3cb37f0029da5d1495ffa"
ARG LICENSE_SHA256="07580f2a3b35709ce703d523f447b242f6dfec7582a8c0df102c7fa2849375f8"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY fmt_control.cpp /build/fmt_control.cpp
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/fmt.tar.gz")
urllib.request.urlretrieve(os.environ["FMT_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["FMT_SHA256"]:
    raise SystemExit(f"fmt SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["FMT_SHA512"]:
    raise SystemExit(f"fmt SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/fmt-10.2.1/LICENSE").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"fmt LICENSE SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/fmt-10.2.1 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/fmt \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DFMT_DOC=OFF \
      -DFMT_TEST=OFF \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/fmt-10.2.1/LICENSE /fmt/LICENSE; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -I/fmt/include /build/fmt_control.cpp /fmt/lib/libfmt.a \
      -o /build/fmt_control; \
    /build/fmt_control; \
    g++ -shared -o /build/libfmt_whole.so \
      -Wl,--whole-archive /fmt/lib/libfmt.a -Wl,--no-whole-archive \
      -Wl,--no-undefined; \
    if readelf -d /build/libfmt_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libfmt_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /fmt/lib/libfmt.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /fmt/include/fmt/core.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "fmt",\n  "version": "10.2.1",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libfmt.a": "%s",\n    "include/fmt/core.h": "%s",\n    "LICENSE": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${FMT_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /fmt/cache-receipt.json

FROM scratch
COPY --from=builder /fmt /fmt
