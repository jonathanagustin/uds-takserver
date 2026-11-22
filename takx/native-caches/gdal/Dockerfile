# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-zlib:1.3.1-amd64 AS zlib_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-proj:4.9.1-amd64 AS proj_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-expat:2.10.0-amd64 AS expat_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-uriparser:0.9.8-amd64 AS uriparser_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-native-libkml:1.3.0-amd64 AS libkml_cache
FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG GDAL_URL="https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/9f6893dd657feacc35ec5de03dad721c2e44170e/depends/gdal-2.4.4-mod.tar.gz"
ARG GDAL_SHA256="a170207c0548cadec57e3a6e57727f66d4b64bf4a9a3ddd6c6bd0bf2479260e2"
ARG LICENSE_SHA256="b82e6cca0b13f5db2f22ab667f22254fb1f4b135ea73d5bd6238ef89aff31f6c"
ARG PATCH_PDF_SHA256="3210c295244077fb94ad005ee1eae9ce500afe506e3de82dd5983d664def0355"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY --from=zlib_cache /zlib /opt/zlib
COPY --from=proj_cache /proj /opt/proj
COPY --from=expat_cache /expat /opt/expat
COPY --from=uriparser_cache /uriparser /opt/uriparser
COPY --from=libkml_cache /libkml /opt/libkml
COPY gdal_control.cpp /build/gdal_control.cpp
COPY patches/gdal-disable-pdf.patch /build/patches/gdal-disable-pdf.patch
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

patch_sha = hashlib.sha256(Path("/build/patches/gdal-disable-pdf.patch").read_bytes()).hexdigest()
if patch_sha != os.environ["PATCH_PDF_SHA256"]:
    raise SystemExit(f"gdal-disable-pdf.patch SHA-256 mismatch: {patch_sha}")

