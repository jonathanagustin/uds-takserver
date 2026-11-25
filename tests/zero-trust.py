#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Verify UDS-native east-west Zero Trust enforcement live in the cluster.

Checks:
1. Both active namespaces (takserver, and cloudtak if installed) are enrolled
   in Istio Ambient mode without sidecar injection.
2. An unauthorized ServiceAccount pod carrying matching Postgres client pod
   labels (batch.kubernetes.io/job-name: takserver-db-setup, which matches
   the Postgres NetworkPolicy rule without triggering Pepr's TAK pod
   init-container injection) is denied east-west access to
   takserver-postgresql:5432 (SPIFFE identity enforcement) and denied
   plaintext port 8080 inside the takserver mesh (takserver-deny-plaintext-8080).
3. If CloudTAK is installed, an unauthorized ServiceAccount pod carrying
   matching pod labels (app: cloudtak) is denied east-west access to
   cloudtak-postgis:5432.
"""

import json
import os
import shlex
import subprocess
import sys
import time

DEFAULT_PROBE_SA = "zt-probe-denied"
PROBE_TIMEOUT_SECONDS = 90
MAX_LOG_CHARS = 4096


def kubectl(*args, input_text=None, check=True):
    configured = os.environ.get("KUBECTL", "")
    base = shlex.split(configured) if configured else ["uds", "zarf", "tools", "kubectl"]
    result = subprocess.run(
        base + list(args),
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return result


def verify_ambient_namespace(namespace):
    """Fail if namespace lacks ambient enrollment or any pod runs an Istio sidecar."""
    ns_res = kubectl("get", "namespace", namespace, "-o", "json")
    ns_obj = json.loads(ns_res.stdout)
    labels = (ns_obj.get("metadata") or {}).get("labels") or {}
    mode = labels.get("istio.io/dataplane-mode")
    if mode != "ambient":
        raise RuntimeError(f"namespace {namespace} istio.io/dataplane-mode={mode!r}, expected 'ambient'")
    injection = labels.get("istio-injection")
    if injection == "enabled":
        raise RuntimeError(f"namespace {namespace} has istio-injection=enabled")

    pods_res = kubectl("get", "pods", "-n", namespace, "-o", "json")
    pods = json.loads(pods_res.stdout).get("items", [])
    if not pods:
        raise RuntimeError(f"no pods found in namespace {namespace}")
    bad = []
    for pod in pods:
        meta = pod.get("metadata") or {}
        name = meta.get("name", "<unknown>")
        pod_labels = meta.get("labels") or {}
        pod_annotations = meta.get("annotations") or {}
        if pod_labels.get("sidecar.istio.io/inject") == "true" or pod_annotations.get("sidecar.istio.io/inject") == "true":
            bad.append(f"{name} has sidecar.istio.io/inject=true")
        spec = pod.get("spec") or {}
        containers = [c.get("name") for c in spec.get("containers", [])]
        containers += [c.get("name") for c in spec.get("initContainers", [])]
        if "istio-proxy" in containers or "istio-validation" in containers:
            bad.append(f"{name} has a sidecar container")
    if bad:
        raise RuntimeError("; ".join(bad))
    print(f"ok: namespace {namespace} is enrolled in ambient mode with {len(pods)} sidecar-free pods")


def build_takserver_probe_pod(image, api_pod_ip, namespace="takserver", name=DEFAULT_PROBE_SA):
    """Build a Pepr-compliant probe pod claiming Postgres NetworkPolicy labels under an unauthorized SA."""
    script = f"""set -euo pipefail
pg_bytes="$( (printf '\\x00\\x00\\x00\\x08\\x04\\xd2\\x16\\x2f' | ncat -w 5 takserver-postgresql 5432 2>/dev/null || true) | wc -c | tr -d '[:space:]' )"
if [ "${{pg_bytes}}" -gt 0 ]; then
  echo "BREACH: unauthorized SA {name} received ${{pg_bytes}} bytes from takserver-postgresql:5432" >&2
  exit 1
fi
code="$(curl -sS --max-time 5 -o /dev/null -w '%{{http_code}}' "http://{api_pod_ip}:8080/" 2>/dev/null || true)"
if [ -n "${{code}}" ] && [ "${{code}}" != "000" ]; then
  echo "BREACH: plaintext http://{api_pod_ip}:8080/ returned HTTP ${{code}}" >&2
  exit 1
