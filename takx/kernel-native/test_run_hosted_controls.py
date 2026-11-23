# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/kernel-native/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_DIR = str(Path(__file__).resolve().parent)
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

SPEC = importlib.util.spec_from_file_location(
    "kernel_native_hosted_controls",
    Path(__file__).with_name("run_hosted_controls.py"),
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def _dynsym_row(ndx: str, bind: str, name: str) -> str:
    return f"   1: 0000000000001000     8 FUNC    {bind:<6} DEFAULT  {ndx:>3} {name}"


class HostedKernelNativeControlsTests(unittest.TestCase):
    def test_verify_offline_succeeds(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        self.assertEqual(info["source_counts"], {"core": 483, "jni": 159, "cesium": 173})
        self.assertEqual(len(info["hosted_cmake_sha256"]), 64)
        self.assertEqual(info["inputs_lock_sha256"], runner.EXPECTED_LOCK_SHA)

    def test_audit_elfs_accepts_valid_core_and_jni(self) -> None:
        archive_defs = {
            "ssl": {"SSL_CTX_new"},
            "crypto": {"EVP_sha256"},
            "expat": {"XML_ParserCreate"},
            "sqlite": {"sqlite3_config", "sqlite3_errstr", "sqlite3_open_v2"},
        }
        core_dynsym = "\n".join([
            _dynsym_row("12", "GLOBAL", "sqlite3_config"),
            _dynsym_row("12", "GLOBAL", "sqlite3_errstr"),
            _dynsym_row("12", "GLOBAL", "atan2"),
        ])
        core_dyn = (
            " 0x0000000000000001 (NEEDED) Shared library: [libGLESv2.so.2]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libgdal.so]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [liblas_c.so.3]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libstdc++.so.6]"
        )
        jni_dynsym = "\n".join([
            _dynsym_row("UND", "GLOBAL", "sqlite3_config"),
            _dynsym_row("UND", "GLOBAL", "sqlite3_errstr"),
            _dynsym_row("12", "GLOBAL", "Java_com_atakmap_map_EngineLibrary_initialize"),
        ])
        jni_dyn = (
            " 0x0000000000000001 (NEEDED) Shared library: [libtakengine.so]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libGLESv2.so.2]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libgdal.so]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libc.so.6]"
        )
        jni_nm = "0000000000001000 T Java_com_atakmap_map_EngineLibrary_initialize\n"

        report = runner.audit_elf_outputs(
            core_so=Path("/out/libtakengine.so"),
            jni_so=Path("/out/libtakenginejni.so"),
            core_dynsym=core_dynsym,
            core_dyn=core_dyn,
            jni_dynsym=jni_dynsym,
            jni_dyn=jni_dyn,
            jni_nm=jni_nm,
            archive_definitions=archive_defs,
        )
        self.assertTrue(report["hidden_crypto_ssl_expat"])
        self.assertTrue(report["shared_sqlcipher_owner"])
        self.assertFalse(report["second_jni_sqlite_body"])
        self.assertEqual(report["elfs"]["Core"]["sqlcipher_exports"], ["sqlite3_config", "sqlite3_errstr"])
        self.assertEqual(report["elfs"]["JNI"]["sqlcipher_imports"], ["sqlite3_config", "sqlite3_errstr"])

    def test_audit_elfs_rejects_textrel_leaks_and_wrong_sqlite_ownership(self) -> None:
        archive_defs = {
            "ssl": {"SSL_CTX_new"},
            "crypto": {"EVP_sha256"},
            "expat": {"XML_ParserCreate"},
            "sqlite": {"sqlite3_config", "sqlite3_errstr", "sqlite3_open_v2"},
        }
        good_core_dynsym = "\n".join([
            _dynsym_row("12", "GLOBAL", "sqlite3_config"),
            _dynsym_row("12", "GLOBAL", "sqlite3_errstr"),
        ])
        good_core_dyn = (
            " 0x0000000000000001 (NEEDED) Shared library: [libGLESv2.so.2]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libgdal.so]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [liblas_c.so.3]"
        )
        good_jni_dynsym = "\n".join([
            _dynsym_row("UND", "GLOBAL", "sqlite3_config"),
            _dynsym_row("UND", "GLOBAL", "sqlite3_errstr"),
        ])
        good_jni_dyn = (
            " 0x0000000000000001 (NEEDED) Shared library: [libtakengine.so]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libGLESv2.so.2]\n"
            " 0x0000000000000001 (NEEDED) Shared library: [libgdal.so]"
        )
        good_jni_nm = "0000000000001000 T Java_com_atakmap_map_EngineLibrary_initialize\n"

        with self.assertRaisesRegex(ValueError, "TEXTREL"):
            runner.audit_elf_outputs(
                Path("/out/libtakengine.so"),
                Path("/out/libtakenginejni.so"),
                good_core_dynsym,
                good_core_dyn + "\n 0x00000016 (TEXTREL) 0x0\n",
                good_jni_dynsym,
                good_jni_dyn,
                good_jni_nm,
                archive_defs,
            )

        with self.assertRaisesRegex(ValueError, "leaked ssl"):
            runner.audit_elf_outputs(
                Path("/out/libtakengine.so"),
                Path("/out/libtakenginejni.so"),
                good_core_dynsym + "\n" + _dynsym_row("12", "GLOBAL", "SSL_CTX_new"),
                good_core_dyn,
                good_jni_dynsym,
                good_jni_dyn,
                good_jni_nm,
                archive_defs,
            )

        with self.assertRaisesRegex(ValueError, "leaked expat"):
            runner.audit_elf_outputs(
                Path("/out/libtakengine.so"),
                Path("/out/libtakenginejni.so"),
                good_core_dynsym,
                good_core_dyn,
                good_jni_dynsym + "\n" + _dynsym_row("UND", "GLOBAL", "XML_ParserCreate"),
                good_jni_dyn,
                good_jni_nm,
                archive_defs,
            )

        with self.assertRaisesRegex(ValueError, "Core SQLCipher boundary"):
            runner.audit_elf_outputs(
                Path("/out/libtakengine.so"),
                Path("/out/libtakenginejni.so"),
                good_core_dynsym + "\n" + _dynsym_row("12", "GLOBAL", "sqlite3_open_v2"),
                good_core_dyn,
                good_jni_dynsym,
                good_jni_dyn,
                good_jni_nm,
                archive_defs,
            )

        with self.assertRaisesRegex(ValueError, "second SQLite implementation"):
            runner.audit_elf_outputs(
                Path("/out/libtakengine.so"),
                Path("/out/libtakenginejni.so"),
                good_core_dynsym,
                good_core_dyn,
                good_jni_dynsym,
                good_jni_dyn,
                good_jni_nm + "0000000000002000 t sqlite3_open_v2\n",
                archive_defs,
            )

        with self.assertRaisesRegex(ValueError, "unreviewed NEEDED"):
            runner.audit_elf_outputs(
                Path("/out/libtakengine.so"),
                Path("/out/libtakenginejni.so"),
                good_core_dynsym,
                good_core_dyn + "\n (NEEDED) Shared library: [libssl.so.3]\n",
                good_jni_dynsym,
                good_jni_dyn,
                good_jni_nm,
                archive_defs,
            )

    def test_parse_runtime_output_accepts_basic_database_logger(self) -> None:
        bound = {
            "/opt/libtakengine.so": "a" * 64,
            "/opt/libtakenginejni.so": "b" * 64,
        }
        maps_lines = "\n".join(f"ELF {sha} {path}" for path, sha in bound.items())

        basic_res = runner.parse_runtime_phase_output(
            "basic",
            f"PASS_KERNEL_JNI_MATRIX_POINT_WKB_NATIVE_PROJECTION\n{maps_lines}\n{runner.REQUIRED_RUNTIME_FOOTER}\n",
            "",
            ("PASS_KERNEL_JNI_MATRIX_POINT_WKB_NATIVE_PROJECTION",),
            bound,
        )
        self.assertEqual(basic_res["strict_jni_warnings"], 0)
        self.assertEqual(basic_res["mapped_elf_count"], 2)

        db_tokens = (
            "PASS_PREPARED_NATIVE_DATABASE2_ENCRYPTED_OPEN_REOPEN_WRONG_KEY_CONTROL",
            "PASS_REAL_GDAL_MEM_EXACT_WRITE_READ",
        )
        db_res = runner.parse_runtime_phase_output(
            "database",
            "\n".join([*db_tokens, maps_lines, runner.REQUIRED_RUNTIME_FOOTER]),
            "",
            db_tokens,
            bound,
        )
        self.assertEqual(db_res["mapped_elf_count"], 2)

        logger_stdout = (
            "LOGGER_CALLBACKS=3 BASELINE_WRONG_KEY=open-SQLException "
            "LISTENER_WRONG_KEY=open-SQLException\n"
            "PASS_REAL_THROWING_LOGGER_BASELINE_REJECTION_MATCH_AND_CORRECT_KEY_RECOVERY\n"
            f"{maps_lines}\n"
            f"{runner.REQUIRED_RUNTIME_FOOTER}"
        )
        logger_res = runner.parse_runtime_phase_output(
            "logger",
            logger_stdout,
            "",
            ("PASS_REAL_THROWING_LOGGER_BASELINE_REJECTION_MATCH_AND_CORRECT_KEY_RECOVERY",),
            bound,
        )
        self.assertEqual(logger_res["logger_callbacks"], 3)

    def test_parse_runtime_output_rejects_xcheck_missing_elf_and_forbidden_libcrypto(self) -> None:
        bound = {"/opt/libtakengine.so": "a" * 64}
        good_stdout = "\n".join([
            "PASS_KERNEL_JNI_MATRIX_POINT_WKB_NATIVE_PROJECTION",
            f"ELF {'a' * 64} /opt/libtakengine.so",
            runner.REQUIRED_RUNTIME_FOOTER,
        ])

        with self.assertRaisesRegex(ValueError, "-Xcheck:jni"):
            runner.parse_runtime_phase_output(
                "basic",
                good_stdout,
                "WARNING in native method: JNI call made without checking exceptions",
                ("PASS_KERNEL_JNI_MATRIX_POINT_WKB_NATIVE_PROJECTION",),
                bound,
            )

        with self.assertRaisesRegex(ValueError, "forbidden shared crypto/sqlite"):
            runner.parse_runtime_phase_output(
                "basic",
                good_stdout + f"\nELF {'c' * 64} /usr/lib/libcrypto.so.3",
                "",
                ("PASS_KERNEL_JNI_MATRIX_POINT_WKB_NATIVE_PROJECTION",),
                bound,
            )

        with self.assertRaisesRegex(ValueError, "absent or hash-mismatched"):
            runner.parse_runtime_phase_output(
                "basic",
                f"PASS_KERNEL_JNI_MATRIX_POINT_WKB_NATIVE_PROJECTION\n{runner.REQUIRED_RUNTIME_FOOTER}",
                "",
                ("PASS_KERNEL_JNI_MATRIX_POINT_WKB_NATIVE_PROJECTION",),
                bound,
            )

        with self.assertRaisesRegex(ValueError, "throwing logger boundary"):
            runner.parse_runtime_phase_output(
                "logger",
                "\n".join([
                    "LOGGER_CALLBACKS=1 BASELINE_WRONG_KEY=open-SQLException LISTENER_WRONG_KEY=null-return",
                    "PASS_REAL_THROWING_LOGGER_BASELINE_REJECTION_MATCH_AND_CORRECT_KEY_RECOVERY",
                    f"ELF {'a' * 64} /opt/libtakengine.so",
                    runner.REQUIRED_RUNTIME_FOOTER,
                ]),
                "",
                ("PASS_REAL_THROWING_LOGGER_BASELINE_REJECTION_MATCH_AND_CORRECT_KEY_RECOVERY",),
                bound,
            )

    def test_normalize_source_command_and_clean_env(self) -> None:
        normalized = runner.normalize_source_command(
            ["git", "--no-lazy-fetch", "-C", "/atak", "cat-file", "blob", "abc"]
        )
        self.assertEqual(normalized, ["git", "-C", "/atak", "cat-file", "blob", "abc"])
        env = runner._clean_build_env(Path("/usr/lib/jvm/java-17-openjdk-amd64"))
        self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "safe.directory")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], "*")


if __name__ == "__main__":
    unittest.main()
