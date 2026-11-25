# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG ABSEIL_URL="https://github.com/abseil/abseil-cpp/archive/20240116.2.tar.gz"
ARG ABSEIL_SHA256="733726b8c3a6d39a4120d7e45ea8b41a434cdacde401cba500f14236c49b39dc"
ARG ABSEIL_SHA512="5062e731ee8c9a757e6d75fc1c558652deb4dd1daab4d6143f7ad52a139501c61365f89acbf82480be0f9a4911a58286560068d8b1a8b6774e6afad51739766e"
ARG LICENSE_SHA256="c79a7fea0e3cac04cd43f20e7b648e5a0ff8fa5344e644b0ee09ca1162b62747"
ARG PATCH1_URL="https://raw.githubusercontent.com/microsoft/vcpkg/1de2026f28ead93ff1773e6e680387643e914ea1/ports/abseil/0001-revert-integer-to-string-conversion-optimizations.patch"
ARG PATCH1_SHA256="bb3268f711e0ae0c33e53bee28b94aa36ce62620bb00dc2f198733b62c015a9d"
ARG PATCH2_URL="https://raw.githubusercontent.com/microsoft/vcpkg/1de2026f28ead93ff1773e6e680387643e914ea1/ports/abseil/0002-Fix-missing-include-random-for-std-uniform_int_distr.patch"
ARG PATCH2_SHA256="bcab8c17f86ed4a60f61a708e3754b90af8e38352ff8eb2b3825a6ad40cc3272"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY abseil_control.cpp /build/abseil_control.cpp
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/abseil.tar.gz")
urllib.request.urlretrieve(os.environ["ABSEIL_URL"], dest)
raw = dest.read_bytes()
if hashlib.sha256(raw).hexdigest() != os.environ["ABSEIL_SHA256"]:
    raise SystemExit("abseil SHA-256 mismatch")
if hashlib.sha512(raw).hexdigest() != os.environ["ABSEIL_SHA512"]:
    raise SystemExit("abseil SHA-512 mismatch")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")

lic = hashlib.sha256(Path("/build/abseil-cpp-20240116.2/LICENSE").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"abseil LICENSE SHA-256 mismatch: {lic}")

for idx in ("1", "2"):
    pdest = Path(f"/build/abseil_{idx}.patch")
    urllib.request.urlretrieve(os.environ[f"PATCH{idx}_URL"], pdest)
    if hashlib.sha256(pdest.read_bytes()).hexdigest() != os.environ[f"PATCH{idx}_SHA256"]:
        raise SystemExit(f"abseil patch {idx} SHA-256 mismatch")
PY

RUN set -x; \
    patch -d /build/abseil-cpp-20240116.2 --fuzz=0 --batch --no-backup-if-mismatch -p1 -i /build/abseil_1.patch; \
    patch -d /build/abseil-cpp-20240116.2 --fuzz=0 --batch --no-backup-if-mismatch -p1 -i /build/abseil_2.patch; \
    cmake -S /build/abseil-cpp-20240116.2 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/abseil \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_CXX_STANDARD=17 \
      -DABSL_PROPAGATE_CXX_STD=ON \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DBUILD_TESTING=OFF \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/abseil-cpp-20240116.2/LICENSE /abseil/LICENSE; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -I/abseil/include /build/abseil_control.cpp \
      /abseil/lib/libabsl_strings.a \
      /abseil/lib/libabsl_strings_internal.a \
      /abseil/lib/libabsl_base.a \
      /abseil/lib/libabsl_raw_logging_internal.a \
      /abseil/lib/libabsl_throw_delegate.a \
      /abseil/lib/libabsl_int128.a \
      /abseil/lib/libabsl_spinlock_wait.a \
      -lpthread \
      -o /build/abseil_control; \
    /build/abseil_control; \
    g++ -shared -o /build/libabsl_strings_whole.so \
      -Wl,--whole-archive /abseil/lib/libabsl_strings.a -Wl,--no-whole-archive \
      /abseil/lib/libabsl_strings_internal.a \
      /abseil/lib/libabsl_base.a \
      /abseil/lib/libabsl_raw_logging_internal.a \
      /abseil/lib/libabsl_throw_delegate.a \
      /abseil/lib/libabsl_int128.a \
      /abseil/lib/libabsl_spinlock_wait.a \
      -Wl,--no-undefined -lpthread; \
    if readelf -d /build/libabsl_strings_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libabsl_strings_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /abseil/lib/libabsl_strings.a | awk '{print $1}')"; \
    base_sha="$(sha256sum /abseil/lib/libabsl_base.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /abseil/include/absl/strings/str_cat.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "abseil",\n  "version": "20240116.2",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libabsl_strings.a": "%s",\n    "lib/libabsl_base.a": "%s",\n    "include/absl/strings/str_cat.h": "%s",\n    "LICENSE": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${ABSEIL_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${base_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /abseil/cache-receipt.json

FROM scratch
COPY --from=builder /abseil /abseil
