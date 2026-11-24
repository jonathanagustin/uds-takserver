# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-quictls:3.0.14-quic1-amd64 AS quictls_cache
FROM docker.io/library/gcc@sha256:82549aa8f90ada3236a8be70c74543132a76662ef33f0c3271ed802b81584a82 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG NGTCP2_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/ngtcp2.tar.bz2"
ARG NGTCP2_SHA256="3e40cfb92ab710295cb7c07721db28d16c6f251f61420a462c448cf39a59cc29"
ARG LICENSE_SHA256="542dd526d2ea23565f0571cc68df6f05ed11248a90ff0f32de19d8ba589db513"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200 \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
    CPPFLAGS="-I/opt/quictls/include" \
    LDFLAGS="-L/opt/quictls/lib" \
    OPENSSL_CFLAGS="-I/opt/quictls/include" \
    OPENSSL_LIBS="-L/opt/quictls/lib -lssl -lcrypto -lpthread -ldl"

COPY --from=quictls_cache /quictls /opt/quictls
COPY ngtcp2_control.c /build/ngtcp2_control.c
WORKDIR /build/ngtcp2-1.6.0

RUN set -x; \
    curl -fsSL "${NGTCP2_URL}" -o /build/ngtcp2.tar.bz2; \
    echo "${NGTCP2_SHA256}  /build/ngtcp2.tar.bz2" | sha256sum -c -; \
    tar -xjf /build/ngtcp2.tar.bz2 -C /build; \
    echo "${LICENSE_SHA256}  /build/ngtcp2-1.6.0/COPYING" | sha256sum -c -; \
    mkdir -p /ngtcp2; \
    ./configure \
      --prefix=/ngtcp2 \
      --with-openssl \
      --disable-shared \
      --enable-static \
      --enable-lib-only; \
    make -j4; \
    make install; \
    cp /build/ngtcp2-1.6.0/COPYING /ngtcp2/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/ngtcp2/include -I/opt/quictls/include \
      /build/ngtcp2_control.c \
      /ngtcp2/lib/libngtcp2_crypto_quictls.a /ngtcp2/lib/libngtcp2.a \
      /opt/quictls/lib/libssl.a /opt/quictls/lib/libcrypto.a \
      -lpthread -ldl \
      -o /build/ngtcp2_control; \
    /build/ngtcp2_control; \
    gcc -shared -o /build/libngtcp2_whole.so \
      -Wl,--whole-archive /ngtcp2/lib/libngtcp2.a /ngtcp2/lib/libngtcp2_crypto_quictls.a -Wl,--no-whole-archive \
      /opt/quictls/lib/libssl.a /opt/quictls/lib/libcrypto.a \
      -Wl,--no-undefined -lpthread -ldl; \
    if readelf -d /build/libngtcp2_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libngtcp2_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /ngtcp2/lib/libngtcp2.a | awk '{print $1}')"; \
    crypto_sha="$(sha256sum /ngtcp2/lib/libngtcp2_crypto_quictls.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /ngtcp2/include/ngtcp2/ngtcp2.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "ngtcp2",\n  "version": "1.6.0",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libngtcp2.a": "%s",\n    "lib/libngtcp2_crypto_quictls.a": "%s",\n    "include/ngtcp2/ngtcp2.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${NGTCP2_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${crypto_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /ngtcp2/cache-receipt.json

FROM scratch
COPY --from=builder /ngtcp2 /ngtcp2
