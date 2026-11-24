# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Multi-stage builder for the prebuilt TAKX PROJ 4.9.1 static PIC OCI cache:
# - Uses the exact digest-pinned GCC 14.2.0 compiler image from geos-cache.
# - Verifies exact archive and COPYING SHA-256 hashes from takx/native-proj/inputs.lock.json.
# - Builds untouched PROJ 4.9.1 (--without-jni --disable-shared --enable-static --with-pic).
# - Runs the genuine C consumer and whole-archive --no-undefined / no-TEXTREL shared link check.
# - Exports only /proj into a minimal scratch OCI artifact.

FROM docker.io/library/gcc@sha256:531d94a9eb84cb69df20050ddde80d308bf8650c83f4cb0accf4ef39bb543ed4 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG PROJ_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/takthirdparty/distfiles/proj.tar.gz"
ARG PROJ_SHA256="fca0388f3f8bc5a1a803d2f6ff30017532367992b30cf144f2d39be88f36c319"
ARG COPYING_SHA256="2dcac4af6990093ef48b896bfb81452d46a31b08d46cce34b27e83f93b2e37f6"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200 \
    CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
    CXXFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"

COPY proj_control.c /build/proj_control.c
WORKDIR /build/work

RUN set -x; \
    curl -fsSL "${PROJ_URL}" -o /build/proj.tar.gz; \
    echo "${PROJ_SHA256}  /build/proj.tar.gz" | sha256sum -c -; \
    tar -xzf /build/proj.tar.gz -C /build; \
    echo "${COPYING_SHA256}  /build/proj-4.9.1/COPYING" | sha256sum -c -; \
    mkdir -p /proj; \
    /build/proj-4.9.1/configure \
      --without-jni \
      --disable-shared \
      --enable-static \
      --with-pic \
      --prefix=/proj; \
    make -j2; \
    make install; \
    cp /build/proj-4.9.1/COPYING /proj/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/proj/include /build/proj_control.c /proj/lib/libproj.a -lm \
      -o /build/proj_control; \
    /build/proj_control; \
    gcc -shared -o /build/libproj_whole.so \
      -Wl,--whole-archive /proj/lib/libproj.a -Wl,--no-whole-archive \
      -Wl,--no-undefined -lm; \
    if readelf -d /build/libproj_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libproj_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /proj/lib/libproj.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /proj/include/proj_api.h | awk '{print $1}')"; \
    lic_sha="$(sha256sum /proj/COPYING | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-proj-cache-v1",\n  "component": "proj",\n  "version": "4.9.1",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "libproj_a_sha256": "%s",\n  "proj_api_h_sha256": "%s",\n  "controls": {\n    "proj_static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${PROJ_SHA256}" "${lic_sha}" "${lib_sha}" "${hdr_sha}" \
      > /proj/proj-receipt.json

FROM scratch
COPY --from=builder /proj /proj
