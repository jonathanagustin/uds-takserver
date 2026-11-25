# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-sqlcipher:4.5.7-amd64 AS sqlcipher_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-openssl:3.5.9-amd64 AS openssl_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-draco:1.5.7-amd64 AS draco_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-asyncplusplus:1.1-amd64 AS asyncplusplus_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-fmt:10.2.1-amd64 AS fmt_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-spdlog:1.14.1-amd64 AS spdlog_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-uriparser:0.9.8-amd64 AS uriparser_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-zlib-ng:2.1.5-amd64 AS zlibng_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-zstd:1.5.6-amd64 AS zstd_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-ktx:4.3.1-amd64 AS ktx_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-meshoptimizer:0.20-amd64 AS meshopt_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-libjpeg-turbo:3.0.3-amd64 AS jpeg_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-tinyxml2:10.0.0-amd64 AS tinyxml2_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-libwebp:1.4.0-amd64 AS webp_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-abseil:20240116.2-amd64 AS abseil_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-s2geometry:0.11.1-amd64 AS s2_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-modp-base64:2020-09-26-amd64 AS modp_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG CESIUM_URL="https://github.com/CesiumGS/cesium-native/archive/f8b38abbbaa24ec2984ff663182240d06285b36c.tar.gz"
ARG CESIUM_SHA256="1383a6a3a4130c3442e274b14481522cba66f58a8d7fec1bd98689cb27816e32"
ARG LICENSE_SHA256="69849221bfb90053de2134ef5e6d540287b4b98062326492f1f96f5da685524b"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY --from=sqlcipher_cache /sqlcipher /opt/sqlcipher
COPY --from=openssl_cache /openssl /opt/openssl
COPY --from=draco_cache /draco /opt/stage/draco
COPY --from=asyncplusplus_cache /asyncplusplus /opt/stage/asyncplusplus
COPY --from=fmt_cache /fmt /opt/stage/fmt
COPY --from=spdlog_cache /spdlog /opt/stage/spdlog
COPY --from=uriparser_cache /uriparser /opt/stage/uriparser
COPY --from=zlibng_cache /zlib-ng /opt/stage/zlib-ng
COPY --from=zstd_cache /zstd /opt/stage/zstd
COPY --from=ktx_cache /ktx /opt/stage/ktx
COPY --from=meshopt_cache /meshoptimizer /opt/stage/meshoptimizer
COPY --from=jpeg_cache /libjpeg-turbo /opt/stage/libjpeg-turbo
COPY --from=tinyxml2_cache /tinyxml2 /opt/stage/tinyxml2
COPY --from=webp_cache /libwebp /opt/stage/libwebp
COPY --from=abseil_cache /abseil /opt/stage/abseil
COPY --from=s2_cache /s2geometry /opt/stage/s2geometry
COPY --from=modp_cache /modp-base64 /opt/stage/modp-base64
COPY cesium_control.cpp /build/cesium_control.cpp
WORKDIR /build

RUN python3 - <<'PY'
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

deps_prefix = Path("/opt/cesium-deps")
for sub in ("include", "lib", "share", "cmake"):
    (deps_prefix / sub).mkdir(parents=True, exist_ok=True)

for staged in sorted(Path("/opt/stage").iterdir()):
    for sub in ("include", "lib", "share", "cmake"):
        src = staged / sub
        if src.is_dir():
            shutil.copytree(src, deps_prefix / sub, dirs_exist_ok=True, symlinks=True)

shutil.copy2(deps_prefix / "lib/libmodp_b64.a", deps_prefix / "lib/liblibmodpbase64.a")

tweakme = deps_prefix / "include/spdlog/tweakme.h"
if tweakme.is_file():
    raw = tweakme.read_text(encoding="utf-8")
    raw = raw.replace(
        "// #define SPDLOG_FMT_EXTERNAL",
        "#ifndef SPDLOG_FMT_EXTERNAL\n#define SPDLOG_FMT_EXTERNAL\n#endif",
    )
    tweakme.write_text(raw, encoding="utf-8")
bundled_fmt = deps_prefix / "include/spdlog/fmt/bundled"
if bundled_fmt.exists():
    shutil.rmtree(bundled_fmt)

uri_base = deps_prefix / "include/uriparser/UriBase.h"
if uri_base.is_file():
    raw = uri_base.read_text(encoding="utf-8")
    raw = raw.replace("defined(URI_STATIC_BUILD)", "1 /* defined(URI_STATIC_BUILD) */")
    uri_base.write_text(raw, encoding="utf-8")

