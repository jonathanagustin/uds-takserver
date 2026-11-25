# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Self-contained image of the TAKX 5.7 desktop prototype: the assembled tree
# (../assemble), the JDK it booted on, an X server that speaks RFB on a Unix
# socket (Xtigervnc), openbox, and the websockify + noVNC browser bridge.
# One image, three entrypoint modes: desktop | web | identity (entrypoint.sh).
#
# What is inside is TAKX 5.7 (0b59a9bb) built from public sources on Kernel
# 8.30.5 + project patches, CommonCommo 1.17.2 patched and the NASA WorldWind
# interop build. It is not the official TAKX 5.7 binary distribution.
#
# Build with build.sh (it passes the three contexts and the manifest digest):
#   context   takx/desktop-prototype (Dockerfile.dockerignore keeps it small)
#   tree      a copy of the assembled tree, dist/takx57
#   notices   licence texts, NOTICE.md, SOURCES.json, patches (gen_notices.py)
# The CA secret is optional and only needed behind a TLS-intercepting proxy.
# It is mounted for single RUN steps and never written to a layer.
#
# No '# syntax=' line on purpose: the frontend built into BuildKit is used, so
# a build needs no pull from Docker Hub.
ARG BASE=mirror.gcr.io/library/debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6

# Eclipse Temurin 17.0.21+9: the same asset and SHA-256 as ../Dockerfile, the
# JDK every proven boot of the prototype ran on.
FROM ${BASE} AS jdk
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
ARG TEMURIN_URL=https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.21%2B9/OpenJDK17U-jdk_x64_linux_hotspot_17.0.21_9.tar.gz
ARG TEMURIN_SHA256=8b9b611d917e3dfd8419d3566b5949226f69b052fed7e109c1368add1dfe8711
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export CURL_CA_BUNDLE=/run/secrets/ca; fi; \
    mkdir -p /tools/temurin-17.0.21_9 \
 && curl -fsSL --retry 5 --retry-all-errors -o /tmp/jdk.tgz "${TEMURIN_URL}" \
 && echo "${TEMURIN_SHA256}  /tmp/jdk.tgz" | sha256sum -c - \
 && tar -xzf /tmp/jdk.tgz -C /tools/temurin-17.0.21_9 --no-same-owner \
 && rm /tmp/jdk.tgz \
 && test -x /tools/temurin-17.0.21_9/jdk-17.0.21+9/bin/keytool \
 && printf 'url\t%s\nsha256\t%s\n' "${TEMURIN_URL}" "${TEMURIN_SHA256}" > /tools/temurin-17.0.21_9/ORIGIN.tsv

