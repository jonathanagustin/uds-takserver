# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

import base64
import binascii
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import threading
import traceback
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("revocation", Path(__file__).with_name("takclient-revocation.py"))
revocation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(revocation)


class PrivateMaterialTests(unittest.TestCase):
    certificate = b"private-certificate-sentinel-643"
    key = b"private-key-sentinel-643"

    def material(self):
        return {name: base64.b64encode(value).decode() for name, value in (
            ("client.pem", self.certificate), ("client.key", self.key))}

    def test_control_files_are_owner_only_even_with_a_permissive_umask(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            previous = os.umask(0)
            try:
                with revocation.control_material(Path(directory), self.material()) as (cert, key):
                    owned = cert.parent
                    self.assertEqual(stat.S_IMODE(owned.stat().st_mode), 0o700)
                    self.assertEqual(stat.S_IMODE(cert.stat().st_mode), 0o600)
                    self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
                    self.assertEqual(cert.read_bytes(), self.certificate)
                    self.assertEqual(key.read_bytes(), self.key)
                self.assertFalse(owned.exists())
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                os.umask(previous)
        self.assertEqual(output.getvalue(), "")

    def test_control_files_are_removed_on_decode_or_certificate_load_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = {**self.material(), "client.key": "not base64!"}
            with self.assertRaises(binascii.Error):
                with revocation.control_material(root, invalid):
                    self.fail("invalid material reached the TLS loader")
            self.assertEqual(list(root.iterdir()), [])
            with self.assertRaisesRegex(ValueError, "certificate load refused"):
                with revocation.control_material(root, self.material()):
                    raise ValueError("certificate load refused")
            self.assertEqual(list(root.iterdir()), [])

    def test_kubernetes_failures_do_not_publish_private_arguments_or_streams(self):
        sentinel = "private-command-sentinel-643"
        for failure, expected in (
            (subprocess.CalledProcessError(7, [sentinel], output=self.key.decode(), stderr=sentinel),
             "Kubernetes operation failed (exit 7)"),
            (subprocess.TimeoutExpired([sentinel], 200, output=self.key, stderr=sentinel),
             "Kubernetes operation timed out"),
            (OSError(2, sentinel, self.key.decode()), "Kubernetes operation could not start"),
        ):
            with self.subTest(expected=expected), \
                    patch.object(revocation.subprocess, "run", side_effect=failure):
                try:
                    revocation.kubectl("get", "secret", sentinel, data=self.key.decode())
                except RuntimeError as error:
                    self.assertEqual(str(error), expected)
                    diagnostic = "".join(traceback.format_exception(error))
                    self.assertNotIn(sentinel, diagnostic)
                    self.assertNotIn(self.key.decode(), diagnostic)
                    self.assertTrue(error.__suppress_context__)
                else:
                    self.fail("the Kubernetes failure was ignored")

    def test_successful_secret_read_is_returned_without_output(self):
        payload = json.dumps({"data": self.material()})
        output = io.StringIO()
        with patch.object(revocation.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, payload)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(revocation.kubectl("get", "secret", "fixture"), payload)
        self.assertEqual(output.getvalue(), "")

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
    def test_loaded_control_context_authenticates_after_its_files_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert, key = root / "fixture.pem", root / "fixture.key"
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                "-addext", "extendedKeyUsage=serverAuth,clientAuth",
                "-keyout", str(key), "-out", str(cert),
            ], check=True, capture_output=True, timeout=30)
            server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server.load_cert_chain(cert, key)
            server.load_verify_locations(cafile=cert)
            server.verify_mode = ssl.CERT_REQUIRED
            client = ssl.create_default_context(cafile=cert)
            data = {"client.pem": base64.b64encode(cert.read_bytes()).decode(),
                    "client.key": base64.b64encode(key.read_bytes()).decode()}
            with revocation.control_material(root, data) as paths:
                client.load_cert_chain(*paths)
                owned = paths[0].parent
            self.assertFalse(owned.exists())
            failures = []
            with socket.socket() as listener:
                listener.settimeout(5)
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)

                def accept_control():
                    try:
                        raw, _address = listener.accept()
                        with raw:
                            raw.settimeout(5)
                            with server.wrap_socket(raw, server_side=True) as secure:
                                self.assertIsNotNone(secure.getpeercert())
                                self.assertEqual(secure.recv(64), b"control")
                                secure.sendall(b"alive")
                    except Exception as error:
                        failures.append(error)

                worker = threading.Thread(target=accept_control, daemon=True)
                worker.start()
                try:
                    with socket.create_connection(listener.getsockname(), timeout=5) as raw:
                        with client.wrap_socket(raw, server_hostname="localhost") as secure:
                            secure.sendall(b"control")
                            self.assertEqual(secure.recv(64), b"alive")
                finally:
                    worker.join(6)
                self.assertFalse(worker.is_alive())
                self.assertEqual(failures, [])


