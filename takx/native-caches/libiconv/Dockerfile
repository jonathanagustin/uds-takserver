# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG LIBICONV_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/libiconv.tar.gz"
ARG LIBICONV_SHA256="ccf536620a45458d26ba83887a983b96827001e92a13847b45e4925cc8913178"
ARG PATCH_URL="https://raw.githubusercontent.com/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/libiconv-pgsc.patch"
ARG PATCH_SHA256="8ad21428e491349f33ab346745b81c0c78d39d7e53577129ba6becc8a6f2610b"
ARG LICENSE_SHA256="56bdea73b6145ef6ac5259b3da390b981d840c24cb03b8e1cbc678de7ecfa18d"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200 \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"

COPY libiconv_control.c /build/libiconv_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/libiconv.tar.gz")
urllib.request.urlretrieve(os.environ["LIBICONV_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["LIBICONV_SHA256"]:
    raise SystemExit(f"libiconv SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")

lic = hashlib.sha256(Path("/build/libiconv-1.15/COPYING.LIB").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"libiconv COPYING.LIB SHA-256 mismatch: {lic}")

patch_dest = Path("/build/libiconv-pgsc.patch")
urllib.request.urlretrieve(os.environ["PATCH_URL"], patch_dest)
pgot = hashlib.sha256(patch_dest.read_bytes()).hexdigest()
if pgot != os.environ["PATCH_SHA256"]:
    raise SystemExit(f"libiconv patch SHA-256 mismatch: {pgot}")
PY

RUN set -x; \
    patch -d /build/libiconv-1.15 --fuzz=0 --batch --no-backup-if-mismatch -p1 -i /build/libiconv-pgsc.patch; \
    mkdir -p /build/out /libiconv; \
    cd /build/out; \
    /build/libiconv-1.15/configure \
      --prefix=/libiconv \
      --disable-shared \
      --enable-static; \
    make -j4; \
    make install-lib; \
    cp /build/libiconv-1.15/COPYING.LIB /libiconv/COPYING.LIB; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/libiconv/include /build/libiconv_control.c /libiconv/lib/libiconv.a \
      -o /build/libiconv_control; \
    /build/libiconv_control; \
    gcc -shared -o /build/libiconv_whole.so \
      -Wl,--whole-archive /libiconv/lib/libiconv.a -Wl,--no-whole-archive \
      -Wl,--no-undefined; \
    if readelf -d /build/libiconv_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libiconv_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /libiconv/lib/libiconv.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /libiconv/include/iconv.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "libiconv",\n  "version": "1.15",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libiconv.a": "%s",\n    "include/iconv.h": "%s",\n    "COPYING.LIB": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${LIBICONV_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /libiconv/cache-receipt.json

FROM scratch
COPY --from=builder /libiconv /libiconv
