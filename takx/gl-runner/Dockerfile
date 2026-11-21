# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Prebuilt TAKX GL/JNI qualification runner image:
# Debian Bookworm + Python 3.12 + JDK 17 + GCC/G++/CMake/Ninja + Mesa llvmpipe + Xvfb.
# Published to ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner to avoid
# repeated apt-get and toolchain installs during hosted TAKX GL/JNI runs.
FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV DEBIAN_FRONTEND=noninteractive \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    LIBGL_ALWAYS_SOFTWARE=1 \
    MESA_LOADER_DRIVER_OVERRIDE=llvmpipe \
    GALLIUM_DRIVER=llvmpipe

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      cmake \
      g++ \
      gcc \
      git \
      libegl1-mesa-dev \
      libgl1-mesa-dri \
      libgles2-mesa-dev \
      libx11-dev \
      make \
      ninja-build \
      openjdk-17-jdk-headless \
      patch \
      pkg-config \
      xvfb \
    && rm -rf /var/lib/apt/lists/*
