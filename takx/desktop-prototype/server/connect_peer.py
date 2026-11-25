#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""A second real TAK streaming client for the connected TAKX run.

Same transport as cot_probe.py: one exported TakClient identity, mutual TLS,
the deployment's CA as the only trust root, the certificate name checked.
Unlike cot_probe.py it sends nothing unless asked, so it can be a silent
listener (the unrelated-group identity), and what it sends is a named point
marker rather than its own position:

  connect_peer.py SECRET_DIR --connect IP --listen 90 --out FILE
      [--marker SYNTHETIC-PEER-1 --lat -28 --lon -12 [--type a-n-G] [--every 5]]

--marker sends a Cursor on Target point with that callsign, uid equal to the
callsign, the remark "synthetic test marker, not a real object", and a stale
time 10 minutes ahead, every --every seconds. Positions must be synthetic
(the defaults are in the open South Atlantic).

Output (FILE and stdout) is one JSON object: TLS facts and every received
event with uid, type, callsign, point and the UTC second it first arrived.
No key or password is read into the output.
"""
import argparse
import datetime
import json
import re
import socket
import ssl
import time
from pathlib import Path


def utc(moment=None):
    moment = moment or datetime.datetime.now(datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def marker(name, kind, lat, lon):
    now = datetime.datetime.now(datetime.timezone.utc)
    return (
        f'<event version="2.0" uid="{name}" type="{kind}" how="h-g-i-g-o" time="{utc(now)}" start="{utc(now)}" '
        f'stale="{utc(now + datetime.timedelta(minutes=10))}">'
        f'<point lat="{lat}" lon="{lon}" hae="0" ce="9999999" le="9999999"/>'
        f'<detail><contact callsign="{name}"/><remarks>synthetic test marker, not a real object</remarks>'
        f'</detail></event>'
    ).encode()


def attribute(text, tag, name):
    found = re.search(rf"<{tag}\b[^>]*\b{name}=['\"]([^'\"]*)['\"]", text)
    return found.group(1) if found else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("secret_dir", type=Path)
    parser.add_argument("--connect", required=True)
    parser.add_argument("--listen", type=float, default=60.0)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--marker")
    parser.add_argument("--type", default="a-n-G")
    parser.add_argument("--lat", type=float, default=-28.0)
    parser.add_argument("--lon", type=float, default=-12.0)
    parser.add_argument("--every", type=float, default=5.0)
    args = parser.parse_args()
    if args.marker and not re.fullmatch(r"SYNTHETIC-[A-Z0-9-]{1,40}", args.marker):
        parser.error("--marker must be named SYNTHETIC-...")

    facts = json.loads((args.secret_dir / "connection.json").read_text())
    host, port = facts["stream"]["certificate_name"], facts["stream"]["port"]
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(args.secret_dir / "ca.pem"))
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(str(args.secret_dir / "client.pem"), str(args.secret_dir / "client.key"))

    result = {"takclient": facts["takclient"], "group": facts["group"], "endpoint": f"{host}:{port} via {args.connect}",
              "started_utc": utc(), "sent": {"marker": args.marker, "type": args.type if args.marker else None,
                                             "lat": args.lat if args.marker else None,
                                             "lon": args.lon if args.marker else None, "count": 0}}
    seen = {}
    buffer = b""
    with socket.create_connection((args.connect, port), timeout=15) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            result["tls"] = tls.version()
            result["server_san"] = [value for _kind, value in tls.getpeercert().get("subjectAltName", ())]
            tls.settimeout(1.0)
            deadline = time.monotonic() + args.listen
            next_send = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if args.marker and time.monotonic() >= next_send:
                    tls.sendall(marker(args.marker, args.type, args.lat, args.lon))
                    result["sent"]["count"] += 1
                    result["sent"].setdefault("first_utc", utc())
                    next_send = time.monotonic() + args.every
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
                    uid = attribute(text, "event", "uid") or "?"
                    entry = seen.setdefault(uid, {
                        "type": attribute(text, "event", "type"), "callsign": attribute(text, "contact", "callsign"),
                        "lat": attribute(text, "point", "lat"), "lon": attribute(text, "point", "lon"),
                        "first_seen_utc": utc(), "count": 0})
                    entry["count"] += 1
    result["ended_utc"] = utc()
    result["received"] = seen
    text = json.dumps(result, indent=1, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
