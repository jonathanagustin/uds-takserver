# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Tests of the bridge's door (web_gate.py) with real sockets.

The gate listens on a loopback TCP port; a stand-in for websockify listens on
a Unix socket, records every request head it is sent and answers with marked
bytes ("noVNC" for a page, "RFB 003.008" for a WebSocket). A test that finds
no head recorded and no marker in the answer has shown that nothing of the
desktop left the gate. Tokens are signed by the OpenSSL command line
(verify/token_tool.py); skipped where `openssl` is missing.
Run: python3 -B -m unittest takx/desktop-prototype/image/test_web_gate.py
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "verify"))

import token_tool  # noqa: E402
import web_auth  # noqa: E402
import web_gate  # noqa: E402

ISSUER = "https://sso.example.test/realms/uds"
AUDIENCE = "uds-takx-desktop"
GROUP = "/UDS Core/Admin"
ORIGIN = "https://takx.example.test"
PAGE = b"<!DOCTYPE html><title>noVNC</title>"
RFB = b"RFB 003.008\n"
KEY = "dGhlIHNhbXBsZSBub25jZQ=="

KEYS = None
SIGNER = FORGER = None


def setUpModule():
    global KEYS, SIGNER, FORGER  # noqa: PLW0603
    if not shutil.which("openssl"):
        raise unittest.SkipTest("the openssl command is needed to make keys and signatures")
    KEYS = tempfile.TemporaryDirectory(prefix="takx-web-gate-test-")
    for name in ("a", "b"):
        Path(KEYS.name, name).mkdir()
    SIGNER = token_tool.Signer(Path(KEYS.name, "a"), "rsa-1")
    FORGER = token_tool.Signer(Path(KEYS.name, "b"), "rsa-1")


def tearDownModule():
    if KEYS is not None:
        KEYS.cleanup()


def token(signer=None, lifetime=60.0, **changes):
    now = time.time()
    claims = {"iss": ISSUER, "aud": AUDIENCE, "azp": AUDIENCE, "sub": "8a5f0000-0000-4000-8000-00000000d0c5",
              "iat": int(now) - 1, "exp": now + lifetime, "groups": [GROUP]}
    claims.update(changes)
    return (signer or SIGNER).token(claims)


def get(path="/vnc.html", *fields, method="GET"):
    return "\r\n".join([f"{method} {path} HTTP/1.1", "Host: takx.example.test", *fields, "", ""]).encode("latin-1")


def upgrade(*fields, path="/websockify"):
    return get(path, "Upgrade: websocket", "Connection: keep-alive, Upgrade", f"Sec-WebSocket-Key: {KEY}",
               "Sec-WebSocket-Version: 13", "Sec-WebSocket-Protocol: binary", *fields)


def auth(value):
    return f"Authorization: Bearer {value}"


