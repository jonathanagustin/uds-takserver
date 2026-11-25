#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Create and deploy the UDS Core bundle in core/uds-bundle.yaml.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"

"${here}/05-host-prep.sh"
HOSTPROC="${HOSTPROC:-/run/takx-hostproc}"

# The istio-cni chart hardcodes hostPath /proc for its host-procfs volume and
# the deploy waits for the DaemonSet, so the volume is repointed while the
# deploy runs (see 05-host-prep.sh). A later Helm upgrade of the cni chart
# resets it; this loop puts it back.
patch_cni() {
  local path
  for _ in $(seq 1 180); do
    path="$(kubectl -n istio-system get ds istio-cni-node \
      -o jsonpath='{.spec.template.spec.volumes[?(@.name=="cni-host-procfs")].hostPath.path}' 2>/dev/null || true)"
    if [ "${path}" = "/proc" ]; then
      kubectl -n istio-system get ds istio-cni-node -o json | python3 -c '
import json, sys
spec = json.load(sys.stdin)["spec"]["template"]["spec"]
index = [v["name"] for v in spec["volumes"]].index("cni-host-procfs")
print(json.dumps([{"op": "replace", "path": "/spec/template/spec/volumes/%d/hostPath/path" % index, "value": sys.argv[1]}]))
' "${HOSTPROC}" > "${WORK}/cni-patch.json"
      kubectl -n istio-system patch ds istio-cni-node --type=json --patch-file "${WORK}/cni-patch.json"
    fi
    sleep 5
  done
}
patch_cni >"${LOGS}/40-core-cni-patch.log" 2>&1 &
patcher=$!
trap 'kill "${patcher}" 2>/dev/null || true' EXIT

out="${WORK}/core-bundle"
mkdir -p "${out}"
cd "${WORK}"
bundle="$(ls "${out}"/uds-bundle-takx-proto-core-amd64-*.tar.zst 2>/dev/null | head -1 || true)"
if [ -z "${bundle}" ]; then
  "${UDS}" create "${here}/core" --confirm --no-color --output "${out}"
  bundle="$(ls "${out}"/uds-bundle-takx-proto-core-amd64-*.tar.zst | head -1)"
fi
"${UDS}" deploy "${bundle}" --confirm --no-color "$@"
kubectl get pods -A
kubectl get svc -A --field-selector spec.type=LoadBalancer
write_status server-core ok "UDS Core ${CORE_VERSION} base + identity-authorization deployed from ${bundle}"