fi
echo "denied: SA {name} rejected by takserver-postgresql:5432 (bytes=${{pg_bytes}}) and {api_pod_ip}:8080 (http=${{code:-000}})"
"""
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            # Matches the Postgres client NetworkPolicy selector (takserver-db-setup -> default SA)
            # without triggering Pepr's app=takserver-* wait-deps/render-oidc init-container injection.
            "labels": {"batch.kubernetes.io/job-name": "takserver-db-setup"},
        },
        "spec": {
            "serviceAccountName": name,
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1001,
                "runAsGroup": 1001,
                "fsGroup": 1001,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [{
                "name": name,
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "command": ["bash", "-c", script],
                "readinessProbe": {
                    "exec": {"command": ["false"]},
                    "periodSeconds": 1,
                },
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                },
                "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                "resources": {
                    "requests": {"cpu": "25m", "memory": "64Mi"},
                    "limits": {"cpu": "200m", "memory": "128Mi"},
                },
            }],
            "volumes": [{"name": "tmp", "emptyDir": {}}],
        },
    }


def build_cloudtak_probe_pod(image, namespace="cloudtak", name=DEFAULT_PROBE_SA):
    """Build a Pepr-compliant probe pod claiming app=cloudtak under an unauthorized SA."""
    js = (
        "const net = require('net');"
        "const sock = net.createConnection({host: 'cloudtak-postgis', port: 5432});"
        "let leaked = false;"
        "sock.setTimeout(5000);"
        "sock.on('connect', () => sock.write(Buffer.from([0,0,0,8,4,210,22,47])));"
        "sock.on('data', (buf) => { if (buf.length > 0) { leaked = true; console.error('BREACH: cloudtak-postgis responded with ' + buf.length + ' bytes'); sock.destroy(); process.exit(1); } });"
        "sock.on('timeout', () => { sock.destroy(); });"
        "sock.on('error', () => {});"
        "sock.on('close', () => { if (!leaked) { console.log('denied: SA " + name + " rejected by cloudtak-postgis:5432'); process.exit(0); } });"
    )
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app": "cloudtak"},
        },
        "spec": {
            "serviceAccountName": name,
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 101,
                "runAsGroup": 101,
                "fsGroup": 101,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [{
                "name": name,
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "command": ["node", "-e", js],
                "readinessProbe": {
                    "exec": {"command": ["node", "-e", "process.exit(1)"]},
                    "periodSeconds": 1,
                },
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                },
                "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                "resources": {
                    "requests": {"cpu": "25m", "memory": "64Mi"},
                    "limits": {"cpu": "200m", "memory": "128Mi"},
                },
            }],
            "volumes": [{"name": "tmp", "emptyDir": {}}],
        },
    }


def _cleanup_probe(namespace, pod_name, sa_name):
    kubectl("delete", "pod", pod_name, "-n", namespace, "--ignore-not-found", "--wait=false", check=False)
    kubectl("delete", "sa", sa_name, "-n", namespace, "--ignore-not-found", check=False)


def run_probe_pod(namespace, sa_name, pod_manifest, timeout_seconds=PROBE_TIMEOUT_SECONDS):
    """Run an ephemeral probe Pod with a dedicated SA and enforce exitCode == 0."""
    pod_name = pod_manifest["metadata"]["name"]
    kubectl("delete", "pod", pod_name, "-n", namespace, "--ignore-not-found", "--wait=true", check=False)
    kubectl("delete", "sa", sa_name, "-n", namespace, "--ignore-not-found", check=False)
    try:
        kubectl("create", "sa", sa_name, "-n", namespace)
        kubectl("apply", "-f", "-", input_text=json.dumps(pod_manifest))
        deadline = time.monotonic() + timeout_seconds
        exit_code = None
        while time.monotonic() < deadline:
            res = kubectl(
                "get", "pod", pod_name, "-n", namespace,
                "-o", "jsonpath={.status.containerStatuses[0].state.terminated.exitCode}",
                check=False,
            )
            val = res.stdout.strip()
            if res.returncode == 0 and val != "":
                exit_code = int(val)
                break
            time.sleep(2)

        logs_res = kubectl("logs", pod_name, "-n", namespace, check=False)
        if logs_res.stdout:
            print(logs_res.stdout[:MAX_LOG_CHARS].rstrip())
        if logs_res.stderr:
            sys.stderr.write(logs_res.stderr[:MAX_LOG_CHARS])

        if exit_code is None:
            desc_res = kubectl("describe", "pod", pod_name, "-n", namespace, check=False)
            if desc_res.stdout:
                sys.stderr.write(desc_res.stdout[:MAX_LOG_CHARS])
            raise RuntimeError(f"probe pod {namespace}/{pod_name} did not terminate within {timeout_seconds}s")
        if exit_code != 0:
            raise RuntimeError(f"probe pod {namespace}/{pod_name} failed with exit code {exit_code}")
    finally:
        _cleanup_probe(namespace, pod_name, sa_name)


def _assert_denied(label, args, expected_substrings, input_text=None):
    """Run a server dry-run admission check and require rejection with an expected message."""
    res = kubectl(*args, input_text=input_text, check=False)
    detail = f"{res.stderr}\n{res.stdout}".strip()
    if res.returncode == 0:
        raise RuntimeError(f"{label}: expected admission denial, got allow: {detail[:400]}")
    if not any(token in detail for token in expected_substrings):
        raise RuntimeError(
            f"{label}: expected denial matching {expected_substrings!r}, got: {detail[:400]}",
        )
    print(f"denied: {label} rejected at admission")


def verify_admission_policies(has_cloudtak=False):
    """Exercise Pepr validating admission policies via non-mutating server dry-run requests."""
    _assert_denied(
        "takserver pod ambient bypass annotation",
        ("create", "--dry-run=server", "-f", "-"),
        ("bypass-inbound-capture", "RestrictIstioAmbientOverrides"),
        input_text=json.dumps({
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": "zt-probe-bad-pod",
                "namespace": "takserver",
                "annotations": {"ambient.istio.io/bypass-inbound-capture": "true"},
            },
            "spec": {"containers": [{"name": "probe", "image": "registry.invalid/probe:1"}]},
        }),
    )
    _assert_denied(
        "takserver plaintext 8080 service",
        ("create", "--dry-run=server", "-f", "-"),
        ("port 8080",),
        input_text=json.dumps({
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "zt-probe-bad-svc", "namespace": "takserver"},
            "spec": {"ports": [{"port": 8080, "targetPort": 8080}]},
        }),
    )
    _assert_denied(
        "takserver core-config tls=false patch",
        (
            "patch",
            "configmap",
            "core-config",
            "-n",
            "takserver",
            "--dry-run=server",
            "--type=merge",
            "-p",
            '{"data":{"CoreConfig.xml":"<connector tls=\\"false\\"/>"}}',
        ),
        ('tls="false"',),
    )
    _assert_denied(
        "takserver cert-migration invalid X.509 PEM patch",
        (
            "patch",
            "configmap",
            "cert-migration",
            "-n",
            "takserver",
            "--dry-run=server",
            "--type=merge",
            "-p",
            '{"binaryData":{"ca.pem":"bm90LWEtY2VydA=="}}',
        ),
        ("X.509 PEM",),
    )
    _assert_denied(
        "takclients invalid TakClient callsign",
        ("create", "--dry-run=server", "-f", "-"),
        ("callsign",),
        input_text=json.dumps({
            "apiVersion": "tak.uds.dev/v1alpha1",
            "kind": "TakClient",
            "metadata": {"name": "zt-probe-bad-client", "namespace": "takclients"},
            "spec": {"callsign": "bad name", "groups": ["Operators"]},
        }),
    )
    if has_cloudtak:
        _assert_denied(
            "cloudtak pod hostNetwork",
            ("create", "--dry-run=server", "-f", "-"),
            ("hostNetwork", "DisallowHostNamespaces"),
            input_text=json.dumps({
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "zt-probe-bad-pod", "namespace": "cloudtak"},
                "spec": {
                    "hostNetwork": True,
                    "containers": [{"name": "probe", "image": "registry.invalid/probe:1"}],
                },
            }),
        )


def main():
    verify_ambient_namespace("takserver")

    api_image = kubectl(
        "get", "deploy", "takserver-api", "-n", "takserver",
        "-o", "jsonpath={.spec.template.spec.containers[?(@.name=='takserver-api')].image}",
    ).stdout.strip()
    if not api_image:
        raise RuntimeError("could not resolve takserver-api container image")

    api_pod_ip = kubectl(
        "get", "pods", "-n", "takserver", "-l", "app=takserver-api",
        "-o", "jsonpath={.items[0].status.podIP}",
    ).stdout.strip()
    if not api_pod_ip:
        raise RuntimeError("could not resolve running takserver-api pod IP")

    tak_probe = build_takserver_probe_pod(api_image, api_pod_ip)
    run_probe_pod("takserver", DEFAULT_PROBE_SA, tak_probe)

    cloudtak_pkg = kubectl("get", "package", "cloudtak", "-n", "cloudtak", check=False)
    has_cloudtak = cloudtak_pkg.returncode == 0
    if has_cloudtak:
        verify_ambient_namespace("cloudtak")
        cloudtak_image = kubectl(
            "get", "deploy", "cloudtak", "-n", "cloudtak",
            "-o", "jsonpath={.spec.template.spec.containers[?(@.name=='cloudtak')].image}",
        ).stdout.strip()
        if not cloudtak_image:
            raise RuntimeError("could not resolve cloudtak container image")
        ct_probe = build_cloudtak_probe_pod(cloudtak_image)
        run_probe_pod("cloudtak", DEFAULT_PROBE_SA, ct_probe)
    else:
        print("cloudtak package is not installed; skipping CloudTAK east-west probe")

    verify_admission_policies(has_cloudtak=has_cloudtak)


if __name__ == "__main__":
    main()
