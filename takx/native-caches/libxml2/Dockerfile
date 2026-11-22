# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-libiconv:1.15-amd64 AS libiconv_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG LIBXML2_URL="https://raw.githubusercontent.com/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/libxml2.tar.xz"
ARG LIBXML2_SHA256="0805d7c180cf09caad71666c7a458a74f041561a532902454da5047d83948138"
ARG LICENSE_SHA256="c99aae1afe013e50b8b3701e089222b351258043c3025b64053a233fd25b4be7"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200 \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
    CPPFLAGS="-I/opt/libiconv/include" \
    LDFLAGS="-L/opt/libiconv/lib"

COPY --from=libiconv_cache /libiconv /opt/libiconv
COPY libxml2_control.c /build/libxml2_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/libxml2.tar.xz")
urllib.request.urlretrieve(os.environ["LIBXML2_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["LIBXML2_SHA256"]:
    raise SystemExit(f"libxml2 SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:xz") as tf:
    tf.extractall("/build")

lic = hashlib.sha256(Path("/build/libxml2-2.13.3/Copyright").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"libxml2 Copyright SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    mkdir -p /build/out /libxml2; \
    cd /build/out; \
    /build/libxml2-2.13.3/configure \
      --prefix=/libxml2 \
      --disable-shared \
      --enable-static \
      --without-python \
      --without-lzma \
      --without-zlib \
      --with-iconv=/opt/libiconv; \
    make -j4; \
    make install; \
    cp /build/libxml2-2.13.3/Copyright /libxml2/Copyright; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/libxml2/include/libxml2 -I/opt/libiconv/include \
      /build/libxml2_control.c \
      /libxml2/lib/libxml2.a /opt/libiconv/lib/libiconv.a -lm -lpthread \
      -o /build/libxml2_control; \
    /build/libxml2_control; \
    gcc -shared -o /build/libxml2_whole.so \
      -Wl,--whole-archive /libxml2/lib/libxml2.a -Wl,--no-whole-archive \
      /opt/libiconv/lib/libiconv.a \
      -Wl,--no-undefined -lm -lpthread; \
    if readelf -d /build/libxml2_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libxml2_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /libxml2/lib/libxml2.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /libxml2/include/libxml2/libxml/parser.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "libxml2",\n  "version": "2.13.3",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libxml2.a": "%s",\n    "include/libxml2/libxml/parser.h": "%s",\n    "Copyright": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${LIBXML2_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /libxml2/cache-receipt.json

FROM scratch
COPY --from=builder /libxml2 /libxml2
