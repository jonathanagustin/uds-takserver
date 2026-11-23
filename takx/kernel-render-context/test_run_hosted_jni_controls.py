# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/kernel-render-context/run_hosted_jni_controls.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "kernel_render_context_jni_hosted_controls",
    Path(__file__).with_name("run_hosted_jni_controls.py"),
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def _dynsym_row(ndx: str, bind: str, name: str) -> str:
    return f"   1: 0000000000001000     8 FUNC    {bind:<6} DEFAULT  {ndx:>3} {name}"


class HostedRenderContextJniControlsTests(unittest.TestCase):
    def test_verify_offline_succeeds(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        self.assertEqual(info["profile"], runner.EXPECTED_PROFILE)
        self.assertEqual(info["official_revision"], runner.EXPECTED_REVISION)
        self.assertEqual(len(info["owned_test_files"]), 7)
        self.assertEqual(info["feature_lookup_rows"], len(runner.FEATURE_LOOKUP_ROWS))

    def test_extract_patch_section_supports_git_and_unified_diffs(self) -> None:
        rc_raw = runner.RENDER_CONTEXT_PATCH.read_bytes()
        for rel in runner.RENDER_CONTEXT_JNI_FILES:
            sec = runner._extract_patch_section(rc_raw, rel)
            self.assertIn(rel.encode("utf-8"), sec)

        client_raw = runner.CLIENT_CONTRACTS_PATCH.read_bytes()
        for rel in runner.CLIENT_INTEROP_PATCH_FILES:
            sec = runner._extract_patch_section(client_raw, rel)
            self.assertIn(rel.encode("utf-8"), sec)

        with self.assertRaisesRegex(ValueError, "patch section missing"):
            runner._extract_patch_section(rc_raw, "missing/File.cpp")

    def test_audit_patched_jni_elf_enforces_boundaries(self) -> None:
        sqlite_syms = {"sqlite3_config", "sqlite3_errstr", "sqlite3_open_v2"}
        good_dynsym = "\n".join(
            [
                _dynsym_row("UND", "GLOBAL", "sqlite3_config"),
                _dynsym_row("UND", "GLOBAL", "sqlite3_errstr"),
                _dynsym_row("12", "GLOBAL", "Java_ManagedBridgeProbe_exercise"),
            ]
        )
        good_dyn = (
            " 0x0000000000000001 (NEEDED) Shared library: [libtakengine.so]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libGLESv2.so.2]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libgdal.so]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libc.so.6]"
        )
        good_nm = "0000000000001000 T Java_ManagedBridgeProbe_exercise\n"

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
        res = runner.audit_control_output(
            "ManagedBridgeProbe",
            "BRIDGE_CALLBACKS_COMPLETED operations=22 listeners=4 children=2\n",
            "",
            0,
            ("BRIDGE_CALLBACKS_COMPLETED operations=22 listeners=4 children=2",),
        )
        self.assertEqual(res["strict_jni_diagnostics"], 0)

        with self.assertRaisesRegex(ValueError, "strict -Xcheck:jni"):
            runner.audit_control_output(
                "ManagedBridgeProbe",
                "BRIDGE_CALLBACKS_COMPLETED operations=22 listeners=4 children=2\n",
                "WARNING in native method: JNI call made\n",
                0,
                ("BRIDGE_CALLBACKS_COMPLETED operations=22 listeners=4 children=2",),
            )

        with self.assertRaisesRegex(ValueError, "missing expected marker"):
            runner.audit_control_output(
                "SceneJNIControls",
                "SCENE_QUERY_EMPTY_PASS\n",
                "",
                0,
                (
                    "SCENE_QUERY_EMPTY_PASS",
                    "SCENE_JNI_CONTROLS_PASS query=true idVisibility=true uriVisibility=true remove=true",
                ),
            )


if __name__ == "__main__":
    unittest.main()
