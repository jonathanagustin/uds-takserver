#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Provision the TAKX desktop identity and an identity in an unrelated group
# for the negative isolation check, then export each device Secret to
# ${SECRETS}/<name>/ (directories 700, files 600). Follows
# docs/how-to/connect-takx-desktop.md steps 1 and 2. Nothing secret is
# printed: only file names, sizes and public certificate facts.
#
#   70-takclients.sh [name:group ...]
#   default: takx-desktop:Operators takx-outsider:TakxIsolation
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"
umask 077
[ "$#" -gt 0 ] || set -- takx-desktop:Operators takx-outsider:TakxIsolation

for pair in "$@"; do
  name="${pair%%:*}"
  group="${pair#*:}"
  kubectl apply -f - <<YAML
apiVersion: tak.uds.dev/v1alpha1
kind: TakClient
metadata:
  name: ${name}
  namespace: takclients
spec:
  callsign: ${name}
  groups:
    - ${group}
YAML
done
for pair in "$@"; do
  name="${pair%%:*}"
  kubectl wait "takclient/${name}" -n takclients --for=condition=Ready --timeout=300s
done
kubectl get takclients -n takclients -o wide

for pair in "$@"; do
  name="${pair%%:*}"
  group="${pair#*:}"
  out="${SECRETS}/${name}"
  mkdir -p "${out}"
  chmod 700 "${out}"
  python3 -I "${here}/export_takclient.py" "${name}" "${group}" "${out}" "${DOMAIN}"
done
write_status server-takclients ok "TakClients $* Ready; material exported under ${SECRETS}/<name>/ (mode 600)"
