#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Bring the live server back after a sandbox VM restart, or check it at any
# time. A restart kills every process and drops the host mounts; the k3s
# datastore, the images, the registry volume and /var/lib/takx-proto survive.
# Nothing is redeployed: k3s restarts the workloads it already knows.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"

"${here}/10-k3s.sh"
# istio-cni must be running before ambient pods get their network.
kubectl -n istio-system rollout status ds/istio-cni-node --timeout=300s
kubectl -n istio-system rollout status ds/ztunnel --timeout=300s
for deployment in takserver-config takserver-messaging takserver-api takserver-plugins; do
  kubectl -n takserver rollout status "deployment/${deployment}" --timeout=900s
done
kubectl -n cloudtak rollout status deployment/cloudtak --timeout=600s
"${here}/45-hosts.sh"
"${here}/60-verify.sh" "${1:-takx-desktop}"
write_status server ok "live server checked by 90-resume.sh; see ${LOGS} and ${SERVER_STATE}/endpoints.env"