class GateCase(unittest.IsolatedAsyncioTestCase):
    WS_MAX = 300.0
    VERIFY = True

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="takx-web-gate-")
        self.socket = os.path.join(self.directory.name, "websockify.sock")
        self.heads, self.lines, self.jwks_fail = [], [], False
        self.upstream = await asyncio.start_unix_server(self.websockify, self.socket)

        def fetch():
            if self.jwks_fail:
                raise OSError("connection refused")
            return token_tool.jwks(SIGNER)

        self.verifier = None
        if self.VERIFY:
            keys = web_auth.KeySet(fetch, log=self.lines.append)
            self.verifier = web_auth.Verifier(keys, issuer=ISSUER, audience=AUDIENCE, groups=[GROUP], leeway=0)
        self.gate = web_gate.Gate(self.verifier, self.socket, origin=ORIGIN, ws_max_seconds=self.WS_MAX,
                                  log=self.lines.append)
        self.server = await asyncio.start_server(self.gate.handle, "127.0.0.1", 0, limit=web_gate.MAX_HEAD)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        for server in (self.server, self.upstream):
            server.close()
            await server.wait_closed()
        self.directory.cleanup()

    async def websockify(self, reader, writer):
        """What stands behind the gate: a page with a marker, or 'RFB' and an echo."""
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            self.heads.append(head)
            if b"Upgrade: websocket" in head:
                writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                             b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n" + RFB)
                while data := await reader.read(4096):
                    writer.write(b"echo:" + data)
                    await writer.drain()
            else:
                body = b"" if head.startswith(b"HEAD") else PAGE
                writer.write(b"HTTP/1.1 200 OK\r\nServer: WebSockify Python/3.13.5\r\nContent-Type: text/html\r\n"
                             b"Content-Length: " + str(len(PAGE)).encode() + b"\r\n\r\n" + body)
                await writer.drain()
        except (asyncio.IncompleteReadError, OSError):
            pass
        finally:
            writer.close()

    async def ask(self, raw, limit=5.0):
        """Send raw bytes on a new connection; everything the gate sends until it closes."""
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(raw)
        await writer.drain()
        try:
            return await asyncio.wait_for(reader.read(-1), limit)
        finally:
            writer.close()

    def refused(self, reply, status):
        """The fixed short answer, nothing from behind the gate, websockify never contacted."""
        self.assertTrue(reply.startswith(f"HTTP/1.1 {status} ".encode()), reply[:60])
        self.assertEqual(self.heads, [])
        for marker in (b"noVNC", b"RFB", b"WebSockify", b"websockify", b"html", b"takx", b"Python"):
            self.assertNotIn(marker, reply)
        self.assertIn(b"Connection: close\r\n", reply)
        self.assertLess(len(reply), 300)


