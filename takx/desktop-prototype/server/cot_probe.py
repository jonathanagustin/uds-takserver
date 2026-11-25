#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""A minimal TAK streaming client for checks against the live server.

Connects to the streaming port with one exported TakClient identity, mutual
TLS, the TAK certificate authority as the only trust root and hostname
verification on. Sends one clearly synthetic position report for its own
callsign every few seconds, and reports which events it received. It speaks
the XML form of Cursor on Target and does not negotiate the protobuf form.

  cot_probe.py SECRET_DIR [--listen 20] [--lat 0.5 --lon 0.5] [--connect IP]

SECRET_DIR is a directory written by export_takclient.py. Output is one JSON
object: the peer certificate subject, the TLS version, and the uid, type and
callsign of each received event. No key or password is read into the output.
"""
import argparse
import datetime
import json
import re
import socket
import ssl
import time
from pathlib import Path


def sa_event(uid, callsign, lat, lon):
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    stale = (now + datetime.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return (
        f'<event version="2.0" uid="{uid}" type="a-f-G-U-C" how="h-g-i-g-o" '
        f'time="{stamp}" start="{stamp}" stale="{stale}">'
        f'<point lat="{lat}" lon="{lon}" hae="0" ce="9999999" le="9999999"/>'
        f'<detail><contact callsign="{callsign}"/><__group name="Cyan" role="Team Member"/>'
        f'<remarks>synthetic test marker</remarks></detail></event>'
    ).encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("secret_dir", type=Path)
    parser.add_argument("--listen", type=float, default=20.0)
    parser.add_argument("--lat", type=float, default=0.5)
    parser.add_argument("--lon", type=float, default=0.5)
    parser.add_argument("--connect", help="address to dial instead of resolving the host name")
    args = parser.parse_args()

    facts = json.loads((args.secret_dir / "connection.json").read_text())
    host, port = facts["stream"]["host"], facts["stream"]["port"]
    callsign = facts["callsign"]
    uid = f"probe-{callsign}"

    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(args.secret_dir / "ca.pem"))
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(str(args.secret_dir / "client.pem"), str(args.secret_dir / "client.key"))

    result = {"callsign": callsign, "group": facts["group"], "host": host, "port": port, "sent_uid": uid}
    seen = {}
    buffer = b""
    with socket.create_connection((args.connect or host, port), timeout=15) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            result["tls"] = tls.version()
            result["server_subject"] = dict(item[0] for item in tls.getpeercert()["subject"])
            result["server_san"] = [value for _kind, value in tls.getpeercert().get("subjectAltName", ())]
            tls.settimeout(1.0)
            deadline = time.monotonic() + args.listen
            next_send = 0.0
            while time.monotonic() < deadline:
                if time.monotonic() >= next_send:
                    tls.sendall(sa_event(uid, callsign, args.lat, args.lon))
                    next_send = time.monotonic() + 5.0
                try:
                    chunk = tls.recv(65536)
                except (socket.timeout, ssl.SSLWantReadError):
                    continue
                if not chunk:
                    result["closed_by_server"] = True
                    break
                buffer += chunk
                while b"</event>" in buffer:
                    event, buffer = buffer.split(b"</event>", 1)
                    text = event.decode("utf-8", "replace")
                    found_uid = re.search(r'<event[^>]*\buid="([^"]*)"', text)
                    found_type = re.search(r'<event[^>]*\btype="([^"]*)"', text)
                    found_call = re.search(r'<contact[^>]*\bcallsign="([^"]*)"', text)
                    key = found_uid.group(1) if found_uid else "?"
                    entry = seen.setdefault(key, {"type": found_type.group(1) if found_type else "?",
                                                  "callsign": found_call.group(1) if found_call else None, "count": 0})
                    entry["count"] += 1
    result["received"] = seen
    print(json.dumps(result, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
