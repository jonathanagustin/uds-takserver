# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG LASZIP_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/depends/LASzip-3.4.3-mod.tar.gz"
ARG LASZIP_SHA256="66336908e17bb92739803c80fe627461860cacb017dcff55e476e131981e7b6d"
ARG LICENSE_SHA256="dbc8eab1421212bf7b392ea00619f6b8286df50b702d21f6e7382805828a1cef"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY laszip_control.c /build/laszip_control.c
COPY laszip-init-failure-upstream-backport.patch /build/laszip.patch
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/laszip.tar.gz")
urllib.request.urlretrieve(os.environ["LASZIP_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["LASZIP_SHA256"]:
    raise SystemExit(f"LASzip SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(Path("/build/LASzip/COPYING").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"LASzip COPYING SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    patch -d /build/LASzip --fuzz=0 --batch --no-backup-if-mismatch -p1 -i /build/laszip.patch; \
    cmake -S /build/LASzip -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/laszip \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DLASZIP_BUILD_STATIC=ON \
      -DCMAKE_C_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/LASzip/COPYING /laszip/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/laszip/include /build/laszip_control.c /laszip/lib/liblaszip.a -lstdc++ -lm \
      -o /build/laszip_control; \
    /build/laszip_control; \
    lib_sha="$(sha256sum /laszip/lib/liblaszip.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /laszip/include/laszip/laszip_api.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "laszip",\n  "version": "3.4.3",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/liblaszip.a": "%s",\n    "include/laszip/laszip_api.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${LASZIP_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /laszip/cache-receipt.json

FROM scratch
COPY --from=builder /laszip /laszip
