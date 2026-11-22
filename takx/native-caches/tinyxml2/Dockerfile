# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG TINYXML2_URL="https://github.com/leethomason/tinyxml2/archive/10.0.0.tar.gz"
ARG TINYXML2_SHA256="3bdf15128ba16686e69bce256cc468e76c7b94ff2c7f391cc5ec09e40bff3839"
ARG TINYXML2_SHA512="a359d33bc12fad455b53d81011dbe12727cae0aabfaa5704f1a25807ca216dd854a571291029886c0beedeca5c3b6393dd49c4718773e18a0e008abbdb3de36a"
ARG LICENSE_SHA256="9332252e9b9e46db8285d4a3f0bf25f139bf1dca6781b956d57f2302efca6432"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY tinyxml2_control.cpp /build/tinyxml2_control.cpp
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/tinyxml2.tar.gz")
urllib.request.urlretrieve(os.environ["TINYXML2_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["TINYXML2_SHA256"]:
    raise SystemExit(f"tinyxml2 SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["TINYXML2_SHA512"]:
    raise SystemExit(f"tinyxml2 SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/tinyxml2-10.0.0/LICENSE.txt").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"tinyxml2 LICENSE.txt SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/tinyxml2-10.0.0 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/tinyxml2 \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -Dtinyxml2_BUILD_TESTING=OFF \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/tinyxml2-10.0.0/LICENSE.txt /tinyxml2/LICENSE.txt; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -I/tinyxml2/include /build/tinyxml2_control.cpp /tinyxml2/lib/libtinyxml2.a \
      -o /build/tinyxml2_control; \
    /build/tinyxml2_control; \
    g++ -shared -o /build/libtinyxml2_whole.so \
      -Wl,--whole-archive /tinyxml2/lib/libtinyxml2.a -Wl,--no-whole-archive \
      -Wl,--no-undefined; \
    if readelf -d /build/libtinyxml2_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libtinyxml2_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /tinyxml2/lib/libtinyxml2.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /tinyxml2/include/tinyxml2.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "tinyxml2",\n  "version": "10.0.0",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libtinyxml2.a": "%s",\n    "include/tinyxml2.h": "%s",\n    "LICENSE.txt": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${TINYXML2_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /tinyxml2/cache-receipt.json

FROM scratch
COPY --from=builder /tinyxml2 /tinyxml2
