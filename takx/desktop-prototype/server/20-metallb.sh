#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Give each Istio gateway Service its own LoadBalancer address.
#
# Adaptation: uds-k3d ships MetalLB inside its cluster package; native k3s has
# only klipper servicelb, which cannot give three Services the same ports.
# Only the MetalLB controller (address assignment) is kept. The speaker
# DaemonSet is removed: on one node kube-proxy already delivers traffic for a
# Service's load balancer address from the host, from pods and from Docker
# bridge networks, so nothing has to be announced on a network segment.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"
METALLB_VERSION="${METALLB_VERSION:-v0.15.2}"
manifest="${WORK}/metallb-native-${METALLB_VERSION}.yaml"

[ -s "${manifest}" ] || curl -sfL --retry 3 -o "${manifest}" \
  "https://raw.githubusercontent.com/metallb/metallb/${METALLB_VERSION}/config/manifests/metallb-native.yaml"
sha256sum "${manifest}"
kubectl apply -f "${manifest}"
kubectl -n metallb-system delete daemonset speaker --ignore-not-found
kubectl -n metallb-system rollout status deployment/controller --timeout=300s
# The validating webhook needs a moment after the controller is Ready.
for _ in $(seq 1 30); do
  if kubectl apply -f - <<YAML
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: takx-proto
  namespace: metallb-system
spec:
  addresses:
    - ${LB_POOL}
YAML
  then
    break
  fi
  sleep 5
done
kubectl -n metallb-system get ipaddresspool takx-proto
write_status server-metallb ok "MetalLB ${METALLB_VERSION} controller only, pool ${LB_POOL}"
