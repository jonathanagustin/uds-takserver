# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG PROJ_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/proj.tar.gz"
ARG PROJ_SHA256="fca0388f3f8bc5a1a803d2f6ff30017532367992b30cf144f2d39be88f36c319"
ARG LICENSE_SHA256="2dcac4af6990093ef48b896bfb81452d46a31b08d46cce34b27e83f93b2e37f6"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY proj_control.c /build/proj_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/proj.tar.gz")
urllib.request.urlretrieve(os.environ["PROJ_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["PROJ_SHA256"]:
    raise SystemExit(f"proj SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")

lic = hashlib.sha256(Path("/build/proj-4.9.1/COPYING").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"proj COPYING SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    mkdir -p /build/out; \
    cd /build/out; \
    CONFIG_SITE=/dev/null \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
    CXXFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
    /build/proj-4.9.1/configure \
      --prefix=/proj \
      --enable-static \
      --disable-shared \
      --with-pic \
      --without-jni; \
    make -j4; \
    make install; \
    cp /build/proj-4.9.1/COPYING /proj/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/proj/include /build/proj_control.c /proj/lib/libproj.a -lm \
      -o /build/proj_control; \
    /build/proj_control; \
    gcc -shared -o /build/libproj_whole.so \
      -Wl,--whole-archive /proj/lib/libproj.a -Wl,--no-whole-archive \
      -Wl,--no-undefined -lm; \
    if readelf -d /build/libproj_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libproj_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /proj/lib/libproj.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /proj/include/proj_api.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "proj",\n  "version": "4.9.1",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libproj.a": "%s",\n    "include/proj_api.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${PROJ_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /proj/cache-receipt.json

FROM scratch
COPY --from=builder /proj /proj
