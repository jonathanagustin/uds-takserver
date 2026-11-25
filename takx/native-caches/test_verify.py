# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for the unified TAKX native OCI caches verifier."""

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "takx_native_caches_verify", Path(__file__).with_name("verify.py")
)
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


def sample_receipt(name: str) -> dict:
    spec = V.COMPONENTS[name]
    return {
        "schema": V.SCHEMA,
        "component": name,
        "version": spec["version"],
        "arch": "x86_64",
        "gcc_version": "14.2.0",
        "source_archive_sha256": spec["archive_sha256"],
        "license_sha256": spec["license_sha256"],
        "artifacts": {rel: "a" * 64 for rel in spec["required_artifacts"]},
        "controls": {ctrl: True for ctrl in V.REQUIRED_CONTROLS},
        "scope": {
            "static_pic_host_abi": True,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }


class NativeCachesVerifyTests(unittest.TestCase):
    def test_verify_all_offline_checks_every_component(self):
        result = V.verify_all_offline()
        self.assertEqual(result["schema"], V.SCHEMA)
        self.assertEqual(set(result["components"].keys()), set(V.COMPONENTS.keys()))

    def test_validate_receipt_accepts_valid_and_rejects_mutations(self):
        for name in V.COMPONENTS:
            base = sample_receipt(name)
            V.validate_receipt(name, base)

            for key in ("sdk_accepted", "security_accepted"):
                over = copy.deepcopy(base)
                over["scope"][key] = True
                with self.assertRaisesRegex(ValueError, key):
                    V.validate_receipt(name, over)

            for ctrl in V.REQUIRED_CONTROLS:
                bad = copy.deepcopy(base)
                bad["controls"][ctrl] = False
                with self.assertRaisesRegex(ValueError, ctrl):
                    V.validate_receipt(name, bad)

    def test_validate_receipt_checks_restored_prefix_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            receipt = sample_receipt("zlib")
            for rel in V.COMPONENTS["zlib"]["required_artifacts"]:
                path = prefix / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"fixture-{rel}".encode("utf-8"))
                receipt["artifacts"][rel] = V.sha256_file(path)
            V.validate_receipt("zlib", receipt, prefix=prefix)

            (prefix / "lib" / "libz.a").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "hash mismatch for lib/libz.a"):
                V.validate_receipt("zlib", receipt, prefix=prefix)


if __name__ == "__main__":
    unittest.main()
