#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Shared settings for the live-server stage scripts. Source it, do not run it.
# Nothing here is a secret. Secrets live under ${SECRETS} with mode 600.

PROTO_STATE="${PROTO_STATE:-/var/lib/takx-proto}"
SERVER_STATE="${PROTO_STATE}/server"
SECRETS="${PROTO_STATE}/secrets"
STATUS_DIR="${PROTO_STATE}/status"
LOGS="${SERVER_STATE}/logs"
WORK="${SERVER_STATE}/work"
TOOLS="${SERVER_STATE}/tools"
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
# The session proxy re-terminates TLS. Its CA is trusted, never bypassed.
SESSION_CA="${SESSION_CA:-/root/.ccr/ca-bundle.crt}"
DOMAIN="${DOMAIN:-uds.dev}"
# Pinned in mise.toml ("aqua:defenseunicorns/uds-cli").
UDS_VERSION="${UDS_VERSION:-0.42.0}"
# Same UDS Core version as bundle/uds-bundle.yaml.
CORE_VERSION="${CORE_VERSION:-1.16.0-upstream}"
# Published package for the current release (release-please manifest).
TAK_PACKAGE_VERSION="${TAK_PACKAGE_VERSION:-1.4.0-upstream}"
# Addresses MetalLB hands to the three Istio gateway Services. They sit in
# 10/8, which the session NO_PROXY already bypasses, and outside the k3s pod
# (10.42/16) and Service (10.43/16) ranges.
LB_POOL="${LB_POOL:-10.44.0.10-10.44.0.19}"
UDS="${TOOLS}/uds-${UDS_VERSION}/uds"
PATH="${TOOLS}/uds-${UDS_VERSION}:${PATH}"

mkdir -p "${LOGS}" "${WORK}" "${TOOLS}" "${STATUS_DIR}"
mkdir -p "${SECRETS}" && chmod 700 "${SECRETS}"

# write_status <stage> <status> <notes>: resumable progress record.
write_status() {
  python3 - "$1" "$2" "$3" "${STATUS_DIR}/$1.json" <<'PY'
import json, sys, time
stage, status, notes, path = sys.argv[1:5]
json.dump({"stage": stage, "status": status, "notes": notes,
           "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
          open(path, "w"), indent=1)
PY
}
