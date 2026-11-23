#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/kernel-client-interop/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "takx_kernel_client_interop_run_hosted_controls",
        HERE / "run_hosted_controls.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


RUNNER = _load_runner()


class HostedKernelClientInteropControlsTests(unittest.TestCase):
    def test_verify_offline_validates_lock_patch_and_sources(self) -> None:
        result = RUNNER.verify_offline()
        self.assertEqual(result["schema"], "uds-takx-kernel-client-interop-hosted-v1")
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(result["locked_upstream_files"], 9)
        self.assertEqual(
            result["public_runtime_jars"], ["geodesy-1.1.3.jar", "jsvg-1.6.1.jar"]
        )

    def test_candidate_urls_prefers_google_maven_mirror(self) -> None:
        urls = RUNNER.candidate_urls(
            "https://repo.maven.apache.org/maven2/com/github/weisj/jsvg/1.6.1/jsvg-1.6.1.jar"
        )
        self.assertEqual(
            urls,
            [
                "https://maven-central.storage-download.googleapis.com/maven2/com/github/weisj/jsvg/1.6.1/jsvg-1.6.1.jar",
                "https://repo.maven.apache.org/maven2/com/github/weisj/jsvg/1.6.1/jsvg-1.6.1.jar",
            ],
        )

    def test_audit_accepts_complete_output_and_rejects_fatal_diagnostics_or_missing_markers(
        self,
    ) -> None:
        good = (
            "PASS_PUBLIC_VIEWPORT_FIT_V1_POLICY\n"
            "PASS_REAL_ROLE_PUBLIC_BITMAP_CLIENT_MARSHAL_PIXEL_CACHE_AND_ISOLATION_CONTROLS\n"
            "PASS_REAL_CDE_FONT_FORWARDING_DEFAULT_AND_CONCURRENT_LOCK\n"
            "PASS_REAL_HEADLESS_CONTROLS checks=1234; multipoint/native acceptance remains false"
        )
        audit = RUNNER.audit_controls_output(good, "", 0)
        self.assertTrue(audit["qualified"])
        self.assertEqual(audit["checks"], 1234)
        self.assertEqual(audit["startup_and_fatal_diagnostics"], 0)
        self.assertFalse(audit["xcheck_jni_enabled"])

        with self.assertRaisesRegex(ValueError, "startup or fatal diagnostic"):
            RUNNER.audit_controls_output(good, "EngineLibrary: Failed to load", 0)

        with self.assertRaisesRegex(
            ValueError, "missing required headless control markers"
        ):
            RUNNER.audit_controls_output(
                good.replace("PASS_PUBLIC_VIEWPORT_FIT_V1_POLICY", ""), "", 0
            )


if __name__ == "__main__":
    unittest.main()
