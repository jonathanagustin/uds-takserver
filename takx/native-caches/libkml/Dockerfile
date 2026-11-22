# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-expat:2.10.0-amd64 AS expat_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-zlib:1.3.1-amd64 AS zlib_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-uriparser:0.9.8-amd64 AS uriparser_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG LIBKML_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/libkml.tar.gz"
ARG LIBKML_SHA256="39692c0184933424e35ef69e5101f1ff0d664f2b50b972887cfc15fda032d1cb"
ARG LICENSE_SHA256="e7952c5e1745f629c235c7aacd066fc36034d37571a964b9142d7fb95b5523eb"
ARG LIBKML_PATCH_SHA256="f8048d5f203888986302aedae8aeb586c983673a5457746229b1262490f30144"
ARG MINIZIP_PATCH_SHA256="2e8743a3ac4b56070658ce9c80a1a12b0a46202588acec95d54750f4b2d40a58"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY --from=expat_cache /expat /opt/expat
COPY --from=zlib_cache /zlib /opt/zlib
COPY --from=uriparser_cache /uriparser /opt/uriparser
COPY CMakeLists.txt /build/CMakeLists.txt
COPY libkml_control.cpp /build/libkml_control.cpp
COPY patches/libkml-pgsc.patch /build/patches/libkml-pgsc.patch
COPY patches/minizip-zlib-crc-type.patch /build/patches/minizip-zlib-crc-type.patch
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

for rel, env_key in (
    ("/build/patches/libkml-pgsc.patch", "LIBKML_PATCH_SHA256"),
    ("/build/patches/minizip-zlib-crc-type.patch", "MINIZIP_PATCH_SHA256"),
):
    got = hashlib.sha256(Path(rel).read_bytes()).hexdigest()
    if got != os.environ[env_key]:
        raise SystemExit(f"patch {rel} SHA-256 mismatch: {got}")

dest = Path("/build/libkml.tar.gz")
urllib.request.urlretrieve(os.environ["LIBKML_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["LIBKML_SHA256"]:
    raise SystemExit(f"libkml SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")

lic = hashlib.sha256(Path("/build/libkml-1.3/COPYING").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"libkml COPYING SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    patch --batch --fuzz=0 -p0 -d /build/libkml-1.3 -i /build/patches/libkml-pgsc.patch; \
    patch --batch --fuzz=0 -p1 -d /build/libkml-1.3/third_party/zlib-1.2.3/contrib/minizip \
      -i /build/patches/minizip-zlib-crc-type.patch; \
    mkdir -p /build/minizip /libkml/lib /libkml/include; \
    mz_dir="/build/libkml-1.3/third_party/zlib-1.2.3/contrib/minizip"; \
    for tu in ioapi iomem_simple unzip zip; do \
      gcc -O2 -fPIC -march=x86-64 -mtune=generic \
        -I/opt/zlib/include -I"${mz_dir}" \
        -c "${mz_dir}/${tu}.c" -o "/build/minizip/${tu}.o"; \
    done; \
    ar rcs /libkml/lib/libminizip.a \
      /build/minizip/ioapi.o \
      /build/minizip/iomem_simple.o \
      /build/minizip/unzip.o \
      /build/minizip/zip.o; \
    cmake -S /build -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/libkml \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DCMAKE_CXX_STANDARD=17 \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      -DKML_SOURCE=/build/libkml-1.3 \
      -DEXPAT_PREFIX=/opt/expat \
      -DBOOST_SOURCE=/build/libkml-1.3/third_party/boost_1_34_1 \
      -DMINIZIP_ARCHIVE=/libkml/lib/libminizip.a \
      -DMINIZIP_INCLUDE=/build/libkml-1.3/third_party/zlib-1.2.3/contrib \
      -DZLIB_PREFIX=/opt/zlib \
      -DURIPARSER_PREFIX=/opt/uriparser; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp -a /build/libkml-1.3/third_party/boost_1_34_1/boost /libkml/include/boost; \
    cp /build/libkml-1.3/COPYING /libkml/COPYING; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -I/libkml/include -I/opt/expat/include -I/opt/uriparser/include -I/opt/zlib/include \
      /build/libkml_control.cpp \
      /libkml/lib/libkmlengine.a \
      /libkml/lib/libkmldom.a \
      /libkml/lib/libkmlbase.a \
      /libkml/lib/libminizip.a \
      /opt/expat/lib/libexpat.a \
      /opt/uriparser/lib/liburiparser.a \
      /opt/zlib/lib/libz.a \
      -lm \
      -o /build/libkml_control; \
    /build/libkml_control; \
    g++ -shared -o /build/libkml_whole.so \
      -Wl,--whole-archive \
      /libkml/lib/libkmlregionator.a \
      /libkml/lib/libkmlconvenience.a \
      /libkml/lib/libkmlengine.a \
      /libkml/lib/libkmldom.a \
      /libkml/lib/libkmlxsd.a \
      /libkml/lib/libkmlbase.a \
      /libkml/lib/libminizip.a \
      -Wl,--no-whole-archive \
      /opt/expat/lib/libexpat.a \
      /opt/uriparser/lib/liburiparser.a \
      /opt/zlib/lib/libz.a \
      -Wl,--no-undefined -lm; \
    if readelf -d /build/libkml_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libkml_whole.so" >&2; \
      exit 1; \
    fi; \
    base_sha="$(sha256sum /libkml/lib/libkmlbase.a | awk '{print $1}')"; \
    dom_sha="$(sha256sum /libkml/lib/libkmldom.a | awk '{print $1}')"; \
    engine_sha="$(sha256sum /libkml/lib/libkmlengine.a | awk '{print $1}')"; \
    conv_sha="$(sha256sum /libkml/lib/libkmlconvenience.a | awk '{print $1}')"; \
    reg_sha="$(sha256sum /libkml/lib/libkmlregionator.a | awk '{print $1}')"; \
    xsd_sha="$(sha256sum /libkml/lib/libkmlxsd.a | awk '{print $1}')"; \
    mz_sha="$(sha256sum /libkml/lib/libminizip.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /libkml/include/kml/dom.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "libkml",\n  "version": "1.3.0",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libkmlbase.a": "%s",\n    "lib/libkmldom.a": "%s",\n    "lib/libkmlengine.a": "%s",\n    "lib/libkmlconvenience.a": "%s",\n    "lib/libkmlregionator.a": "%s",\n    "lib/libkmlxsd.a": "%s",\n    "lib/libminizip.a": "%s",\n    "include/kml/dom.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${LIBKML_SHA256}" "${LICENSE_SHA256}" \
      "${base_sha}" "${dom_sha}" "${engine_sha}" "${conv_sha}" "${reg_sha}" "${xsd_sha}" "${mz_sha}" \
      "${hdr_sha}" "${LICENSE_SHA256}" \
      > /libkml/cache-receipt.json

FROM scratch
COPY --from=builder /libkml /libkml