ports = [
    (
        "picosha2",
        "https://github.com/okdshin/PicoSHA2/archive/b699e6c900be6e00152db5a3d123c1db42ea13d0.tar.gz",
        "5972dfe7675c560a0c58491e72938e8e5ea46eabb2e2f564086001eda21b237035aa0deef94bb36a21154a4b5cbc160b9b0f6b5988f6580d693ad74cfbc53bb5",
        "headers",
        [],
    ),
    (
        "stb",
        "https://github.com/nothings/stb/archive/5736b15f7ea0ffb08dd38af21067c314d6a3aae9.tar.gz",
        "55bc75284cf8a092c527d1ae18c461c9d0ab6aacdcf3b873abde54c06d9b8a0ae249ce47c7ad25809e075bfbb58e9c879d43e1df2708083860c07ac3bbb30d60",
        "headers",
        [],
    ),
    (
        "earcut-hpp",
        "https://github.com/mapbox/earcut.hpp/archive/v2.2.4.tar.gz",
        "87f52bf99273dc47f78ebacd4ee0ccbab4edd3f9b85d97aed1c0d1165b3e2523e1a71f3a37a118e82170e79d57a2e09644d4115facb63dc6f704affb9c428e6b",
        "headers",
        [],
    ),
    (
        "expected-lite",
        "https://github.com/martinmoene/expected-lite/archive/v0.8.0.tar.gz",
        "1e2b36e4966d66aa202c9fd9c251e643593cd3e08d5ecbff8849e2a41abab199188aaca25f1d4e84f1b3cb2387875a9750900dfc4ee56c2dbf153af9c2520943",
        "cmake",
        ["-DEXPECTED_LITE_OPT_BUILD_TESTS=OFF", "-DEXPECTED_LITE_OPT_BUILD_EXAMPLES=OFF"],
    ),
    (
        "ms-gsl",
        "https://github.com/Microsoft/GSL/archive/a3534567187d2edc428efd3f13466ff75fe5805c.tar.gz",
        "5bd6aad37fee3b56a2ee2fed10d6ef02fdcf37a1f40b3fb1bbec8146a573e235169b315405d010ab75175674ed82658c8202f40b128a849c5250b4a1b8b0a1b3",
        "cmake",
        ["-DGSL_TEST=OFF"],
    ),
    (
        "rapidjson",
        "https://github.com/Tencent/rapidjson/archive/a95e013b97ca6523f32da23f5095fcc9dd6067e5.tar.gz",
        "19bf9a579df70cbeaf60c7ccf25c92c327bffe95b0df14f27f2132134d5bb214e98a45e021eb287c4790e301f84bb095e0bdb3c97f65a37fbeb254970d97c005",
        "cmake",
        ["-DRAPIDJSON_BUILD_DOC=OFF", "-DRAPIDJSON_BUILD_EXAMPLES=OFF", "-DRAPIDJSON_BUILD_TESTS=OFF"],
    ),
    (
        "libmorton",
        "https://github.com/Forceflow/libmorton/archive/v0.2.12.tar.gz",
        "020d9ba97204b8c028a8700d7212821dd75b6dbe1b4a77776777d06ef29bcad75cdc4e830f211daf6250779cc81ed4842a0632f89a7b7017eb071869a3c938fa",
        "cmake",
        ["-DBUILD_TESTING=OFF"],
    ),
    (
        "cpp-httplib",
        "https://github.com/yhirose/cpp-httplib/archive/v0.16.0.tar.gz",
        "63dc5a50f425e7be909d60db138caa1b9d7260c5d4db26603011a329f0d6a645d56f436c79466fbe662f24a94bc0f72926062b3ed49cb658eb91a6bdb2ddf25b",
        "cmake",
        [
            "-DHTTPLIB_REQUIRE_BROTLI=OFF",
            "-DHTTPLIB_REQUIRE_OPENSSL=OFF",
            "-DHTTPLIB_REQUIRE_ZLIB=OFF",
            "-DHTTPLIB_USE_OPENSSL_IF_AVAILABLE=OFF",
            "-DHTTPLIB_USE_ZLIB_IF_AVAILABLE=OFF",
            "-DHTTPLIB_USE_BROTLI_IF_AVAILABLE=OFF",
        ],
    ),
    (
        "glm",
        "https://github.com/g-truc/glm/archive/1.0.1.tar.gz",
        "c6c6fa1ea7a7e97820e36ee042a78be248ae828c99c1b1111080d9bf334a5160c9993a70312351c92a867cd49907c95f9f357c8dfe2bc29946da6e83e27ba20c",
        "cmake",
        ["-DGLM_BUILD_LIBRARY=ON", "-DGLM_BUILD_TESTS=OFF", "-DGLM_BUILD_INSTALL=ON"],
    ),
]

