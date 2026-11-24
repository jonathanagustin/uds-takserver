# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM docker.io/library/gcc@sha256:82549aa8f90ada3236a8be70c74543132a76662ef33f0c3271ed802b81584a82 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG QUICTLS_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/openssl.tar.gz"
ARG QUICTLS_SHA256="75b0d9425c914913840d376388d8d8e6105ec8a03a9c33ab56fc3ac0384003a2"
ARG PATCH_URL="https://raw.githubusercontent.com/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/openssl-pgsc.patch"
ARG PATCH_SHA256="d53ed973b1aed04f07dce2b166a7801bedcc25ccc7ce45c1fd709b925e7f7f88"
ARG LICENSE_SHA256="7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY quictls_control.c /build/quictls_control.c
WORKDIR /build/openssl-openssl-3.0.14-quic1

RUN set -x; \
    curl -fsSL "${QUICTLS_URL}" -o /build/quictls.tar.gz; \
    echo "${QUICTLS_SHA256}  /build/quictls.tar.gz" | sha256sum -c -; \
    curl -fsSL "${PATCH_URL}" -o /build/openssl-pgsc.patch; \
    echo "${PATCH_SHA256}  /build/openssl-pgsc.patch" | sha256sum -c -; \
    tar -xzf /build/quictls.tar.gz -C /build; \
    echo "${LICENSE_SHA256}  /build/openssl-openssl-3.0.14-quic1/LICENSE.txt" | sha256sum -c -; \
    patch -d /build/openssl-openssl-3.0.14-quic1 --fuzz=0 --batch --no-backup-if-mismatch -p0 -i /build/openssl-pgsc.patch; \
    mkdir -p /quictls; \
    ./Configure linux-x86_64 no-shared no-tests no-dso \
      -fPIC -O2 -march=x86-64 -mtune=generic \
      --prefix=/quictls --libdir=lib; \
    make -j4 build_sw; \
    make install_sw; \
    cp /build/openssl-openssl-3.0.14-quic1/LICENSE.txt /quictls/LICENSE.txt; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/quictls/include /build/quictls_control.c \
      /quictls/lib/libssl.a /quictls/lib/libcrypto.a -lpthread -ldl \
      -o /build/quictls_control; \
    /build/quictls_control; \
    ssl_sha="$(sha256sum /quictls/lib/libssl.a | awk '{print $1}')"; \
    crypto_sha="$(sha256sum /quictls/lib/libcrypto.a | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "quictls",\n  "version": "3.0.14+quic1",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libssl.a": "%s",\n    "lib/libcrypto.a": "%s",\n    "LICENSE.txt": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${QUICTLS_SHA256}" "${LICENSE_SHA256}" "${ssl_sha}" "${crypto_sha}" "${LICENSE_SHA256}" \
      > /quictls/cache-receipt.json

FROM scratch
COPY --from=builder /quictls /quictls
