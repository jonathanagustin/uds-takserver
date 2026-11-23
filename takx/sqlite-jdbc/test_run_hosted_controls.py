# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/sqlite-jdbc/run_hosted_controls.py."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "sqlite_jdbc_hosted_controls",
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


class HostedSqliteJdbcControlsTests(unittest.TestCase):
    def test_verify_offline_validates_lock_and_sources(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        self.assertEqual(info["lock_sha256"], runner.LOCK_SHA)
        self.assertEqual(info["input_count"], 74)
        self.assertEqual(info["total_bytes"], 15814646)

    def test_candidate_urls_adds_google_maven_mirror_fallback(self) -> None:
        sqlite_url = "https://www.sqlite.org/2026/sqlite-src-3540100.zip"
        self.assertEqual(runner.candidate_urls(sqlite_url), [sqlite_url])

        maven_url = "https://repo.maven.apache.org/maven2/org/slf4j/slf4j-api/1.7.36/slf4j-api-1.7.36.jar"
        expected_mirror = (
            "https://maven-central.storage-download.googleapis.com/maven2/"
            "org/slf4j/slf4j-api/1.7.36/slf4j-api-1.7.36.jar"
        )
        self.assertEqual(runner.candidate_urls(maven_url), [maven_url, expected_mirror])

    def test_parse_load_order_maps_accepts_clean_and_rejects_violations(self) -> None:
        jdbc_native = Path("/tmp/jdbc-build/native/libsqlitejdbc.so")
        modern_group = Path("/tmp/load-orders/modern-group.so")
        valid_output = "\n".join([
            "PASS actual source-built SQLite JDBC/native: 42 checks",
            "encrypt_reopen_wrong_key=PASS",
            "PASS actual source-built SQLite JDBC/native: 42 checks",
            "jdbc_plain_and_sqlcipher_encrypted_controls=PASS",
            "map=7f1000-7f2000 r-xp 0000 /tmp/jdbc-build/native/libsqlitejdbc.so",
            "map=7f3000-7f4000 r-xp 0000 /tmp/load-orders/modern-group.so",
            "map=7f5000-7f6000 r-xp 0000 /lib/x86_64-linux-gnu/libc.so.6",
        ])

        with mock.patch.object(Path, "resolve", lambda self: self):
            mapped = runner.parse_load_order_maps(valid_output, jdbc_native, modern_group)
            self.assertEqual(
                mapped,
                [
                    "/lib/x86_64-linux-gnu/libc.so.6",
                    "/tmp/jdbc-build/native/libsqlitejdbc.so",
                    "/tmp/load-orders/modern-group.so",
                ],
            )

            # Reject missing pass markers.
            with self.assertRaises(ValueError):
                runner.parse_load_order_maps(
                    valid_output.replace("PASS actual source-built SQLite JDBC/native:", "SINGLE", 1),
                    jdbc_native,
                    modern_group,
                )
            for marker in ("encrypt_reopen_wrong_key=PASS", "jdbc_plain_and_sqlcipher_encrypted_controls=PASS"):
                with self.assertRaises(ValueError):
                    runner.parse_load_order_maps(valid_output.replace(marker, "FAIL"), jdbc_native, modern_group)

            # Reject missing expected shared objects.
            with self.assertRaises(ValueError):
                runner.parse_load_order_maps(
                    valid_output.replace("/tmp/load-orders/modern-group.so", "/tmp/other.so"),
                    jdbc_native,
                    modern_group,
                )

            # Reject leaked host SQLite or OpenSSL libraries.
            for leaked in (
                "/usr/lib/x86_64-linux-gnu/libsqlite3.so.0",
                "/usr/lib/x86_64-linux-gnu/libcrypto.so.3",
            ):
                with self.assertRaises(ValueError):
                    runner.parse_load_order_maps(
                        f"{valid_output}\nmap=7f7000-7f8000 r-xp 0000 {leaked}",
                        jdbc_native,
                        modern_group,
                    )

    def test_preseed_maven_inputs_falls_back_to_google_mirror_and_checks_sha256(self) -> None:
        payload = b"synthetic-slf4j-jar-bytes"
        payload_sha = hashlib.sha256(payload).hexdigest()
        lock = {
            "inputs": [
                {
                    "path": "target/classpath/slf4j-api.jar",
                    "url": "https://repo.maven.apache.org/maven2/org/slf4j/slf4j-api/1.7.36/slf4j-api-1.7.36.jar",
                    "bytes": len(payload),
                    "sha256": payload_sha,
                },
                {
                    "path": "target/sqlite-3.54.1-src.zip",
                    "url": "https://www.sqlite.org/2026/sqlite-src-3540100.zip",
                    "bytes": 10,
                    "sha256": "0" * 64,
                },
            ]
        }

        calls: list[str] = []

        def fake_urlopen(req: mock.MagicMock, timeout: int = 60) -> _FakeHttpResponse:
            del timeout
            url = req.full_url
            calls.append(url)
            if url.startswith(runner.MAVEN_CENTRAL_PREFIX):
                raise urllib.error.URLError("HTTP 503 Service Unavailable")
            return _FakeHttpResponse(payload)

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            with mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_urlopen):
                seeded = runner.preseed_maven_inputs(lock, cache_dir)
            self.assertEqual(len(calls), 2)
            self.assertTrue(calls[0].startswith(runner.MAVEN_CENTRAL_PREFIX))
            self.assertTrue(calls[1].startswith(runner.GOOGLE_MAVEN_MIRROR_PREFIX))
            self.assertEqual(len(seeded), 1)
            self.assertEqual(seeded[0]["sha256"], payload_sha)
            self.assertEqual((cache_dir / payload_sha).read_bytes(), payload)

        bad_lock = {
            "inputs": [
                {
                    "path": "target/classpath/slf4j-api.jar",
                    "url": "https://repo.maven.apache.org/maven2/org/slf4j/slf4j-api/1.7.36/slf4j-api-1.7.36.jar",
                    "bytes": len(payload),
                    "sha256": "f" * 64,
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache-bad"
            with mock.patch.object(
                runner.urllib.request, "urlopen", side_effect=lambda *_a, **_kw: _FakeHttpResponse(payload)
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to preseed Maven input"):
                    runner.preseed_maven_inputs(bad_lock, cache_dir)
            self.assertFalse((cache_dir / ("f" * 64)).exists())


if __name__ == "__main__":
    unittest.main()
