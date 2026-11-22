# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-zlib:1.3.1-amd64 AS zlib_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-libiconv:1.15-amd64 AS libiconv_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-libxml2:2.13.3-amd64 AS libxml2_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-quictls:3.0.14-quic1-amd64 AS quictls_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-nghttp2:1.62.1-amd64 AS nghttp2_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-ngtcp2:1.6.0-amd64 AS ngtcp2_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-libmicrohttpd:1.0.1-amd64 AS libmicrohttpd_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-protobuf:3.21.0-amd64 AS protobuf_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG ATAK_REPO="https://github.com/TAK-Product-Center/atak-civ.git"
ARG ATAK_REV="9f6893dd657feacc35ec5de03dad721c2e44170e"
ARG CURL_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/curl.tar.bz2"
ARG CURL_SHA256="b57285d9e18bf12a5f2309fc45244f6cf9cb14734e7454121099dd0a83d669a3"
ARG CURL_PGSC_PATCH_SHA256="f8acc0914f2415c39f02f81fde2acfb958ad41d233a80b90680621bd077b8e27"
ARG CURL_SIGPIPE_PATCH_SHA256="e8fd1edee44adda7bba1ca7c43ec0b4e1c8dc978ab0e560d4d0109541f7cdbf0"
ARG LICENSE_SHA256="3db8b919e1f1cb3dff5114ea875d3a758ff367e6490622780d0f7092daebd8f9"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY --from=zlib_cache /zlib /opt/deps/zlib
COPY --from=libiconv_cache /libiconv /opt/deps/libiconv
COPY --from=libxml2_cache /libxml2 /opt/deps/libxml2
COPY --from=quictls_cache /quictls /opt/deps/quictls
COPY --from=nghttp2_cache /nghttp2 /opt/deps/nghttp2
COPY --from=ngtcp2_cache /ngtcp2 /opt/deps/ngtcp2
COPY --from=libmicrohttpd_cache /libmicrohttpd /opt/deps/libmicrohttpd
COPY --from=protobuf_cache /protobuf /opt/deps/protobuf
COPY commoncommo_control.cpp /build/commoncommo_control.cpp
COPY patches/curl-8.9.1-sigpipe-init.patch /build/patches/curl-8.9.1-sigpipe-init.patch
WORKDIR /build

RUN set -x; \
    mkdir -p /opt/commo-deps/include /opt/commo-deps/lib /opt/commo-deps/bin; \
    for d in zlib libiconv libxml2 quictls nghttp2 ngtcp2 libmicrohttpd protobuf; do \
      if [ -d "/opt/deps/${d}/include" ]; then \
        cp -a "/opt/deps/${d}/include/." /opt/commo-deps/include/; \
      fi; \
      if [ -d "/opt/deps/${d}/lib" ]; then \
        cp -a "/opt/deps/${d}/lib/." /opt/commo-deps/lib/; \
      fi; \
      if [ -d "/opt/deps/${d}/bin" ]; then \
        cp -a "/opt/deps/${d}/bin/." /opt/commo-deps/bin/; \
      fi; \
    done; \
    git init /build/atak; \
    git -C /build/atak remote add origin "${ATAK_REPO}"; \
    git -C /build/atak fetch --depth=1 --filter=blob:none origin "${ATAK_REV}"; \
    git -C /build/atak sparse-checkout init --no-cone; \
    git -C /build/atak sparse-checkout set /commoncommo/ /takthirdparty/distfiles/curl-pgsc.patch /LICENSE.md; \
    git -C /build/atak checkout FETCH_HEAD

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

for rel, env_key in (
    ("/build/atak/LICENSE.md", "LICENSE_SHA256"),
    ("/build/atak/takthirdparty/distfiles/curl-pgsc.patch", "CURL_PGSC_PATCH_SHA256"),
    ("/build/patches/curl-8.9.1-sigpipe-init.patch", "CURL_SIGPIPE_PATCH_SHA256"),
):
    got = hashlib.sha256(Path(rel).read_bytes()).hexdigest()
    if got != os.environ[env_key]:
        raise SystemExit(f"file {rel} SHA-256 mismatch: {got}")

