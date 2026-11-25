#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Offline unit tests for tests/zero-trust.py."""

import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("zero_trust_checks", ROOT / "tests" / "zero-trust.py")
ZT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ZT)


class ZeroTrustTests(unittest.TestCase):
    def _assert_pepr_compliant(self, pod):
        spec = pod["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertEqual(spec["restartPolicy"], "Never")
        sc = spec["securityContext"]
        self.assertTrue(sc["runAsNonRoot"])
        self.assertEqual(sc["seccompProfile"], {"type": "RuntimeDefault"})
        container = spec["containers"][0]
        self.assertEqual(container["imagePullPolicy"], "IfNotPresent")
        self.assertIn("readinessProbe", container)
        csc = container["securityContext"]
        self.assertFalse(csc["allowPrivilegeEscalation"])
        self.assertTrue(csc["readOnlyRootFilesystem"])
        self.assertEqual(csc["capabilities"], {"drop": ["ALL"]})
        self.assertIn("requests", container["resources"])
        self.assertIn("limits", container["resources"])

    def test_build_takserver_probe_pod_contract(self):
        pod = ZT.build_takserver_probe_pod("ghcr.io/example/takserver-api:1.0", "10.42.0.15")
        self._assert_pepr_compliant(pod)
        self.assertEqual(pod["metadata"]["namespace"], "takserver")
        self.assertEqual(pod["metadata"]["labels"], {"batch.kubernetes.io/job-name": "takserver-db-setup"})
        self.assertNotIn("app", pod["metadata"]["labels"])
        self.assertEqual(pod["spec"]["serviceAccountName"], "zt-probe-denied")
        cmd = " ".join(pod["spec"]["containers"][0]["command"])
        self.assertIn("takserver-postgresql 5432", cmd)
        self.assertIn("\\x00\\x00\\x00\\x08\\x04\\xd2\\x16\\x2f", cmd)
        self.assertIn("http://10.42.0.15:8080/", cmd)

    def test_build_cloudtak_probe_pod_contract(self):
        pod = ZT.build_cloudtak_probe_pod("ghcr.io/example/cloudtak:1.0")
        self._assert_pepr_compliant(pod)
        self.assertEqual(pod["metadata"]["namespace"], "cloudtak")
        self.assertEqual(pod["metadata"]["labels"], {"app": "cloudtak"})
        self.assertEqual(pod["spec"]["serviceAccountName"], "zt-probe-denied")
        self.assertEqual(pod["spec"]["securityContext"]["runAsUser"], 101)
        cmd = " ".join(pod["spec"]["containers"][0]["command"])
        self.assertIn("cloudtak-postgis", cmd)
        self.assertIn("5432", cmd)
        self.assertIn("[0,0,0,8,4,210,22,47]", cmd)

    def test_verify_ambient_namespace_accepts_clean_ambient_and_rejects_drift(self):
        good_ns = {"metadata": {"labels": {"istio.io/dataplane-mode": "ambient"}}}
        good_pods = {"items": [{"metadata": {"name": "takserver-api-0"}, "spec": {"containers": [{"name": "takserver-api"}]}}]}

        def fake_kubectl(*args, **kwargs):
            if args[:2] == ("get", "namespace"):
                return subprocess.CompletedProcess(args, 0, json.dumps(ns_obj), "")
            if args[:2] == ("get", "pods"):
                return subprocess.CompletedProcess(args, 0, json.dumps(pods_obj), "")
            self.fail(f"unexpected kubectl call: {args}")

        ns_obj, pods_obj = good_ns, good_pods
        with patch.object(ZT, "kubectl", side_effect=fake_kubectl):
            ZT.verify_ambient_namespace("takserver")

        for bad_ns in (
            {"metadata": {"labels": {}}},
            {"metadata": {"labels": {"istio.io/dataplane-mode": "ambient", "istio-injection": "enabled"}}},
        ):
            ns_obj, pods_obj = bad_ns, good_pods
            with patch.object(ZT, "kubectl", side_effect=fake_kubectl):
                with self.assertRaises(RuntimeError):
                    ZT.verify_ambient_namespace("takserver")

        for bad_pods in (
            {"items": []},
            {"items": [{"metadata": {"name": "p1", "labels": {"sidecar.istio.io/inject": "true"}}, "spec": {"containers": []}}]},
            {"items": [{"metadata": {"name": "p2"}, "spec": {"containers": [{"name": "istio-proxy"}]}}]},
            {"items": [{"metadata": {"name": "p3"}, "spec": {"containers": [], "initContainers": [{"name": "istio-validation"}]}}]},
        ):
            ns_obj, pods_obj = good_ns, bad_pods
            with patch.object(ZT, "kubectl", side_effect=fake_kubectl):
                with self.assertRaises(RuntimeError):
                    ZT.verify_ambient_namespace("takserver")

    def test_run_probe_pod_passes_fails_closed_and_always_cleans_up(self):
        pod = ZT.build_cloudtak_probe_pod("ghcr.io/example/cloudtak:1.0")
        calls = []

        def make_fake(status_sequence):
            idx = {"i": 0}

            def fake_kubectl(*args, **kwargs):
                calls.append(args)
                if args[:3] == ("get", "pod", "zt-probe-denied"):
                    val = status_sequence[min(idx["i"], len(status_sequence) - 1)]
                    idx["i"] += 1
                    return subprocess.CompletedProcess(args, 0, val, "")
                return subprocess.CompletedProcess(args, 0, "probe output", "")

            return fake_kubectl

        # Exit code 0 succeeds and deletes pod + SA in finally.
        with patch.object(ZT, "kubectl", side_effect=make_fake(["", "0"])), \
                patch.object(ZT.time, "sleep", return_value=None):
            ZT.run_probe_pod("cloudtak", "zt-probe-denied", pod, timeout_seconds=10)
        self.assertEqual(calls[-2][:4], ("delete", "pod", "zt-probe-denied", "-n"))
        self.assertEqual(calls[-1][:4], ("delete", "sa", "zt-probe-denied", "-n"))

        # Exit code 1 (breach) fails closed and still cleans up pod + SA.
        calls.clear()
        with patch.object(ZT, "kubectl", side_effect=make_fake(["1"])), \
                patch.object(ZT.time, "sleep", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "exit code 1"):
                ZT.run_probe_pod("cloudtak", "zt-probe-denied", pod, timeout_seconds=10)
        self.assertEqual(calls[-2][:4], ("delete", "pod", "zt-probe-denied", "-n"))
        self.assertEqual(calls[-1][:4], ("delete", "sa", "zt-probe-denied", "-n"))

        # Timeout fails closed and still cleans up pod + SA.
        calls.clear()
        monotonic_ticks = iter([0.0, 0.1, 100.0])
        with patch.object(ZT, "kubectl", side_effect=make_fake([""])), \
                patch.object(ZT.time, "sleep", return_value=None), \
                patch.object(ZT.time, "monotonic", side_effect=lambda: next(monotonic_ticks, 200.0)):
            with self.assertRaisesRegex(RuntimeError, "did not terminate"):
                ZT.run_probe_pod("cloudtak", "zt-probe-denied", pod, timeout_seconds=5)
        self.assertEqual(calls[-2][:4], ("delete", "pod", "zt-probe-denied", "-n"))
        self.assertEqual(calls[-1][:4], ("delete", "sa", "zt-probe-denied", "-n"))

    def test_verify_admission_policies_enforces_dry_run_denials(self):
        seen = []

        def fake_kubectl(*args, **kwargs):
            seen.append((args, kwargs.get("input_text")))
            raw = f"{args} {kwargs.get('input_text') or ''}"
            if "bypass-inbound-capture" in raw:
                return subprocess.CompletedProcess(args, 1, "", "denied: ambient.istio.io/bypass-inbound-capture annotation is forbidden")
            if "zt-probe-bad-svc" in raw:
                return subprocess.CompletedProcess(args, 1, "", "denied: Services must not publish plaintext port 8080")
            if "core-config" in raw:
                return subprocess.CompletedProcess(args, 1, "", 'denied: CoreConfig.xml must not contain plaintext tls="false" connectors')
            if "cert-migration" in raw:
                return subprocess.CompletedProcess(args, 1, "", "denied: cert-migration ca.pem must be a valid X.509 PEM certificate")
            if "zt-probe-bad-client" in raw:
                return subprocess.CompletedProcess(args, 1, "", "denied: spec.callsign must be 4 to 63")
            if "hostNetwork" in raw:
                return subprocess.CompletedProcess(args, 1, "", "denied: hostNetwork, hostPID, and hostIPC are forbidden")
            self.fail(f"unexpected kubectl call: {args}")

        with patch.object(ZT, "kubectl", side_effect=fake_kubectl):
            ZT.verify_admission_policies(has_cloudtak=False)
        self.assertEqual(len(seen), 5)

        seen.clear()
        with patch.object(ZT, "kubectl", side_effect=fake_kubectl):
            ZT.verify_admission_policies(has_cloudtak=True)
        self.assertEqual(len(seen), 6)

        with patch.object(ZT, "kubectl", return_value=subprocess.CompletedProcess((), 0, "ok", "")):
            with self.assertRaisesRegex(RuntimeError, "expected admission denial, got allow"):
                ZT.verify_admission_policies(has_cloudtak=False)

        with patch.object(ZT, "kubectl", return_value=subprocess.CompletedProcess((), 1, "", "unrelated error")):
            with self.assertRaisesRegex(RuntimeError, "expected denial matching"):
                ZT.verify_admission_policies(has_cloudtak=False)


if __name__ == "__main__":
    unittest.main()