for name, url, sha512, kind, opts in ports:
    arc = Path(f"/build/{name}.tar.gz")
    urllib.request.urlretrieve(url, arc)
    got = hashlib.sha512(arc.read_bytes()).hexdigest()
    if got != sha512:
        raise SystemExit(f"{name} SHA-512 mismatch: {got}")
    unpack = Path(f"/build/port-{name}")
    unpack.mkdir(parents=True)
    with tarfile.open(arc, "r:gz") as tf:
        tf.extractall(unpack)
    arc.unlink()
    roots = [p for p in unpack.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise SystemExit(f"expected single root in {name}: {roots}")
    src = roots[0]
    if kind == "headers":
        if name == "picosha2":
            shutil.copy2(src / "picosha2.h", deps_prefix / "include/picosha2.h")
        elif name == "stb":
            for hdr in src.glob("*.h"):
                shutil.copy2(hdr, deps_prefix / "include" / hdr.name)
        elif name == "earcut-hpp":
            (deps_prefix / "include/mapbox").mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / "include/mapbox/earcut.hpp", deps_prefix / "include/mapbox/earcut.hpp")
    else:
        bdir = Path(f"/build/port-build-{name}")
        subprocess.run(
            [
                "cmake",
                "-S",
                str(src),
                "-B",
                str(bdir),
                "-G",
                "Ninja",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
                "-DBUILD_SHARED_LIBS=OFF",
                "-DCMAKE_C_STANDARD=99",
                "-DCMAKE_CXX_STANDARD=17",
                "-DCMAKE_CXX_EXTENSIONS=OFF",
                f"-DCMAKE_INSTALL_PREFIX={deps_prefix}",
                "-DCMAKE_INSTALL_LIBDIR=lib",
                f"-DCMAKE_PREFIX_PATH={deps_prefix}",
                *opts,
            ],
            check=True,
        )
        subprocess.run(["cmake", "--build", str(bdir), "--parallel", "4"], check=True)
        subprocess.run(["cmake", "--install", str(bdir)], check=True)
        shutil.rmtree(bdir)

cesium_arc = Path("/build/cesium-native.tar.gz")
urllib.request.urlretrieve(os.environ["CESIUM_URL"], cesium_arc)
got256 = hashlib.sha256(cesium_arc.read_bytes()).hexdigest()
if got256 != os.environ["CESIUM_SHA256"]:
    raise SystemExit(f"cesium SHA-256 mismatch: {got256}")
with tarfile.open(cesium_arc, "r:gz") as tf:
    tf.extractall("/build")
cesium_arc.unlink()

csrc = Path("/build/cesium-native-f8b38abbbaa24ec2984ff663182240d06285b36c")
lic_sha = hashlib.sha256((csrc / "LICENSE").read_bytes()).hexdigest()
if lic_sha != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"cesium LICENSE SHA-256 mismatch: {lic_sha}")

def replace_exact(path: Path, old: str, new: str) -> None:
    raw = path.read_text(encoding="utf-8")
    if raw.count(old) != 1:
        raise SystemExit(f"expected single match in {path}: {old[:60]!r}")
    path.write_text(raw.replace(old, new), encoding="utf-8")

