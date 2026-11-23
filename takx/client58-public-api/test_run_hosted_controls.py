# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/client58-public-api/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("client58_public_api_hosted", HERE / "run_hosted_controls.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class HostedClient58PublicApiControlsTests(unittest.TestCase):
    def test_verify_phase_validates_contract_without_network(self):
        result = MOD.verify_only()
        self.assertEqual(result["schema"], MOD.SCHEMA)
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(result["profile"], "public-vehicle-metadata-v2")
        self.assertEqual(len(result["owned_files"]), 3)
        self.assertFalse(result["scope"]["hosted_linux_jdk17_client58_public_api"])

    def test_verify_phase_rejects_qualify_paths(self):
        with self.assertRaisesRegex(ValueError, "explicit --phase qualify"):
            MOD.main(["--phase", "verify", "--output", "/tmp/unused"])

    def test_qualify_phase_requires_inputs(self):
        with self.assertRaisesRegex(ValueError, "--kernel-classes and --output are required"):
            MOD.main(["--phase", "qualify"])

    def test_qualify_rejects_missing_kernel_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "CotEvent.class missing"):
                MOD.qualify(root, root / "out")

    def test_qualify_rejects_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kclasses = root / "classes"
            (kclasses / "gov/tak/api/cot/event").mkdir(parents=True)
            (kclasses / "gov/tak/api/cot/detail").mkdir(parents=True)
            (kclasses / "gov/tak/api/cot/event/CotEvent.class").write_bytes(b"")
            (kclasses / "gov/tak/api/cot/detail/ICotDetailHandler2.class").write_bytes(b"")
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(ValueError, "fresh output directory required"):
                MOD.qualify(kclasses, existing)


if __name__ == "__main__":
    unittest.main()
