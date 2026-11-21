# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for the TAKX libglvnd 1.7.0 OCI cache verifier."""

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "takx_gles_cache_verify", Path(__file__).with_name("verify.py")
)
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


def sample_receipt():
    return {
        "schema": V.SCHEMA,
        "component": "libglvnd",
        "version": "1.7.0",
        "arch": "x86_64",
        "source_archive_sha256": V.EXPECTED_GLVND_SHA256,
        "meson_wheel_sha256": V.EXPECTED_MESON_SHA256,
        "libraries": {lib: "a" * 64 for lib in V.REQUIRED_LIBRARIES},
        "controls": {name: True for name in V.REQUIRED_CONTROLS},
        "scope": {
            "software_mesa_llvmpipe": True,
            "hardware_gpu_accepted": False,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }


class GlesCacheVerifyTests(unittest.TestCase):
    def test_verify_offline_checks_locked_hashes(self):
        result = V.verify_offline()
        self.assertEqual(result["schema"], V.SCHEMA)
        self.assertEqual(result["locked_glvnd_sha256"], V.EXPECTED_GLVND_SHA256)
        self.assertEqual(result["locked_meson_sha256"], V.EXPECTED_MESON_SHA256)

    def test_validate_receipt_accepts_valid_and_rejects_mutations(self):
        base = sample_receipt()
        V.validate_receipt(base)

        for key in ("hardware_gpu_accepted", "sdk_accepted", "security_accepted"):
            over = copy.deepcopy(base)
            over["scope"][key] = True
            with self.assertRaisesRegex(ValueError, key):
                V.validate_receipt(over)

        for ctrl in V.REQUIRED_CONTROLS:
            bad = copy.deepcopy(base)
            bad["controls"][ctrl] = False
            with self.assertRaisesRegex(ValueError, ctrl):
                V.validate_receipt(bad)

    def test_validate_receipt_checks_restored_prefix_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            libdir = prefix / "lib"
            libdir.mkdir()
            receipt = sample_receipt()
            for lib in V.REQUIRED_LIBRARIES:
                p = libdir / lib
                p.write_bytes(f"elf-{lib}".encode("utf-8"))
                receipt["libraries"][lib] = V.sha256_file(p)
            V.validate_receipt(receipt, prefix=prefix)

            (libdir / "libEGL.so.1").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "hash mismatch for libEGL.so.1"):
                V.validate_receipt(receipt, prefix=prefix)


if __name__ == "__main__":
    unittest.main()
