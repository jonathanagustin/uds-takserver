# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-laszip:3.4.3-amd64 AS laszip_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG LIBLAS_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/depends/libLAS-1.8.2-mod.tar.gz"
ARG LIBLAS_SHA256="8decbf40c5e7690a98a48d53c729f7c7670ff472290c2f46a26c7413ed53e641"
ARG LASZIP_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/depends/LASzip-3.4.3-mod.tar.gz"
ARG LASZIP_SHA256="66336908e17bb92739803c80fe627461860cacb017dcff55e476e131981e7b6d"
ARG LICENSE_SHA256="255ba531725caaed436a919896e69a1bcadefcc98eaa030b64710e33b4ea3f18"
ARG BOOST_LICENSE_SHA256="c9bff75738922193e67fa726fa225535870d2aa1059f91452c411736284ad566"
ARG PATCH_PREFIX_SHA256="3d16e566a76c9c52a804a71de1fb6e014f2ba3bb01c0bbacfce0de3882c91536"
ARG PATCH_CURSOR_SHA256="cde91bb6d5bc409117fd1907cfba5f9d0f39a0038fa545e8c57e7613bd9e882b"
ARG PATCH_SEEK_SHA256="47bb155e1ec34742b9ca6aee6b4653be0284376dcc0cc32297beb011762f9caf"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY --from=laszip_cache /laszip /opt/laszip
COPY liblas_control.cpp /build/liblas_control.cpp
COPY patches/liblas-public-prefix-explicit-boost.patch /build/patches/liblas-public-prefix-explicit-boost.patch
COPY patches/liblas-zip-cursor-state-project.patch /build/patches/liblas-zip-cursor-state-project.patch
COPY patches/liblas-c-api-seek-error-project.patch /build/patches/liblas-c-api-seek-error-project.patch
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, shutil, tarfile, urllib.request
from pathlib import Path

for rel, env_key in (
    ("/build/patches/liblas-public-prefix-explicit-boost.patch", "PATCH_PREFIX_SHA256"),
    ("/build/patches/liblas-zip-cursor-state-project.patch", "PATCH_CURSOR_SHA256"),
    ("/build/patches/liblas-c-api-seek-error-project.patch", "PATCH_SEEK_SHA256"),
):
    got = hashlib.sha256(Path(rel).read_bytes()).hexdigest()
    if got != os.environ[env_key]:
        raise SystemExit(f"patch {rel} SHA-256 mismatch: {got}")

laszip_dest = Path("/build/LASzip-3.4.3-mod.tar.gz")
urllib.request.urlretrieve(os.environ["LASZIP_URL"], laszip_dest)
got = hashlib.sha256(laszip_dest.read_bytes()).hexdigest()
if got != os.environ["LASZIP_SHA256"]:
    raise SystemExit(f"LASzip SHA-256 mismatch: {got}")
with tarfile.open(laszip_dest, "r:gz") as tf:
    tf.extractall("/build")
hdr_dir = Path("/opt/laszip/include/laszip")
hdr_dir.mkdir(parents=True, exist_ok=True)
for hpp in Path("/build/LASzip/src").glob("*.hpp"):
    shutil.copyfile(hpp, hdr_dir / hpp.name)
laszip_dest.unlink()
shutil.rmtree("/build/LASzip")

