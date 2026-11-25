#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Boot native k3s with the sandbox owner's bootstrap. Idempotent: run it again
# after a VM restart (every process dies, the disk and the datastore survive).
#
# Adaptation from the repository's k3d-core-slim-dev path: the bootstrap starts
# k3s with klipper servicelb enabled, and servicelb can bind host ports 80/443
# for only one of the three Istio gateway Services. /etc/rancher/k3s/config.yaml
# is read by `k3s server` in addition to the bootstrap's flags, so servicelb is
# disabled there and 20-metallb.sh supplies the addresses, as uds-k3d does.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"
BOOTSTRAP="${BOOTSTRAP:-/home/user/marketplace/plugins/ccw-k3s/skills/cluster/scripts/bootstrap-k3s.sh}"

avail_kb="$(df --output=avail -k / | tail -1 | tr -d ' ')"
echo "disk available: $((avail_kb / 1024)) MiB"
if [ "${avail_kb}" -lt $((12 * 1024 * 1024)) ]; then
  echo "less than 12 GiB available; image pulls would trip the kubelet eviction threshold" >&2
  exit 1
fi

"${here}/05-host-prep.sh"

mkdir -p /etc/rancher/k3s
if ! grep -qs 'servicelb' /etc/rancher/k3s/config.yaml; then
  if pgrep -f 'k3s[ ]server' >/dev/null; then
    echo "k3s is running without the servicelb setting; stop it first" >&2
    exit 1
  fi
  cat >> /etc/rancher/k3s/config.yaml <<'CFG'
# Written by takx/desktop-prototype/server/10-k3s.sh.
disable:
  - servicelb
CFG
fi

"${BOOTSTRAP}" --verify
kubectl get nodes -o wide
if kubectl -n kube-system get ds -o name | grep -q svclb; then
  echo "servicelb DaemonSets exist; the disable setting did not apply" >&2
  exit 1
fi
write_status server-k3s ok "k3s up via ${BOOTSTRAP} --verify; servicelb disabled in /etc/rancher/k3s/config.yaml"