class Unauthenticated(GateCase):
    async def test_no_token(self):
        for raw in (get("/"), get("/vnc.html"), get("/core/rfb.js"), get("/websockify"), get(method="HEAD"), upgrade()):
            reply = await self.ask(raw)
            self.refused(reply, 401)
            self.assertIn(b"WWW-Authenticate: Bearer\r\n", reply)

    async def test_bad_tokens(self):
        expired = token(lifetime=-5)
        cases = [(token(FORGER), 401), (expired, 401), (token(aud="uds-other-app", azp="uds-other-app"), 401),
                 (token(iss="https://sso.example.test/realms/other"), 401), (token(nbf=time.time() + 60), 401),
                 (token(groups=["/CloudTAK/Users"]), 403), (token(groups=None), 403), ("a.b.c", 401), ("x", 401)]
        for value, status in cases:
            for raw in (get("/vnc.html", auth(value)), upgrade(auth(value))):
                self.refused(await self.ask(raw), status)
        self.assertTrue(all("denied:" in line for line in self.lines if line[:3] in ("401", "403")))

    async def test_token_anywhere_but_the_authorization_header(self):
        good = token()
        for raw in (get(f"/vnc.html?token={good}"), get(f"/vnc.html?access_token={good}&id_token={good}"),
                    get("/vnc.html", f"Cookie: token={good}; id_token={good}"), get("/vnc.html", f"X-Auth-Token: {good}"),
                    get("/vnc.html", f"X-Forwarded-Access-Token: {good}", f"Proxy-Authorization: Bearer {good}"),
                    upgrade(path=f"/websockify?token={good}"), upgrade(f"Sec-WebSocket-Protocol: bearer.{good}")):
            self.refused(await self.ask(raw), 401)

    async def test_non_bearer_scheme_and_repeated_header(self):
        good = token()
        for fields in ((f"Authorization: Basic {good}",), ("Authorization: Basic ZG91ZzpwYXNzd29yZA==",),
                       (f"Authorization: {good}",), (f"Authorization: Bearer {good} extra",),
                       (auth(good), "Authorization: Basic ZG91ZzpwYXNzd29yZA=="), (auth(good), auth(good)),
                       ("Authorization: Basic ZG91ZzpwYXNzd29yZA==", auth(good)), ("Authorization:",)):
            for raw in (get("/vnc.html", *fields), upgrade(*fields)):
                self.refused(await self.ask(raw), 401)

    async def test_oversized_header(self):
        good = token()
        self.refused(await self.ask(get("/vnc.html", auth(good), "X-Padding: " + "x" * web_gate.MAX_HEAD)), 431)
        self.refused(await self.ask(get("/vnc.html", "Authorization: Bearer " + "A" * 20000 + ".B.C")), 431)
        self.refused(await self.ask(get("/vnc.html", *[f"X-{n}: 1" for n in range(web_gate.MAX_HEADERS + 1)])), 400)

    async def test_requests_that_are_not_plain_http_1(self):
        good = auth(token())
        for raw in (b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n", b"GET /vnc.html\r\n\r\n", b"\x16\x03\x01\x02\x00\x01\r\n\r\n",
                    b"GET http://takx/vnc.html HTTP/1.1\r\n\r\n", get("/a/../../etc/passwd", good), get("//etc/passwd", good),
                    get("/vnc.html", good).replace(b"\r\nHost", b"\nHost"), get("/vnc.html", good, " folded: line"),
                    get("/vnc.html", good, "Content-Length: 5") + b"hello", get("/vnc.html", good, "Transfer-Encoding: chunked"),
                    get("/vnc.html", good, method="POST")[:-4] + b"\r\nContent-Length: 3\r\n\r\nabc",
                    get("/vnc\x00.html", good), get("/vnc.html", "Bad Name: x", good), b"\r\n\r\n"):
            self.refused(await self.ask(raw), 400)

    async def test_incomplete_head_is_dropped_without_an_answer(self):
        original, web_gate.HEAD_TIMEOUT = web_gate.HEAD_TIMEOUT, 0.3
        try:
            started = time.monotonic()
            self.assertEqual(await self.ask(b"GET /vnc.html HTTP/1.1\r\nHost: x\r\n"), b"")
            self.assertLess(time.monotonic() - started, 3)
        finally:
            web_gate.HEAD_TIMEOUT = original
        self.assertEqual(self.heads, [])

    async def test_keys_unavailable_fails_closed(self):
        self.jwks_fail = True
        for raw in (get("/vnc.html", auth(token())), upgrade(auth(token())), get("/vnc.html")):
            reply = await self.ask(raw)
            self.refused(reply, 401 if b"Authorization" not in raw else 503)

    async def test_healthz_answers_without_a_token_and_says_nothing(self):
        for method in ("GET", "HEAD"):
            reply = await self.ask(get("/healthz", method=method))
            self.assertEqual(reply.split(b"\r\n\r\n", 1)[1], b"ok\n")
            self.refused(reply, 200)
        self.jwks_fail = True
        self.assertTrue((await self.ask(get("/healthz"))).startswith(b"HTTP/1.1 200 "))
        for raw in (get("/healthz/"), get("/healthz/../vnc.html"), get("/healthz", method="POST"), get("/HEALTHZ"),
                    get("/healthz?x=/vnc.html", "Upgrade: websocket", "Connection: Upgrade")):
            reply = await self.ask(raw)
            self.assertFalse(reply.startswith(b"HTTP/1.1 200 ") and b"ok\n" not in reply)
            self.assertNotIn(b"noVNC", reply)
        self.assertEqual(self.heads, [])


