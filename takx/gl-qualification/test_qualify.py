# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for the Mesa + Xvfb GL qualification harness."""

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "takx_gl_qualify", Path(__file__).with_name("qualify.py")
)
Q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(Q)


def sample_receipt():
    return {
        "schema": Q.SCHEMA,
        "phase": "qualify",
        "arch": "x86_64",
        "control_source": "gl_controls.c",
        "control_source_sha256": "a" * 64,
        "binary_sha256": "b" * 64,
        "display": ":99",
        "egl": "1.5",
        "version": "OpenGL ES 3.2 Mesa 24.2.8-1ubuntu1~24.04.1",
        "renderer": "llvmpipe (LLVM 19.1.1, 256 bits)",
        "pixels": {
            "clear": "64,128,191,255",
            "atlas": "229,160,13,255",
            "recreated": "51,212,153,255",
            "layer_on": "230,161,13,255",
            "layer_off": "10,13,18,255",
        },
        "controls": {name: True for name in Q.REQUIRED_CONTROLS},
        "providers": [
            {
                "path": "/usr/lib/x86_64-linux-gnu/libEGL_mesa.so.0",
                "resolved": "/usr/lib/x86_64-linux-gnu/libEGL_mesa.so.0.0.0",
                "bytes": 12345,
                "sha256": "c" * 64,
            }
        ],
        "scope": {
            "software_mesa_xvfb": True,
            "hardware_gpu_accepted": False,
            "sdk_accepted": False,
            "client_accepted": False,
        },
    }


class GlQualificationTests(unittest.TestCase):
    def test_verify_offline_checks_control_source_and_markers(self):
        result = Q.verify_offline()
        self.assertEqual(result["schema"], Q.SCHEMA)
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(len(result["control_source_sha256"]), 64)
        self.assertEqual(tuple(result["required_controls"]), Q.REQUIRED_CONTROLS)

    def test_parse_control_output_extracts_fields_and_providers(self):
        raw = "\n".join([
            "display=:99",
            "egl=1.5",
            "renderer=llvmpipe (LLVM 19.1.1)",
            "xvfb_es3_clear_readback=true",
            "providers_begin",
            "/usr/lib/x86_64-linux-gnu/libEGL.so.1",
            "/usr/lib/x86_64-linux-gnu/libGLESv2.so.2",
            "/usr/lib/x86_64-linux-gnu/libEGL.so.1",
            "providers_end",
        ])
        parsed = Q.parse_control_output(raw)
        self.assertEqual(parsed["fields"]["display"], ":99")
        self.assertEqual(parsed["fields"]["xvfb_es3_clear_readback"], "true")
        self.assertEqual(parsed["providers"], [
            "/usr/lib/x86_64-linux-gnu/libEGL.so.1",
            "/usr/lib/x86_64-linux-gnu/libGLESv2.so.2",
        ])

    def test_parse_control_output_rejects_unterminated_providers(self):
        with self.assertRaisesRegex(ValueError, "unterminated"):
            Q.parse_control_output("providers_begin\n/usr/lib/libEGL.so.1\n")

    def test_validate_receipt_accepts_valid_and_rejects_mutations(self):
        base = sample_receipt()
        Q.validate_receipt(base)

        bad_renderer = copy.deepcopy(base)
        bad_renderer["renderer"] = "stub-renderer"
        with self.assertRaisesRegex(ValueError, "llvmpipe"):
            Q.validate_receipt(bad_renderer)

        for control in Q.REQUIRED_CONTROLS:
            missing = copy.deepcopy(base)
            missing["controls"][control] = False
            with self.assertRaisesRegex(ValueError, control):
                Q.validate_receipt(missing)

        for scope_key in ("hardware_gpu_accepted", "sdk_accepted", "client_accepted"):
            overclaimed = copy.deepcopy(base)
            overclaimed["scope"][scope_key] = True
            with self.assertRaisesRegex(ValueError, scope_key):
                Q.validate_receipt(overclaimed)

        empty_providers = copy.deepcopy(base)
        empty_providers["providers"] = []
        with self.assertRaisesRegex(ValueError, "providers"):
            Q.validate_receipt(empty_providers)

    def test_hash_providers_hashes_existing_files_and_skips_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_so = Path(tmp) / "libfake.so"
            fake_so.write_bytes(b"ELF-fixture")
            records = Q.hash_providers([str(fake_so), "/nonexistent/libnope.so", "relative.so"])
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["bytes"], len(b"ELF-fixture"))
            self.assertEqual(len(records[0]["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
