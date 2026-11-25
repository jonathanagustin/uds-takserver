# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG PROTOBUF_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/protobuf.tar.gz"
ARG PROTOBUF_SHA256="28e74a8a241a91c0a6aec997d0d6efda9bc0baa0ed7acfaf392555b88bca8b7c"
ARG PATCH_URL="https://raw.githubusercontent.com/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/protobuf-pgsc.patch"
ARG PATCH_SHA256="0b745d006de7e52b379518540d33188b20f5c81bf8ea72538e38101b9737a618"
ARG LICENSE_SHA256="6e5e117324afd944dcf67f36cf329843bc1a92229a8cd9bb573d7a83130fea7d"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200 \
    DIST_LANG=cpp \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
    CXXFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"

COPY protobuf_control.cpp /build/protobuf_control.cpp
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/protobuf.tar.gz")
urllib.request.urlretrieve(os.environ["PROTOBUF_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["PROTOBUF_SHA256"]:
    raise SystemExit(f"protobuf SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")

lic = hashlib.sha256(Path("/build/protobuf-3.21.0/LICENSE").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"protobuf LICENSE SHA-256 mismatch: {lic}")

patch_dest = Path("/build/protobuf-pgsc.patch")
urllib.request.urlretrieve(os.environ["PATCH_URL"], patch_dest)
pgot = hashlib.sha256(patch_dest.read_bytes()).hexdigest()
if pgot != os.environ["PATCH_SHA256"]:
    raise SystemExit(f"protobuf patch SHA-256 mismatch: {pgot}")

Path("/build/protobuf-3.21.0").rename(Path("/build/protobuf"))
PY

RUN set -x; \
    patch -d /build --fuzz=0 --batch --no-backup-if-mismatch -p0 -i /build/protobuf-pgsc.patch; \
    mkdir -p /build/out /protobuf/bin /protobuf/lib; \
    cd /build/out; \
    /build/protobuf/configure \
      --prefix=/protobuf \
      --enable-static \
      --disable-shared \
      --without-zlib; \
    make -j4 -C src protoc libprotobuf-lite.la; \
    cp src/protoc /protobuf/bin/protoc; \
    cp src/.libs/libprotobuf-lite.a /protobuf/lib/libprotobuf-lite.a; \
    make -C src install-nobase_includeHEADERS; \
    cp /build/protobuf/LICENSE /protobuf/LICENSE; \
    /protobuf/bin/protoc --version | grep -qx "libprotoc 3.21.0"; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -I/protobuf/include /build/protobuf_control.cpp \
      /protobuf/lib/libprotobuf-lite.a -lpthread \
      -o /build/protobuf_control; \
    /build/protobuf_control; \
    g++ -shared -o /build/libprotobuf_lite_whole.so \
      -Wl,--whole-archive /protobuf/lib/libprotobuf-lite.a -Wl,--no-whole-archive \
      -Wl,--no-undefined -lpthread; \
    if readelf -d /build/libprotobuf_lite_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libprotobuf_lite_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /protobuf/lib/libprotobuf-lite.a | awk '{print $1}')"; \
    protoc_sha="$(sha256sum /protobuf/bin/protoc | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /protobuf/include/google/protobuf/stubs/common.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "protobuf",\n  "version": "3.21.0",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libprotobuf-lite.a": "%s",\n    "bin/protoc": "%s",\n    "include/google/protobuf/stubs/common.h": "%s",\n    "LICENSE": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${PROTOBUF_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${protoc_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /protobuf/cache-receipt.json

FROM scratch
COPY --from=builder /protobuf /protobuf
