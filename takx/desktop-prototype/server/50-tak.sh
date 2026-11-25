#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Create and deploy the TAK Server bundle in tak/uds-bundle.yaml.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"


out="${WORK}/tak-bundle"
mkdir -p "${out}"
cd "${WORK}"
pattern="${out}/uds-bundle-takx-proto-tak-amd64-*.tar.zst"
bundle="$(compgen -G "${pattern}" | head -1 || true)"
if [ -z "${bundle}" ]; then
  "${UDS}" create "${here}/tak" --confirm --no-color --output "${out}"
  bundle="$(compgen -G "${pattern}" | head -1)"
fi
"${UDS}" deploy "${bundle}" --confirm --no-color "$@"
kubectl get pods -n takserver
kubectl get pods -n cloudtak
write_status server-tak ok "takserver ${TAK_PACKAGE_VERSION} with CloudTAK deployed from ${bundle}"
