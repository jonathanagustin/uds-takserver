#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Export one TakClient device Secret to a private directory.

Reads Secret takclients/<name> into memory and writes the files a TAKX
connection needs. Prints file names, sizes and public certificate facts only:
never a key, a password or PKCS#12 content.

Files written (mode 600):
  client.p12       client certificate and key, the "Client Cert File"
  truststore.p12   TAK certificate authority, the "CA File"
  password.txt     protects both PKCS#12 files
  package.zip      the device data package (manifest, server.pref, both .p12)
  client.pem, client.key, ca.pem   PEM forms for openssl and curl checks
  connection.json  endpoint facts and public fingerprints, no secrets
"""
import base64
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path


def run(*args, data=None):
    return subprocess.run(args, input=data, check=True, capture_output=True).stdout


def write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
    os.chmod(path, 0o600)


def main():
    name, group, out, domain = sys.argv[1], sys.argv[2], Path(sys.argv[3]), sys.argv[4]
    secret = json.loads(run("kubectl", "get", "secret", name, "-n", "takclients", "-o", "json"))
    data = {key: base64.b64decode(value) for key, value in secret["data"].items()}
    for key in ("client.pem", "client.key", "ca.pem"):
        write(out / key, data[key])
    write(out / "password.txt", data["password"])
    write(out / "package.zip", data["package.zip"])
    with zipfile.ZipFile(io.BytesIO(data["package.zip"])) as package:
        entries = package.namelist()
        write(out / "client.p12", package.read(f"certs/{name}.p12"))
        write(out / "truststore.p12", package.read("certs/truststore.p12"))

    def x509(pem, *fields):
        return run("openssl", "x509", "-noout", *fields, data=pem).decode().strip()

    facts = {
        "takclient": name,
        "callsign": name,
        "group": group,
        "stream": {"host": f"takserver-stream.{domain}", "port": 8089, "protocol": "ssl"},
        "api": {"host": f"takserver-api.{domain}", "port": 443},
        "client_certificate": {
            "subject": x509(data["client.pem"], "-subject"),
            "issuer": x509(data["client.pem"], "-issuer"),
            "not_after": x509(data["client.pem"], "-enddate"),
            "sha256": x509(data["client.pem"], "-fingerprint", "-sha256"),
        },
        "ca_certificate": {
            "subject": x509(data["ca.pem"], "-subject"),
            "sha256": x509(data["ca.pem"], "-fingerprint", "-sha256"),
        },
        "package_entries": entries,
    }
    write(out / "connection.json", (json.dumps(facts, indent=1) + "\n").encode())
    for path in sorted(out.iterdir()):
        print(f"{path} {path.stat().st_size} bytes mode {oct(path.stat().st_mode & 0o777)}")
    print(json.dumps({k: facts[k] for k in ("takclient", "group", "client_certificate", "ca_certificate", "package_entries")}, indent=1))


if __name__ == "__main__":
    main()
