#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Ask the TAKX desktop bridge for the desktop with and without a valid
token, over HTTP and over a WebSocket, and say what came back.

  auth_probe.py HOST:PORT [--expiry | --hold SECONDS] [--label TEXT]
                [--connect ADDRESS:PORT] [--tls [--cafile FILE]]

The request variants are read as JSON from the environment variable
TAKX_AUTH_CASES (token_tool.py cases): name, header lines, an optional query
string, and what the bridge has to do, 'open' or an HTTP status.

For each variant, on a new connection each time:

  HTTP        GET /vnc.html. 'open' needs 200 and the noVNC page. Otherwise
              the status must be the expected one and the answer must hold
              nothing of the page.
  WebSocket   upgrade on /websockify (RFC 6455, subprotocol 'binary'). 'open'
              needs 101, the RFB banner "RFB 003.008" from the X server and
              its list of security types. Otherwise the status must be the
              expected one, and every byte the bridge sends until it closes
              is read: none of it may be a WebSocket frame or RFB.

GET /healthz must answer 200 "ok" without a token. With --expiry only the
variant 'short-lived-token' is used: its WebSocket must stay open until the
token expires, be closed by the bridge then, and not open again. That is for
a bridge that runs with TAKX_AUTH_WS_MAX_SECONDS=0.

With --hold SECONDS only the variant 'good-token' is used: its WebSocket is
taken through the RFB handshake and then left alone, no screen is asked for.
It must still be open after SECONDS; the pings the bridge sent meanwhile are
counted (and answered). That is a session with a still screen, for a path
with idle timers on it.

--connect names the address to dial when it differs from the HOST of the
request (a gateway address for a host name, a pod address). --tls speaks TLS
with HOST as the server name; the certificate is always verified, against
--cafile or the system's CAs. The variants are header lines, so a session
cookie of a signed-in browser can be a variant too.

Prints one JSON document; exit status 0 only when every variant behaved as
expected. Standard library only, so it can be piped into 'python3 -I -' in
the image:
  docker exec -i -e TAKX_AUTH_CASES CONTAINER python3 -I - 127.0.0.1:6080 <auth_probe.py
"""
import argparse
import base64
import json
import os
import socket
import ssl
import struct
import sys
import time

BANNER = b"RFB 003.008\n"
DIAL = {"address": None, "tls": None}


def exchange(host, port, request, limit=10.0):
    """Send the request, return (status, head, everything after the head, socket)."""
    sock = socket.create_connection(DIAL["address"] or (host, port), timeout=limit)
    if DIAL["tls"] is not None:
        sock = DIAL["tls"].wrap_socket(sock, server_hostname=host)
    sock.sendall(request)
    data = b""
    while b"\r\n\r\n" not in data:
        block = sock.recv(65536)
        if not block:
            break
        data += block
    head, _, rest = data.partition(b"\r\n\r\n")
    try:
        status = int(head.split(b" ", 2)[1])
    except (IndexError, ValueError):
        status = 0
    return status, head, rest, sock


def drain(sock, rest, limit=5.0):
    """Everything the peer still sends until it closes the connection."""
    sock.settimeout(limit)
    closed = False
    try:
        while True:
            block = sock.recv(65536)
            if not block:
                closed = True
                break
            rest += block
    except OSError:
        pass
    sock.close()
    return rest, closed


def request(method, path, host, port, headers, extra=()):
    lines = [f"{method} {path} HTTP/1.1", f"Host: {host}:{port}", *extra, *headers, "", ""]
    return "\r\n".join(lines).encode("latin-1")


def upgrade_fields():
    key = base64.b64encode(os.urandom(16)).decode()
    return ["Upgrade: websocket", "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
            "Sec-WebSocket-Protocol: binary"]


class Frames:
    """Just enough of RFC 6455 to read the X server's bytes and to answer."""

    def __init__(self, sock, leftover):
        self.sock, self.buffer, self.payload, self.closed, self.pings = sock, leftover, b"", False, 0

    def _raw(self, count):
        while len(self.buffer) < count:
            block = self.sock.recv(65536)
            if not block:
                raise EOFError
            self.buffer += block
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def send(self, data, opcode=2):
        mask = os.urandom(4)
        body = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(data)]) + mask + body)

    def pump(self):
        """Take one frame from the peer: keep its payload, answer a ping."""
        first, second = self._raw(2)
        length = second & 0x7f
        if length == 126:
            length, = struct.unpack(">H", self._raw(2))
        elif length == 127:
            length, = struct.unpack(">Q", self._raw(8))
        data = self._raw(length)
        opcode = first & 0x0f
        if opcode in (0, 2):
            self.payload += data
        elif opcode == 9:
            self.pings += 1
            self.send(data, opcode=10)
        elif opcode == 8:
            self.closed = True
            raise EOFError

    def read(self, count):
        while len(self.payload) < count:
            self.pump()
        data, self.payload = self.payload[:count], self.payload[count:]
        return data


