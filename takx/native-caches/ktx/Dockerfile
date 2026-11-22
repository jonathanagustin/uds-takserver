# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-zstd:1.5.6-amd64 AS zstd_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG KTX_URL="https://github.com/KhronosGroup/KTX-Software/archive/v4.3.1.tar.gz"
ARG KTX_SHA256="98f051c91e723f85f3a5136a75dfa0ee186d72cc93223fd0daa1b3c8077ccb69"
ARG KTX_SHA512="8ecb10d188b42c2104ae1ad1df8ba01a86af8b9ff673d74a767f742d2b5aa7effdc4765c2c280df43d717af3a14765189421d6b162f39515972dc439bd624619"
ARG LICENSE_SHA256="0f92b4ce771585dfebc963ede074a30b4431b9e327911db44097d83ccf372128"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY --from=zstd_cache /zstd /opt/zstd
COPY ktx_control.cpp /build/ktx_control.cpp
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/ktx.tar.gz")
urllib.request.urlretrieve(os.environ["KTX_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["KTX_SHA256"]:
    raise SystemExit(f"ktx SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["KTX_SHA512"]:
    raise SystemExit(f"ktx SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")

lic = hashlib.sha256(Path("/build/KTX-Software-4.3.1/LICENSE.md").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"ktx LICENSE.md SHA-256 mismatch: {lic}")

patches = [
    ("0001-Use-vcpkg-zstd.patch", "44d95d0724f37b8a664e8bd742e29343db5264de4901649c2cb9daa33e90a69e"),
    ("0002-Fix-versioning.patch", "a3b482708c49efbb1b39fedd7a321c05b9f3d89f30362338765e398f31903438"),
    ("0003-mkversion.patch", "f61692e560771e10421b9daf15e506a610a5f2cdf00faf4a497937b042cae61a"),
    ("0004-quirks.patch", "7cf9e647c57ce0edf2e200aef5b207caaec475decef320d7b0ce6d8a6eec1114"),
    ("0005-no-vendored-libs.patch", "6ed8b4c341e0888bc8cabd215e2a6c580f33e3957310188f0c5ca844383ba301"),
    ("0006-fix-ios-install.patch", "1c224e79e0c3caff4a7d3d970992154ff451ce661c119f40deab2fbb5aba7921"),
]
base_url = "https://raw.githubusercontent.com/microsoft/vcpkg/1de2026f28ead93ff1773e6e680387643e914ea1/ports/ktx"
pdir = Path("/build/patches")
pdir.mkdir(parents=True, exist_ok=True)
for name, expected in patches:
    pfile = pdir / name
    urllib.request.urlretrieve(f"{base_url}/{name}", pfile)
    got = hashlib.sha256(pfile.read_bytes()).hexdigest()
    if got != expected:
        raise SystemExit(f"patch {name} SHA-256 mismatch: {got}")
PY

RUN set -x; \
    for p in /build/patches/000*.patch; do \
      patch -d /build/KTX-Software-4.3.1 --fuzz=0 --batch --no-backup-if-mismatch -p1 -i "${p}"; \
    done; \
    rm -f /build/KTX-Software-4.3.1/other_include/zstd_errors.h; \
    cmake -S /build/KTX-Software-4.3.1 -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/ktx \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_PREFIX_PATH=/opt/zstd \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DKTX_VERSION_FULL=v4.3.1 \
      -DKTX_FEATURE_TESTS=OFF \
      -DKTX_FEATURE_LOADTEST_APPS=OFF \
      -DKTX_FEATURE_STATIC_LIBRARY=ON \
      -DKTX_FEATURE_TOOLS=OFF \
      -DKTX_FEATURE_VK_UPLOAD=OFF \
      -DCMAKE_C_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic"; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp /build/KTX-Software-4.3.1/LICENSE.md /ktx/LICENSE.md; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -isystem /ktx/include /build/ktx_control.cpp \
      /ktx/lib/libktx.a /opt/zstd/lib/libzstd.a -lm -lpthread \
      -o /build/ktx_control; \
    /build/ktx_control; \
    g++ -shared -o /build/libktx_whole.so \
      -Wl,--whole-archive /ktx/lib/libktx.a -Wl,--no-whole-archive \
      /opt/zstd/lib/libzstd.a \
      -Wl,--no-undefined -lm -lpthread; \
    if readelf -d /build/libktx_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libktx_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /ktx/lib/libktx.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /ktx/include/ktx.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "ktx",\n  "version": "4.3.1",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libktx.a": "%s",\n    "include/ktx.h": "%s",\n    "LICENSE.md": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${KTX_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /ktx/cache-receipt.json

FROM scratch
COPY --from=builder /ktx /ktx
