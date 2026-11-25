#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Create and deploy the UDS Core bundle in core/uds-bundle.yaml.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"

# Istio ambient: the CNI agent bind-mounts pod network namespaces and needs a
# shared root mount. The sandbox root mount is private.
if [ "$(findmnt -n -o PROPAGATION /)" != "shared" ]; then
  mount --make-rshared /
fi

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