def try_http(host, port, case):
    path = "/vnc.html" + case.get("query", "")
    status, head, rest, sock = exchange(host, port, request("GET", path, host, port, case["headers"], ["Connection: close"]))
    body, _ = drain(sock, rest)
    facts = {"status": status, "body_bytes": len(body), "page": b"noVNC" in body}
    if case["expect"] == "open":
        facts["as_expected"] = status == 200 and facts["page"] and len(body) > 1000
    else:
        facts["as_expected"] = (status == case["expect"] and not facts["page"] and len(body) < 64
                                and b"<" not in body and b"websockify" not in head.lower())
    return facts


def try_websocket(host, port, case):
    path = "/websockify" + case.get("query", "")
    status, _, rest, sock = exchange(host, port, request("GET", path, host, port, case["headers"], upgrade_fields()))
    facts = {"status": status}
    if status == 101:
        frames = Frames(sock, rest)
        try:
            facts["rfb_banner"] = frames.read(12).decode("latin-1")
            frames.send(BANNER)
            count = frames.read(1)[0]
            facts["rfb_security_types"] = list(frames.read(count))
        except (EOFError, OSError) as error:
            facts["error"] = type(error).__name__
        sock.close()
        facts["reached_the_desktop"] = facts.get("rfb_banner") == BANNER.decode()
    else:
        after, closed = drain(sock, rest)
        facts.update({"bytes_after_the_head": len(after), "closed_by_the_bridge": closed,
                      "reached_the_desktop": b"RFB" in after or len(after) > 64})
    if case["expect"] == "open":
        facts["as_expected"] = status == 101 and facts["reached_the_desktop"] and bool(facts.get("rfb_security_types"))
    else:
        facts["as_expected"] = status == case["expect"] and not facts["reached_the_desktop"] and facts["closed_by_the_bridge"]
    return facts


def try_expiry(host, port, case):
    """The WebSocket of a token that is about to expire: open and busy until then, cut then, not opened again."""
    status, _, rest, sock = exchange(host, port, request("GET", "/websockify", host, port, case["headers"], upgrade_fields()))
    facts = {"status": status, "token_expires_at": case["expires_at"]}
    if status != 101:
        facts["as_expected"] = False
        return facts
    frames = Frames(sock, rest)
    received = 0
    try:
        received += len(frames.read(12))
        frames.send(BANNER)
        received += len(frames.read(frames.read(1)[0]))
        frames.send(b"\x01")          # security type None
        received += len(frames.read(4))
        frames.send(b"\x01")          # shared session
        width, height = struct.unpack(">HH", frames.read(4))
        frames.read(16)
        frames.read(struct.unpack(">I", frames.read(4))[0])
        facts["desktop_size"] = f"{width}x{height}"
        sock.settimeout(case["expires_at"] - time.time() + 30)
        while True:
            # Keep asking for the screen and take whatever comes: a session in use.
            frames.send(struct.pack(">BBHHHH", 3, 1, 0, 0, width, height))
            block = sock.recv(1 << 20)
            if not block:
                facts["ended"] = "connection closed by the bridge"
                break
            received += len(block)
    except (ConnectionResetError, BrokenPipeError):
        facts["ended"] = "connection reset by the bridge"
    except (EOFError, OSError) as error:
        facts["ended"] = f"{type(error).__name__}"
    ended = time.time()
    sock.close()
    facts["bytes_received_while_open"] = received
    facts["closed_seconds_after_expiry"] = round(ended - case["expires_at"], 1)
    again = try_websocket(host, port, {**case, "expect": 401})
    facts["same_token_again"] = {"status": again["status"], "reached_the_desktop": again["reached_the_desktop"]}
    facts["as_expected"] = (facts.get("ended", "").endswith("by the bridge") and received > 100000
                            and -1.5 <= facts["closed_seconds_after_expiry"] <= 3.0 and again["as_expected"])
    return facts


