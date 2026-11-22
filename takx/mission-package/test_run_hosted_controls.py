#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/mission-package/run_hosted_controls.py."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load_runner():
    spec = importlib.util.spec_from_file_location("mp_hosted", HERE / "run_hosted_controls.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HostedMissionPackageControlsTests(unittest.TestCase):
    def test_verify_phase_checks_lock_and_profile_without_network(self):
        runner = load_runner()
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("verify must not perform network I/O")):
            manifest = runner.verify_only()
        self.assertEqual(manifest["dependency_count"], 9)
        self.assertEqual(manifest["takx_revision"], "0b59a9bb2d0e90dc35c9edfff1c2056daab2b713")
        self.assertEqual(manifest["admission_profile_id"], "takx57-mission-admission-v2")
        self.assertEqual(
            manifest["dependencies"]["org.apache.commons--commons-lang3-3.20.0.jar"],
            "https://repo1.maven.org/maven2/org/apache/commons/commons-lang3/3.20.0/commons-lang3-3.20.0.jar",
        )
        self.assertFalse(manifest["scope"]["hosted_linux_jdk17_mission_package"])
        self.assertFalse(manifest["scope"]["full_client_accepted"])
        self.assertFalse(manifest["scope"]["native_transfer_accepted"])
        self.assertFalse(manifest["scope"]["sdk_accepted"])
        self.assertFalse(manifest["scope"]["security_accepted"])

    def test_verify_phase_rejects_qualify_paths(self):
        runner = load_runner()
        with self.assertRaisesRegex(ValueError, "explicit --phase qualify"):
            runner.main(["--phase", "verify", "--output", "/tmp/unused"])

    def test_qualify_phase_requires_inputs(self):
        runner = load_runner()
        with self.assertRaisesRegex(ValueError, "required for --phase qualify"):
            runner.main(["--phase", "qualify"])

    def test_qualify_rejects_overlapping_output(self):
        runner = load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            takx = Path(tmp) / "takx"
            takx.mkdir()
            with self.assertRaisesRegex(ValueError, "output overlaps inputs"):
                runner.main([
                    "--phase", "qualify",
                    "--takx-source", str(takx),
                    "--output", str(takx / "nested"),
                ])


if __name__ == "__main__":
    unittest.main()