cm = csrc / "CMakeLists.txt"
cm_txt = cm.read_text(encoding="utf-8")
start = cm_txt.index("if (NOT CMAKE_TOOLCHAIN_FILE)")
end = cm_txt.index("# Set defaults", start)
replace_exact(
    cm,
    cm_txt[start:end],
    'if(NOT CESIUM_PUBLIC_PREFIX)\n  message(FATAL_ERROR "Explicit verified public prefix required")\nendif()\n\n',
)
replace_exact(cm, 'set(PACKAGE_BASE_DIR "${EZVCPKG_PACKAGES_DIR}")', 'set(PACKAGE_BASE_DIR "${CESIUM_PUBLIC_PREFIX}")')
replace_exact(cm, 'set(PACKAGE_BUILD_DIR "${EZVCPKG_DIR}")', 'set(PACKAGE_BUILD_DIR "${CESIUM_PUBLIC_PREFIX}")')
cm_txt = cm.read_text(encoding="utf-8")
start = cm_txt.index("if(VCPKG_MANIFEST_MODE)\n    set(PACKAGE_BUILD_DIR")
end = cm_txt.index("\nendif()", start) + len("\nendif()")
replace_exact(cm, cm_txt[start:end], "# Explicit verified public prefix replaces vcpkg layout selection.")
replace_exact(
    cm,
    "find_package(Catch2 CONFIG REQUIRED)",
    "if(CESIUM_TESTS_ENABLED)\n  find_package(Catch2 CONFIG REQUIRED)\nendif()",
)
replace_exact(
    cm,
    "find_package(unofficial-sqlite3 CONFIG REQUIRED)",
    'if(NOT TARGET SQLCipher::SQLCipher)\n  message(FATAL_ERROR "Actual verified SQLCipher target required")\nendif()',
)
replace_exact(csrc / "CesiumAsync/CMakeLists.txt", "unofficial::sqlite3::sqlite3", "SQLCipher::SQLCipher")

parent = Path("/build/cesium-parent")
parent.mkdir()
(parent / "CMakeLists.txt").write_text(
    f'''cmake_minimum_required(VERSION 3.24)
project(PublicCesium LANGUAGES C CXX)
find_package(OpenSSL REQUIRED)
add_library(SQLCipher::SQLCipher STATIC IMPORTED GLOBAL)
set_target_properties(SQLCipher::SQLCipher PROPERTIES
  IMPORTED_LOCATION "/opt/sqlcipher/lib/libsqlite3.a"
  INTERFACE_INCLUDE_DIRECTORIES "/opt/sqlcipher/include"
  INTERFACE_LINK_LIBRARIES "OpenSSL::Crypto;${{CMAKE_DL_LIBS}};m")
add_subdirectory("{csrc}" cesium)
''',
    encoding="utf-8",
)
PY