def try_hold(host, port, case, seconds):
    """A WebSocket with a still screen: handshake, then silence but for the bridge's pings."""
    status, _, rest, sock = exchange(host, port, request("GET", "/websockify", host, port, case["headers"], upgrade_fields()))
    facts = {"status": status, "hold_seconds": seconds}
    if status != 101:
        facts["as_expected"] = False
        return facts
    frames = Frames(sock, rest)
    started = time.time()
    try:
        frames.read(12)
        frames.send(BANNER)
        frames.read(frames.read(1)[0])
        frames.send(b"\x01")
        frames.read(4)
        frames.send(b"\x01")
        frames.read(20)
        frames.read(struct.unpack(">I", frames.read(4))[0])
        facts["handshake_done_after_s"] = round(time.time() - started, 1)
        # Nothing is requested from here on: the only traffic is ping and pong.
        while time.time() - started < seconds:
            sock.settimeout(max(0.1, seconds - (time.time() - started)))
            try:
                frames.pump()
            except socket.timeout:
                break
        facts["ended"] = "still open"
    except EOFError:
        facts["ended"] = "close frame" if frames.closed else "connection closed by the peer"
    except OSError as error:
        facts["ended"] = type(error).__name__
    facts["open_for_s"] = round(time.time() - started, 1)
    facts["pings_received_and_answered"] = frames.pings
    facts["payload_bytes_after_the_handshake"] = len(frames.payload)
    sock.close()
    facts["as_expected"] = facts["ended"] == "still open" and facts["open_for_s"] >= seconds - 1
    return facts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", metavar="HOST:PORT")
    parser.add_argument("--expiry", action="store_true")
    parser.add_argument("--hold", type=float, default=0.0, metavar="SECONDS")
    parser.add_argument("--label", default="")
    parser.add_argument("--connect", metavar="ADDRESS:PORT")
    parser.add_argument("--tls", action="store_true")
    parser.add_argument("--cafile")
    options = parser.parse_args(argv)
    host, _, port = options.target.rpartition(":")
    port = int(port)
    if options.connect:
        address, _, connect_port = options.connect.rpartition(":")
        DIAL["address"] = (address, int(connect_port))
    if options.tls:
        DIAL["tls"] = ssl.create_default_context(cafile=options.cafile)
    cases = json.loads(os.environ["TAKX_AUTH_CASES"])
    report = {"target": options.target, "label": options.label, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "cases": {}}
    try:
        status, _, rest, sock = exchange(host, port, request("GET", "/healthz", host, port, [], ["Connection: close"]))
        body, _ = drain(sock, rest)
        report["healthz_without_a_token"] = {"status": status, "body": body.decode("latin-1"),
                                             "as_expected": status == 200 and body == b"ok\n"}
        for case in cases:
            if case["name"] == "short-lived-token":
                if options.expiry:
                    report["token_expiry"] = try_expiry(host, port, case)
                continue
            if options.hold and case["name"] == "good-token":
                report["quiet_websocket"] = try_hold(host, port, case, options.hold)
            if options.expiry or options.hold:
                continue
            report["cases"][case["name"]] = {"expected": case["expect"], "http": try_http(host, port, case),
                                             "websocket": try_websocket(host, port, case)}
    except OSError as error:
        report["error"] = f"{type(error).__name__}: {error}"
    verdicts = [report.get("healthz_without_a_token", {}).get("as_expected", False)]
    verdicts += [item[kind]["as_expected"] for item in report["cases"].values() for kind in ("http", "websocket")]
    if options.expiry:
        verdicts.append(report.get("token_expiry", {}).get("as_expected", False))
    if options.hold:
        verdicts.append(report.get("quiet_websocket", {}).get("as_expected", False))
    opened = [name for name, item in report["cases"].items() if item["websocket"].get("reached_the_desktop")]
    report["variants_that_reached_the_desktop"] = opened
    complete = bool(report.get("token_expiry") if options.expiry else
                    report.get("quiet_websocket") if options.hold else report["cases"])
    report["status"] = "ok" if all(verdicts) and "error" not in report and complete else "failed"
    print(json.dumps(report, indent=1))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
