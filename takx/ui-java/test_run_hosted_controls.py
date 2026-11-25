#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/ui-java/run_hosted_controls.py."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load_runner():
    spec = importlib.util.spec_from_file_location("ui_java_hosted", HERE / "run_hosted_controls.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HostedUiJavaControlsTests(unittest.TestCase):
    def test_verify_phase_checks_five_original_artifacts_without_network(self):
        runner = load_runner()
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("verify must not perform network I/O")):
            manifest = runner.verify_only("original")
        self.assertEqual(manifest["artifact_count"], 5)
        self.assertEqual(
            set(manifest["artifacts"]),
            {
                "docking-core",
                "docking-common",
                "colorchooser-author-1.5",
                "toast-1.0.3",
                "salamander-tag-v1.1.1",
            },
        )
        self.assertFalse(manifest["scope"]["hosted_linux_jdk17_ui_java"])
        self.assertFalse(manifest["scope"]["complete_client_or_runtime_acceptance"])
        self.assertFalse(manifest["scope"]["sdk_accepted"])
        self.assertFalse(manifest["scope"]["security_accepted"])

    def test_verify_phase_rejects_qualify_paths(self):
        runner = load_runner()
        with self.assertRaisesRegex(ValueError, "explicit --phase qualify"):
            runner.main(["--phase", "verify", "--output", "/tmp/unused"])

    def test_qualify_phase_requires_output(self):
        runner = load_runner()
        with self.assertRaisesRegex(ValueError, "required for --phase qualify"):
            runner.main(["--phase", "qualify"])

    def test_qualify_rejects_repo_overlapping_output(self):
        runner = load_runner()
        with self.assertRaisesRegex(ValueError, "output overlaps repository"):
            runner.main(["--phase", "qualify", "--output", str(HERE / "nested-out")])

    def test_qualify_rejects_existing_output(self):
        runner = load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "fresh output directory required"):
                runner.main(["--phase", "qualify", "--output", tmp])

    def test_maven_central_candidate_urls_include_google_mirror(self):
        runner = load_runner()
        urls = runner.candidate_urls("https://repo.maven.apache.org/maven2/org/apache/ant/ant/1.8.1/ant-1.8.1.jar")
        self.assertEqual(
            urls,
            [
                "https://maven-central.storage-download.googleapis.com/maven2/org/apache/ant/ant/1.8.1/ant-1.8.1.jar",
                "https://repo1.maven.org/maven2/org/apache/ant/ant/1.8.1/ant-1.8.1.jar",
                "https://repo.maven.apache.org/maven2/org/apache/ant/ant/1.8.1/ant-1.8.1.jar",
            ],
        )
        self.assertEqual(
            runner.candidate_urls("https://codeload.github.com/example/repo/tar.gz/abc"),
            ["https://codeload.github.com/example/repo/tar.gz/abc"],
        )


if __name__ == "__main__":
    unittest.main()