RUN set -x; \
    targets="CesiumUtility CesiumGltf CesiumGeometry CesiumGeospatial CesiumJsonReader CesiumJsonWriter CesiumGltfContent CesiumGltfReader CesiumGltfWriter CesiumAsync Cesium3DTiles Cesium3DTilesReader Cesium3DTilesWriter Cesium3DTilesContent CesiumRasterOverlays Cesium3DTilesSelection CesiumIonClient CesiumQuantizedMeshTerrain"; \
    cmake -S /build/cesium-parent -B /build/cesium-out -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DCMAKE_C_STANDARD=99 \
      -DCMAKE_CXX_STANDARD=17 \
      -DCMAKE_CXX_EXTENSIONS=OFF \
      -DCMAKE_C_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      -DCMAKE_CXX_FLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      -DCMAKE_PREFIX_PATH="/opt/cesium-deps;/opt/openssl;/opt/sqlcipher" \
      -DCMAKE_FIND_ROOT_PATH="/opt/cesium-deps;/opt/openssl;/opt/sqlcipher" \
      -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY \
      -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY \
      -DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY \
      -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF \
      -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF \
      -DOPENSSL_ROOT_DIR=/opt/openssl \
      -DOPENSSL_USE_STATIC_LIBS=TRUE \
      -DVCPKG_MANIFEST_MODE=ON \
      -DVCPKG_MANIFEST_INSTALL=OFF \
      -DCESIUM_PUBLIC_PREFIX=/opt/cesium-deps \
      -DCESIUM_NATIVE_STB_INCLUDE_DIR=/opt/cesium-deps/include \
      -DCESIUM_TESTS_ENABLED=OFF \
      -DCESIUM_GLM_STRICT_ENABLED=ON \
      -DCESIUM_INSTALL_STATIC_LIBS=OFF \
      -DCESIUM_INSTALL_HEADERS=OFF; \
    cmake --build /build/cesium-out --parallel 4 --target ${targets}; \
    mkdir -p /cesium/lib /cesium/include; \
    csrc="/build/cesium-native-f8b38abbbaa24ec2984ff663182240d06285b36c"; \
    for t in ${targets}; do \
      cp -a "/build/cesium-out/cesium/${t}/lib${t}.a" /cesium/lib/; \
      if [ -d "${csrc}/${t}/include" ]; then \
        cp -a "${csrc}/${t}/include/." /cesium/include/; \
      fi; \
      if [ -d "${csrc}/${t}/generated/include" ]; then \
        cp -a "${csrc}/${t}/generated/include/." /cesium/include/; \
      fi; \
      if [ -d "/build/cesium-out/cesium/${t}/generated/include" ]; then \
        cp -a "/build/cesium-out/cesium/${t}/generated/include/." /cesium/include/; \
      fi; \
    done; \
    if [ -f /opt/cesium-deps/lib/libglm.a ]; then cp -a /opt/cesium-deps/lib/libglm.a /cesium/lib/; fi; \
    for hdr_dir in glm gsl rapidjson nonstd mapbox; do \
      if [ -d "/opt/cesium-deps/include/${hdr_dir}" ]; then \
        cp -a "/opt/cesium-deps/include/${hdr_dir}" /cesium/include/; \
      fi; \
    done; \
    for hdr_file in libmorton httplib.h picosha2.h stb_*.h; do \
      for match in /opt/cesium-deps/include/${hdr_file}; do \
        if [ -e "${match}" ]; then cp -a "${match}" /cesium/include/; fi; \
      done; \
    done; \
    cp "${csrc}/LICENSE" /cesium/LICENSE; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 -DSPDLOG_FMT_EXTERNAL=1 \
      -DGLM_FORCE_XYZW_ONLY -DGLM_FORCE_EXPLICIT_CTOR -DGLM_FORCE_SIZE_T_LENGTH \
      -isystem /cesium/include -isystem /opt/cesium-deps/include \
      /build/cesium_control.cpp \
      -Wl,--start-group /cesium/lib/libCesium*.a /opt/cesium-deps/lib/*.a /opt/sqlcipher/lib/libsqlite3.a /opt/openssl/lib/*.a -Wl,--end-group \
      -lpthread -ldl -lm \
      -o /build/cesium_control; \
    /build/cesium_control; \
    g++ -shared -o /build/libcesium_whole.so \
      -Wl,--whole-archive /cesium/lib/libCesiumGeospatial.a /cesium/lib/libCesiumGeometry.a /cesium/lib/libCesiumUtility.a -Wl,--no-whole-archive \
      -Wl,--start-group /opt/cesium-deps/lib/*.a /opt/sqlcipher/lib/libsqlite3.a /opt/openssl/lib/*.a -Wl,--end-group \
      -Wl,--no-undefined -lpthread -ldl -lm; \
    if readelf -d /build/libcesium_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libcesium_whole.so" >&2; \
      exit 1; \
    fi

RUN python3 - <<'PY'
import hashlib
import json
import os
import subprocess
from pathlib import Path

targets = [
    "CesiumUtility", "CesiumGltf", "CesiumGeometry", "CesiumGeospatial",
    "CesiumJsonReader", "CesiumJsonWriter", "CesiumGltfContent", "CesiumGltfReader",
    "CesiumGltfWriter", "CesiumAsync", "Cesium3DTiles", "Cesium3DTilesReader",
    "Cesium3DTilesWriter", "Cesium3DTilesContent", "CesiumRasterOverlays",
    "Cesium3DTilesSelection", "CesiumIonClient", "CesiumQuantizedMeshTerrain",
]
artifacts = {}
for t in targets:
    rel = f"lib/lib{t}.a"
    artifacts[rel] = hashlib.sha256((Path("/cesium") / rel).read_bytes()).hexdigest()
for rel in ("include/CesiumGeospatial/Ellipsoid.h", "include/CesiumGltfReader/GltfReader.h", "LICENSE"):
    artifacts[rel] = hashlib.sha256((Path("/cesium") / rel).read_bytes()).hexdigest()

gcc_ver = subprocess.check_output(["gcc", "-dumpfullversion", "-dumpversion"], text=True).strip()
receipt = {
    "schema": "uds-takx-native-cache-v1",
    "component": "cesium",
    "version": "0.40.1",
    "arch": "x86_64",
    "gcc_version": gcc_ver,
    "source_archive_sha256": os.environ["CESIUM_SHA256"],
    "license_sha256": os.environ["LICENSE_SHA256"],
    "artifacts": artifacts,
    "controls": {
        "static_pic_control": True,
        "whole_archive_no_undefined": True,
        "no_textrel": True,
    },
    "scope": {
        "static_pic_host_abi": True,
        "sdk_accepted": False,
        "security_accepted": False,
    },
}
Path("/cesium/cache-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
PY

FROM scratch
COPY --from=builder /cesium /cesium
