#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""The door of the TAKX desktop bridge: listens on TCP 6080, lets a request
through to websockify only after web_auth.py accepted its token.

  client --TCP 6080--> web_gate.py --Unix socket--> websockify --Unix socket--> Xtigervnc

websockify (noVNC's files and the WebSocket-to-RFB bridge) listens on a Unix
socket in a directory only the web user can enter, so this process is the only
way to it. What it does with a connection, in this order:

  1. Read one request head: at most 16 KiB, within 10 seconds. Anything that
     is not a plain HTTP/1.x request without a body is answered 400 or 431.
  2. `GET /healthz` is answered `200 ok` at once. It says that this process
     runs and nothing else; it is for the container's own probes.
  3. Verify the Authorization header (web_auth.Verifier). No token, a bad
     token or a token for somebody else: 401. A genuine token without a
     required group: 403. No usable keys: 503. In every one of these cases the
     connection is closed after a fixed short answer, websockify is never
     contacted and not one byte of noVNC or of the desktop is sent.
  4. Only GET and HEAD go on (405 otherwise). A WebSocket upgrade must ask for
     /websockify and, when it names an Origin and TAKX_AUTH_ORIGIN is set,
     come from that origin (403 otherwise).
  5. Open a connection to websockify and send it a request head written here
     from the few header fields it needs. The token, cookies and every other
     field of the client stay behind.
  6. A page or script: pass websockify's answer on and close. A WebSocket:
     pass bytes both ways until one side closes or the session limit is
     reached, then cut both (a reset: nothing that is still queued is
     delivered). The limit is TAKX_AUTH_WS_MAX_SECONDS after the upgrade, or
     the token's own expiry when that is 0. The browser has to come back
     through the sign-in gate for a new WebSocket.

One request per connection: every answer says `Connection: close`. A second
request needs a second connection and is authenticated on its own.

TAKX_AUTH_MODE=none switches step 3 off, for a desktop on a developer's own
machine with the port published on loopback only. The Helm chart never sets
it.

Environment: TAKX_WEB_LISTEN (default 0.0.0.0:6080), TAKX_WEB_UPSTREAM (the
websockify socket), TAKX_AUTH_MODE (oidc, the default, or none),
TAKX_AUTH_ORIGIN, TAKX_AUTH_WS_MAX_SECONDS (default 300), and the TAKX_AUTH_*
values of web_auth.from_environment.
"""
from __future__ import annotations

import asyncio
import os
import re
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import web_auth  # noqa: E402

MAX_HEAD = 16384
MAX_HEADERS = 100
HEAD_TIMEOUT = 10.0
UPSTREAM_TIMEOUT = 10.0
PAGE_TIMEOUT = 60.0
CLOSE_TIMEOUT = 5.0
MAX_CONNECTIONS = 256
WEBSOCKET_PATH = "/websockify"

_REQUEST = re.compile(rb"\A([A-Z]{1,16}) (\S{1,2048}) HTTP/1\.[01]\Z")
_TARGET = re.compile(rb"\A/[\x21-\x7e]*\Z")
# One quantifier per stretch of the line: these run before any token is
# checked, on whatever a client sends, and must not backtrack.
_FIELD = re.compile(rb"\A([!#$%&'*+\-.^_`|~0-9A-Za-z]{1,64}):([\t\x20-\x7e\x80-\xff]*)\Z")
# Values that are written into the request for websockify.
_KEY = re.compile(r"\A[A-Za-z0-9+/]{22}==\Z")
_VERSION = re.compile(r"\A[0-9]{1,3}\Z")
_PROTOCOLS = re.compile(r"\A[A-Za-z0-9._-]{1,32}(, ?[A-Za-z0-9._-]{1,32}){0,7}\Z")
_DATE = re.compile(r"\A[A-Za-z0-9,: -]{1,40}\Z")

REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
           405: "Method Not Allowed", 431: "Request Header Fields Too Large", 502: "Bad Gateway",
           503: "Service Unavailable", 504: "Gateway Timeout"}


def log(message):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} takx-web-gate: {message}", file=sys.stderr, flush=True)


class BadRequest(Exception):
    pass


class Request:
    """One parsed request head. Field names are lower case; a field that
    occurs twice keeps every value."""

    def __init__(self, method, target, fields):
        self.method, self.target, self.fields = method, target, fields
        self.path = target.split("?", 1)[0]

    def values(self, name):
        return self.fields.get(name, [])

    def one(self, name):
        values = self.values(name)
        if len(values) > 1:
            raise BadRequest(f"more than one {name} field")
        return values[0] if values else None

    def wants_upgrade(self):
        connection = ",".join(self.values("connection")).lower()
        upgrade = ",".join(self.values("upgrade")).lower()
        return upgrade.strip() == "websocket" and "upgrade" in [token.strip() for token in connection.split(",")]


def parse_head(head):
    """head: the bytes up to and including the blank line. Raises BadRequest."""
    lines = head[:-4].split(b"\r\n")
    if any(b"\r" in line or b"\n" in line or b"\0" in line for line in lines):
        raise BadRequest("bare line break or NUL in the head")
    match = _REQUEST.match(lines[0])
    if not match or not _TARGET.match(match.group(2)):
        raise BadRequest("not an HTTP/1.x request line")
    if len(lines) - 1 > MAX_HEADERS:
        raise BadRequest("too many header fields")
    fields = {}
    for line in lines[1:]:
        field = _FIELD.match(line)
        if not field:
            raise BadRequest("malformed header field")
        fields.setdefault(field.group(1).decode("ascii").lower(), []).append(
            field.group(2).strip(b" \t").decode("latin-1"))
    request = Request(match.group(1).decode("ascii"), match.group(2).decode("ascii"), fields)
    # No request this bridge serves has a body; refusing one keeps a second,
    # unauthenticated request from hiding behind the first.
    if "transfer-encoding" in fields or any(value.strip() != "0" for value in request.values("content-length")):
        raise BadRequest("a request body is not accepted")
    if ".." in request.path or "\\" in request.path or "//" in request.path:
        raise BadRequest("path not accepted")
    return request


def upstream_head(request, upgrade):
    """The request head websockify gets: written here, field by field."""
    lines = [f"{request.method} {request.target if not upgrade else WEBSOCKET_PATH} HTTP/1.1", "Host: takx-desktop"]
    if upgrade:
        key, version = request.one("sec-websocket-key"), request.one("sec-websocket-version")
        protocols = request.one("sec-websocket-protocol")
        if key is None or not _KEY.match(key) or version is None or not _VERSION.match(version):
            raise BadRequest("not a WebSocket handshake")
        if protocols is not None and not _PROTOCOLS.match(protocols):
            raise BadRequest("subprotocol list not accepted")
        lines += ["Upgrade: websocket", "Connection: Upgrade", f"Sec-WebSocket-Key: {key}",
                  f"Sec-WebSocket-Version: {version}"]
        if protocols is not None:
            lines.append(f"Sec-WebSocket-Protocol: {protocols}")
    else:
        lines.append("Connection: close")
        since = request.one("if-modified-since")
        if since is not None and _DATE.match(since):
            lines.append(f"If-Modified-Since: {since}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def answer(status, extra=()):
    body = (REASONS[status].lower() if status != 200 else "ok").encode("ascii") + b"\n"
    lines = [f"HTTP/1.1 {status} {REASONS[status]}", "Content-Type: text/plain; charset=utf-8",
             f"Content-Length: {len(body)}", "Cache-Control: no-store", "Connection: close", *extra]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


def with_connection_close(head):
    """websockify's answer head with `Connection: close` in it, once."""
    lines = [line for line in head[:-4].split(b"\r\n") if not line.lower().startswith(b"connection:")]
    return b"\r\n".join([*lines, b"Connection: close"]) + b"\r\n\r\n"


async def _copy(reader, writer):
    while True:
        data = await reader.read(65536)
        if not data:
            return
        writer.write(data)
        await writer.drain()


async def _close(writer, cut=False):
    """Close a connection. cut=True drops what is still queued for the peer
    and resets the connection: for a session that has reached its limit, so
    that a client that reads slowly gets nothing more. A peer that does not
    take the rest of an ordinary answer within five seconds is cut as well."""
    try:
        if cut:
            sock = writer.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            writer.transport.abort()
        else:
            writer.close()
        await asyncio.wait_for(writer.wait_closed(), CLOSE_TIMEOUT)
    except asyncio.TimeoutError:
        writer.transport.abort()
    except (OSError, asyncio.CancelledError):
        pass


class Gate:
    def __init__(self, verifier, upstream, origin="", ws_max_seconds=300.0, clock=time.time, log=log):  # noqa: PLR0913
        if not 0 <= ws_max_seconds <= 86400:
            raise ValueError("TAKX_AUTH_WS_MAX_SECONDS must be between 0 and 86400")
        self.verifier, self.upstream, self.origin = verifier, upstream, origin
        self.ws_max_seconds, self.clock, self.log = ws_max_seconds, clock, log
        self.connections = 0

    async def handle(self, reader, writer):
        if self.connections >= MAX_CONNECTIONS:
            await _close(writer)
            return
        self.connections += 1
        try:
            await self._serve(reader, writer)
        except (OSError, asyncio.IncompleteReadError):
            pass
        except Exception as error:  # noqa: BLE001 - whatever went wrong, the connection ends here
            self.log(f"error: {type(error).__name__}; connection closed")
        finally:
            self.connections -= 1
            await _close(writer)

    async def _reply(self, writer, status, note, request=None, extra=()):
        where = f"{request.method} {request.path[:200]}" if request else "-"
        self.log(f"{status} {where} {note}")
        writer.write(answer(status, extra))
        await writer.drain()

    async def _serve(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
        except asyncio.LimitOverrunError:
            return await self._reply(writer, 431, "request head too large")
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return None
        try:
            request = parse_head(head)
        except BadRequest as error:
            return await self._reply(writer, 400, str(error))
        if request.path == "/healthz" and request.method in ("GET", "HEAD"):
            writer.write(answer(200))
            return await writer.drain()

        deadline = None
        if self.verifier is not None:
            try:
                identity = await asyncio.get_running_loop().run_in_executor(
                    None, self.verifier.verify, request.values("authorization"))
            except web_auth.Forbidden as error:
                return await self._reply(writer, 403, f"denied: {error}", request)
            except web_auth.Denied as error:
                return await self._reply(writer, 401, f"denied: {error}", request, ('WWW-Authenticate: Bearer',))
            except web_auth.Unavailable as error:
                return await self._reply(writer, 503, f"closed: {error}", request)
            who = f"sub={re.sub('[^A-Za-z0-9._:@-]', '?', identity.subject[:64]) or '-'}"
            deadline = identity.expires_at if self.ws_max_seconds == 0 else self.clock() + self.ws_max_seconds
        else:
            who = "authentication off"

        if request.method not in ("GET", "HEAD"):
            return await self._reply(writer, 405, who, request, ("Allow: GET, HEAD",))
        upgrade = request.wants_upgrade()
        if upgrade:
            if request.method != "GET" or request.path != WEBSOCKET_PATH:
                return await self._reply(writer, 404, f"{who} upgrade on another path", request)
            origin = request.values("origin")
            if self.origin and origin and origin != [self.origin]:
                return await self._reply(writer, 403, f"{who} upgrade from another origin", request)
        try:
            outgoing = upstream_head(request, upgrade)
        except BadRequest as error:
            return await self._reply(writer, 400, f"{who} {error}", request)
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.upstream, limit=MAX_HEAD), UPSTREAM_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            return await self._reply(writer, 502, f"{who} websockify does not answer", request)
        try:
            up_writer.write(outgoing)
            await up_writer.drain()
            try:
                reply = await asyncio.wait_for(up_reader.readuntil(b"\r\n\r\n"), UPSTREAM_TIMEOUT)
            except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, asyncio.TimeoutError):
                return await self._reply(writer, 502, f"{who} no answer head from websockify", request)
            status = reply[9:12].decode("ascii", "replace")
            if upgrade and status == "101":
                self.log(f"101 {request.method} {request.path} {who} websocket open")
                writer.write(reply)
                await writer.drain()
                ended = await self._tunnel(reader, writer, up_reader, up_writer, deadline)
                self.log(f"websocket closed: {ended} {who}")
            else:
                self.log(f"{status} {request.method} {request.path[:200]} {who}")
                writer.write(with_connection_close(reply))
                await writer.drain()
                try:
                    await asyncio.wait_for(_copy(up_reader, writer), PAGE_TIMEOUT)
                except asyncio.TimeoutError:
                    pass
        finally:
            await _close(up_writer)
        return None

    async def _tunnel(self, reader, writer, up_reader, up_writer, deadline):
        tasks = {asyncio.ensure_future(_copy(reader, up_writer)): "the browser closed",
                 asyncio.ensure_future(_copy(up_reader, writer)): "websockify closed"}
        timeout = None if deadline is None else max(0.0, deadline - self.clock())
        try:
            done, _ = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if not done:
            await _close(writer, cut=True)
            await _close(up_writer, cut=True)
            return "session limit reached, the browser must come back through the sign-in"
        return tasks[next(iter(done))]


