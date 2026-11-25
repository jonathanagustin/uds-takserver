# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/sdk-package/run_hosted_controls.py."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

MODULE_DIR = str(Path(__file__).resolve().parent)
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

SPEC = importlib.util.spec_from_file_location(
    "sdk_package_hosted_controls",
    Path(__file__).with_name("run_hosted_controls.py"),
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def _sample_manifest() -> dict[str, str]:
    return {
        f"lib{name}.so": hashlib.sha256(f"provider-{name}".encode()).hexdigest()
        for name in runner.EXPECTED_NATIVE_ORDER
    }


def _sample_output(
    extraction: Path,
    manifest: dict[str, str],
    markers: tuple[str, ...],
    extra_lines: list[str] | None = None,
) -> str:
    lines = list(markers)
    lines.append(f"EXTRACTION_DIRECTORY {extraction}")
    for name, sha in manifest.items():
        lines.append(f"PACKAGED_NATIVE {sha} {extraction / name}")
        lines.append(f"ELF {sha} {extraction / name}")
    lines.append(f"ELF {'a' * 64} /lib/x86_64-linux-gnu/libc.so.6")
    if extra_lines:
        lines.extend(extra_lines)
    return "\n".join(lines) + "\n"


class HostedSdkPackageControlsTests(unittest.TestCase):
    def test_verify_offline_succeeds(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        self.assertEqual(info["official_revision"], runner.EXPECTED_REVISION)
        self.assertEqual(info["native_order"], runner.EXPECTED_NATIVE_ORDER)
        self.assertEqual(info["locked_runtime_resources"], 118)
        self.assertEqual(len(info["patch_hashes"]), 5)

    def test_deterministic_jar_write_produces_identical_sorted_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries_a = {"b/file.txt": b"beta", "a/file.txt": b"alpha"}
            entries_b = {"a/file.txt": b"alpha", "b/file.txt": b"beta"}
            jar_a = root / "a.jar"
            jar_b = root / "b.jar"
            runner.deterministic_jar_write(jar_a, entries_a)
            runner.deterministic_jar_write(jar_b, entries_b)
            self.assertEqual(jar_a.read_bytes(), jar_b.read_bytes())
            with zipfile.ZipFile(jar_a, "r") as jar:
                self.assertEqual(jar.namelist(), ["a/file.txt", "b/file.txt"])
                self.assertEqual(jar.getinfo("a/file.txt").date_time, runner.FIXED_ZIP_TIME)

    def test_audit_phase_output_accepts_all_four_phases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extraction = Path(tmp) / "stock/takkernel/engine/abc/lib/linux64"
            extraction.mkdir(parents=True)
            manifest = _sample_manifest()
            forbidden = {"/caches/gles/lib/libGLESv2.so.2.1.0", "/kernel-native/libtakengine.so"}

            basic_text = _sample_output(extraction, manifest, runner.ALWAYS_REQUIRED_PASS_MARKERS)
            basic = runner.audit_phase_output("basic", "basic", basic_text, extraction, manifest, forbidden)
            self.assertEqual(basic["packaged_provider_count"], 9)
            self.assertEqual(basic["strict_jni_warnings"], 0)

            db_markers = runner.ALWAYS_REQUIRED_PASS_MARKERS + runner.DATABASE_REQUIRED_PASS_MARKERS
            db_text = _sample_output(extraction, manifest, db_markers)
            db = runner.audit_phase_output("database", "database", db_text, extraction, manifest, forbidden)
            self.assertEqual(db["mode"], "database")

            reloc_text = _sample_output(extraction, manifest, db_markers)
            reloc = runner.audit_phase_output("relocated", "database", reloc_text, extraction, manifest, forbidden)
            self.assertEqual(reloc["mode"], "database")

            logger_markers = runner.ALWAYS_REQUIRED_PASS_MARKERS + (runner.LOGGER_REQUIRED_PASS_MARKER,)
            logger_text = _sample_output(
                extraction,
                manifest,
                logger_markers,
                ["LOGGER_CALLBACKS=5 BASELINE_WRONG_KEY=open-SQLException LISTENER_WRONG_KEY=open-SQLException"],
            )
            logger = runner.audit_phase_output("logger", "logger", logger_text, extraction, manifest, forbidden)
            self.assertEqual(logger["logger_callbacks"], 5)

    def test_audit_phase_output_rejects_failures_and_substitutions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extraction = (root / "stock/lib/linux64").resolve()
            wrong_dir = (root / "other/lib/linux64").resolve()
            extraction.mkdir(parents=True)
            wrong_dir.mkdir(parents=True)
            manifest = _sample_manifest()
            forbidden = {"/kernel-native/libtakengine.so"}
            valid_text = _sample_output(extraction, manifest, runner.ALWAYS_REQUIRED_PASS_MARKERS)

            with self.assertRaisesRegex(ValueError, "missing PASS markers"):
                runner.audit_phase_output(
                    "database", "database", valid_text, extraction, manifest, forbidden
                )

            with self.assertRaisesRegex(ValueError, "-Xcheck:jni diagnostic"):
                runner.audit_phase_output(
                    "basic",
                    "basic",
                    valid_text + "WARNING in native method: JNI call made without checking exceptions\n",
                    extraction,
                    manifest,
                    forbidden,
                )

            with self.assertRaisesRegex(ValueError, "extraction directory mismatch"):
                runner.audit_phase_output(
                    "basic",
                    "basic",
                    _sample_output(wrong_dir, manifest, runner.ALWAYS_REQUIRED_PASS_MARKERS).replace(
                        "PACKAGED_NATIVE", "IGNORED_PREFIX"
                    ),
                    extraction,
                    manifest,
                    forbidden,
                )

            with self.assertRaisesRegex(ValueError, "original pre-packaged native path"):
                runner.audit_phase_output(
                    "basic",
                    "basic",
                    valid_text + f"ELF {'b' * 64} /kernel-native/libtakengine.so\n",
                    extraction,
                    manifest,
                    forbidden,
                )

            with self.assertRaisesRegex(ValueError, "host provider substitution"):
                runner.audit_phase_output(
                    "basic",
                    "basic",
                    valid_text + f"ELF {'c' * 64} /usr/lib/x86_64-linux-gnu/libsqlite3.so.0\n",
                    extraction,
                    manifest,
                    forbidden,
                )

            bad_logger = _sample_output(
                extraction,
                manifest,
                runner.ALWAYS_REQUIRED_PASS_MARKERS + (runner.LOGGER_REQUIRED_PASS_MARKER,),
                ["LOGGER_CALLBACKS=2 BASELINE_WRONG_KEY=open-SQLException LISTENER_WRONG_KEY=read-SQLException"],
            )
            with self.assertRaisesRegex(ValueError, "throwing logger"):
                runner.audit_phase_output("logger", "logger", bad_logger, extraction, manifest, forbidden)


if __name__ == "__main__":
    unittest.main()
