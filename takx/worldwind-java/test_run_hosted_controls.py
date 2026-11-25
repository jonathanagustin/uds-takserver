# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/worldwind-java/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("worldwind_java_hosted", HERE / "run_hosted_controls.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class HostedWorldWindJavaControlsTests(unittest.TestCase):
    def test_verify_phase_validates_locks_without_network(self):
        result = MOD.verify_only()
        self.assertEqual(result["schema"], MOD.SCHEMA)
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(result["java_count"], 1726)
        self.assertEqual(set(result["public_jars"]), {"gluegen-rt-2.5.0.jar", "jogl-all-2.5.0.jar"})
        self.assertFalse(result["scope"]["hosted_linux_jdk17_worldwind_java"])

    def test_verify_phase_rejects_qualify_paths(self):
        with self.assertRaisesRegex(ValueError, "explicit --phase qualify"):
            MOD.main(["--phase", "verify", "--output", "/tmp/unused"])

    def test_qualify_phase_requires_inputs(self):
        with self.assertRaisesRegex(ValueError, "--worldwind-source, --gis-build, and --output are required"):
            MOD.main(["--phase", "qualify"])

    def test_qualify_rejects_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "fresh output directory required"):
                MOD.qualify(root / "src", root / "gis", root)

    def test_qualify_rejects_repo_overlapping_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "output overlaps inputs"):
                MOD.qualify(root / "src", root / "gis", MOD.REPO / "nonexistent-worldwind-out")


if __name__ == "__main__":
    unittest.main()
