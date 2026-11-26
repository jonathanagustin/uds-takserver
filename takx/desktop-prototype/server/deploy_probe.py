#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""TCP reachability facts for the desktop package's isolation checks.

  deploy_probe.py listeners
  deploy_probe.py connect LABEL=HOST:PORT [LABEL=HOST:PORT ...]

listeners  the TCP sockets in LISTEN state of this network namespace, read
           from /proc/net/tcp and tcp6: family, address, port.
connect    one attempt per target, 5 s to connect. After a connect it sends
           an HTTP request line and reads for 5 s, because a policy that sits
           in the path as a proxy (ztunnel) accepts the TCP handshake and
           closes afterwards: "connected" alone does not mean "reached".
           Outcomes: reached (bytes came back), accepted-then-closed (reset
           or end of stream, no byte), accepted-silent (open, no byte in
           5 s), refused, timeout, unresolved, error.

Prints one JSON object per line. Standard library only, so it can be piped
into a pod:  kubectl exec -i POD -- python3 -I - connect ... <deploy_probe.py
"""
import json
import socket
import struct
import sys
import time


def listeners():
    found = set()
    for family, path in (("tcp4", "/proc/net/tcp"), ("tcp6", "/proc/net/tcp6")):
        try:
            with open(path, encoding="ascii") as stream:
                rows = stream.read().splitlines()[1:]
        except FileNotFoundError:
            continue
        for row in rows:
            fields = row.split()
            if fields[3] != "0A":
                continue
            address, port = fields[1].rsplit(":", 1)
            if family == "tcp4":
                address = socket.inet_ntoa(struct.pack("<I", int(address, 16)))
            else:
                raw = b"".join(struct.pack("<I", int(address[i:i + 8], 16)) for i in range(0, 32, 8))
                address = socket.inet_ntop(socket.AF_INET6, raw)
            found.add((int(port, 16), family, address))
    for port, family, address in sorted(found):
        print(json.dumps({"family": family, "address": address, "port": port}))


def connect(label, host, port):
    fact = {"target": label, "host": host, "port": port}
    started = time.monotonic()
    try:
        address = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4]
    except socket.gaierror as error:
        fact.update(outcome="unresolved", detail=str(error))
        return fact
    fact["address"] = address[0]
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect(address)
    except ConnectionRefusedError:
        fact["outcome"] = "refused"
    except socket.timeout:
        fact["outcome"] = "timeout"
    except OSError as error:
        fact.update(outcome="error", detail=str(error))
    else:
        try:
            sock.sendall(b"GET / HTTP/1.0\r\nHost: probe\r\n\r\n")
            data = sock.recv(256)
            if data:
                fact.update(outcome="reached", first_bytes=data[:40].decode("latin-1").splitlines()[0][:40])
            else:
                fact["outcome"] = "accepted-then-closed"
        except socket.timeout:
            fact["outcome"] = "accepted-silent"
        except OSError as error:
            fact.update(outcome="accepted-then-closed", detail=str(error))
    finally:
        sock.close()
    fact["seconds"] = round(time.monotonic() - started, 2)
    return fact


def main():
    if sys.argv[1:] == ["listeners"]:
        listeners()
    elif len(sys.argv) > 2 and sys.argv[1] == "connect":
        for item in sys.argv[2:]:
            label, _, target = item.partition("=")
            host, _, port = target.rpartition(":")
            print(json.dumps(connect(label, host, int(port))), flush=True)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