async def keep_keys_warm(keys, log=log):
    """Load the keys at start and say so in the log; retry until it works.
    After that, requests refresh them when they are due."""
    loop = asyncio.get_running_loop()
    while not await loop.run_in_executor(None, keys.refresh):
        await asyncio.sleep(10)
    log("ready: signing keys loaded")


def gate_from_environment(environ=None):
    environ = os.environ if environ is None else environ
    mode = environ.get("TAKX_AUTH_MODE") or "oidc"
    if mode == "none":
        verifier = None
    elif mode == "oidc":
        verifier = web_auth.from_environment(environ, log=log)
    else:
        raise ValueError("TAKX_AUTH_MODE must be oidc or none")
    upstream = environ.get("TAKX_WEB_UPSTREAM") or "/run/takx/web/websockify.sock"
    origin = environ.get("TAKX_AUTH_ORIGIN", "")
    if origin and not re.match(r"\Ahttps?://[A-Za-z0-9.-]+(:[0-9]{1,5})?\Z", origin):
        raise ValueError("TAKX_AUTH_ORIGIN must be scheme://host[:port]")
    try:
        ws_max = float(environ.get("TAKX_AUTH_WS_MAX_SECONDS") or 300)
    except ValueError:
        raise ValueError("TAKX_AUTH_WS_MAX_SECONDS must be a number of seconds") from None
    return Gate(verifier, upstream, origin=origin, ws_max_seconds=ws_max)


async def main():
    listen = os.environ.get("TAKX_WEB_LISTEN") or "0.0.0.0:6080"
    host, _, port = listen.rpartition(":")
    try:
        gate = gate_from_environment()
    except ValueError as error:
        log(f"configuration error: {error}")
        return 64
    server = await asyncio.start_server(gate.handle, host, int(port), limit=MAX_HEAD, backlog=128)
    warm = None
    if gate.verifier is None:
        log("WARNING: TAKX_AUTH_MODE=none: no token is checked; whoever reaches this port controls the desktop")
    else:
        verifier = gate.verifier
        log(f"listening on {listen}; tokens: issuer {verifier.issuer}, audience {verifier.audience}, "
            f"{len(verifier.groups)} required group(s) in claim '{verifier.groups_claim}', "
            f"algorithms {','.join(verifier.keys.algorithms)}; websocket limit "
            f"{'the token expiry' if gate.ws_max_seconds == 0 else f'{gate.ws_max_seconds:g} s'}")
        warm = asyncio.ensure_future(keep_keys_warm(verifier.keys))
    async with server:
        await server.serve_forever()
    if warm is not None:
        warm.cancel()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
