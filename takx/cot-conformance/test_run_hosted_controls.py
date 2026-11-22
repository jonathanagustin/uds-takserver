# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "run_hosted_controls.py"
SPEC = importlib.util.spec_from_file_location("cot_conformance_hosted", MODULE_PATH)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class CotConformanceHostedControlsTests(unittest.TestCase):
    def test_verify_offline_validates_provenance_and_patch(self) -> None:
        result = MOD.verify_offline()
        self.assertEqual(result["schema"], MOD.SCHEMA)
        self.assertEqual(result["revision"], MOD.ATAK_REV)
        self.assertGreaterEqual(result["provenance_files_verified"], 1)
        self.assertEqual(len(result["smoke_sources"]), 4)

    def test_check_exported_jni_symbols_allows_only_jni_entry_points(self) -> None:
        good = (
            "   1: 0000000000001000    10 FUNC    GLOBAL DEFAULT   12 JNI_OnLoad\n"
            "   2: 0000000000001020    10 FUNC    GLOBAL DEFAULT   12 Java_com_atakmap_commoncommo_Commo_init\n"
            "   3: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND free@GLIBC_2.2.5\n"
        )
        syms = MOD.check_exported_jni_symbols(good)
        self.assertEqual(syms, ["JNI_OnLoad", "Java_com_atakmap_commoncommo_Commo_init"])

        bad = good + "   4: 0000000000002000    10 FUNC    GLOBAL DEFAULT   12 SSL_CTX_new\n"
        with self.assertRaises(ValueError):
            MOD.check_exported_jni_symbols(bad)

    def test_check_dynamic_section_rejects_textrel_and_shared_crypto(self) -> None:
        MOD.check_dynamic_section(" 0x0000000000000001 (NEEDED) Shared library: [libc.so.6]\n")
        with self.assertRaises(ValueError):
            MOD.check_dynamic_section(" 0x0000000000000016 (TEXTREL) 0x0\n")
        with self.assertRaises(ValueError):
            MOD.check_dynamic_section(" 0x0000000000000001 (NEEDED) Shared library: [libcrypto.so.3]\n")


if __name__ == "__main__":
    unittest.main()
