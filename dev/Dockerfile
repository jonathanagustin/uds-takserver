# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Official multiarchitecture index: native Linux amd64 and arm64.
FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash ca-certificates coreutils curl git jq patch procps util-linux zstd \
    && rm -rf /var/lib/apt/lists/*

COPY install-tools /tmp/uds-dev-install-tools
RUN sh /tmp/uds-dev-install-tools /usr/local/bin \
    && rm /tmp/uds-dev-install-tools

COPY requirements.txt /tmp/uds-dev-requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore -r /tmp/uds-dev-requirements.txt \
    && rm /tmp/uds-dev-requirements.txt \
    && ln -s /usr/local/bin/python3 /usr/bin/python3

COPY check /usr/local/bin/uds-dev
COPY smoke /usr/local/bin/uds-dev-smoke
RUN chmod 0755 /usr/local/bin/uds-dev /usr/local/bin/uds-dev-smoke

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/uds-dev"]
CMD ["check"]
