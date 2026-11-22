# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG SQLITE3_URL="https://sqlite.org/2024/sqlite-autoconf-3460000.tar.gz"
ARG SQLITE3_SHA256="6f8e6a7b335273748816f9b3b62bbdc372a889de8782d7f048c653a447417a7d"
ARG SQLITE3_SHA512="631ffe4b39dffbafdcb8ac09a6a84cd7959505ecc588d8ad9278d0ff0c3ea467f87c11167e1b1a3f56d62178e679780e2be313ae3badae8ea056709d71bd4817"
ARG LICENSE_SHA256="409765acd2efdd885e9251f1aee9214969f9a82cb92bc7bcbb35fe5e71cea1ef"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY sqlite3_control.c /build/sqlite3_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/sqlite3.tar.gz")
urllib.request.urlretrieve(os.environ["SQLITE3_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["SQLITE3_SHA256"]:
    raise SystemExit(f"sqlite3 SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["SQLITE3_SHA512"]:
    raise SystemExit(f"sqlite3 SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")

lic = hashlib.sha256(Path("/build/sqlite-autoconf-3460000/README.txt").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"sqlite3 README.txt SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    cd /build/sqlite-autoconf-3460000; \
    ./configure \
      --prefix=/sqlite3 \
      --enable-static \
      --disable-shared \
      --with-pic \
      --enable-fts5 \
      --enable-rtree \
      --enable-math \
      CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic -DSQLITE_ENABLE_COLUMN_METADATA=1 -DSQLITE_ENABLE_JSON1=1"; \
    make -j4; \
    make install; \
    cp /build/sqlite-autoconf-3460000/README.txt /sqlite3/README.txt; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/sqlite3/include /build/sqlite3_control.c /sqlite3/lib/libsqlite3.a -lpthread -ldl -lm \
      -o /build/sqlite3_control; \
    /build/sqlite3_control; \
    gcc -shared -o /build/libsqlite3_whole.so \
      -Wl,--whole-archive /sqlite3/lib/libsqlite3.a -Wl,--no-whole-archive \
      -Wl,--no-undefined -lpthread -ldl -lm; \
    if readelf -d /build/libsqlite3_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libsqlite3_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /sqlite3/lib/libsqlite3.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /sqlite3/include/sqlite3.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "sqlite3",\n  "version": "3.46.0",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libsqlite3.a": "%s",\n    "include/sqlite3.h": "%s",\n    "README.txt": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${SQLITE3_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /sqlite3/cache-receipt.json

FROM scratch
COPY --from=builder /sqlite3 /sqlite3
