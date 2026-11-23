# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/native-gdal-jni/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "native_gdal_jni_hosted_controls",
    Path(__file__).with_name("run_hosted_controls.py"),
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class HostedNativeGdalJniControlsTests(unittest.TestCase):
    def test_verify_offline_validates_sources(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        for key in ("probe_java_sha256", "exception_controls_sha256", "build_script_sha256"):
            self.assertEqual(len(info[key]), 64)

    def test_audit_elf_shared_provider_accepts_clean_and_rejects_textrel_expat_or_embedded_gdal(self) -> None:
        libgdal = Path("/tmp/lib/libgdal.so")
        libjni = Path("/tmp/lib/libgdalalljni.so")
        clean_gdal_dyn = " 0x0000000000000001 (NEEDED) Shared library: [libstdc++.so.6]\n"
        clean_gdal_syms = "0000000000001000 T GDALVersionInfo\n0000000000002000 T CPLMalloc\n"
        clean_jni_dyn = " 0x0000000000000001 (NEEDED) Shared library: [libgdal.so]\n"
        clean_jni_syms = "0000000000003000 T Java_org_gdal_gdal_gdalJNI_VersionInfo__Ljava_lang_String_2\n"

        runner.audit_elf_shared_provider(
            libgdal, libjni, clean_gdal_dyn, clean_gdal_syms, clean_jni_dyn, clean_jni_syms
        )

        with self.assertRaisesRegex(ValueError, "TEXTREL"):
            runner.audit_elf_shared_provider(
                libgdal, libjni, clean_gdal_dyn + " TEXTREL\n", clean_gdal_syms, clean_jni_dyn, clean_jni_syms
            )
        with self.assertRaisesRegex(ValueError, "TEXTREL"):
            runner.audit_elf_shared_provider(
                libgdal, libjni, clean_gdal_dyn, clean_gdal_syms, clean_jni_dyn + " TEXTREL\n", clean_jni_syms
            )
        with self.assertRaisesRegex(ValueError, "NEEDED libgdal.so"):
            runner.audit_elf_shared_provider(
                libgdal, libjni, clean_gdal_dyn, clean_gdal_syms, "", clean_jni_syms
            )
        with self.assertRaisesRegex(ValueError, "private Expat"):
            runner.audit_elf_shared_provider(
                libgdal, libjni, clean_gdal_dyn, clean_gdal_syms + "0001 T XML_ParserCreate\n",
                clean_jni_dyn, clean_jni_syms,
            )
        with self.assertRaisesRegex(ValueError, "GDALVersionInfo"):
            runner.audit_elf_shared_provider(
                libgdal, libjni, clean_gdal_dyn, clean_gdal_syms, clean_jni_dyn,
                clean_jni_syms + "0002 T GDALVersionInfo\n",
            )

    def test_parse_probe_and_exception_outputs_enforce_markers_maps_and_xcheck_jni(self) -> None:
        libgdal = Path("/out/lib/libgdal.so")
        libjni = Path("/out/lib/libgdalalljni.so")
        probe_stdout = "\n".join([
            "ELF /out/lib/libgdal.so",
            "ELF /out/lib/libgdalalljni.so",
            "ELF /lib/x86_64-linux-gnu/libc.so.6",
            runner.PROBE_MARKER,
        ])
        exc_stdout = f"info\n{runner.EXCEPTION_MARKER}\n"

        with mock.patch.object(Path, "resolve", lambda self: self):
            elves = runner.parse_probe_output(probe_stdout, "", libgdal, libjni)
            self.assertEqual(
                elves,
                ["/lib/x86_64-linux-gnu/libc.so.6", "/out/lib/libgdal.so", "/out/lib/libgdalalljni.so"],
            )
            runner.parse_exception_output(exc_stdout, "")

            for bad_diag in ("WARNING in native method: JNI call", "FATAL ERROR in native method: bad ref"):
                with self.assertRaisesRegex(ValueError, "-Xcheck:jni"):
                    runner.parse_probe_output(probe_stdout, bad_diag, libgdal, libjni)
                with self.assertRaisesRegex(ValueError, "-Xcheck:jni"):
                    runner.parse_exception_output(exc_stdout + "\n" + bad_diag, "")

            with self.assertRaisesRegex(ValueError, "GdalJniProbe"):
                runner.parse_probe_output("ELF /out/lib/libgdal.so\nELF /out/lib/libgdalalljni.so", "", libgdal, libjni)
            with self.assertRaisesRegex(ValueError, "mapped ELFs"):
                runner.parse_probe_output(f"ELF /out/lib/libgdal.so\n{runner.PROBE_MARKER}", "", libgdal, libjni)
            with self.assertRaisesRegex(ValueError, "GdalExceptionControls"):
                runner.parse_exception_output("no marker", "")


if __name__ == "__main__":
    unittest.main()
