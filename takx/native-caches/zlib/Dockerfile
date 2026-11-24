# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM docker.io/library/gcc@sha256:531d94a9eb84cb69df20050ddde80d308bf8650c83f4cb0accf4ef39bb543ed4 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG ZLIB_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/zlib.tar.gz"
ARG ZLIB_SHA256="9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23"
ARG LICENSE_SHA256="845efc77857d485d91fb3e0b884aaa929368c717ae8186b66fe1ed2495753243"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200 \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"

COPY zlib_control.c /build/zlib_control.c
WORKDIR /build/zlib-1.3.1

RUN set -x; \
    curl -fsSL "${ZLIB_URL}" -o /build/zlib.tar.gz; \
    echo "${ZLIB_SHA256}  /build/zlib.tar.gz" | sha256sum -c -; \
    tar -xzf /build/zlib.tar.gz -C /build; \
    echo "${LICENSE_SHA256}  /build/zlib-1.3.1/LICENSE" | sha256sum -c -; \
    mkdir -p /zlib; \
    ./configure --static --prefix=/zlib; \
    make -j2; \
    make install; \
    cp /build/zlib-1.3.1/LICENSE /zlib/LICENSE; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/zlib/include /build/zlib_control.c /zlib/lib/libz.a \
      -o /build/zlib_control; \
    /build/zlib_control; \
    gcc -shared -o /build/libz_whole.so \
      -Wl,--whole-archive /zlib/lib/libz.a -Wl,--no-whole-archive \
      -Wl,--no-undefined; \
    if readelf -d /build/libz_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libz_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /zlib/lib/libz.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /zlib/include/zlib.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "zlib",\n  "version": "1.3.1",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libz.a": "%s",\n    "include/zlib.h": "%s",\n    "LICENSE": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${ZLIB_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /zlib/cache-receipt.json

FROM scratch
COPY --from=builder /zlib /zlib
