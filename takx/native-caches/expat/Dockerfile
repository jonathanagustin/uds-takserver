# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM docker.io/library/gcc@sha256:531d94a9eb84cb69df20050ddde80d308bf8650c83f4cb0accf4ef39bb543ed4 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG EXPAT_URL="https://github.com/libexpat/libexpat/releases/download/R_2_10_0/expat-2.10.0.tar.xz"
ARG EXPAT_SHA256="c624ba2998fc65df1163a49641ff3f9ab73138b11641d92c86bee0fb01b1bc7f"
ARG COPYING_SHA256="31b15de82aa19a845156169a17a5488bf597e561b2c318d159ed583139b25e87"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200 \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
    CXXFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"

COPY expat_control.c /build/expat_control.c
WORKDIR /build/work

RUN set -x; \
    curl -fsSL "${EXPAT_URL}" -o /build/expat.tar.xz; \
    echo "${EXPAT_SHA256}  /build/expat.tar.xz" | sha256sum -c -; \
    tar -xJf /build/expat.tar.xz -C /build; \
    echo "${COPYING_SHA256}  /build/expat-2.10.0/COPYING" | sha256sum -c -; \
    mkdir -p /expat; \
    /build/expat-2.10.0/configure \
      --disable-shared \
      --enable-static \
      --with-pic \
      --without-xmlwf \
      --without-examples \
      --without-tests \
      --without-docbook \
      --prefix=/expat; \
    make -j2; \
    make install; \
    cp /build/expat-2.10.0/COPYING /expat/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/expat/include /build/expat_control.c /expat/lib/libexpat.a \
      -o /build/expat_control; \
    /build/expat_control; \
    gcc -shared -o /build/libexpat_whole.so \
      -Wl,--whole-archive /expat/lib/libexpat.a -Wl,--no-whole-archive \
      -Wl,--no-undefined -lm; \
    if readelf -d /build/libexpat_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libexpat_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /expat/lib/libexpat.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /expat/include/expat.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "expat",\n  "version": "2.10.0",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libexpat.a": "%s",\n    "include/expat.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${EXPAT_SHA256}" "${COPYING_SHA256}" "${lib_sha}" "${hdr_sha}" "${COPYING_SHA256}" \
      > /expat/cache-receipt.json

FROM scratch
COPY --from=builder /expat /expat