dest = Path("/build/gdal-2.4.4-mod.tar.gz")
urllib.request.urlretrieve(os.environ["GDAL_URL"], dest)
got = hashlib.sha256(dest.read_bytes()).hexdigest()
if got != os.environ["GDAL_SHA256"]:
    raise SystemExit(f"GDAL SHA-256 mismatch: {got}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
dest.unlink()

lic = hashlib.sha256(Path("/build/gdal/LICENSE.TXT").read_bytes()).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"GDAL LICENSE.TXT SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    patch --batch --binary --fuzz=0 -p1 -d /build/gdal -i /build/patches/gdal-disable-pdf.patch; \
    mkdir -p /build/pkgconfig; \
    printf 'Name: libkml\nDescription: hash-bound public LibKML static closure\nVersion: 1.3.0\nCflags: -I/opt/libkml/include\nLibs: -Wl,--start-group /opt/libkml/lib/libkmlengine.a /opt/libkml/lib/libkmldom.a /opt/libkml/lib/libkmlbase.a /opt/libkml/lib/libkmlconvenience.a /opt/libkml/lib/libkmlregionator.a /opt/libkml/lib/libkmlxsd.a /opt/libkml/lib/libminizip.a /opt/uriparser/lib/liburiparser.a /opt/expat/lib/libexpat.a /opt/zlib/lib/libz.a -Wl,--end-group\n' \
      > /build/pkgconfig/libkml.pc; \
    (cd /build/gdal && \
      PKG_CONFIG_LIBDIR=/build/pkgconfig \
      PKG_CONFIG_PATH="" \
      CFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      CXXFLAGS="-O2 -fPIC -march=x86-64 -mtune=generic" \
      CPPFLAGS="-I/opt/proj/include -I/opt/zlib/include -I/opt/expat/include -I/opt/libkml/include" \
      LDFLAGS="-L/opt/proj/lib -L/opt/zlib/lib -L/opt/expat/lib -L/opt/libkml/lib -L/opt/uriparser/lib" \
      ./configure \
        --prefix=/gdal \
        --with-proj=/opt/proj \
        --with-libz=/opt/zlib \
        --with-expat=/opt/expat \
        --with-libkml=/opt/libkml \
        --disable-shared \
        --enable-static \
        --with-threads=yes \
        --with-libtiff=internal \
        --with-geotiff=internal \
        --with-jpeg=internal \
        --with-png=internal \
        --with-gif=internal \
        --without-jpeg12 \
        --with-proj5-api=no \
        --with-liblzma=no \
        --with-zstd=no \
        --with-pg=no \
        --with-grass=no \
        --with-libgrass=no \
        --with-cfitsio=no \
        --with-dds=no \
        --with-gta=no \
        --with-ogdi=no \
        --with-fme=no \
        --with-sosi=no \
        --with-mongocxx=no \
        --with-hdf4=no \
        --with-hdf5=no \
        --with-kea=no \
        --with-netcdf=no \
        --with-jasper=no \
        --with-openjpeg=no \
        --with-fgdb=no \
        --with-ecw=no \
        --with-kakadu=no \
        --with-mrsid=no \
        --with-jp2mrsid=no \
        --with-mrsid_lidar=no \
        --with-jp2lura=no \
        --with-msg=no \
        --with-oci=no \
        --with-mysql=no \
        --with-ingres=no \
        --with-xerces=no \
        --with-odbc=no \
        --with-dods-root=no \
        --with-curl=no \
        --with-xml2=no \
        --with-spatialite=no \
        --with-sqlite3=no \
        --with-rasterlite2=no \
        --with-pcre=no \
        --with-teigha=no \
        --with-idb=no \
        --with-sde=no \
        --with-epsilon=no \
        --with-webp=no \
        --with-geos=no \
        --with-sfcgal=no \
        --with-opencl=no \
        --with-freexl=no \
        --with-poppler=no \
        --with-podofo=no \
        --with-pdfium=no \
        --with-perl=no \
        --with-python=no \
        --with-java=no \
        --with-hdfs=no \
        --with-mdb=no \
        --with-rasdaman=no \
        --with-armadillo=no \
        --with-cryptopp=no \
        --with-crypto=no \
        --without-libiconv-prefix); \
    grep -Eq '^HAVE_EXPAT[[:space:]]*=[[:space:]]*yes$' /build/gdal/GDALmake.opt; \
    grep -Eq '^HAVE_LIBKML[[:space:]]*=[[:space:]]*yes$' /build/gdal/GDALmake.opt; \
    grep -Eq '^PROJ_STATIC[[:space:]]*=[[:space:]]*yes$' /build/gdal/GDALmake.opt; \
    mkdir -p /build/bin; \
    printf '#!/usr/bin/env python3\nimport pathlib, sys\nfor arg in sys.argv[1:]:\n    p = pathlib.Path(arg)\n    p.write_bytes(p.read_bytes().replace(b"\\r\\n", b"\\n"))\n' > /build/bin/dos2unix; \
    chmod +x /build/bin/dos2unix; \
    PATH="/build/bin:${PATH}" make -C /build/gdal -j4 lib-target; \
    mkdir -p /gdal/lib /gdal/include /gdal/share/gdal; \
    make -C /build/gdal install-static-lib; \
    for d in port gcore alg ogr ogr/ogrsf_frmts; do \
      cp /build/gdal/${d}/*.h /gdal/include/ 2>/dev/null || true; \
    done; \
    cp /build/gdal/LICENSE.TXT /gdal/LICENSE.TXT; \
    cp /build/gdal/data/*.* /gdal/share/gdal/; \
    g++ -std=c++17 -Wall -Wextra -Werror -O2 \
      -isystem /gdal/include \
      /build/gdal_control.cpp \
      /gdal/lib/libgdal.a \
      -Wl,--start-group \
        /opt/proj/lib/libproj.a \
        /opt/libkml/lib/libkmlengine.a \
        /opt/libkml/lib/libkmldom.a \
        /opt/libkml/lib/libkmlbase.a \
        /opt/libkml/lib/libkmlconvenience.a \
        /opt/libkml/lib/libkmlregionator.a \
        /opt/libkml/lib/libkmlxsd.a \
        /opt/libkml/lib/libminizip.a \
        /opt/uriparser/lib/liburiparser.a \
        /opt/expat/lib/libexpat.a \
        /opt/zlib/lib/libz.a \
      -Wl,--end-group \
      -lpthread -ldl -lm \
      -o /build/gdal_control; \
    /build/gdal_control; \
    g++ -shared -o /build/libgdal_whole.so \
      -Wl,--whole-archive /gdal/lib/libgdal.a -Wl,--no-whole-archive \
      -Wl,--start-group \
        /opt/proj/lib/libproj.a \
        /opt/libkml/lib/libkmlengine.a \
        /opt/libkml/lib/libkmldom.a \
        /opt/libkml/lib/libkmlbase.a \
        /opt/libkml/lib/libkmlconvenience.a \
        /opt/libkml/lib/libkmlregionator.a \
        /opt/libkml/lib/libkmlxsd.a \
        /opt/libkml/lib/libminizip.a \
        /opt/uriparser/lib/liburiparser.a \
        /opt/expat/lib/libexpat.a \
        /opt/zlib/lib/libz.a \
      -Wl,--end-group \
      -Wl,--no-undefined -lpthread -ldl -lm; \
    if readelf -d /build/libgdal_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libgdal_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /gdal/lib/libgdal.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /gdal/include/gdal.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "gdal",\n  "version": "2.4.4",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libgdal.a": "%s",\n    "include/gdal.h": "%s",\n    "LICENSE.TXT": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${GDAL_SHA256}" "${LICENSE_SHA256}" \
      "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /gdal/cache-receipt.json

FROM scratch
COPY --from=builder /gdal /gdal
