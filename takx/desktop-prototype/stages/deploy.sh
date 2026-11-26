#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, deploy stage: put the takx-desktop Zarf package
# (stages/package.sh) on a cluster that already runs UDS Core and the TAK
# Server package, and collect the evidence that only a deploy can give
# (../zarf/README.md, "What the deploy showed"). Opt-in; nothing in the
# TAK Server package or its CI calls it. It was written against the sandbox
# cluster of ../server/README.md and has run there only.
#
#   deploy.sh STEP [STEP ...]
#
#   preflight   memory, disk and node conditions; every pod Ready; records
#               `kubectl get pods -A` as the baseline. Refuses when the
#               desktop namespace already exists
#   deploy      `uds zarf package deploy` with both optional components
#   admission   the admitted pod next to the Deployment's pod template: what
#               Pepr, the UDS operator and the Zarf agent changed; the list
#               of policy exemptions
#   identity    TakClient condition, the two-key Secret copy (names and sizes
#               only), the init container's log, modes of the staged files
#   boot        one frame of the pod's display (RFB on its Unix socket, via
#               kubectl exec), memory and CPU from the container's cgroup,
#               restart counts. Whether the frame shows the globe is for a
#               person to judge
#   gate        requests without a session to / and /websockify, then a real
#               browser: a user in the allowed group must reach the desktop,
#               a user outside it must not (../server/deploy_browser.cjs)
#   isolation   pod ports from a pod in another namespace, from a pod in the
#               desktop namespace and from the node; egress from the desktop
#               container (../server/deploy_probe.py). Creates and deletes
#               namespace takx-probe and pod takx-desktop/takx-probe
#   lifecycle   deletes the pod, waits for its replacement, takes a frame
#   remove      `uds zarf package remove`, deletes the namespace the package
#               created, compares the cluster with the baseline
#
# Environment: TAKX_PROTO_ROOT (default /var/lib/takx-proto), TAKX_PACKAGE
# (default: the one package under pkg/out), UDS (default: the pinned CLI of
# ../server/15-uds-cli.sh), TAKX_DEPLOY_MIN_MEMORY_MIB (default 4096),
# TAKX_DEPLOY_MIN_DISK_MIB (default 6144), TAKX_DEVUSERS (directory of
# ../server/deploy_devusers.py, default secrets/keycloak-dev-users).
#
# Nothing secret is printed or stored in the evidence: Secret values are
# compared and counted, never shown; the browser steps read user names and
# passwords from mode 600 files. Evidence goes to $TAKX_PROTO_ROOT/deploy/run.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
proto="$(cd "${here}/.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
out="${root}/deploy/run"
uds="${UDS:-${root}/server/tools/uds-0.42.0/uds}"
ns=takx-desktop
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
# The gateways are reached directly, never through the session proxy.
export NO_PROXY=".uds.dev,${NO_PROXY:-}" no_proxy=".uds.dev,${no_proxy:-}"
mkdir -p "${out}"