class Authenticated(GateCase):
    async def test_page(self):
        good = token()
        reply = await self.ask(get("/vnc.html?autoconnect=1", auth(good), "Cookie: session=secret-cookie-value",
                                   "If-Modified-Since: Thu, 26 Nov 2026 10:00:00 GMT", "X-Forwarded-For: 10.0.0.1"))
        head, body = reply.split(b"\r\n\r\n", 1)
        self.assertTrue(head.startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertEqual(body, PAGE)
        self.assertEqual(head.lower().count(b"connection: close"), 1)
        # What websockify was sent: written by the gate, without the credentials.
        (sent,) = self.heads
        self.assertEqual(sent, b"GET /vnc.html?autoconnect=1 HTTP/1.1\r\nHost: takx-desktop\r\nConnection: close\r\n"
                               b"If-Modified-Since: Thu, 26 Nov 2026 10:00:00 GMT\r\n\r\n")
        for secret in (good, good.split(".")[2], "secret-cookie-value"):
            self.assertNotIn(secret.encode(), sent)
            self.assertFalse(any(secret in line for line in self.lines))
        self.assertTrue(any("200 GET /vnc.html sub=8a5f0000" in line for line in self.lines))
        reply = await self.ask(get("/vnc.html", auth(good), method="HEAD"))
        self.assertTrue(reply.startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertTrue(reply.endswith(b"\r\n\r\n"))

    async def test_each_connection_serves_one_request(self):
        good = auth(token())
        reply = await self.ask(get("/vnc.html", good) + get("/second.js") + get("/third.js", good))
        self.assertEqual(reply.count(b"HTTP/1.1 "), 1)
        self.assertEqual(len(self.heads), 1)
        self.assertNotIn(b"second", self.heads[0])

    async def test_other_methods(self):
        good = auth(token())
        for method in ("POST", "PUT", "DELETE", "OPTIONS", "CONNECT", "TRACE"):
            reply = await self.ask(get("/vnc.html", good, method=method))
            self.refused(reply, 405)
            self.refused(await self.ask(get("/vnc.html", method=method)), 401)

    async def test_websocket(self):
        good = token()
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(upgrade(auth(good), f"Origin: {ORIGIN}", "Cookie: session=secret-cookie-value"))
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        self.assertTrue(head.startswith(b"HTTP/1.1 101 "))
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(RFB)), 5), RFB)
        writer.write(b"\x82\x83frame")
        self.assertEqual(await asyncio.wait_for(reader.readexactly(12), 5), b"echo:\x82\x83frame")
        writer.close()
        (sent,) = self.heads
        self.assertEqual(sent, b"GET /websockify HTTP/1.1\r\nHost: takx-desktop\r\nUpgrade: websocket\r\n"
                               b"Connection: Upgrade\r\nSec-WebSocket-Key: " + KEY.encode() + b"\r\n"
                               b"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: binary\r\n\r\n")
        await asyncio.sleep(0.1)
        self.assertTrue(any("websocket closed: the browser closed" in line for line in self.lines), self.lines)
        self.assertFalse(any(good in line or "secret-cookie-value" in line for line in self.lines))

    async def test_websocket_origin_path_and_handshake(self):
        good = auth(token())
        self.refused(await self.ask(upgrade(good, "Origin: https://evil.example.test")), 403)
        self.refused(await self.ask(upgrade(good, f"Origin: {ORIGIN}", "Origin: https://evil.example.test")), 403)
        self.refused(await self.ask(upgrade(good, "Origin: null")), 403)
        self.refused(await self.ask(upgrade(good, path="/vnc.html")), 404)
        self.refused(await self.ask(upgrade(good, path="/websockify/other")), 404)
        self.refused(await self.ask(get("/websockify", good, "Upgrade: websocket", "Connection: Upgrade")), 400)
        self.refused(await self.ask(upgrade(good).replace(KEY.encode(), b"short")), 400)
        self.refused(await self.ask(upgrade(good, "Sec-WebSocket-Key: " + KEY)), 400)
        # No Origin at all is a client that is not a browser; it still needs the token.
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(upgrade(good))
        self.assertTrue((await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)).startswith(b"HTTP/1.1 101 "))
        writer.close()

    async def test_websockify_down(self):
        self.upstream.close()
        await self.upstream.wait_closed()
        if os.path.exists(self.socket):
            os.unlink(self.socket)
        self.refused(await self.ask(get("/vnc.html", auth(token()))), 502)
        self.refused(await self.ask(get("/vnc.html")), 401)


class SessionLimit(GateCase):
    WS_MAX = 0.0

    async def open(self, value):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(upgrade(auth(value)))
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        self.assertTrue(head.startswith(b"HTTP/1.1 101 "))
        self.assertEqual(await reader.readexactly(len(RFB)), RFB)
        return reader, writer

    async def test_websocket_ends_when_the_token_expires(self):
        value = token(lifetime=1.2)
        started = time.monotonic()
        reader, writer = await self.open(value)
        # Traffic does not extend it.
        writer.write(b"ping")
        self.assertEqual(await reader.readexactly(9), b"echo:ping")
        self.assertEqual(await asyncio.wait_for(reader.read(-1), 5), b"")
        lasted = time.monotonic() - started
        self.assertGreater(lasted, 0.9)
        self.assertLess(lasted, 2.5)
        writer.close()
        self.assertTrue(any("session limit reached" in line for line in self.lines))
        # The same token does not open a second one.
        self.heads.clear()
        self.refused(await self.ask(upgrade(auth(value))), 401)

    async def test_fixed_limit_is_counted_from_the_upgrade(self):
        self.gate.ws_max_seconds = 0.6
        started = time.monotonic()
        reader, writer = await self.open(token(lifetime=3600))
        self.assertEqual(await asyncio.wait_for(reader.read(-1), 5), b"")
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertGreater(time.monotonic() - started, 0.5)
        writer.close()


