# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-sqlcipher:4.5.7-amd64 AS sqlcipher_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-sqlite3:3.46.0-amd64 AS sqlite3_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-openssl:3.5.9-amd64 AS openssl_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-proj:4.9.1-amd64 AS proj_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-geos:3.4.3-amd64 AS geos_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-libiconv:1.15-amd64 AS libiconv_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-libxml2:2.13.3-amd64 AS libxml2_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-zlib:1.3.1-amd64 AS zlib_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG SPATIALITE_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/libspatialite.tar.gz"
ARG SPATIALITE_SHA256="88900030a4762904a7880273f292e5e8ca6b15b7c6c3fb88ffa9e67ee8a5a499"
ARG LICENSE_SHA256="b81965482914858383484d7e63b5de1d0c4647fa7f527e78d4499e4a7b76aab5"
ARG PATCH_URL="https://raw.githubusercontent.com/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/libspatialite-pgsc.patch"
ARG PATCH_RAW_SHA256="44b02d5e1506d93036b1823ac4b257a2fc642d80a7b9d9b2248dae5b68f068cb"
ARG PATCH_NORM_SHA256="2bd6c395c2d9adab613d04819081c978fa57d263b3258804c64c35b19a91a99a"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY --from=sqlcipher_cache /sqlcipher /opt/sqlcipher
COPY --from=sqlite3_cache /sqlite3/include/sqlite3ext.h /opt/sqlcipher/include/sqlite3ext.h
COPY --from=openssl_cache /openssl /opt/openssl
COPY --from=proj_cache /proj /opt/proj
COPY --from=geos_cache /geos /geos
COPY --from=libiconv_cache /libiconv /opt/libiconv
COPY --from=libxml2_cache /libxml2 /opt/libxml2
COPY --from=zlib_cache /zlib /opt/zlib
COPY spatialite_control.c /build/spatialite_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/libspatialite.tar.gz")
urllib.request.urlretrieve(os.environ["SPATIALITE_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["SPATIALITE_SHA256"]:
    raise SystemExit(f"libspatialite SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
dest.unlink()

lic = hashlib.sha256(Path("/build/libspatialite-4.3.0a/COPYING").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"libspatialite COPYING SHA-256 mismatch: {lic}")

pdest = Path("/build/libspatialite-pgsc.patch")
urllib.request.urlretrieve(os.environ["PATCH_URL"], pdest)
raw = pdest.read_bytes()
got_raw = hashlib.sha256(raw).hexdigest()
if got_raw != os.environ["PATCH_RAW_SHA256"]:
    raise SystemExit(f"libspatialite patch raw SHA-256 mismatch: {got_raw}")

old_ctx = b" \t| d10v | d30v | dlx | dsp16xx | dvp \\\n"
new_ctx = b" \t| d10v | d30v | dlx | dsp16xx \\\n"
if raw.count(old_ctx) != 1:
    raise SystemExit("unexpected context match count in libspatialite-pgsc.patch")
norm = raw.replace(old_ctx, new_ctx, 1)
got_norm = hashlib.sha256(norm).hexdigest()
if got_norm != os.environ["PATCH_NORM_SHA256"]:
    raise SystemExit(f"libspatialite patch normalized SHA-256 mismatch: {got_norm}")
pdest.write_bytes(norm)
PY

RUN set -x; \
    patch -d /build/libspatialite-4.3.0a --fuzz=0 --batch --no-backup-if-mismatch -p1 -i /build/libspatialite-pgsc.patch; \
    gcc -O2 -o /build/connection-cache-generator \
      /build/libspatialite-4.3.0a/src/connection_cache/generator/code_generator.c; \
    mkdir -p /build/generated-cache; \
    (cd /build/generated-cache && /build/connection-cache-generator 1024); \
    rm -f /build/libspatialite-4.3.0a/src/connection_cache/cache_aux_*.h; \
    cp /build/generated-cache/cache_aux_*.h /build/libspatialite-4.3.0a/src/connection_cache/; \
    mkdir -p /build/out; \
    cd /build/out; \
    CONFIG_SITE=/dev/null \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
    CPPFLAGS="-I/opt/zlib/include -I/opt/sqlcipher/include -I/geos/include -I/opt/proj/include -I/opt/libiconv/include -I/opt/libxml2/include/libxml2 -I/opt/openssl/include" \
    LDFLAGS="-L/geos/lib -L/opt/proj/lib -L/opt/sqlcipher/lib -L/opt/openssl/lib -L/opt/libiconv/lib -L/opt/libxml2/lib -L/opt/zlib/lib" \
    LIBS="-L/opt/libxml2/lib -L/opt/libiconv/lib -L/opt/zlib/lib -L/geos/lib -L/opt/proj/lib -L/opt/sqlcipher/lib -L/opt/openssl/lib -lxml2 -liconv -lz -lgeos_c -lgeos -lproj -lsqlite3 -lcrypto -lstdc++ -ldl -pthread -lm" \
    LIBXML2_CFLAGS="-I/opt/libxml2/include/libxml2" \
    LIBXML2_LIBS="-L/opt/libxml2/lib -lxml2 -L/opt/libiconv/lib -liconv" \
    PKG_CONFIG_LIBDIR="/opt/libxml2/lib/pkgconfig" \
    /build/libspatialite-4.3.0a/configure \
      --prefix=/spatialite \
      --disable-shared \
      --enable-static \
      --enable-geos \
      --enable-proj \
      --enable-iconv \
      --enable-libxml2 \
      --disable-freexl \
      --disable-examples \
      --with-geosconfig=/geos/bin/geos-config; \
    make -j4; \
    make install; \
    cp /build/libspatialite-4.3.0a/COPYING /spatialite/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/spatialite/include -I/opt/sqlcipher/include \
      /build/spatialite_control.c \
      /spatialite/lib/libspatialite.a \
      /opt/libxml2/lib/libxml2.a \
      /opt/libiconv/lib/libiconv.a \
      /opt/zlib/lib/libz.a \
      /geos/lib/libgeos_c.a \
      /geos/lib/libgeos.a \
      /opt/proj/lib/libproj.a \
      /opt/sqlcipher/lib/libsqlite3.a \
      /opt/openssl/lib/libcrypto.a \
      -lstdc++ -lpthread -ldl -lm \
      -o /build/spatialite_control; \
    /build/spatialite_control; \
    g++ -shared -o /build/libspatialite_whole.so \
      -Wl,--whole-archive /spatialite/lib/libspatialite.a -Wl,--no-whole-archive \
      /opt/libxml2/lib/libxml2.a \
      /opt/libiconv/lib/libiconv.a \
      /opt/zlib/lib/libz.a \
      /geos/lib/libgeos_c.a \
      /geos/lib/libgeos.a \
      /opt/proj/lib/libproj.a \
      /opt/sqlcipher/lib/libsqlite3.a \
      /opt/openssl/lib/libcrypto.a \
      -Wl,--no-undefined -lpthread -ldl -lm; \
    if readelf -d /build/libspatialite_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libspatialite_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /spatialite/lib/libspatialite.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /spatialite/include/spatialite.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "spatialite",\n  "version": "4.3.0a",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libspatialite.a": "%s",\n    "include/spatialite.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${SPATIALITE_SHA256}" "${LICENSE_SHA256}" \
      "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /spatialite/cache-receipt.json

FROM scratch
COPY --from=builder /spatialite /spatialite
