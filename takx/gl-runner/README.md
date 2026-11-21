<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Prebuilt TAKX GL/JNI runner image

`ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner` pre-packages the hosted Linux `amd64` toolchain used by TAKX OpenGL ES 3.0 and JNI qualification jobs:

- Digest-pinned Debian Bookworm + Python 3.12
- OpenJDK 17 (`JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64`)
- Native build toolchain (`gcc`, `g++`, `cmake`, `ninja-build`, `make`, `pkg-config`, `git`, `patch`)
- Software OpenGL ES 3.x stack (`xvfb`, `libx11-dev`, `libegl1-mesa-dev`, `libgles2-mesa-dev`, `libgl1-mesa-dri`, with `LIBGL_ALWAYS_SOFTWARE=1` and `llvmpipe` defaults)

Pull requests build and smoke-test the image locally without pushing to GHCR. Merges to `main` (or manual `workflow_dispatch` on `main` with `publish: true`) publish the immutable image to GHCR with build provenance attestation.