# noVNC from Debian without its nodejs dependency: the package is unpacked,
# not installed. Only the static web files and the copyright file are kept.
FROM ${BASE} AS novnc
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /tmp/novnc
RUN apt-get update \
 && apt-get download novnc \
 && rm -rf /var/lib/apt/lists/* \
 && dpkg-deb -x novnc_*.deb /tmp/novnc/x \
 && mkdir -p /out/web-root /out/doc \
 && cp -a /tmp/novnc/x/usr/share/novnc/. /out/web-root/ \
 && cp /tmp/novnc/x/usr/share/doc/novnc/copyright /out/doc/copyright \
 && test -s /out/web-root/vnc.html \
 && test -z "$(find /out/web-root -xtype l)" \
 && printf '%s\t%s\t%s\t%s\tunpacked with dpkg-deb -x, not installed\n' \
      "$(dpkg-deb -f novnc_*.deb Package)" "$(dpkg-deb -f novnc_*.deb Version)" \
      "$(dpkg-deb -f novnc_*.deb Source | cut -d' ' -f1 | grep . || dpkg-deb -f novnc_*.deb Package)" \
      "$(dpkg-deb -f novnc_*.deb Version)" > /out/novnc-package.tsv

# Operating system layer: display stack, Mesa software rendering, fonts and
# the libraries the JVM and the bundled natives need. No compiler, no nodejs,
# no ffmpeg, no Xvfb.
FROM ${BASE} AS os
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tini tigervnc-standalone-server websockify python3 openbox xdotool x11-utils xauth \
      libstdc++6 libgl1 libglx-mesa0 libgl1-mesa-dri libegl1 libegl-mesa0 libgles2 libglvnd0 libgbm1 \
      fontconfig fonts-dejavu-core fonts-liberation2 fonts-noto-core \
      libx11-6 libxext6 libxrender1 libxtst6 libxi6 libxrandr2 libxcursor1 libxinerama1 \
      libxxf86vm1 libxfixes3 libxcomposite1 libxdamage1 libfreetype6 libasound2t64 \
      libgtk-3-0t64 \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f \
 && groupadd --gid 10001 takx \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /run/takx/session/home --shell /usr/sbin/nologin takx \
 && groupadd --gid 10002 takxweb \
 && useradd --uid 10002 --gid 10002 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin takxweb \
 && mkdir -p /run/takx/session /run/takx/vnc /run/takx/identity /run/takx/secret /usr/share/takx-desktop \
 && for tool in nodejs node ffmpeg Xvfb gcc cc; do \
      if command -v "${tool}" >/dev/null; then echo "unexpected tool ${tool}"; exit 1; fi; done
# The mount points carry the ownership and mode their volumes need: Docker
# copies both to a new named volume on first use, and a Kubernetes emptyDir
# with fsGroup 10001 ends up equivalent. The web user (10002) reaches the RFB
# socket through the supplementary group 10001 and nothing else.
RUN chown 10001:10001 /run/takx/session /run/takx/vnc /run/takx/identity \
 && chmod 0750 /run/takx/session /run/takx/identity \
 && chmod 2770 /run/takx/vnc
COPY --from=jdk /tools /tools
COPY --from=novnc /out/web-root /opt/takx-desktop/web-root
COPY --from=novnc /out/doc/copyright /usr/share/doc/novnc/copyright
COPY --from=novnc /out/novnc-package.tsv /usr/share/takx-desktop/novnc-package.tsv
ENV JAVA_HOME=/tools/temurin-17.0.21_9/jdk-17.0.21+9
ENV PATH=/tools/temurin-17.0.21_9/jdk-17.0.21+9/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Binary and source package versions of everything Debian installed here.
RUN dpkg-query -W -f='${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n' \
      > /usr/share/takx-desktop/debian-packages.tsv \
 && cat /usr/share/takx-desktop/novnc-package.tsv >> /usr/share/takx-desktop/debian-packages.tsv \
 && rm /usr/share/takx-desktop/novnc-package.tsv \
 && test ! -e /var/lib/takx-proto

FROM os AS base
# The tree is copied byte for byte: no patchelf, no symlinks, no repacked JAR,
# so manifest.json keeps describing exactly what is in the image. File modes
# stay as assembled (0644/0755, owner root): the client runs as uid 10001 on
# a read-only root and cannot change them. They must not be made read-only
# for the owner: TAKX copies conf/Default*.xml into a new project together
# with their mode, and a read-only copy ends in an "Access Denied" dialog
# instead of the globe (seen with --chmod=a-w on this COPY).
COPY --from=tree --chown=0:0 . /opt/takx
COPY --chmod=0555 assemble/launch.sh /opt/takx-desktop/launch.sh
COPY --chmod=0555 image/entrypoint.sh image/healthcheck.sh image/stage_identity.py \
     image/verify_tree.py image/check_native_closure.py image/elf_audit.sh /opt/takx-desktop/
COPY --chmod=0444 image/web/index.html /opt/takx-desktop/web-root/index.html
COPY --from=notices --chown=0:0 . /usr/share/takx-desktop/
RUN test ! -e /var/lib/takx-proto \
 && test -s /usr/share/takx-desktop/NOTICE.md \
 && test -s /usr/share/takx-desktop/SOURCES.json \
 && test -s /usr/share/takx-desktop/LICENSES/GPL-3.0-only.txt

# Gates (verify/build_gates.sh). The tools they need (binutils, mesa-utils)
# live in a stage of their own on top of 'os', so they are not installed
# again for every change of the tree or the scripts. The tree and the scripts
# are bind-mounted from 'base', the stage the final image is made of. Only
# the reports in /verify reach the final image.
FROM os AS verify-tools
RUN apt-get update \
 && apt-get install -y --no-install-recommends binutils mesa-utils \
 && rm -rf /var/lib/apt/lists/*

FROM verify-tools AS verify
ARG TREE_MANIFEST_SHA256
RUN --mount=type=bind,from=base,source=/opt,target=/opt \
    --mount=type=bind,source=image/verify/build_gates.sh,target=/tmp/build_gates.sh \
    bash /tmp/build_gates.sh "${TREE_MANIFEST_SHA256}" /verify

FROM base
ARG TREE_MANIFEST_SHA256
COPY --from=verify /verify /usr/share/takx-desktop/verify
LABEL org.opencontainers.image.title="TAKX 5.7 desktop prototype (public-source build)" \
      org.opencontainers.image.description="TAKX 5.7 (0b59a9bb) built from public sources on Kernel 8.30.5 + project patches, CommonCommo 1.17.2 patched and the NASA WorldWind interop build; Xtigervnc, openbox, websockify and noVNC from Debian trixie. Prototype: not the official TAKX 5.7 distribution, not qualified." \
      org.opencontainers.image.licenses="GPL-3.0-only" \
      org.opencontainers.image.source="https://github.com/jonathanagustin/uds-takserver" \
      io.github.jonathanagustin.uds-takserver.takx.tree-manifest-sha256="${TREE_MANIFEST_SHA256}" \
      io.github.jonathanagustin.uds-takserver.takx.notices="/usr/share/takx-desktop"
ENV HOME=/run/takx/session/home
WORKDIR /run/takx/session
EXPOSE 6080
USER 10001:10001
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/takx-desktop/entrypoint.sh"]
CMD ["desktop"]
