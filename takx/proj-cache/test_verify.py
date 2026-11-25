# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for the TAKX PROJ 4.9.1 OCI cache verifier."""

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "takx_proj_cache_verify", Path(__file__).with_name("verify.py")
)
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


def sample_receipt():
    return {
        "schema": V.SCHEMA,
        "component": "proj",
        "version": "4.9.1",
        "arch": "x86_64",
        "gcc_version": "14.2.0",
        "source_archive_sha256": V.EXPECTED_ARCHIVE_SHA256,
        "license_sha256": V.EXPECTED_LICENSE_SHA256,
        "libproj_a_sha256": "a" * 64,
        "proj_api_h_sha256": "b" * 64,
        "controls": {name: True for name in V.REQUIRED_CONTROLS},
        "scope": {
            "static_pic_host_abi": True,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }


class ProjCacheVerifyTests(unittest.TestCase):
    def test_verify_offline_checks_locked_hashes(self):
        result = V.verify_offline()
        self.assertEqual(result["schema"], V.SCHEMA)
        self.assertEqual(result["locked_archive_sha256"], V.EXPECTED_ARCHIVE_SHA256)
        self.assertEqual(result["locked_license_sha256"], V.EXPECTED_LICENSE_SHA256)

    def test_validate_receipt_accepts_valid_and_rejects_mutations(self):
        base = sample_receipt()
        V.validate_receipt(base)

        for key in ("sdk_accepted", "security_accepted"):
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
            (prefix / "lib").mkdir()
            (prefix / "include").mkdir()
            lib = prefix / "lib" / "libproj.a"
            hdr = prefix / "include" / "proj_api.h"
            lic = prefix / "COPYING"
            lib.write_bytes(b"fake-libproj-archive")
            hdr.write_bytes(b"#define PJ_VERSION 491\n")
            lic.write_bytes(b"MIT license text")

            receipt = sample_receipt()
            receipt["libproj_a_sha256"] = V.sha256_file(lib)
            receipt["proj_api_h_sha256"] = V.sha256_file(hdr)
            # License hash is pinned to upstream COPYING SHA-256, so mismatched bytes must fail.
            with self.assertRaisesRegex(ValueError, "hash mismatch for COPYING"):
                V.validate_receipt(receipt, prefix=prefix)


if __name__ == "__main__":
    unittest.main()
