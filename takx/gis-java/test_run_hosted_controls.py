# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/gis-java/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "gis_java_hosted_controls",
    Path(__file__).with_name("run_hosted_controls.py"),
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


class HostedGisJavaControlsTests(unittest.TestCase):
    def test_verify_offline_validates_lock_and_profile(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        self.assertEqual(info["revision"], runner.EXPECTED_REVISION)
        self.assertEqual(info["swig_version"], "4.3.0")
        self.assertEqual(info["profile_id"], runner.EXPECTED_PROFILE_ID)
        self.assertEqual(len(info["lock_sha256"]), 64)

    def test_audit_jar_accepts_expected_counts_and_rejects_mismatch_or_native_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "valid.jar"
            _write_zip(valid, {
                "org/gdal/gdal/gdal.class": b"class1",
                "org/gdal/ogr/ogr.class": b"class2",
                "META-INF/LICENSE": b"MIT",
                "META-INF/NOTICE": b"notice",
            })
            res = runner.audit_jar(valid, expected_classes=2, require_notice=True)
            self.assertEqual(res["class_count"], 2)
            self.assertEqual(res["path"], "valid.jar")
            self.assertEqual(res["sha256"], runner.sha256_file(valid))

            no_notice = root / "no-notice.jar"
            _write_zip(no_notice, {
                "a.class": b"class1",
                "META-INF/LICENSE": b"BSD",
            })
            self.assertEqual(runner.audit_jar(no_notice, 1, require_notice=False)["class_count"], 1)
            with self.assertRaisesRegex(ValueError, "NOTICE"):
                runner.audit_jar(no_notice, 1, require_notice=True)

            no_license = root / "no-license.jar"
            _write_zip(no_license, {"a.class": b"class1"})
            with self.assertRaisesRegex(ValueError, "LICENSE"):
                runner.audit_jar(no_license, 1, require_notice=False)

            native = root / "native.jar"
            _write_zip(native, {
                "a.class": b"class1",
                "META-INF/LICENSE": b"MIT",
                "libgdalalljni.so": b"elf",
            })
            with self.assertRaisesRegex(ValueError, "native binary"):
                runner.audit_jar(native, 1, require_notice=False)

            wrong_count = root / "wrong-count.jar"
            _write_zip(wrong_count, {"a.class": b"class1", "META-INF/LICENSE": b"MIT"})
            with self.assertRaisesRegex(ValueError, "class count"):
                runner.audit_jar(wrong_count, 2, require_notice=False)

    def test_audit_generated_wrappers_accepts_complete_set_and_rejects_missing_or_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gen = Path(tmp) / "generated"
            gen.mkdir()
            for name in runner.EXPECTED_WRAPPERS:
                (gen / name).write_text(f"/* {name} */\n", encoding="utf-8")

            hashes = runner.audit_generated_wrappers(gen)
            self.assertEqual(set(hashes), set(runner.EXPECTED_WRAPPERS))

            (gen / "gnm_wrap.cpp").write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "empty generated SWIG wrapper"):
                runner.audit_generated_wrappers(gen)

            (gen / "gnm_wrap.cpp").unlink()
            with self.assertRaises(FileNotFoundError):
                runner.audit_generated_wrappers(gen)


if __name__ == "__main__":
    unittest.main()
