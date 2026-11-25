#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Server-side and client-side records of a TAKX connection (connect.sh).

  connect_evidence.py ROOT ATTEMPT stream [--identity takx-desktop]
  connect_evidence.py ROOT ATTEMPT cot UID [--name LABEL]
  connect_evidence.py ROOT ATTEMPT search TEXT [--name LABEL]

stream  the TAK Server's own subscription list (/Marti/api/subscriptions/all),
        reduced to the entries of the certificate identity; the server's
        client list (/Marti/api/clientEndPoints); the CommonCommo lines of
        the client log that name the stream; and the messaging pod's log
        lines that name the identity. Exit status 0 only when the server
        lists a subscription for the identity.
cot     the latest event the server stored for one uid
        (/Marti/api/cot/xml/UID): type, callsign, point, time. Exit 0 when
        the server has it.
search  uids, types and callsigns of the events the server stored in the last
        hour whose callsign contains TEXT (the cot_router table of the
        server's own PostgreSQL, read with kubectl exec). Used to find the
        uid TAKX gave a marker that was named in its user interface.

The API is queried as a different TakClient (--as, default takx-peer) with
mutual TLS through the passthrough gateway: SNI is the API route name, the
name checked in the certificate is TAK_CERT_NAME, the only trust root is the
deployment's CA. Results go to ROOT/connect/ATTEMPT/evidence/*.json and to
stdout. No key, password or certificate body is read into the output.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

KUBECONFIG = "/etc/rancher/k3s/k3s.yaml"


def endpoints(root):
    values = {}
    for line in (root / "server" / "endpoints.env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def api(root, env, identity, path):
    """One GET with the identity's client certificate; returns (status, body)."""
    secrets = root / "secrets" / identity
    request = f"GET {path} HTTP/1.0\r\nHost: {env['TAK_API_HOST']}\r\nAccept: */*\r\n\r\n"
    done = subprocess.run(
        ["openssl", "s_client", "-quiet", "-connect", f"{env['PASSTHROUGH_GATEWAY_IP']}:{env['TAK_API_PORT']}",
         "-servername", env["TAK_API_HOST"], "-CAfile", str(secrets / "ca.pem"), "-verify_return_error",
         "-verify_hostname", env["TAK_CERT_NAME"], "-cert", str(secrets / "client.pem"),
         "-key", str(secrets / "client.key")],
        input=request.encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=40, check=False)
    head, _, body = done.stdout.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].decode("ascii", "replace")
    return status, body.decode("utf-8", "replace")


def kubectl(*args):
    done = subprocess.run(["kubectl", "--kubeconfig", KUBECONFIG, *args], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=90, check=False)
    return done.stdout.decode("utf-8", "replace")


def save(root, attempt, name, record):
    out = root / "connect" / attempt / "evidence"
    out.mkdir(parents=True, exist_ok=True)
    record["recorded_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (out / f"{name}.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps(record, indent=1, sort_keys=True))


def do_stream(root, env, args):
    status, body = api(root, env, args.asid, "/Marti/api/subscriptions/all")
    entries = []
    try:
        for item in json.loads(body).get("data", []):
            if f"CN={args.identity}" in item.get("dn", "").replace(" ", ""):
                entries.append({key: item.get(key) for key in (
                    "dn", "username", "callsign", "clientUid", "protocol", "ipAddress", "port", "team", "role",
                    "takClient", "takVersion", "lastReportMilliseconds", "numProcessed", "appFramerate")}
                    | {"groups": sorted({f"{g['name']}:{g['direction']}" for g in item.get("groups", [])})})
    except ValueError:
        pass
    status2, body2 = api(root, env, args.asid, "/Marti/api/clientEndPoints")
    clients = []
    try:
        clients = [c for c in json.loads(body2).get("data", [])]
    except ValueError:
        pass
    log = root / "logs" / f"{args.attempt}.log"
    commo = [line.rstrip()[:240] for line in log.read_text(errors="replace").splitlines()
             if re.search(r"Stream connection to|Initiating streaming network connection|Proto Negotiate|"
                          r'TAK Server (Connected|Added|Disconnected)|Commo Interface', line)] if log.exists() else []
    pod = [line[:300] for line in kubectl("logs", "-n", "takserver", "deploy/takserver-messaging", "--since=3h",
                                          "--all-containers").splitlines() if args.identity in line]
    save(root, args.attempt, args.name or f"stream-{args.identity}", {
        "identity": args.identity, "queried_as": args.asid,
        "endpoint": f"{env['TAK_CERT_NAME']}:{env['TAK_STREAM_PORT']} -> {env['PASSTHROUGH_GATEWAY_IP']}",
        "subscriptions_http_status": status, "server_subscriptions": entries,
        "client_endpoints_http_status": status2, "server_client_endpoints": clients,
        "client_commo_log": commo[-24:], "messaging_pod_log": pod[-12:]})
    return 0 if entries else 1


def describe(xml):
    def attr(tag, name):
        found = re.search(rf"<{tag}\b[^>]*\b{name}=['\"]([^'\"]*)['\"]", xml)
        return found.group(1) if found else None
    return {"uid": attr("event", "uid"), "type": attr("event", "type"), "how": attr("event", "how"),
            "time": attr("event", "time"), "lat": attr("point", "lat"), "lon": attr("point", "lon"),
            "callsign": attr("contact", "callsign")}


def do_cot(root, env, args):
    status, body = api(root, env, args.asid, f"/Marti/api/cot/xml/{args.uid}")
    found = "<event" in body
    save(root, args.attempt, args.name or f"cot-{args.uid}", {
        "uid": args.uid, "queried_as": args.asid, "http_status": status, "stored_by_server": found,
        "event": describe(body) if found else None})
    return 0 if found else 1


def do_search(root, env, args):
    if not re.fullmatch(r"[A-Za-z0-9 ._-]{3,64}", args.text):
        raise SystemExit("search text: letters, digits, space, dot, underscore and hyphen only")
    query = ("select uid, cot_type, servertime, st_y(event_pt), st_x(event_pt), "
             "substring(detail from 'callsign=.([^''\"]*)') from cot_router "
             f"where servertime > now() - interval '1 hour' and detail like '%{args.text}%' order by servertime")
    rows = [line.split("|") for line in kubectl(
        "exec", "-n", "takserver", "takserver-postgresql-0", "--", "sh", "-c",
        'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-cot}" -At -c "$0"',
        query).splitlines() if line.count("|") == 5]
    events = [dict(zip(("uid", "type", "servertime", "lat", "lon", "callsign"), row)) for row in rows]
    save(root, args.attempt, args.name or "search-" + re.sub(r"[^A-Za-z0-9]+", "-", args.text),
         {"text": args.text, "source": "cot_router table of the TAK Server database", "events": events})
    return 0 if events else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path)
    parser.add_argument("attempt")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("stream", "cot", "search"):
        one = sub.add_parser(name)
        one.add_argument("--as", dest="asid", default="takx-peer")
        one.add_argument("--name")
        if name == "stream":
            one.add_argument("--identity", default="takx-desktop")
        elif name == "cot":
            one.add_argument("uid")
        else:
            one.add_argument("text")
    args = parser.parse_args()
    env = endpoints(args.root)
    return {"stream": do_stream, "cot": do_cot, "search": do_search}[args.command](args.root, env, args)


if __name__ == "__main__":
    sys.exit(main())
