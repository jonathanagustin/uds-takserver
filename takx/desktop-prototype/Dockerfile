# syntax=docker/dockerfile:1
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Opt-in toolchain and runtime images for the TAKX 5.7 desktop prototype.
# The images hold tools only. No recipe output is baked into a layer; every
# build stage runs later as a container over /var/lib/takx-proto.
#
# Build (the CA secret is optional and is only needed behind a TLS-intercepting
# proxy; it is mounted for single RUN steps and never written to a layer):
#   DOCKER_BUILDKIT=1 docker build --target toolchain -t takx-desktop-proto:toolchain \
#     --secret id=ca,src=/root/.ccr/ca-bundle.crt takx/desktop-prototype
#   DOCKER_BUILDKIT=1 docker build --target runtime -t takx-desktop-proto:runtime \
#     --secret id=ca,src=/root/.ccr/ca-bundle.crt takx/desktop-prototype
ARG BASE=mirror.gcr.io/library/debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6

FROM ${BASE} AS toolchain
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC

# Gated native toolchain. Versions are asserted further down, so a drifting
# Debian archive fails the build instead of producing a different toolchain.
# Deliberately absent: zlib, OpenSSL, expat, libxml2, SQLite, curl, GEOS and PROJ
# development packages. The recipes build those from pinned sources and several
# audit that no host copy was picked up.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl wget git gnupg debian-archive-keyring dpkg-dev \
      gcc g++ libc6-dev make binutils cmake swig \
      patch perl pkg-config dos2unix file \
      autoconf automake libtool libtool-bin m4 bison flex gettext \
      tcl \
      python3 python3-venv python3-pip \
      xz-utils bzip2 unzip zip rsync jq procps time \
      libegl1 libegl-mesa0 libgles2 libgl1 libglx-mesa0 libgl1-mesa-dri libgbm1 \
      libglvnd-dev libegl-dev libgles-dev libgl-dev libopengl-dev \
 && rm -rf /var/lib/apt/lists/*

# Eclipse Temurin 17.0.21+9 at the path the plan names. Fetched by URL (this is
# the asset api.adoptium.net redirects to) and checked against a fixed SHA-256.
ARG TEMURIN_URL=https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.21%2B9/OpenJDK17U-jdk_x64_linux_hotspot_17.0.21_9.tar.gz
ARG TEMURIN_SHA256=8b9b611d917e3dfd8419d3566b5949226f69b052fed7e109c1368add1dfe8711
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export CURL_CA_BUNDLE=/run/secrets/ca; fi; \
    mkdir -p /tools/temurin-17.0.21_9 \
 && curl -fsSL --retry 5 --retry-all-errors -o /tmp/jdk.tgz "${TEMURIN_URL}" \
 && echo "${TEMURIN_SHA256}  /tmp/jdk.tgz" | sha256sum -c - \
 && tar -xzf /tmp/jdk.tgz -C /tools/temurin-17.0.21_9 --no-same-owner \
 && rm /tmp/jdk.tgz \
 && test -x /tools/temurin-17.0.21_9/jdk-17.0.21+9/bin/javac
ENV JAVA_HOME=/tools/temurin-17.0.21_9/jdk-17.0.21+9
ENV PATH=/tools/temurin-17.0.21_9/jdk-17.0.21+9/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Apache Ant from Debian without Debian's default JRE (OpenJDK 21 in trixie):
# a control-only package declares that the JRE dependency is met by Temurin, so
# the only JDK in the image is the gated one. It ships no files.
RUN mkdir -p /tmp/jre-provides/DEBIAN \
 && printf '%s\n' \
      'Package: takx-temurin-jre-provides' 'Version: 17.0.21+9' 'Architecture: all' \
      'Maintainer: uds-takserver authors <noreply@invalid>' \
      'Provides: default-jre-headless, java-runtime-headless, java8-runtime-headless' \
      'Description: dependency marker; the JRE is Temurin under /tools' \
      > /tmp/jre-provides/DEBIAN/control \
 && dpkg-deb --build --root-owner-group /tmp/jre-provides /tmp/jre-provides.deb \
 && dpkg -i /tmp/jre-provides.deb \
 && rm -rf /tmp/jre-provides /tmp/jre-provides.deb \
 && apt-get update \
 && apt-get install -y --no-install-recommends ant \
 && rm -rf /var/lib/apt/lists/* \
 && ! compgen -G '/usr/lib/jvm/*' >/dev/null

# CMake and Ninja at the path several recipes hard-code
# (takx/native-cesium/build.py, native-curl, native-kml). uv is installed with
# them for the recipes' documented `uv pip` flow. Wheels are hash-pinned.
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    mkdir -p /workspace/takx-native-tools \
 && python3 -m venv /workspace/takx-native-tools/venv \
 && printf '%s\n' \
      'cmake==3.31.6 --hash=sha256:1c8b05df0602365da91ee6a3336fe57525b137706c4ab5675498f662ae1dbcec' \
      'ninja==1.11.1.4 --hash=sha256:096487995473320de7f65d622c3f1d16c3ad174797602218ca8c967f51ec38a0' \
      > /workspace/takx-native-tools/requirements.txt \
 && /workspace/takx-native-tools/venv/bin/pip install --no-cache-dir --disable-pip-version-check \
      --only-binary=:all: --require-hashes -r /workspace/takx-native-tools/requirements.txt

# Fail closed on any gated tool that is missing or at another version.
RUN want() { local name="$1" expect="$2"; shift 2; local got; got="$("$@" 2>&1 | head -n 3 | tr '\n' ' ')"; \
      case "$got" in *"$expect"*) echo "ok   $name: $expect";; *) echo "FAIL $name: wanted '$expect' in '$got'"; exit 1;; esac; }; \
    want gcc ' 14.2.0' /usr/bin/gcc --version; \
    want g++ ' 14.2.0' /usr/bin/g++ --version; \
    want make 'GNU Make 4.4.1' /usr/bin/make --version; \
    want ld ' 2.44' /usr/bin/ld --version; \
    want as ' 2.44' /usr/bin/as --version; \
    want cmake-apt 'cmake version 3.31.6' /usr/bin/cmake --version; \
    want cmake-venv 'cmake version 3.31.6' /workspace/takx-native-tools/venv/bin/cmake --version; \
    want ninja-venv '1.11.1.git.kitware.jobserver-1' /workspace/takx-native-tools/venv/bin/ninja --version; \
    want swig 'SWIG Version 4.3.0' /usr/bin/swig -version; \
    want javac 'javac 17.0.21' "$JAVA_HOME/bin/javac" --version; \
    want java 'Temurin-17.0.21+9' "$JAVA_HOME/bin/java" -version; \
    want ant 'Apache Ant' /usr/bin/ant -version; \
    for tool in git patch perl pkg-config python3 curl tclsh gpg dpkg-deb dpkg-query readelf nm ar ranlib ldd \
                dos2unix autoconf automake libtoolize unzip zip xz env; do \
      test -x "/usr/bin/$tool" || { echo "FAIL missing /usr/bin/$tool"; exit 1; }; done; \
    test -s /usr/share/keyrings/debian-archive-keyring.gpg; \
    test -s /usr/share/glvnd/egl_vendor.d/50_mesa.json; \
    test -e /usr/lib/x86_64-linux-gnu/libEGL_mesa.so.0; \
    test -e /usr/lib/x86_64-linux-gnu/dri/swrast_dri.so; \
    for header in zlib.h zconf.h openssl/crypto.h expat.h sqlite3.h curl/curl.h geos_c.h proj.h; do \
      test ! -e "/usr/include/$header" || { echo "FAIL host header /usr/include/$header present"; exit 1; }; done; \
    git --version

WORKDIR /var/lib/takx-proto
CMD ["/bin/bash"]

# Same glibc, Mesa and JDK as the toolchain (natives embed absolute RPATHs and
# were linked against this glibc), plus what a headless desktop session and a
# screen capture need.
FROM toolchain AS runtime
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      xvfb xauth openbox xdotool x11-utils x11-xserver-utils mesa-utils ffmpeg \
      fontconfig fonts-dejavu-core fonts-dejavu-extra fonts-liberation2 fonts-noto-core \
      libx11-6 libxext6 libxrender1 libxtst6 libxi6 libxrandr2 libxcursor1 libxinerama1 \
      libxxf86vm1 libxfixes3 libxcomposite1 libxdamage1 libfreetype6 libasound2t64 \
      libgtk-3-0t64 \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f
RUN xvfb-run -a -s '-screen 0 1920x1080x24' glxinfo -B | tee /tmp/glxinfo.txt \
 && grep -q 'OpenGL renderer string: llvmpipe' /tmp/glxinfo.txt \
 && ffmpeg -hide_banner -version | head -n 1 \
 && rm /tmp/glxinfo.txt
CMD ["/bin/bash"]