class AuthenticationOff(GateCase):
    VERIFY = False

    async def test_mode_none_checks_nothing_else_stays(self):
        reply = await self.ask(get("/vnc.html"))
        self.assertTrue(reply.startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertIn(PAGE, reply)
        self.assertTrue((await self.ask(get("/vnc.html", method="POST"))).startswith(b"HTTP/1.1 405 "))
        self.assertTrue((await self.ask(get("/a/../b"))).startswith(b"HTTP/1.1 400 "))
        self.assertTrue(any("authentication off" in line for line in self.lines))


class Settings(unittest.TestCase):
    BASE = {"TAKX_AUTH_ISSUER": ISSUER, "TAKX_AUTH_AUDIENCE": AUDIENCE, "TAKX_AUTH_GROUPS": json.dumps([GROUP]),
            "TAKX_AUTH_JWKS_URL": "http://keycloak-http.keycloak.svc.cluster.local:8080/realms/uds/protocol/openid-connect/certs"}

    def test_default_mode_needs_the_whole_configuration(self):
        gate = web_gate.gate_from_environment(self.BASE)
        self.assertIsNotNone(gate.verifier)
        self.assertEqual((gate.upstream, gate.origin, gate.ws_max_seconds), ("/run/takx/web/websockify.sock", "", 300.0))
        for missing in self.BASE:
            with self.assertRaises(ValueError):
                web_gate.gate_from_environment({name: value for name, value in self.BASE.items() if name != missing})
        with self.assertRaises(ValueError):
            web_gate.gate_from_environment({})

    def test_mode_none_must_be_asked_for_by_name(self):
        self.assertIsNone(web_gate.gate_from_environment({"TAKX_AUTH_MODE": "none"}).verifier)
        for mode in ("off", "NONE", "disabled", "0", "false"):
            with self.assertRaises(ValueError):
                web_gate.gate_from_environment({**self.BASE, "TAKX_AUTH_MODE": mode})

    def test_other_settings(self):
        gate = web_gate.gate_from_environment({**self.BASE, "TAKX_AUTH_ORIGIN": ORIGIN, "TAKX_AUTH_WS_MAX_SECONDS": "0",
                                               "TAKX_WEB_UPSTREAM": "/tmp/w.sock"})
        self.assertEqual((gate.upstream, gate.origin, gate.ws_max_seconds), ("/tmp/w.sock", ORIGIN, 0.0))
        for change in ({"TAKX_AUTH_ORIGIN": "takx.example.test"}, {"TAKX_AUTH_ORIGIN": ORIGIN + "/"},
                       {"TAKX_AUTH_WS_MAX_SECONDS": "-1"}, {"TAKX_AUTH_WS_MAX_SECONDS": "forever"},
                       {"TAKX_AUTH_WS_MAX_SECONDS": "86401"}):
            with self.assertRaises(ValueError, msg=change):
                web_gate.gate_from_environment({**self.BASE, **change})

    def test_answers_are_fixed_texts(self):
        for status in web_gate.REASONS:
            reply = web_gate.answer(status)
            self.assertIn(b"Connection: close\r\n", reply)
            self.assertIn(b"Cache-Control: no-store\r\n", reply)
            self.assertNotIn(b"Server:", reply)
        self.assertEqual(web_gate.with_connection_close(b"HTTP/1.1 200 OK\r\nconnection: keep-alive\r\nA: b\r\n\r\n"),
                         b"HTTP/1.1 200 OK\r\nA: b\r\nConnection: close\r\n\r\n")


if __name__ == "__main__":
    unittest.main()
