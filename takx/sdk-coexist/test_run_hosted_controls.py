#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/sdk-coexist/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "takx_sdk_coexist_run_hosted_controls", HERE / "run_hosted_controls.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


RUNNER = _load_runner()


class HostedSdkCoexistControlsTests(unittest.TestCase):
    def test_verify_offline_checks_sources_and_resources_lock(self) -> None:
        result = RUNNER.verify_offline()
        self.assertEqual(result["schema"], "uds-takx-sdk-coexist-hosted-v1")
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(result["load_orders"], ["sdk-first", "common-first"])
        self.assertGreater(result["locked_runtime_resources"], 0)
        self.assertIn("JoinedSdkCommo.java", result["source_hashes"])
        self.assertIn("GdalReducedProfileSmoke.java", result["source_hashes"])
        self.assertIn("CommonCommoThrowingLogger.java", result["source_hashes"])

    def _sample_inputs(self, root: Path):
        lib_dir = root / "tmp/takkernel/engine/abc/lib/linux64"
        lib_dir.mkdir(parents=True)
        commo_dir = root / "commo/lib"
        commo_dir.mkdir(parents=True)
        commo_so = commo_dir / "libcommoncommojni.so"
        commo_so.write_bytes(b"\x7fELF")
        native_manifest = {
            f"libmock{i}.so": f"{i:064x}" for i in range(1, 10)
        }
        commo_sha = "a" * 64
        elf_lines = [
            f"JOINED_ELF {sha} {(lib_dir / name).resolve()}"
            for name, sha in native_manifest.items()
        ]
        elf_lines.append(f"JOINED_ELF {commo_sha} {commo_so.resolve()}")
        elf_lines.append(f"JOINED_ELF {'b' * 64} /lib/x86_64-linux-gnu/libc.so.6")
        return lib_dir, native_manifest, commo_so, commo_sha, "\n".join(elf_lines)

    def _valid_stdout(self, order: str, elf_block: str) -> str:
        first = "PASS_JOINED_FIRST_GROUP_SDK" if order == "sdk-first" else "PASS_JOINED_FIRST_GROUP_COMMONCOMMO"
        return "\n".join(
            [
                first,
                "PASS_STOCK_ENGINE_RESOURCE_LOADER_SCOPED_COMPONENT",
                "PASS_REAL_REDUCED_PROFILE_GDAL_SKIP_INIT_REGISTRY_NO_SUPPRESSION",
                "PASS_JOINED_COMMON_CONSTRUCTOR_PKCS12_SIGNAL_ROUTING_LOGGER",
                "PASS_JOINED_KERNEL_CONTROLS_AFTER_COMMON",
                elf_block,
                f"PASS_JOINED_STOCK_SDK_COMMONCOMMO {order}",
            ]
        )

    def test_audit_accepts_valid_sdk_first_and_common_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib_dir, native_manifest, commo_so, commo_sha, elf_block = self._sample_inputs(Path(tmp))
            for order in ("sdk-first", "common-first"):
                audit = RUNNER.audit_coexist_output(
                    order=order,
                    profile="reduced-kml",
                    stdout=self._valid_stdout(order, elf_block),
                    stderr="",
                    exit_code=0,
                    library_path=lib_dir,
                    native_manifest=native_manifest,
                    commo_jni_path=commo_so,
                    commo_jni_sha256=commo_sha,
                )
                self.assertTrue(audit["qualified"])
                self.assertEqual(audit["packaged_sdk_provider_count"], 9)
                self.assertTrue(audit["commoncommo_jni_verified"])
                self.assertEqual(audit["strict_jni_warnings"], 0)

    def test_audit_rejects_jni_warning_and_forbidden_host_library(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib_dir, native_manifest, commo_so, commo_sha, elf_block = self._sample_inputs(Path(tmp))
            valid = self._valid_stdout("sdk-first", elf_block)
            with self.assertRaisesRegex(ValueError, "strict -Xcheck:jni"):
                RUNNER.audit_coexist_output(
                    order="sdk-first",
                    profile="reduced-kml",
                    stdout=valid,
                    stderr="WARNING in native method: JNI call made without checking exceptions",
                    exit_code=0,
                    library_path=lib_dir,
                    native_manifest=native_manifest,
                    commo_jni_path=commo_so,
                    commo_jni_sha256=commo_sha,
                )

            bad_elf = valid + f"\nJOINED_ELF {'c' * 64} /usr/lib/x86_64-linux-gnu/libcrypto.so.3\n"
            with self.assertRaisesRegex(ValueError, "forbidden shared/host provider"):
                RUNNER.audit_coexist_output(
                    order="sdk-first",
                    profile="reduced-kml",
                    stdout=bad_elf,
                    stderr="",
                    exit_code=0,
                    library_path=lib_dir,
                    native_manifest=native_manifest,
                    commo_jni_path=commo_so,
                    commo_jni_sha256=commo_sha,
                )

    def test_audit_rejects_missing_marker_or_mismatched_commo_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib_dir, native_manifest, commo_so, commo_sha, elf_block = self._sample_inputs(Path(tmp))
            valid = self._valid_stdout("common-first", elf_block)
            with self.assertRaisesRegex(ValueError, "missing required coexistence markers"):
                RUNNER.audit_coexist_output(
                    order="common-first",
                    profile="reduced-kml",
                    stdout=valid.replace("PASS_JOINED_KERNEL_CONTROLS_AFTER_COMMON", ""),
                    stderr="",
                    exit_code=0,
                    library_path=lib_dir,
                    native_manifest=native_manifest,
                    commo_jni_path=commo_so,
                    commo_jni_sha256=commo_sha,
                )

            with self.assertRaisesRegex(ValueError, "CommonCommo JNI provider absent or mismatched"):
                RUNNER.audit_coexist_output(
                    order="common-first",
                    profile="reduced-kml",
                    stdout=valid,
                    stderr="",
                    exit_code=0,
                    library_path=lib_dir,
                    native_manifest=native_manifest,
                    commo_jni_path=commo_so,
                    commo_jni_sha256="f" * 64,
                )


if __name__ == "__main__":
    unittest.main()
