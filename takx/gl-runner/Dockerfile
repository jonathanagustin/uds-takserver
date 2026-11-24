# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Prebuilt TAKX GL/JNI qualification runner image:
# Debian Bookworm + Python 3.12 + JDK 17 + GCC/G++/CMake/Ninja + Mesa llvmpipe + Xvfb.
# Published to ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner to avoid
# repeated apt-get and toolchain installs during hosted TAKX GL/JNI runs.
FROM python:3.15.2-slim-bookworm@sha256:498829c717b1e59fc42f0e7865eafe4e04ca0f70a3769d4ae70ba218df54507c

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
      xauth \
      xvfb \
    && rm -rf /var/lib/apt/lists/*
