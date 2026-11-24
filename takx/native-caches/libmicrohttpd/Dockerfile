# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM docker.io/library/gcc@sha256:531d94a9eb84cb69df20050ddde80d308bf8650c83f4cb0accf4ef39bb543ed4 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG MHD_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/libmicrohttpd.tar.gz"
ARG MHD_SHA256="a89e09fc9b4de34dde19f4fcb4faaa1ce10299b9908db1132bbfa1de47882b94"
ARG LICENSE_SHA256="7399547209438c93f9b90297954698773d4846cea44cde5ca982c84c45952a3b"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200 \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"

COPY libmicrohttpd_control.c /build/libmicrohttpd_control.c
WORKDIR /build/libmicrohttpd-1.0.1

RUN set -x; \
    curl -fsSL "${MHD_URL}" -o /build/libmicrohttpd.tar.gz; \
    echo "${MHD_SHA256}  /build/libmicrohttpd.tar.gz" | sha256sum -c -; \
    tar -xzf /build/libmicrohttpd.tar.gz -C /build; \
    echo "${LICENSE_SHA256}  /build/libmicrohttpd-1.0.1/COPYING" | sha256sum -c -; \
    mkdir -p /libmicrohttpd; \
    ./configure \
      --prefix=/libmicrohttpd \
      --disable-shared \
      --enable-static \
      --disable-epoll \
      --disable-https \
      --disable-curl \
      --disable-postprocessor \
      --disable-dauth; \
    make -j4; \
    make install; \
    cp /build/libmicrohttpd-1.0.1/COPYING /libmicrohttpd/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/libmicrohttpd/include /build/libmicrohttpd_control.c \
      /libmicrohttpd/lib/libmicrohttpd.a -lpthread \
      -o /build/libmicrohttpd_control; \
    /build/libmicrohttpd_control; \
    gcc -shared -o /build/libmicrohttpd_whole.so \
      -Wl,--whole-archive /libmicrohttpd/lib/libmicrohttpd.a -Wl,--no-whole-archive \
      -Wl,--no-undefined -lpthread; \
    if readelf -d /build/libmicrohttpd_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libmicrohttpd_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /libmicrohttpd/lib/libmicrohttpd.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /libmicrohttpd/include/microhttpd.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "libmicrohttpd",\n  "version": "1.0.1",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libmicrohttpd.a": "%s",\n    "include/microhttpd.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${MHD_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /libmicrohttpd/cache-receipt.json

FROM scratch
COPY --from=builder /libmicrohttpd /libmicrohttpd
