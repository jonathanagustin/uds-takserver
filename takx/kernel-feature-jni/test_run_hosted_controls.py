# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/kernel-feature-jni/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "kernel_feature_jni_hosted_controls",
    Path(__file__).with_name("run_hosted_controls.py"),
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def _dynsym_row(ndx: str, bind: str, name: str) -> str:
    return f"   1: 0000000000001000     8 FUNC    {bind:<6} DEFAULT  {ndx:>3} {name}"


class HostedKernelFeatureJniControlsTests(unittest.TestCase):
    def test_verify_offline_succeeds(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        self.assertEqual(info["profile"], runner.EXPECTED_PROFILE)
        self.assertEqual(info["official_revision"], runner.EXPECTED_REVISION)
        self.assertEqual(info["inputs_lock_sha256"], runner.EXPECTED_LOCK_SHA)
        self.assertEqual(info["patched_files_count"], 10)
        self.assertEqual(len(info["client_contracts_patch_sha256"]), 64)
        self.assertEqual(len(info["owned_controls"]), 17)
        self.assertEqual(len(runner.HEADLESS_JAVA_CONTROLS), 10)
        self.assertEqual(len(runner.PROBE_CPP_SOURCES), 6)
        self.assertEqual(len(runner.POINTER_LIFETIME_MODES), 20)

    def test_extract_patch_section_extracts_layered_client_diffs(self) -> None:
        raw = runner.CLIENT_CONTRACTS_PATCH.read_bytes()
        cpp_patch = runner._extract_patch_section(raw, runner.FEATURE_INTEROP_REL)
        java_patch = runner._extract_patch_section(raw, runner.FEATURE_LAYER3_JAVA_REL)
        self.assertIn(runner.FEATURE_INTEROP_REL.encode("utf-8"), cpp_patch)
        self.assertIn(runner.FEATURE_LAYER3_JAVA_REL.encode("utf-8"), java_patch)
        self.assertNotIn(runner.FEATURE_LAYER3_JAVA_REL.encode("utf-8"), cpp_patch)
        self.assertNotIn(runner.FEATURE_INTEROP_REL.encode("utf-8"), java_patch)
        with self.assertRaisesRegex(ValueError, "patch section missing"):
            runner._extract_patch_section(raw, "missing/File.cpp")

    def test_audit_patched_jni_elf_accepts_valid_and_rejects_leaks(self) -> None:
        sqlite_syms = {"sqlite3_config", "sqlite3_errstr", "sqlite3_open_v2"}
        good_dynsym = "\n".join(
            [
                _dynsym_row("UND", "GLOBAL", "sqlite3_config"),
                _dynsym_row("UND", "GLOBAL", "sqlite3_errstr"),
                _dynsym_row(
                    "12", "GLOBAL", "Java_com_atakmap_map_layer_NativeLayer_wrap"
                ),
            ]
        )
        good_dyn = (
            " 0x0000000000000001 (NEEDED) Shared library: [libtakengine.so]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libGLESv2.so.2]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libgdal.so]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libc.so.6]"
        )
        good_nm = "0000000000001000 T Java_com_atakmap_map_layer_NativeLayer_wrap\n"

        report = runner.audit_patched_jni_elf(
            good_dynsym, good_dyn, good_nm, sqlite_syms
        )
        self.assertEqual(
            report["sqlcipher_imports"], ["sqlite3_config", "sqlite3_errstr"]
        )
        self.assertFalse(report["second_jni_sqlite_body"])

        with self.assertRaisesRegex(ValueError, "TEXTREL"):
            runner.audit_patched_jni_elf(
                good_dynsym,
                good_dyn + "\n 0x00000016 (TEXTREL) 0x0\n",
                good_nm,
                sqlite_syms,
            )

        with self.assertRaisesRegex(ValueError, "SQLCipher boundary mismatch"):
            runner.audit_patched_jni_elf(
                good_dynsym,
                good_dyn,
                good_nm + "0000000000002000 T sqlite3_open_v2\n",
                sqlite_syms,
            )

    def test_audit_control_output_enforces_markers_and_clean_jni(self) -> None:
        count = runner.audit_control_output(
            "LayerVisibilityControls",
            "LAYER_VISIBILITY_CACHE_CONTROLS_PASS checks=17\n",
            "",
            0,
            ("LAYER_VISIBILITY_CACHE_CONTROLS_PASS checks=",),
            checks_regex=r"LAYER_VISIBILITY_CACHE_CONTROLS_PASS checks=(\d+)\b",
        )
        self.assertEqual(count, 17)

        with self.assertRaisesRegex(ValueError, "strict -Xcheck:jni"):
            runner.audit_control_output(
                "LayerVisibilityControls",
                "LAYER_VISIBILITY_CACHE_CONTROLS_PASS checks=17\n",
                "WARNING in native method: JNI call made\n",
                0,
                ("LAYER_VISIBILITY_CACHE_CONTROLS_PASS checks=",),
            )

        with self.assertRaisesRegex(ValueError, "missing expected marker"):
            runner.audit_control_output(
                "PointerEmptyControls",
                "EMPTY_SHARED_RESULT [1, 0, 0, 0]\n",
                "",
                0,
                ("EMPTY_SHARED_RESULT [0, 1, 1, 1]",),
            )


if __name__ == "__main__":
    unittest.main()
