# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-openssl:3.5.9-amd64 AS openssl_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-zlib:1.3.1-amd64 AS zlib_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG CURL_URL="https://github.com/curl/curl/releases/download/curl-8_23_0/curl-8.23.0.tar.xz"
ARG CURL_SHA256="8dadd7d2536a314f982ad05a651b713f426bf7d02cc2c76cffb9796f2218e6c5"
ARG LICENSE_SHA256="82f2f4427d6545ee5aaac4f0b80428da6cc8ba41c2cf5da3a03680ec327b9681"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY --from=openssl_cache /openssl /opt/openssl
COPY --from=zlib_cache /zlib /opt/zlib
COPY curl_control.c /build/curl_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/curl.tar.xz")
urllib.request.urlretrieve(os.environ["CURL_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["CURL_SHA256"]:
    raise SystemExit(f"curl SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:xz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/curl-8.23.0/COPYING").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"curl COPYING SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cmake -S /build/curl-8.23.0 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/curl \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DCMAKE_C_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      -DOPENSSL_ROOT_DIR=/opt/openssl \
      -DOPENSSL_INCLUDE_DIR=/opt/openssl/include \
      -DOPENSSL_SSL_LIBRARY=/opt/openssl/lib/libssl.a \
      -DOPENSSL_CRYPTO_LIBRARY=/opt/openssl/lib/libcrypto.a \
      -DZLIB_INCLUDE_DIR=/opt/zlib/include \
      -DZLIB_LIBRARY=/opt/zlib/lib/libz.a \
      -DBUILD_CURL_EXE=OFF \
      -DBUILD_SHARED_LIBS=OFF \
      -DBUILD_STATIC_LIBS=ON \
      -DBUILD_TESTING=OFF \
      -DBUILD_EXAMPLES=OFF \
      -DBUILD_LIBCURL_DOCS=OFF \
      -DBUILD_MISC_DOCS=OFF \
      -DENABLE_CURL_MANUAL=OFF \
      -DCURL_USE_OPENSSL=ON \
      -DCURL_ZLIB=ON \
      -DCURL_USE_PKGCONFIG=OFF \
      -DCURL_USE_CMAKECONFIG=OFF \
      -DCURL_USE_LIBPSL=OFF \
      -DUSE_LIBIDN2=OFF \
      -DCURL_USE_LIBSSH2=OFF \
      -DCURL_USE_LIBSSH=OFF \
      -DCURL_USE_GSASL=OFF \
      -DCURL_USE_GSSAPI=OFF \
      -DCURL_USE_LIBBACKTRACE=OFF \
      -DCURL_USE_LIBUV=OFF \
      -DCURL_BROTLI=OFF \
      -DCURL_ZSTD=OFF \
      -DUSE_NGHTTP2=OFF \
      -DUSE_NGTCP2=OFF \
      -DUSE_QUICHE=OFF \
      -DCURL_DISABLE_LDAP=ON \
      -DCURL_DISABLE_LDAPS=ON \
      -DCURL_DISABLE_OPENSSL_AUTO_LOAD_CONFIG=ON \
      -DCURL_CA_BUNDLE=none \
      -DCURL_CA_PATH=none; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/curl-8.23.0/COPYING /curl/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/curl/include /build/curl_control.c \
      /curl/lib/libcurl.a /opt/openssl/lib/libssl.a /opt/openssl/lib/libcrypto.a /opt/zlib/lib/libz.a \
      -lpthread -ldl \
      -o /build/curl_control; \
    /build/curl_control; \
    lib_sha="$(sha256sum /curl/lib/libcurl.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /curl/include/curl/curl.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "curl",\n  "version": "8.23.0",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libcurl.a": "%s",\n    "include/curl/curl.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${CURL_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /curl/cache-receipt.json

FROM scratch
COPY --from=builder /curl /curl
