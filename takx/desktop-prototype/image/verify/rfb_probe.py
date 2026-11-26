#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Read one frame of the TAKX desktop the way a browser would, and save it.

  rfb_probe.py --ws HOST:PORT [--png FILE|-] [--second-after SECONDS]
  rfb_probe.py --unix SOCKET  [--png FILE|-] [--second-after SECONDS]

--ws    fetch /vnc.html and / over HTTP, open the /websockify WebSocket
        (RFC 6455, subprotocol 'binary') and speak RFB 3.8 through it: the
        path noVNC takes through websockify.
--unix  speak RFB 3.8 on the Xtigervnc Unix socket directly (inside the
        desktop container, no bridge involved).

The probe requires security type None, asks for one full raw frame in 32-bit
true colour, writes it as a PNG and prints facts as JSON on standard error
(on standard output when the PNG goes to a file): size, desktop name, luma
minimum, maximum and mean, number of distinct colours in a sample. With
--second-after it reads a second frame later and counts the pixels that
changed, which shows the desktop is live and not a still picture.

Exit status 0 needs: every protocol step, the --min-size, and a luma range
above --min-luma-range. Whether the frame shows the TAKX globe is for a
person to judge from the PNG.

The bridge wants the signed-in user's token on every request. When the
environment variable TAKX_PROBE_BEARER holds one, --ws sends it as
`Authorization: Bearer ...`, as Authservice does in a cluster.

Standard library only, so it can be piped into 'python3 -I -' in the image:
  docker exec -i CONTAINER python3 -I - --ws 127.0.0.1:6080 --png - <rfb_probe.py >frame.png
