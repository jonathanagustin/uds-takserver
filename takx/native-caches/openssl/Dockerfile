# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM docker.io/library/gcc@sha256:9af0ba1a5281cac2a79aa718b6d21af8670322cf4378198ee306efd5acc1955f AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG OPENSSL_URL="https://github.com/openssl/openssl/releases/download/openssl-3.5.9/openssl-3.5.9.tar.gz"
ARG OPENSSL_SHA256="603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a"
ARG LICENSE_SHA256="7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY openssl_control.c /build/openssl_control.c
WORKDIR /build/openssl-3.5.9

RUN set -x; \
    curl -fsSL "${OPENSSL_URL}" -o /build/openssl.tar.gz; \
    echo "${OPENSSL_SHA256}  /build/openssl.tar.gz" | sha256sum -c -; \
    tar -xzf /build/openssl.tar.gz -C /build; \
    echo "${LICENSE_SHA256}  /build/openssl-3.5.9/LICENSE.txt" | sha256sum -c -; \
    mkdir -p /openssl; \
    ./Configure linux-x86_64 no-shared no-tests no-dso \
      -fPIC -O2 -march=x86-64 -mtune=generic \
      --prefix=/openssl --libdir=lib; \
    make -j4 build_sw; \
    make install_sw; \
    cp /build/openssl-3.5.9/LICENSE.txt /openssl/LICENSE.txt; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/openssl/include /build/openssl_control.c \
      /openssl/lib/libssl.a /openssl/lib/libcrypto.a -lpthread -ldl \
      -o /build/openssl_control; \
    /build/openssl_control; \
    ssl_sha="$(sha256sum /openssl/lib/libssl.a | awk '{print $1}')"; \
    crypto_sha="$(sha256sum /openssl/lib/libcrypto.a | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "openssl",\n  "version": "3.5.9",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libssl.a": "%s",\n    "lib/libcrypto.a": "%s",\n    "LICENSE.txt": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${OPENSSL_SHA256}" "${LICENSE_SHA256}" "${ssl_sha}" "${crypto_sha}" "${LICENSE_SHA256}" \
      > /openssl/cache-receipt.json

FROM scratch
COPY --from=builder /openssl /openssl
