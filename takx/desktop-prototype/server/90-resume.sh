#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Bring the live server back after a sandbox VM restart, or check it at any
# time. A restart kills every process and drops the host mounts; the k3s
# datastore, the images, the registry volume and /var/lib/takx-proto survive.
# Nothing is redeployed: k3s restarts the workloads it already knows.
#
# Order matters after a restart (README.md, "After a VM restart"):
#   1. k3s, then admission (10-k3s.sh waits for the Pepr webhooks before it
#      asks the bootstrap for its pod proof);
#   2. the Zarf registry, because every workload image is pulled from it and
#      a pod that starts first sits in ErrImagePull until its back-off ends;
#   3. the mesh, then the workloads;
#   4. one known casualty, the CloudTAK pod (below);
#   5. the exported TakClient identities, which a restart invalidates.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"

"${here}/10-k3s.sh"
kubectl -n zarf rollout status deployment/zarf-docker-registry --timeout=600s
kubectl -n zarf rollout status deployment/agent-hook --timeout=300s
# istio-cni must be running before ambient pods get their network.
kubectl -n istio-system rollout status ds/istio-cni-node --timeout=300s
kubectl -n istio-system rollout status ds/ztunnel --timeout=300s
kubectl -n istio-system rollout status deployment/istiod --timeout=600s
for gateway in admin tenant passthrough; do
  kubectl -n "istio-${gateway}-gateway" rollout status "deployment/${gateway}-ingressgateway" --timeout=600s
done
kubectl -n keycloak rollout status statefulset/keycloak --timeout=900s
for deployment in takserver-config takserver-messaging takserver-api takserver-plugins; do
  kubectl -n takserver rollout status "deployment/${deployment}" --timeout=900s
done

# CloudTAK of package 1.4.0 does not survive a node restart by itself. Its
# init container merge-trust writes ca-bundle.pem with mode 0444 into an
# emptyDir. After a restart the kubelet runs the init container again in the
# same pod, the volume still holds the file, and the write fails with EACCES
# on every retry (seen 2026-11-26: 17 restarts in 66 minutes, pod 0/3). A new
# pod gets an empty volume, so a pod in that state is deleted once. The cause
# is in charts/cloudtak/files/merge-trust.mjs, not in this directory.
cloudtak_stuck() {
  kubectl -n cloudtak get pods -o json | python3 -c '
import json, sys
for pod in json.load(sys.stdin)["items"]:
    for status in pod.get("status", {}).get("initContainerStatuses", []):
        failed = status.get("lastState", {}).get("terminated", {}).get("exitCode", 0) != 0
        if status["name"] == "merge-trust" and not status.get("ready") and failed:
            print(pod["metadata"]["name"])
'
}
deadline=$((SECONDS + 600))
until kubectl -n cloudtak rollout status deployment/cloudtak --timeout=30s; do
  stuck="$(cloudtak_stuck)"
  if [[ -n "${stuck}" ]]; then
    echo "cloudtak: init container merge-trust cannot rewrite its bundle after the restart; replacing ${stuck//$'\n'/ }"
    # shellcheck disable=SC2086 # one pod name per word
    kubectl -n cloudtak delete pod ${stuck} --wait=false
  fi
  if ((SECONDS > deadline)); then
    echo "cloudtak did not become available" >&2
    kubectl -n cloudtak get pods -o wide >&2
    exit 1
  fi
done

"${here}/45-hosts.sh"

# The restart of 2026-11-26 left every exported identity revoked: when the TAK
# Server pods came back, the takserver-oidc watcher issued a new certificate
# for each TakClient (new serial, Ready transition at the restart) and the
# API answered the old ones with "Attempt to use revoked certificate". The
# API log shows the file users being created again, so the likely cause is
# the operator's rule that lost private enrollment state forces a re-issue;
# that was not followed further. The files under ${SECRETS} are exports, so
# an export whose certificate is no longer the one in the Secret is renewed.
stale=()
while read -r name group; do
  [[ -s "${SECRETS}/${name}/client.pem" ]] || continue
  want="$(kubectl -n takclients get secret "${name}" -o jsonpath='{.metadata.annotations.tak\.uds\.dev/fingerprint}')"
  have="$(openssl x509 -in "${SECRETS}/${name}/client.pem" -noout -fingerprint -sha256 | cut -d= -f2)"
  if [[ -n "${want}" && "${want}" != "${have}" ]]; then
    stale+=("${name}:${group}")
  fi
done < <(kubectl -n takclients get takclients -o jsonpath='{range .items[*]}{.metadata.name} {.spec.groups[0]}{"\n"}{end}')
if ((${#stale[@]})); then
  echo "exported identities no longer match their TakClient Secret; exporting again: ${stale[*]}"
  "${here}/70-takclients.sh" "${stale[@]}"
fi

"${here}/60-verify.sh" "${1:-takx-desktop}"
# Every pod, so a workload this script does not name still shows up.
kubectl get pods -A --no-headers | awk '
  { split($3, ready, "/") }
  $4 != "Completed" && ($4 != "Running" || ready[1] != ready[2]) { bad = 1; print "NOT READY: " $0 }
  END { exit bad }'
write_status server ok "live server checked by 90-resume.sh; see ${LOGS} and ${SERVER_STATE}/endpoints.env"