"""
import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time
import zlib

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class ProbeError(Exception):
    pass


class Plain:
    """RFB byte stream on a connected socket."""

    def __init__(self, sock):
        self.sock = sock

    def send(self, data):
        self.sock.sendall(data)

    def recv(self, count):
        parts = []
        while count:
            block = self.sock.recv(min(count, 1 << 20))
            if not block:
                raise ProbeError("the server closed the connection")
            parts.append(block)
            count -= len(block)
        return b"".join(parts)


class WebSocket(Plain):
    """RFB byte stream carried in binary WebSocket frames."""

    def __init__(self, sock, leftover=b""):
        super().__init__(sock)
        self.raw = leftover
        self.payload = b""

    def _raw(self, count):
        while len(self.raw) < count:
            block = self.sock.recv(1 << 20)
            if not block:
                raise ProbeError("the bridge closed the connection")
            self.raw += block
        data, self.raw = self.raw[:count], self.raw[count:]
        return data

    def _frame(self, opcode, data):
        mask = os.urandom(4)
        head = bytes([0x80 | opcode])
        if len(data) < 126:
            head += bytes([0x80 | len(data)])
        elif len(data) < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", len(data))
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", len(data))
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.sock.sendall(head + mask + masked)

    def send(self, data):
        self._frame(2, data)

    def recv(self, count):
        while len(self.payload) < count:
            first, second = self._raw(2)
            opcode, length = first & 0x0F, second & 0x7F
            if second & 0x80:
                raise ProbeError("the bridge sent a masked frame")
            if length == 126:
                length, = struct.unpack(">H", self._raw(2))
            elif length == 127:
                length, = struct.unpack(">Q", self._raw(8))
            data = self._raw(length)
            if opcode in (0, 2):
                self.payload += data
            elif opcode == 9:
                self._frame(10, data)
            elif opcode == 8:
                raise ProbeError("the bridge closed the WebSocket")
            elif opcode != 10:
                raise ProbeError(f"unexpected WebSocket opcode {opcode}")
        data, self.payload = self.payload[:count], self.payload[count:]
        return data


def bearer():
    """The Authorization header line for the bridge, or nothing."""
    token = os.environ.get("TAKX_PROBE_BEARER", "")
    return f"Authorization: Bearer {token}\r\n".encode() if token else b""


def http_get(host, port, path):
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n".encode() + bearer() + b"\r\n")
        data = b""
        while True:
            block = sock.recv(65536)
            if not block:
                break
            data += block
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), body


def open_websocket(host, port, timeout):
    sock = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16))
    sock.sendall(b"GET /websockify HTTP/1.1\r\nHost: " + f"{host}:{port}".encode() +
                 b"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: " + key +
                 b"\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: binary\r\n" + bearer() + b"\r\n")
    data = b""
    while b"\r\n\r\n" not in data:
        block = sock.recv(4096)
        if not block:
            raise ProbeError("no reply to the WebSocket upgrade")
        data += block
    head, _, leftover = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers = {name.strip().lower(): value.strip() for name, _, value in (line.partition(":") for line in lines[1:])}
    accept = base64.b64encode(hashlib.sha1(key + GUID).digest()).decode()  # noqa: S324 - RFC 6455 handshake
    if status != 101 or headers.get("sec-websocket-accept") != accept:
        raise ProbeError(f"WebSocket upgrade failed: status {status}")
    return WebSocket(sock, leftover), status


def handshake(stream):
    """RFB 3.8 handshake; return (banner, security types, width, height, name)."""
    banner = stream.recv(12)
    if banner != b"RFB 003.008\n":
        raise ProbeError(f"unexpected RFB banner {banner!r}")
    stream.send(b"RFB 003.008\n")
    count = stream.recv(1)[0]
    if count == 0:
        length, = struct.unpack(">I", stream.recv(4))
        raise ProbeError("the server refused: " + stream.recv(length).decode("utf-8", "replace"))
    types = list(stream.recv(count))
    if 1 not in types:
        raise ProbeError(f"security type None is not offered: {types}")
    stream.send(b"\x01")
    result, = struct.unpack(">I", stream.recv(4))
    if result != 0:
        raise ProbeError("the security handshake failed")
    stream.send(b"\x01")  # shared session
    width, height = struct.unpack(">HH", stream.recv(4))
    stream.recv(16)
    length, = struct.unpack(">I", stream.recv(4))
    name = stream.recv(length).decode("utf-8", "replace")
    # 32 bits per pixel, depth 24, little endian, true colour, bytes B G R X.
    stream.send(struct.pack(">BxxxBBBBHHHBBBxxx", 0, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
    stream.send(struct.pack(">BxHi", 2, 1, 0))  # raw encoding only
    return banner.decode().strip(), types, width, height, name


def read_frame(stream, width, height):
    """Request and return one full frame as BGRX bytes."""
    frame = bytearray(width * height * 4)
    covered = 0
    stream.send(struct.pack(">BBHHHH", 3, 0, 0, 0, width, height))
    while covered < width * height:
        kind = stream.recv(1)[0]
        if kind == 0:
            _pad, rectangles = struct.unpack(">BH", stream.recv(3))
            for _ in range(rectangles):
                x, y, w, h, encoding = struct.unpack(">HHHHi", stream.recv(12))
                if encoding != 0:
                    raise ProbeError(f"unexpected encoding {encoding}")
                data = stream.recv(w * h * 4)
                for row in range(h):
                    start = ((y + row) * width + x) * 4
                    frame[start:start + w * 4] = data[row * w * 4:(row + 1) * w * 4]
                covered += w * h
        elif kind == 2:
            pass
        elif kind == 3:
            stream.recv(3)
            length, = struct.unpack(">I", stream.recv(4))
            stream.recv(length)
        elif kind == 1:
            _pad, _first, colours = struct.unpack(">BHH", stream.recv(5))
            stream.recv(colours * 6)
        else:
            raise ProbeError(f"unexpected RFB message type {kind}")
    return bytes(frame)


def png(frame, width, height):
    rows = bytearray()
    for row in range(height):
        line = frame[row * width * 4:(row + 1) * width * 4]
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = line[2::4], line[1::4], line[0::4]
        rows += b"\0" + rgb

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b""))


def luma_facts(frame, stride=5):
    blue, green, red = frame[0::4 * stride], frame[1::4 * stride], frame[2::4 * stride]
    values = [(299 * r + 587 * g + 114 * b) // 1000 for r, g, b in zip(red, green, blue)]
    colours = len(set(zip(red, green, blue)))
    return {"luma_min": min(values), "luma_max": max(values), "luma_mean": round(sum(values) / len(values), 1),
            "distinct_colours_in_sample": colours, "sampled_pixels": len(values)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--ws", metavar="HOST:PORT")
    target.add_argument("--unix", metavar="SOCKET")
    parser.add_argument("--png", default=None, help="file for the frame, or - for standard output")
    parser.add_argument("--second-after", type=float, default=0.0)
    parser.add_argument("--min-size", default="1920x1080")
    parser.add_argument("--min-luma-range", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=60.0)
    options = parser.parse_args(argv)
    facts = {"transport": "websocket" if options.ws else "unix socket", "status": "failed"}
    report = sys.stderr if options.png == "-" else sys.stdout
    try:
        if options.ws:
            host, _, port = options.ws.rpartition(":")
            for path, needle in (("/vnc.html", b"noVNC"), ("/", b"vnc.html?autoconnect=1")):
                status, body = http_get(host, int(port), path)
                facts[f"http_get_{path}"] = {"status": status, "bytes": len(body), "has_expected_text": needle in body}
                if status != 200 or needle not in body:
                    raise ProbeError(f"GET {path} did not return the expected page (status {status})")
            stream, upgrade = open_websocket(host, int(port), options.timeout)
            facts["websocket_upgrade_status"] = upgrade
        else:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(options.timeout)
            sock.connect(options.unix)
            stream = Plain(sock)
        banner, types, width, height, name = handshake(stream)
        facts.update({"rfb_banner": banner, "rfb_security_types": types, "rfb_security_used": "None",
                      "width": width, "height": height, "desktop_name": name})
        frame = read_frame(stream, width, height)
        facts.update(luma_facts(frame))
        facts["frame_sha256"] = hashlib.sha256(frame).hexdigest()
        if options.second_after > 0:
            time.sleep(options.second_after)
            second = read_frame(stream, width, height)
            changed = sum(1 for index in range(0, len(frame), 4) if frame[index:index + 3] != second[index:index + 3])
            facts["second_frame"] = {"seconds_later": options.second_after, "pixels_changed": changed}
        stream.sock.close()
        if options.png:
            data = png(frame, width, height)
            if options.png == "-":
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            else:
                with open(options.png, "wb") as output:
                    output.write(data)
            facts["png_bytes"] = len(data)
        want_width, _, want_height = options.min_size.partition("x")
        problems = []
        if width < int(want_width) or height < int(want_height):
            problems.append(f"frame is {width}x{height}, wanted at least {options.min_size}")
        if facts["luma_max"] - facts["luma_min"] <= options.min_luma_range:
            problems.append("the frame is blank: luma range is not above the threshold")
        facts["problems"] = problems
        facts["status"] = "failed" if problems else "ok"
    except (ProbeError, OSError, ValueError, struct.error, IndexError) as error:
        facts["error"] = f"{error.__class__.__name__}: {error}"
    print(json.dumps(facts, indent=1, sort_keys=True), file=report)
    return 0 if facts["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
