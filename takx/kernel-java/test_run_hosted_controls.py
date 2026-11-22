# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/kernel-java/run_hosted_controls.py."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "kernel_java_hosted_controls",
    Path(__file__).with_name("run_hosted_controls.py"),
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class _FakeHttpResponse(io.BytesIO):
    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class HostedKernelJavaControlsTests(unittest.TestCase):
    def test_verify_offline_and_select_patched_shared_jars(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        self.assertEqual(info["revision"], runner.EXPECTED_REVISION)
        self.assertEqual(info["base_public_jars"], 23)
        self.assertEqual(info["shared_public_jars"], 10)
        self.assertEqual(len(info["lock_sha256"]), 64)

        lock = json.loads(runner.LOCK_PATH.read_text(encoding="utf-8"))
        selected = runner.select_patched_shared_jars(lock)
        self.assertEqual(len(selected), 33)
        self.assertIn("guava-33.7.2-jre.jar", selected)
        self.assertIn("jackson-databind-2.23.0.jar", selected)
        self.assertNotIn("guava-30.0-jre.jar", selected)
        self.assertNotIn("jackson-databind-2.13.1.jar", selected)

    def test_candidate_urls_and_preseed_public_jars_mirror_fallback(self) -> None:
        jogamp_url = "https://jogamp.org/deployment/maven/org/jogamp/jogl/jogl-all/2.5.0/jogl-all-2.5.0.jar"
        self.assertEqual(runner.candidate_urls(jogamp_url), [jogamp_url])

        maven_url = "https://repo.maven.apache.org/maven2/com/google/guava/guava/32.1.3-jre/guava-32.1.3-jre.jar"
        self.assertEqual(
            runner.candidate_urls(maven_url),
            [
                maven_url,
                (
                    "https://maven-central.storage-download.googleapis.com/maven2/"
                    "com/google/guava/guava/32.1.3-jre/guava-32.1.3-jre.jar"
                ),
            ],
        )

        payload = b"synthetic-guava-jar"
        payload_sha = hashlib.sha256(payload).hexdigest()
        jars = {
            "guava-32.1.3-jre.jar": {
                "url": maven_url,
                "bytes": len(payload),
                "sha256": payload_sha,
            }
        }
        seen_urls: list[str] = []

        def fake_urlopen(req: mock.MagicMock, timeout: int = 60) -> _FakeHttpResponse:
            del timeout
            seen_urls.append(req.full_url)
            if req.full_url.startswith(runner.MAVEN_CENTRAL_PREFIX):
                raise urllib.error.URLError("HTTP 503")
            return _FakeHttpResponse(payload)

        with tempfile.TemporaryDirectory() as tmp:
            cp_dir = Path(tmp) / "jars"
            with mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_urlopen):
                hashes = runner.preseed_public_jars(jars, cp_dir)
            self.assertEqual(len(seen_urls), 2)
            self.assertEqual(hashes, {"guava-32.1.3-jre.jar": payload_sha})
            self.assertEqual((cp_dir / "guava-32.1.3-jre.jar").read_bytes(), payload)

        bad_jars = {
            "guava-32.1.3-jre.jar": {
                "url": maven_url,
                "bytes": len(payload),
                "sha256": "a" * 64,
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            cp_dir = Path(tmp) / "bad-jars"
            with mock.patch.object(
                runner.urllib.request, "urlopen", side_effect=lambda *_a, **_kw: _FakeHttpResponse(payload)
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to preseed public JAR"):
                    runner.preseed_public_jars(bad_jars, cp_dir)
            self.assertFalse((cp_dir / "guava-32.1.3-jre.jar").exists())

    def test_validate_build_result_accepts_complete_and_rejects_errors_or_missing_headers(self) -> None:
        valid_result = {
            "exit_code": 0,
            "errors": 0,
            "classpath_profile": "patched",
            "source_scope": "engine-shared",
            "engine_sources": 862,
            "shared_sources": 231,
            "upstream_sources": 1176,
            "adapter_sources": 59,
            "all_sources": 1235,
            "exact_public_jars": 33,
            "source_rebuilt_gis_jars": 2,
            "gis_classes_identical_to_original_probe": 107,
            "compiled_class_files": 2500,
            "required_jni_headers": 120,
            "generated_jni_headers": 140,
        }
        valid_coverage = {"missing_headers": []}
        runner.validate_build_result(valid_result, valid_coverage)

        with self.assertRaisesRegex(ValueError, "errors"):
            runner.validate_build_result({**valid_result, "errors": 1}, valid_coverage)
        with self.assertRaisesRegex(ValueError, "all_sources"):
            runner.validate_build_result({**valid_result, "all_sources": 1234}, valid_coverage)
        with self.assertRaisesRegex(ValueError, "compiled class files"):
            runner.validate_build_result({**valid_result, "compiled_class_files": 0}, valid_coverage)
        with self.assertRaisesRegex(ValueError, "incomplete JNI header generation"):
            runner.validate_build_result({**valid_result, "generated_jni_headers": 119}, valid_coverage)
        with self.assertRaisesRegex(ValueError, "missing required JNI headers"):
            runner.validate_build_result(valid_result, {"missing_headers": ["com_atakmap_Foo.h"]})


if __name__ == "__main__":
    unittest.main()
