# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM docker.io/library/gcc@sha256:82549aa8f90ada3236a8be70c74543132a76662ef33f0c3271ed802b81584a82 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG NGHTTP2_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/nghttp2.tar.bz2"
ARG NGHTTP2_SHA256="3966ec82fda7fc380506d372a260d8d9b6e946be4deaef1fecc1a74b4809ae3d"
ARG LICENSE_SHA256="6b94f3abc1aabd0c72a7c7d92a77f79dda7c8a0cb3df839a97890b4116a2de2a"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200 \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"

COPY nghttp2_control.c /build/nghttp2_control.c
WORKDIR /build/nghttp2-1.62.1

RUN set -x; \
    curl -fsSL "${NGHTTP2_URL}" -o /build/nghttp2.tar.bz2; \
    echo "${NGHTTP2_SHA256}  /build/nghttp2.tar.bz2" | sha256sum -c -; \
    tar -xjf /build/nghttp2.tar.bz2 -C /build; \
    echo "${LICENSE_SHA256}  /build/nghttp2-1.62.1/COPYING" | sha256sum -c -; \
    mkdir -p /nghttp2; \
    ./configure \
      --prefix=/nghttp2 \
      --enable-lib-only \
      --disable-shared \
      --enable-static; \
    make -j4; \
    make install; \
    cp /build/nghttp2-1.62.1/COPYING /nghttp2/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/nghttp2/include /build/nghttp2_control.c /nghttp2/lib/libnghttp2.a \
      -o /build/nghttp2_control; \
    /build/nghttp2_control; \
    gcc -shared -o /build/libnghttp2_whole.so \
      -Wl,--whole-archive /nghttp2/lib/libnghttp2.a -Wl,--no-whole-archive \
      -Wl,--no-undefined; \
    if readelf -d /build/libnghttp2_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libnghttp2_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /nghttp2/lib/libnghttp2.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /nghttp2/include/nghttp2/nghttp2.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "nghttp2",\n  "version": "1.62.1",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libnghttp2.a": "%s",\n    "include/nghttp2/nghttp2.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${NGHTTP2_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /nghttp2/cache-receipt.json

FROM scratch
COPY --from=builder /nghttp2 /nghttp2
