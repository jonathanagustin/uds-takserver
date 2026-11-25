# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

backup:
  dump:
    publishImage: "ghcr.io/jonathanagustin/uds-takserver/takserver-config:{{ .Values.tak.imageTag }}"
