#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Ask the TAKX desktop bridge for the desktop with and without a valid
token, over HTTP and over a WebSocket, and say what came back.

  auth_probe.py HOST:PORT [--expiry] [--label TEXT]

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
import struct
import sys
import time

BANNER = b"RFB 003.008\n"


def exchange(host, port, request, limit=10.0):
    """Send the request, return (status, head, everything after the head, socket)."""
    sock = socket.create_connection((host, port), timeout=limit)
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
        self.sock, self.buffer, self.payload, self.closed = sock, leftover, b"", False

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

    def read(self, count):
        while len(self.payload) < count:
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
                self.send(data, opcode=10)
            elif opcode == 8:
                self.closed = True
                raise EOFError
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
    """The WebSocket of a token that is about to expire: open until then, closed then, not opened again."""
    status, _, rest, sock = exchange(host, port, request("GET", "/websockify", host, port, case["headers"], upgrade_fields()))
    facts = {"status": status, "token_expires_at": case["expires_at"]}
    if status != 101:
        facts["as_expected"] = False
        return facts
    frames = Frames(sock, rest)
    received = 0
    sock.settimeout(case["expires_at"] - time.time() + 30)
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
        while True:
            # Keep asking for the screen: an active session, not an idle one.
            frames.send(struct.pack(">BBHHHH", 3, 1, 0, 0, width, height))
            received += len(frames.read(1))
            time.sleep(0.5)
    except EOFError:
        facts["ended"] = "close frame" if frames.closed else "connection closed by the bridge"
    except OSError as error:
        facts["ended"] = f"{type(error).__name__}"
    ended = time.time()
    sock.close()
    facts["bytes_received_while_open"] = received
    facts["closed_seconds_after_expiry"] = round(ended - case["expires_at"], 1)
    again = try_websocket(host, port, {**case, "expect": 401})
    facts["same_token_again"] = {"status": again["status"], "reached_the_desktop": again["reached_the_desktop"]}
    facts["as_expected"] = (facts.get("ended") == "connection closed by the bridge" and received > 16
                            and -1.5 <= facts["closed_seconds_after_expiry"] <= 3.0 and again["as_expected"])
    return facts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", metavar="HOST:PORT")
    parser.add_argument("--expiry", action="store_true")
    parser.add_argument("--label", default="")
    options = parser.parse_args(argv)
    host, _, port = options.target.rpartition(":")
    port = int(port)
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
            if options.expiry:
                continue
            report["cases"][case["name"]] = {"expected": case["expect"], "http": try_http(host, port, case),
                                             "websocket": try_websocket(host, port, case)}
    except OSError as error:
        report["error"] = f"{type(error).__name__}: {error}"
    verdicts = [report.get("healthz_without_a_token", {}).get("as_expected", False)]
    verdicts += [item[kind]["as_expected"] for item in report["cases"].values() for kind in ("http", "websocket")]
    if options.expiry:
        verdicts.append(report.get("token_expiry", {}).get("as_expected", False))
    opened = [name for name, item in report["cases"].items() if item["websocket"].get("reached_the_desktop")]
    report["variants_that_reached_the_desktop"] = opened
    complete = bool(report.get("token_expiry")) if options.expiry else bool(report["cases"])
    report["status"] = "ok" if all(verdicts) and "error" not in report and complete else "failed"
    print(json.dumps(report, indent=1))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