dest = Path("/build/libLAS-1.8.2-mod.tar.gz")
urllib.request.urlretrieve(os.environ["LIBLAS_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["LIBLAS_SHA256"]:
    raise SystemExit(f"libLAS SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
dest.unlink()

lic = hashlib.sha256(Path("/build/libLAS/LICENSE.txt").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"libLAS LICENSE.txt SHA-256 mismatch: {lic}")
blic = hashlib.sha256(Path("/build/libLAS/find_package/boost/LICENSE_1_0.txt").read_bytes()).hexdigest()
if blic != os.environ["BOOST_LICENSE_SHA256"]:
    raise SystemExit(f"Boost LICENSE_1_0.txt SHA-256 mismatch: {blic}")
PY

RUN set -x; \
    patch --batch --binary --fuzz=0 -p1 -d /build/libLAS -i /build/patches/liblas-public-prefix-explicit-boost.patch; \
    patch --batch --binary --fuzz=0 -p1 -d /build/libLAS -i /build/patches/liblas-zip-cursor-state-project.patch; \
    patch --batch --binary --fuzz=0 -p1 -d /build/libLAS -i /build/patches/liblas-c-api-seek-error-project.patch; \
    boost_dir="/build/libLAS/find_package/boost"; \
    printf 'using gcc : 14.2 : %s ;\n' "$(command -v g++)" > /build/boost-user-config.jam; \
    (cd "${boost_dir}" && ./bootstrap.sh --with-toolset=gcc \
      --with-libraries=program_options,thread,system,iostreams,filesystem,chrono,date_time,atomic); \
    (cd "${boost_dir}" && ./b2 --ignore-site-config --user-config=/build/boost-user-config.jam \
      toolset=gcc-14.2 -j4 variant=release link=static threading=multi cxxstd=11 \
      cxxflags="-O2 -fPIC -march=x86-64 -mtune=generic" --layout=system \
      --build-dir=/build/boost-build --stagedir=/build/boost-stage \
      -sNO_ZLIB=1 -sNO_BZIP2=1 -sNO_LZMA=1 -sNO_ZSTD=1 \
      --with-program_options --with-thread --with-system --with-iostreams \
      --with-filesystem --with-chrono --with-date_time --with-atomic stage); \
    cmake -S /build/libLAS -B /build/out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/liblas \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DCMAKE_C_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      -DCMAKE_SHARED_LINKER_FLAGS="-Wl,--no-undefined" \
      -DCMAKE_POLICY_DEFAULT_CMP0167=OLD \
      -DPUBLIC_LASZIP_PREFIX=/opt/laszip \
      -DBOOST_ROOT="${boost_dir}" \
      -DBOOST_LIBRARYDIR=/build/boost-stage/lib \
      -DBoost_INCLUDE_DIR="${boost_dir}" \
      -DBoost_LIBRARY_DIR_RELEASE=/build/boost-stage/lib \
      -DBoost_LIBRARY_DIR_DEBUG=/build/boost-stage/lib \
      -DBoost_NO_SYSTEM_PATHS=ON \
      -DBoost_NO_BOOST_CMAKE=ON \
      -DBoost_USE_STATIC_LIBS=ON \
      -DBoost_USE_MULTITHREADED=ON \
      -DBoost_USE_STATIC_RUNTIME=OFF \
      -DWITH_UTILITIES=OFF \
      -DWITH_TESTS=OFF \
      -DWITH_GDAL=OFF \
      -DWITH_GEOTIFF=OFF \
      -DWITH_LASZIP=ON; \
    ninja -C /build/out -j4; \
    cmake --install /build/out; \
    cp -a "${boost_dir}/boost" /liblas/include/boost; \
    cp /build/libLAS/LICENSE.txt /liblas/LICENSE.txt; \
    cp "${boost_dir}/LICENSE_1_0.txt" /liblas/BOOST_LICENSE_1_0.txt; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -isystem /liblas/include \
      /build/liblas_control.cpp \
      -L/liblas/lib -Wl,--disable-new-dtags,-rpath,/liblas/lib \
      -llas_c -llas -pthread -ldl \
      -o /build/liblas_control; \
    /build/liblas_control; \
    g++ -shared -o /build/liblas_deps_whole.so \
      -Wl,--whole-archive \
      /opt/laszip/lib/liblaszip.a \
      /build/boost-stage/lib/libboost_*.a \
      -Wl,--no-whole-archive \
      -Wl,--no-undefined -pthread -ldl -lrt; \
    for so in /build/liblas_deps_whole.so /liblas/lib/liblas.so /liblas/lib/liblas_c.so; do \
      if readelf -d "${so}" | grep -q TEXTREL; then \
        echo "unexpected TEXTREL in ${so}" >&2; \
        exit 1; \
      fi; \
    done; \
    las_sha="$(sha256sum /liblas/lib/liblas.so | awk '{print $1}')"; \
    lasc_sha="$(sha256sum /liblas/lib/liblas_c.so | awk '{print $1}')"; \
    hpp_sha="$(sha256sum /liblas/include/liblas/liblas.hpp | awk '{print $1}')"; \
    h_sha="$(sha256sum /liblas/include/liblas/capi/liblas.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "liblas",\n  "version": "1.8.2",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/liblas.so": "%s",\n    "lib/liblas_c.so": "%s",\n    "include/liblas/liblas.hpp": "%s",\n    "include/liblas/capi/liblas.h": "%s",\n    "LICENSE.txt": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${LIBLAS_SHA256}" "${LICENSE_SHA256}" \
      "${las_sha}" "${lasc_sha}" "${hpp_sha}" "${h_sha}" "${LICENSE_SHA256}" \
      > /liblas/cache-receipt.json

FROM scratch
COPY --from=builder /liblas /liblas