say() { printf '== %s %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { echo "deploy.sh: $*" >&2; exit 1; }
desktop_pod() { kubectl -n "${ns}" get pod -l app.kubernetes.io/name=takx-desktop -o jsonpath='{.items[0].metadata.name}'; }

package() {
  local found=("${root}"/pkg/out/zarf-package-takx-desktop-amd64-*.tar.zst)
  if [[ -n "${TAKX_PACKAGE:-}" ]]; then
    echo "${TAKX_PACKAGE}"
  elif [[ "${#found[@]}" -eq 1 && -s "${found[0]}" ]]; then
    echo "${found[0]}"
  else
    die "expected exactly one package under ${root}/pkg/out; set TAKX_PACKAGE"
  fi
}

# Every pod of the cluster, without the desktop's own namespaces: name, UID
# and restart count. Two of these are compared by `remove`.
pods_state() {
  kubectl get pods -A -o json | python3 -I -c '
import json, sys
for pod in json.load(sys.stdin)["items"]:
    meta = pod["metadata"]
    if meta["namespace"] in ("takx-desktop", "takx-probe"):
        continue
    statuses = pod["status"].get("containerStatuses", [])
    ready = "%d/%d" % (sum(s["ready"] for s in statuses), len(statuses))
    print(meta["namespace"], meta["name"], meta["uid"], pod["status"]["phase"], ready,
          sum(s["restartCount"] for s in statuses))'
}

not_ready() {
  kubectl get pods -A --no-headers | awk '
    { split($3, ready, "/") }
    $4 != "Completed" && ($4 != "Running" || ready[1] != ready[2]) { print }'
}

step_preflight() {
  local memory disk pressure waiting
  if kubectl get namespace "${ns}" >/dev/null 2>&1; then
    die "namespace ${ns} exists; the baseline must be taken before the deploy"
  fi
  memory="$(free -m | awk 'NR == 2 { print $7 }')"
  disk="$(df -m --output=avail / | tail -1 | tr -d ' ')"
  echo "memory available ${memory} MiB, disk available ${disk} MiB"
  [[ "${memory}" -ge "${TAKX_DEPLOY_MIN_MEMORY_MIB:-4096}" ]] || die "not enough memory for the desktop's 5 GiB limit"
  [[ "${disk}" -ge "${TAKX_DEPLOY_MIN_DISK_MIB:-6144}" ]] || die "not enough disk: the deploy needs about 4 GiB while it runs"
  pressure="$(kubectl get nodes -o jsonpath='{range .items[*]}{range .status.conditions[?(@.status=="True")]}{.type}{" "}{end}{end}')"
  echo "node conditions that are True: ${pressure}"
  [[ "${pressure}" == "Ready " ]] || die "a node reports something other than Ready"
  waiting="$(not_ready)"
  [[ -z "${waiting}" ]] || die "pods are not ready: ${waiting}"
  kubectl get pods -A -o wide >"${out}/baseline-pods.txt"
  pods_state >"${out}/baseline-pods-state.txt"
  kubectl get namespaces -o name >"${out}/baseline-namespaces.txt"
  kubectl get takclients.tak.uds.dev,packages.uds.dev,exemptions.uds.dev -A -o name >"${out}/baseline-custom-resources.txt"
  kubectl -n takclients get secrets -o name >"${out}/baseline-takclient-secrets.txt"
  echo "baseline: $(wc -l <"${out}/baseline-pods-state.txt") pods in ${out}/baseline-pods.txt"
}

step_deploy() {
  local pkg
  pkg="$(package)"
  [[ -s "${out}/baseline-pods-state.txt" ]] || die "no baseline; run preflight first"
  say "deploying ${pkg} ($(sha256sum "${pkg}" | cut -c1-64))"
  (cd "${out}" && "${uds}" zarf package deploy "${pkg}" \
    --components takx-desktop-takclient,takx-desktop-identity --confirm --no-color) 2>&1 | tee "${out}/deploy.log"
  kubectl -n "${ns}" get pods,packages.uds.dev,networkpolicies,authorizationpolicies.security.istio.io -o wide
}

step_admission() {
  local pod
  pod="$(desktop_pod)"
  kubectl -n "${ns}" get pod "${pod}" -o json >"${out}/pod.json"
  kubectl -n "${ns}" get deployment takx-desktop -o json >"${out}/deployment.json"
  python3 -I - "${out}/deployment.json" "${out}/pod.json" <<'PY' | tee "${out}/admission.txt"
import json
import sys

template = json.load(open(sys.argv[1], encoding="utf-8"))["spec"]["template"]
pod = json.load(open(sys.argv[2], encoding="utf-8"))


def flat(value, prefix=""):
    if isinstance(value, dict):
        return {k: v for key, item in value.items() for k, v in flat(item, f"{prefix}/{key}").items()}
    if isinstance(value, list):
        return {k: v for index, item in enumerate(value)
                for k, v in flat(item, f"{prefix}[{item.get('name', index) if isinstance(item, dict) else index}]").items()}
    return {prefix: value}


before, after = flat(template["spec"]), flat(pod["spec"])
for key in sorted(after):
    if key not in before:
        print("spec added  ", key, "=", after[key])
    elif before[key] != after[key]:
        print("spec changed", key, ":", before[key], "->", after[key])
for key in sorted(set(before) - set(after)):
    print("spec removed", key, "=", before[key])
for kind in ("labels", "annotations"):
    had = template["metadata"].get(kind, {})
    for key, value in sorted(pod["metadata"].get(kind, {}).items()):
        if key not in had:
            print(f"{kind} added", key, "=", value)
PY
  echo "policy exemptions in the cluster (none may name ${ns}):"
  kubectl get exemptions.uds.dev -A -o json | python3 -I -c '
import json, sys
for item in json.load(sys.stdin)["items"]:
    spaces = sorted({m.get("namespace", "") for e in item["spec"]["exemptions"] for m in [e["matcher"]]})
    print(" ", item["metadata"]["name"], "matches namespaces", spaces)
    if "takx-desktop" in spaces:
        sys.exit("an exemption names takx-desktop")'
}

step_identity() {
  local pod name
  pod="$(desktop_pod)"
  name="$(kubectl -n "${ns}" get configmap takx-desktop-identity-info -o jsonpath='{.data.takclient}')"
  {
    kubectl -n takclients get takclient "${name}" \
      -o jsonpath='TakClient {.metadata.name}: {.status.conditions[0].type}={.status.conditions[0].status} ({.status.conditions[0].reason}){"\n"}'
    python3 -I - "${name}" <<'PY'
import base64
import json
import subprocess
import sys


def data(namespace, name):
    done = subprocess.run(["kubectl", "-n", namespace, "get", "secret", name, "-o", "json"],
                          check=True, capture_output=True)
    return json.loads(done.stdout)["data"]


source, copy = data("takclients", sys.argv[1]), data("takx-desktop", "takx-desktop-identity")
print("source Secret keys and sizes:", {key: len(base64.b64decode(value)) for key, value in source.items()})
print("copied Secret keys and sizes:", {key: len(base64.b64decode(value)) for key, value in copy.items()})
print("copied keys identical to the source:", all(source.get(key) == value for key, value in copy.items()))
print("keys that stay in takclients:", sorted(set(source) - set(copy)))
PY
    echo "init container log:"
    kubectl -n "${ns}" logs "${pod}" -c identity
    echo "staged files, as the desktop container sees them:"
    kubectl -n "${ns}" exec "${pod}" -c desktop -- sh -c 'id; stat -c "%a %U:%G %s %n" /run/takx/identity /run/takx/identity/*; grep " /run/takx/identity " /proc/mounts'
    echo "the web container:"
    kubectl -n "${ns}" exec "${pod}" -c web -- sh -c 'id; ls -A /run/takx/identity /run/takx/secret; stat -c "%a %U:%G %n" /run/takx/vnc/rfb.sock'
  } 2>&1 | tee "${out}/identity.txt"
}

# frame FILE: the pod's display as a PNG, facts as FILE.json
frame() {
  kubectl -n "${ns}" exec -i "$(desktop_pod)" -c desktop -- python3 -I - --unix /run/takx/vnc/rfb.sock --png - \
    --second-after 5 <"${proto}/image/verify/rfb_probe.py" >"$1" 2>"$1.json"
}

step_boot() {
  local pod
  pod="$(desktop_pod)"
  frame "${out}/pod-frame.png"
  python3 -I -c 'import json, sys; f = json.load(open(sys.argv[1])); print("frame", f["width"], "x", f["height"], f["status"], "pixels changed after 5 s:", f["second_frame"]["pixels_changed"])' "${out}/pod-frame.png.json"
  # shellcheck disable=SC2016 # the arithmetic is for the shell in the container
  kubectl -n "${ns}" exec "${pod}" -c desktop -- sh -c '
    echo "desktop container memory now $(( $(cat /sys/fs/cgroup/memory/memory.usage_in_bytes) / 1048576 )) MiB, peak $(( $(cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes) / 1048576 )) MiB, CPU used $(( $(cat /sys/fs/cgroup/cpuacct/cpuacct.usage) / 1000000000 )) s"'
  kubectl -n "${ns}" get pod "${pod}" \
    -o jsonpath='{.metadata.name} started {.status.startTime}{range .status.containerStatuses[*]} {.name}: restarts {.restartCount}{end}{"\n"}'
  echo "look at ${out}/pod-frame.png"
}

step_gate() {
  local path users="${TAKX_DEVUSERS:-${root}/secrets/keycloak-dev-users}" state="${root}/secrets/takx-desktop-browser"
  {
    for path in / /vnc.html /websockify; do
      printf 'GET %s without a session: ' "${path}"
      curl -sS -o /dev/null --max-time 15 -w '%{http_code} -> %{redirect_url}\n' "https://takx.uds.dev${path}" | sed -E 's/\?.*//'
    done
    printf 'WebSocket upgrade to /websockify without a session: '
    curl -sS -o /dev/null --max-time 15 --http1.1 -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
      -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
      -w '%{http_code} -> %{redirect_url}\n' "https://takx.uds.dev/websockify" | sed -E 's/\?.*//'
  } | tee "${out}/gate-unauthenticated.txt"
  if grep -vq '^.*: 302 -> https://sso\.' "${out}/gate-unauthenticated.txt"; then
    die "a request without a session was not sent to Keycloak"
  fi
  [[ -s "${users}/doug.json" && -s "${users}/mapuser.json" ]] || die "no test users; run server/deploy_devusers.py"
  mkdir -p "${state}" && chmod 700 "${state}"
  NODE_PATH="${NODE_PATH:-$(npm root -g)}"
  export NODE_PATH
  node "${proto}/server/deploy_browser.cjs" gate "${users}/doug.json" "${out}/gate-allowed" allowed "${state}/doug-state.json" >/dev/null
  node "${proto}/server/deploy_browser.cjs" gate "${users}/mapuser.json" "${out}/gate-denied" denied >/dev/null
  echo "allowed user reached the desktop: ${out}/gate-allowed/desktop-through-browser.png; refused user: ${out}/gate-denied/gate-mapuser.json"
  kubectl -n "${ns}" logs deployment/uds-takx-desktop-waypoint | grep -c 'ext_authz_denied' | sed 's/^/requests the waypoint answered itself (ext_authz_denied): /'
}

probe_pod() { # namespace
  local image
  image="$(kubectl -n "${ns}" get deployment takx-desktop -o jsonpath='{.spec.template.spec.containers[0].image}')"
  kubectl apply -f - <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: takx-probe
  namespace: $1
  labels:
    app.kubernetes.io/name: takx-probe
spec:
  automountServiceAccountToken: false
  enableServiceLinks: false
  terminationGracePeriodSeconds: 1
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: probe
      image: "${image}"
      imagePullPolicy: IfNotPresent
      command: ["sleep", "3600"]
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
      resources:
        requests: {cpu: 10m, memory: 32Mi}
        limits: {cpu: 500m, memory: 128Mi}
YAML
}

step_isolation() {
  local pod ip service gateway probe="${proto}/server/deploy_probe.py" targets=() line
  pod="$(desktop_pod)"
  ip="$(kubectl -n "${ns}" get pod "${pod}" -o jsonpath='{.status.podIP}')"
  service="$(kubectl -n "${ns}" get service takx-desktop -o jsonpath='{.spec.clusterIP}')"
  gateway="$(kubectl -n istio-passthrough-gateway get service passthrough-ingressgateway -o jsonpath='{.spec.clusterIP}')"
  {
    say "listeners in the pod"
    kubectl -n "${ns}" exec -i "${pod}" -c desktop -- python3 -I - listeners <"${probe}" | tee "${out}/listeners.jsonl"
    while read -r line; do
      targets+=("pod-${line}=${ip}:${line}")
    done < <(python3 -I -c '
import json, sys
for row in map(json.loads, open(sys.argv[1])):
    if row["address"] not in ("127.0.0.1", "::1") and not 15000 <= row["port"] <= 15100:
        print(row["port"])' "${out}/listeners.jsonl")
    targets+=("service-6080=${service}:6080")

    # The image is already on the node, but the kubelet checks that a pod may
    # pull it, so the probe namespace gets a copy of the registry pull Secret.
    kubectl create namespace takx-probe
    kubectl -n "${ns}" get secret private-registry -o json | python3 -I -c '
import json, sys
secret = json.load(sys.stdin)
print(json.dumps({"apiVersion": "v1", "kind": "Secret", "type": secret["type"], "data": secret["data"],
                  "metadata": {"name": "private-registry", "namespace": "takx-probe"}}))' | kubectl apply -f - >/dev/null
    probe_pod takx-probe
    probe_pod "${ns}"
    kubectl -n takx-probe wait pod/takx-probe --for=condition=Ready --timeout=120s
    kubectl -n "${ns}" wait pod/takx-probe --for=condition=Ready --timeout=120s

    say "from a pod in another namespace (control first: the Kubernetes API must answer)"
    kubectl -n takx-probe exec -i takx-probe -- python3 -I - connect control-kube-api=10.43.0.1:443 "${targets[@]}" <"${probe}"
    say "from another pod in the desktop namespace"
    kubectl -n "${ns}" exec -i takx-probe -- python3 -I - connect "${targets[@]}" <"${probe}"
    say "from the node. Expected to get through: the NetworkPolicy controller and Istio ambient both let the node's own traffic reach its pods"
    python3 -I "${probe}" connect "${targets[@]}"
    say "egress from the desktop container (reached = an answer came back)"
    kubectl -n "${ns}" exec -i "${pod}" -c desktop -- python3 -I - connect \
      "tak-streaming=takserver:8089" "kube-api=10.43.0.1:443" "kube-api-name=kubernetes.default.svc.cluster.local:443" \
      "internet-address=1.1.1.1:443" "internet-name=example.com:443" "gateway-443=${gateway}:443" \
      "tak-messaging-direct=takserver-messaging-service.takserver.svc.cluster.local:8089" \
      "keycloak=keycloak-http.keycloak.svc.cluster.local:8080" <"${probe}"
    say "the allowed route, as a TLS handshake that checks the server name against the staged CA"
    kubectl -n "${ns}" exec "${pod}" -c desktop -- sh -c '
      umask 077
      openssl pkcs12 -in /run/takx/identity/truststore.p12 -passin file:/run/takx/identity/password.txt -nokeys 2>/dev/null |
        sed -n "/BEGIN CERT/,/END CERT/p" >/tmp/probe-ca.pem
      timeout 15 openssl s_client -connect takserver:8089 -servername takserver -CAfile /tmp/probe-ca.pem \
        -verify_return_error -verify_hostname takserver </dev/null 2>&1 | grep -E "^subject=|Verified peername|Verify return code"
      rm -f /tmp/probe-ca.pem'
  } 2>&1 | tee "${out}/isolation.txt"
  kubectl -n "${ns}" delete pod takx-probe --wait=false
  kubectl delete namespace takx-probe --wait=false
  # Refused means no byte came back. Only the node may get through.
  if sed -n '/from a pod in another namespace/,/from the node/p' "${out}/isolation.txt" | grep '"target": "pod-\|"target": "service-' | grep -q '"outcome": "reached"'; then
    die "a pod port answered another pod"
  fi
}

step_lifecycle() {
  local old new
  old="$(desktop_pod)"
  say "deleting ${old}"
  kubectl -n "${ns}" delete pod "${old}" --wait=true
  kubectl -n "${ns}" rollout status deployment/takx-desktop --timeout=900s
  new="$(desktop_pod)"
  kubectl -n "${ns}" get pod "${new}" -o jsonpath='{.metadata.name} started {.status.startTime}{range .status.containerStatuses[*]} {.name}: restarts {.restartCount}{end}{"\n"}'
  frame "${out}/pod-frame-after-delete.png"
  echo "the project lives in an emptyDir (persistence.enabled=false), so the new pod has no TAK Server connection: look at ${out}/pod-frame-after-delete.png"
}

step_remove() {
  say "removing the package"
  (cd "${out}" && "${uds}" zarf package remove takx-desktop --confirm --no-color) 2>&1 | tee "${out}/remove.log"
  # Zarf deletes what its charts created. The namespace is created by Zarf
  # itself, outside any chart, and stays; it is this package's alone.
  kubectl delete namespace "${ns}" --ignore-not-found --wait=true --timeout=180s
  kubectl wait --for=delete "namespace/${ns}" --timeout=120s 2>/dev/null || true
  {
    echo "namespace ${ns}: $(kubectl get namespace "${ns}" -o name 2>&1)"
    echo "TakClient and Secret in takclients that the package made: $(kubectl -n takclients get takclient,secret -l app.kubernetes.io/part-of=takx-desktop -o name 2>&1 | tr '\n' ' ')"
    kubectl get takclients.tak.uds.dev,packages.uds.dev,exemptions.uds.dev -A -o name | diff "${out}/baseline-custom-resources.txt" - && echo "custom resources: as in the baseline"
    kubectl -n takclients get secrets -o name | diff "${out}/baseline-takclient-secrets.txt" - && echo "Secrets in takclients: as in the baseline"
    kubectl get namespaces -o name | diff "${out}/baseline-namespaces.txt" - && echo "namespaces: as in the baseline"
    pods_state | diff "${out}/baseline-pods-state.txt" - && echo "pods outside the package: same names, UIDs, readiness and restart counts as in the baseline"
  } 2>&1 | tee "${out}/remove-comparison.txt"
}

(($#)) || { sed -n '5,50p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
for step in "$@"; do
  case "${step}" in
    preflight | deploy | admission | identity | boot | gate | isolation | lifecycle | remove)
      say "${step}"
      "step_${step}"
      ;;
    -h | --help) sed -n '5,50p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
    *) die "unknown step '${step}'" ;;
  esac
done
