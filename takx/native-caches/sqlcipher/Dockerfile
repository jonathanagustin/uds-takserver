# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-openssl:3.5.9-amd64 AS openssl_cache
FROM docker.io/library/gcc@sha256:82549aa8f90ada3236a8be70c74543132a76662ef33f0c3271ed802b81584a82 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG TCL_URL="https://prdownloads.sourceforge.net/tcl/tcl8.6.18-src.tar.gz"
ARG TCL_SHA256="14f9af32b1767ff718477a8f974ad03c34341097e6b43f4ce54644ee974e268e"
ARG SQLCIPHER_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/sqlcipher.tar.gz"
ARG SQLCIPHER_SHA256="b670845f28da0a3c717e991e9f18a334e08f6bc977190bbce6be864ca229f96d"
ARG LICENSE_SHA256="09e4af560ce2e3c9c2aa6b564e35947b03db7d1ae345f22a32793ed46542cc14"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY --from=openssl_cache /openssl /opt/openssl
COPY sqlcipher_control.c /build/sqlcipher_control.c
WORKDIR /build/tcl-work

RUN set -x; \
    curl -fsSL "${TCL_URL}" -o /build/tcl.tar.gz; \
    echo "${TCL_SHA256}  /build/tcl.tar.gz" | sha256sum -c -; \
    tar -xzf /build/tcl.tar.gz -C /build; \
    /build/tcl8.6.18/unix/configure --prefix=/opt/tcl --disable-shared --enable-64bit; \
    make -j4; \
    make install

WORKDIR /build/sqlcipher-work

RUN set -x; \
    curl -fsSL "${SQLCIPHER_URL}" -o /build/sqlcipher.tar.gz; \
    echo "${SQLCIPHER_SHA256}  /build/sqlcipher.tar.gz" | sha256sum -c -; \
    tar -xzf /build/sqlcipher.tar.gz -C /build; \
    echo "${LICENSE_SHA256}  /build/sqlcipher-4.5.7/LICENSE.md" | sha256sum -c -; \
    FLAGS="-DSQLCIPHER_CRYPTO_OPENSSL -DSQLITE_THREADSAFE=1 -DSQLITE_ENABLE_MEMORY_MANAGEMENT=1 -DSQLITE_DEFAULT_JOURNAL_SIZE_LIMIT=1048576 -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_FTS3_PARENTHESIS -DSQLITE_ENABLE_RTREE=1 -DSQLITE_TEMP_STORE=3 -DSQLITE_HAS_CODEC"; \
    TCLSH_CMD=/opt/tcl/bin/tclsh8.6 \
    CPPFLAGS="-I/opt/openssl/include ${FLAGS}" \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
    LDFLAGS="-L/opt/openssl/lib" \
    LIBS="/opt/openssl/lib/libcrypto.a -ldl -lpthread" \
      /build/sqlcipher-4.5.7/configure \
      --disable-shared --enable-static --disable-tcl --disable-readline \
      --with-crypto-lib=openssl --prefix=/sqlcipher; \
    make -j2 sqlite3.c sqlite3.h TCLSH_CMD=/opt/tcl/bin/tclsh8.6; \
    mkdir -p /sqlcipher/lib /sqlcipher/include; \
    gcc -O2 -fPIC -march=x86-64 -mtune=generic -I/opt/openssl/include ${FLAGS} \
      -c sqlite3.c -o sqlite3.o; \
    ar rcs /sqlcipher/lib/libsqlite3.a sqlite3.o; \
    cp sqlite3.h /sqlcipher/include/sqlite3.h; \
    cp /build/sqlcipher-4.5.7/LICENSE.md /sqlcipher/LICENSE.md; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 -DSQLITE_HAS_CODEC \
      -I/sqlcipher/include -I/opt/openssl/include /build/sqlcipher_control.c \
      /sqlcipher/lib/libsqlite3.a /opt/openssl/lib/libcrypto.a -ldl -lpthread -lm \
      -o /build/sqlcipher_control; \
    /build/sqlcipher_control; \
    gcc -shared -o /build/libsqlcipher_whole.so \
      -Wl,--whole-archive /sqlcipher/lib/libsqlite3.a -Wl,--no-whole-archive \
      /opt/openssl/lib/libcrypto.a -Wl,--no-undefined -ldl -lpthread -lm; \
    if readelf -d /build/libsqlcipher_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libsqlcipher_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /sqlcipher/lib/libsqlite3.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /sqlcipher/include/sqlite3.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "sqlcipher",\n  "version": "4.5.7",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libsqlite3.a": "%s",\n    "include/sqlite3.h": "%s",\n    "LICENSE.md": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${SQLCIPHER_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /sqlcipher/cache-receipt.json

FROM scratch
COPY --from=builder /sqlcipher /sqlcipher
