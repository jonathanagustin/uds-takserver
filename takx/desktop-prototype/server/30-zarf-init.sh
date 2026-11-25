#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Initialise Zarf (in-cluster registry and agent) and add the CoreDNS
# ConfigMap that uds-k3d would have shipped.
#
# Adaptation: k3d-core-slim-dev brings a Zarf-initialised cluster and the
# kube-system/coredns-custom ConfigMap. Native k3s mounts that ConfigMap as
# optional and does not create it. Without it the takserver-oidc watcher skips
# its passthrough rewrites and pods cannot resolve sso.<domain>.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"

re="${DOMAIN//./\\.}"
kubectl -n kube-system get configmap coredns-custom >/dev/null 2>&1 || kubectl apply -f - <<YAML
apiVersion: v1
kind: ConfigMap
metadata:
  name: coredns-custom
  namespace: kube-system
data:
  uds.override: |
    rewrite stop {
      name regex (.*\.admin\.${re}) admin-ingressgateway.istio-admin-gateway.svc.cluster.local answer auto
    }
    rewrite stop {
      name regex (.*\.${re}) tenant-ingressgateway.istio-tenant-gateway.svc.cluster.local answer auto
    }
YAML
kubectl -n kube-system rollout restart deployment/coredns
kubectl -n kube-system rollout status deployment/coredns --timeout=180s

cd "${WORK}"
if ! kubectl -n zarf get secret zarf-state >/dev/null 2>&1; then
  ls "${WORK}"/zarf-init-amd64-*.tar.zst >/dev/null 2>&1 ||
    "${UDS}" zarf tools download-init --output-directory "${WORK}"
  "${UDS}" zarf init --confirm --no-color
  rm -f "${WORK}"/zarf-init-amd64-*.tar.zst
fi
kubectl -n zarf get pods
write_status server-zarf-init ok "zarf $("${UDS}" zarf version) init done; coredns-custom created for ${DOMAIN}"