class ActiveEndpointsTests(unittest.TestCase):
    def test_real_api_envelope_uses_active_filter_and_fresh_admin_tls(self):
        ctx = MagicMock()
        api = ctx.wrap_socket.return_value.__enter__.return_value
        # The default API includes these disconnected historical rows. The
        # active query excludes them server-side; it must not delete history.
        history = {"version": "3", "type": "com.bbn.marti.remote.ClientEndpoint", "data": [
            {"callsign": "revoked", "uid": "revoked", "lastStatus": "Disconnected"},
        ]}
        active = {**history, "data": []}
        with patch.object(revocation.socket, "create_connection") as connect, \
                patch.object(revocation.http.client, "HTTPResponse") as response:
            response.return_value.status = 200
            response.return_value.read.return_value = json.dumps(active).encode()
            for _ in range(2):
                self.assertEqual(revocation.active_endpoints(ctx, 443, "uds.dev"), [])
            self.assertEqual(connect.call_count, 2)
            self.assertEqual(ctx.wrap_socket.call_count, 2)
            request = api.sendall.call_args.args[0]
            self.assertIn(b"GET /Marti/api/clientEndPoints?showCurrentlyConnectedClients=true HTTP/1.1", request)
            self.assertEqual(history["data"][0]["lastStatus"], "Disconnected")

    def test_admin_rejection_is_immediate(self):
        with patch.object(revocation.socket, "create_connection"), \
                patch.object(revocation.http.client, "HTTPResponse") as response:
            response.return_value.status = 403
            with self.assertRaisesRegex(AssertionError, "admin certificate"):
                revocation.active_endpoints(MagicMock(), 443, "uds.dev")
            response.return_value.read.assert_not_called()

    def test_malformed_identity_fails_closed_and_optional_callsign_may_be_null(self):
        ctx = MagicMock()
        with patch.object(revocation.socket, "create_connection"), \
                patch.object(revocation.http.client, "HTTPResponse") as response:
            response.return_value.status = 200
            for row in ({}, {"unknown": "revoked"}, {"uid": None}, {"uid": 7},
                        {"uid": ""}, {"uid": "revoked", "callsign": 7}):
                with self.subTest(row=row):
                    response.return_value.read.return_value = json.dumps({"data": [row]}).encode()
                    with self.assertRaisesRegex(AssertionError, "invalid active endpoint identity"):
                        revocation.active_endpoints(ctx, 443, "uds.dev")
            response.return_value.read.return_value = json.dumps({"data": [{"uid": "control", "callsign": None}]}).encode()
            self.assertEqual(revocation.active_endpoints(ctx, 443, "uds.dev"), [{"uid": "control", "callsign": None}])

    def test_cached_active_entry_expires_and_controls_checked_each_poll(self):
        now = [0]
        fetch = MagicMock(side_effect=[[{"callsign": "revoked", "lastStatus": "Connected"}]] * 5 + [[]])
        controls = MagicMock()
        revocation.wait_for_revoked_endpoint("revoked", fetch, controls,
            clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        self.assertEqual(now[0], 5)
        self.assertEqual(controls.call_count, 6)

    def test_persistent_active_entry_fails_at_deadline(self):
        now = [0]
        with self.assertRaisesRegex(AssertionError, "after 30 seconds"):
            revocation.wait_for_revoked_endpoint("revoked", lambda: [{"uid": "revoked"}], lambda: None,
                clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        self.assertEqual(now[0], 30)

    def test_poll_does_not_mask_admin_or_control_failure(self):
        for failure in (AssertionError("admin certificate no longer authenticates"), OSError("TLS failed")):
            fetch = MagicMock(side_effect=failure)
            with self.assertRaises(type(failure)):
                revocation.wait_for_revoked_endpoint("revoked", fetch, lambda: None)
            self.assertEqual(fetch.call_count, 1)
        fetch = MagicMock()
        with self.assertRaisesRegex(AssertionError, "control closed"):
            revocation.wait_for_revoked_endpoint("revoked", fetch,
                MagicMock(side_effect=AssertionError("control closed")))
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