dest = Path("/build/curl.tar.bz2")
urllib.request.urlretrieve(os.environ["CURL_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["CURL_SHA256"]:
    raise SystemExit(f"curl SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:bz2") as tf:
    tf.extractall("/build")
dest.unlink()
PY

RUN set -x; \
    mv /build/curl-8.9.1 /build/curl; \
    patch --fuzz=0 --batch --no-backup-if-mismatch -p0 -d /build \
      -i /build/atak/takthirdparty/distfiles/curl-pgsc.patch; \
    patch --fuzz=0 --batch --no-backup-if-mismatch -p1 -d /build/curl \
      -i /build/patches/curl-8.9.1-sigpipe-init.patch; \
    mkdir -p /build/curl-build; \
    (cd /build/curl-build && \
      CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      CPPFLAGS="-I/opt/commo-deps/include" \
      LDFLAGS="-L/opt/commo-deps/lib" \
      PKG_CONFIG_PATH="/opt/commo-deps/lib/pkgconfig" \
      /build/curl/configure \
        --prefix=/opt/commo-deps \
        --with-openssl=/opt/commo-deps \
        --with-zlib=/opt/commo-deps \
        --with-nghttp2=/opt/commo-deps \
        --disable-shared \
        --enable-static \
        --disable-ldap \
        --disable-symbol-hiding \
        --without-ca-bundle \
        --without-ca-path \
        --without-libpsl \
        --without-libidn2 \
        --without-brotli \
        --without-zstd \
        --without-libssh2 \
        --without-gssapi \
        --without-librtmp); \
    make -C /build/curl-build -j4; \
    make -C /build/curl-build install; \
    make -C /build/atak/commoncommo/core/impl buildstampgen; \
    make -C /build/atak/commoncommo/core/impl -j4 \
      TAKTHIRDPARTYDIR=/opt/commo-deps \
      COMMO_CXXFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      CXX=g++ AR=ar RANLIB=ranlib \
      PROTOC=/opt/commo-deps/bin/protoc \
      libcommoncommo.a; \
    mkdir -p /commoncommo/lib /commoncommo/include; \
    cp /build/atak/commoncommo/core/impl/libcommoncommo.a /commoncommo/lib/libcommoncommo.a; \
    cp /opt/commo-deps/lib/libcurl.a /commoncommo/lib/libcurl.a; \
    cp -a /build/atak/commoncommo/core/include/. /commoncommo/include/; \
    cp -a /opt/commo-deps/include/curl /commoncommo/include/curl; \
    cp /build/atak/LICENSE.md /commoncommo/LICENSE.md; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -DCURL_STATICLIB \
      -isystem /commoncommo/include \
      -isystem /opt/commo-deps/include \
      -isystem /opt/commo-deps/include/libxml2 \
      /build/commoncommo_control.cpp \
      /commoncommo/lib/libcommoncommo.a \
      /commoncommo/lib/libcurl.a \
      /opt/commo-deps/lib/libmicrohttpd.a \
      /opt/commo-deps/lib/libxml2.a \
      /opt/commo-deps/lib/libiconv.a \
      /opt/commo-deps/lib/libprotobuf-lite.a \
      /opt/commo-deps/lib/libngtcp2_crypto_quictls.a \
      /opt/commo-deps/lib/libngtcp2.a \
      /opt/commo-deps/lib/libnghttp2.a \
      /opt/commo-deps/lib/libssl.a \
      /opt/commo-deps/lib/libcrypto.a \
      /opt/commo-deps/lib/libz.a \
      -lpthread -ldl -lm \
      -o /build/commoncommo_control; \
    /build/commoncommo_control; \
    g++ -shared -o /build/commoncommo_whole.so \
      -Wl,--whole-archive \
      /commoncommo/lib/libcommoncommo.a \
      -Wl,--no-whole-archive \
      /commoncommo/lib/libcurl.a \
      /opt/commo-deps/lib/libmicrohttpd.a \
      /opt/commo-deps/lib/libxml2.a \
      /opt/commo-deps/lib/libiconv.a \
      /opt/commo-deps/lib/libprotobuf-lite.a \
      /opt/commo-deps/lib/libngtcp2_crypto_quictls.a \
      /opt/commo-deps/lib/libngtcp2.a \
      /opt/commo-deps/lib/libnghttp2.a \
      /opt/commo-deps/lib/libssl.a \
      /opt/commo-deps/lib/libcrypto.a \
      /opt/commo-deps/lib/libz.a \
      -Wl,--no-undefined -lpthread -ldl -lm; \
    if readelf -d /build/commoncommo_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in commoncommo_whole.so" >&2; \
      exit 1; \
    fi; \
    commo_sha="$(sha256sum /commoncommo/lib/libcommoncommo.a | awk '{print $1}')"; \
    curl_sha="$(sha256sum /commoncommo/lib/libcurl.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /commoncommo/include/commo.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "commoncommo",\n  "version": "1.17.2",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libcommoncommo.a": "%s",\n    "lib/libcurl.a": "%s",\n    "include/commo.h": "%s",\n    "LICENSE.md": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${CURL_SHA256}" "${LICENSE_SHA256}" \
      "${commo_sha}" "${curl_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /commoncommo/cache-receipt.json

FROM scratch
COPY --from=builder /commoncommo /commoncommo
