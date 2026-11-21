# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-zlib:1.3.1-amd64 AS zlib_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG ASSIMP_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/depends/assimp-4.0.1-mod.tar.gz"
ARG ASSIMP_SHA256="459eadafbb84251a93f94c70f2fd8922c169911ff89fe526f1529bac6f699ae0"
ARG LICENSE_SHA256="a26ccc3dbf2f58ea99c100945a8a126fa0f9f4d7fd2b49aa8bdb8e09355864d8"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY --from=zlib_cache /zlib /opt/zlib
COPY assimp_control.c /build/assimp_control.c
COPY assimp-public-static-core.patch /build/assimp.patch
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/assimp.tar.gz")
urllib.request.urlretrieve(os.environ["ASSIMP_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["ASSIMP_SHA256"]:
    raise SystemExit(f"Assimp SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/assimp/LICENSE").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"Assimp LICENSE SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    patch -d /build/assimp --fuzz=0 --batch --no-backup-if-mismatch -p1 -i /build/assimp.patch; \
    cmake -S /build/assimp -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/assimp \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DASSIMP_BUILD_TESTS=OFF \
      -DASSIMP_BUILD_ASSIMP_TOOLS=OFF \
      -DASSIMP_BUILD_SAMPLES=OFF \
      -DASSIMP_BUILD_ZLIB=OFF \
      -DASSIMP_INSTALL_PDB=OFF \
      -DASSIMP_DOUBLE_PRECISION=OFF \
      -DASSIMP_NO_EXPORT=OFF \
      -DASSIMP_BUILD_JASSIMP=OFF \
      -DSYSTEM_IRRXML=OFF \
      -DPUBLIC_ZLIB_PREFIX=/opt/zlib \
      -DZLIB_INCLUDE_DIR=/opt/zlib/include \
      -DZLIB_LIBRARIES=/opt/zlib/lib/libz.a \
      -DCMAKE_C_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    if [ -f /build/out/contrib/irrXML/libIrrXML.a ] && [ ! -f /assimp/lib/libIrrXML.a ]; then \
      cp /build/out/contrib/irrXML/libIrrXML.a /assimp/lib/libIrrXML.a; \
    fi; \
    cp /build/assimp/LICENSE /assimp/LICENSE; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/assimp/include /build/assimp_control.c \
      /assimp/lib/libassimp.a /assimp/lib/libIrrXML.a /opt/zlib/lib/libz.a \
      -lstdc++ -lm \
      -o /build/assimp_control; \
    /build/assimp_control; \
    lib_sha="$(sha256sum /assimp/lib/libassimp.a | awk '{print $1}')"; \
    irr_sha="$(sha256sum /assimp/lib/libIrrXML.a | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "assimp",\n  "version": "4.0.1",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libassimp.a": "%s",\n    "lib/libIrrXML.a": "%s",\n    "LICENSE": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${ASSIMP_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${irr_sha}" "${LICENSE_SHA256}" \
      > /assimp/cache-receipt.json

FROM scratch
COPY --from=builder /assimp /assimp
