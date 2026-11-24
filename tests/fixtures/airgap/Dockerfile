# syntax=docker/dockerfile:1
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf
RUN --mount=type=secret,id=ca,required=true SSL_CERT_FILE=/run/secrets/ca apk add --no-cache iptables=1.8.11-r1 curl=8.14.1-r4 busybox-extras=1.37.0-r20
CMD ["sleep", "infinity"]
