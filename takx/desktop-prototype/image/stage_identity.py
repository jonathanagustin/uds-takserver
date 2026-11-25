#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Stage a TakClient identity for a manual import into TAKX (init step).

Input, a read-only directory (TAKX_IDENTITY_SECRET_DIR, default
/run/takx/secret) with the two keys of the copied device Secret:

  package.zip   the TakClient data package: MANIFEST/manifest.xml,
                certs/server.pref, certs/truststore.p12, certs/<callsign>.p12
  password      protects both PKCS#12 files

Output (TAKX_IDENTITY_DIR, default /run/takx/identity), every file mode 0400:

  package.zip          the data package, byte for byte
  client.p12           the "Client Cert File" of the TAKX connection dialog
  truststore.p12       the "CA File" as issued (OpenSSL reads it; JDK 17
                       keytool lists no entry in it)
  truststore-java.p12  the same CA certificate, taken from the chain in
                       client.p12 and written by keytool, for Java code
  password.txt         the password of all three stores
  takx-package.zip     only with TAKX_IDENTITY_CONNECT_HOST: the package with
                       the host of the connect string replaced by that name

With no input directory, or an empty one, nothing is staged and the exit
status is 0: the desktop then starts without an identity. Anything else that
is wrong is an error and nothing stays staged: a missing key, an unexpected or
oversized zip entry, a connect string that is not HOST:PORT:ssl or not for
TAKX_IDENTITY_EXPECT_HOST (when set), a wrong password, a certificate outside
its validity period, a client store without the CA certificate.

Only names, sizes, validity dates and SHA-256 fingerprints of the public
certificates are printed: never the password, a key or PKCS#12 content.
The import itself is done by a person in the TAKX window; this step proves
nothing about a connection.
"""
import datetime
import hashlib
import io
import os
import re
import subprocess
import sys
import zipfile

MAX_PACKAGE = 1 << 20
MAX_ENTRY = 256 << 10
CONNECT = re.compile(rb'(<entry key="connectString0" class="class java\.lang\.String">)([^<]*)(</entry>)')
CONNECT_VALUE = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?):([0-9]{1,5}):ssl$")
HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
FIXED = ("MANIFEST/manifest.xml", "certs/server.pref", "certs/truststore.p12")
CLIENT = re.compile(r"^certs/([A-Za-z0-9][A-Za-z0-9._-]{0,127})\.p12$")
STAGED = ("package.zip", "client.p12", "truststore.p12", "truststore-java.p12", "password.txt", "takx-package.zip")
KEYTOOL_JVM = ("-J-XX:-UsePerfData", "-J-Duser.timezone=UTC", "-J-Duser.language=en", "-J-Duser.country=US")
DATE = "%a %b %d %H:%M:%S UTC %Y"


class Rejected(Exception):
    """The identity is not acceptable; the message never holds secret data."""


def read_package(data):
    """Return (entries, client entry name) of a TakClient data package."""
    if len(data) > MAX_PACKAGE:
        raise Rejected(f"package.zip is larger than {MAX_PACKAGE} bytes")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise Rejected("package.zip is not a zip archive") from error
    with archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise Rejected("package.zip has duplicate entries")
        clients = [name for name in names if name not in FIXED and CLIENT.match(name)]
        unexpected = sorted(set(names) - set(FIXED) - set(clients))
        if unexpected:
            raise Rejected(f"package.zip has unexpected entries: {', '.join(repr(name) for name in unexpected)[:300]}")
        missing = [name for name in FIXED if name not in names]
        if missing:
            raise Rejected(f"package.zip lacks {', '.join(missing)}")
        if len(clients) != 1:
            raise Rejected(f"package.zip must hold exactly one client store certs/<callsign>.p12, found {len(clients)}")
        entries = {}
        for info in archive.infolist():
            if info.file_size > MAX_ENTRY:
                raise Rejected(f"package.zip entry {info.filename} is larger than {MAX_ENTRY} bytes")
            entries[info.filename] = archive.read(info)
    return entries, clients[0]


def connect_string(pref, expect_host=None):
    """Return (host, port) of the single connect string in server.pref."""
    found = CONNECT.findall(pref)
    if len(found) != 1:
        raise Rejected(f"server.pref must hold exactly one connectString0 entry, found {len(found)}")
    match = CONNECT_VALUE.match(found[0][1].decode("ascii", "replace"))
    if not match:
        raise Rejected("the connect string in server.pref is not HOST:PORT:ssl")
    host, port = match.group(1), int(match.group(2))
    if not 0 < port < 65536:
        raise Rejected("the connect string in server.pref has no valid port")
    if expect_host and host.lower() != expect_host.lower():
        raise Rejected(f"the connect string names host '{host}', expected '{expect_host}'")
    return host, port


def rewrite_package(entries, order, new_host):
    """Return the package with the connect-string host replaced (stored, fixed dates)."""
    if not HOST.match(new_host):
        raise Rejected("TAKX_IDENTITY_CONNECT_HOST is not a host name")
    _host, port = connect_string(entries["certs/server.pref"])
    pref = CONNECT.sub(lambda match: match.group(1) + f"{new_host}:{port}:ssl".encode() + match.group(3),
                       entries["certs/server.pref"], count=1)
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as archive:
        for name in order:
            content = pref if name == "certs/server.pref" else entries[name]
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), content)
    return target.getvalue()


def write_private(path, data):
    """Write data to path with mode 0400, replacing an earlier file."""
    if os.path.lexists(path):
        os.chmod(path, 0o600)
        os.unlink(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    os.chmod(path, 0o400)


def keytool(*arguments, data=None):
    """Run keytool; return stdout. Its output is never passed on."""
    try:
        done = subprocess.run(("keytool",) + KEYTOOL_JVM + arguments, input=data, capture_output=True, check=False)
    except OSError as error:
        raise Rejected(f"keytool cannot be started: {error.strerror}") from error
    if done.returncode != 0:
        raise Rejected(f"keytool {arguments[0]} failed with status {done.returncode}")
    return done.stdout.decode("utf-8", "replace")


def certificate_facts(pem):
    """Owner, issuer, validity and SHA-256 fingerprint of one PEM certificate."""
    text = keytool("-printcert", data=pem.encode())
    facts = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Owner: "):
            facts["owner"] = line[7:]
        elif line.startswith("Issuer: "):
            facts["issuer"] = line[8:]
        elif line.startswith("Valid from: ") and " until: " in line:
            start, end = line[12:].split(" until: ")
            facts["not_before"] = datetime.datetime.strptime(start, DATE).replace(tzinfo=datetime.timezone.utc)
            facts["not_after"] = datetime.datetime.strptime(end, DATE).replace(tzinfo=datetime.timezone.utc)
        elif line.startswith("SHA256: "):
            facts["sha256"] = line[8:]
    if set(facts) != {"owner", "issuer", "not_before", "not_after", "sha256"}:
        raise Rejected("keytool -printcert output was not understood")
    return facts


def client_chain(store, password_file):
    """PEM certificates of the key entry in the client store, leaf first."""
    try:
        text = keytool("-list", "-rfc", "-storetype", "PKCS12", "-keystore", store, "-storepass:file", password_file)
    except Rejected as error:
        raise Rejected("client.p12 cannot be opened with the given password") from error
    if "PrivateKeyEntry" not in text:
        raise Rejected("client.p12 holds no private key entry")
    return re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, re.S)


def stage(secret_dir, out_dir, expect_host=None, connect_host=None, now=None, log=print):
    """Validate and stage; return the list of staged file names."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    present = [name for name in ("package.zip", "password") if os.path.isfile(os.path.join(secret_dir, name))]
    if not present:
        log(f"no identity staged: {secret_dir} holds neither package.zip nor password")
        return []
    if len(present) != 2:
        raise Rejected(f"{secret_dir} holds {present[0]} but not both package.zip and password")
    with open(os.path.join(secret_dir, "package.zip"), "rb") as stream:
        package = stream.read(MAX_PACKAGE + 1)
    with open(os.path.join(secret_dir, "password"), "rb") as stream:
        password = stream.read(1025).rstrip(b"\r\n")
    if not 8 <= len(password) <= 1024 or not all(32 < byte < 127 for byte in password):
        raise Rejected("the password is not 8 to 1024 printable ASCII characters without spaces")

    entries, client = read_package(package)
    host, port = connect_string(entries["certs/server.pref"], expect_host)
    staged = []
    try:
        def put(name, data):
            write_private(os.path.join(out_dir, name), data)
            staged.append(name)

        for name in STAGED:
            path = os.path.join(out_dir, name)
            if os.path.lexists(path):
                os.chmod(path, 0o600)
                os.unlink(path)
        put("password.txt", password + b"\n")
        put("client.p12", entries[client])
        password_file = os.path.join(out_dir, "password.txt")
        chain = client_chain(os.path.join(out_dir, "client.p12"), password_file)
        if not chain:
            raise Rejected("client.p12 holds no certificate")
        facts = [certificate_facts(pem) for pem in chain]
        for label, fact in zip(["client certificate"] + ["chain certificate"] * (len(facts) - 1), facts):
            if not fact["not_before"] <= now <= fact["not_after"]:
                raise Rejected(f"the {label} is outside its validity period "
                               f"({fact['not_before']:%Y-%m-%dT%H:%M:%SZ} to {fact['not_after']:%Y-%m-%dT%H:%M:%SZ})")
        if len(chain) < 2 or facts[-1]["owner"] != facts[-1]["issuer"]:
            raise Rejected("client.p12 does not carry the self-signed CA certificate after the client certificate")
        java_store = os.path.join(out_dir, "truststore-java.p12")
        keytool("-importcert", "-noprompt", "-trustcacerts", "-alias", "tak-ca", "-keystore", java_store,
                "-storetype", "PKCS12", "-storepass:file", password_file, data=chain[-1].encode())
        os.chmod(java_store, 0o400)
        staged.append("truststore-java.p12")
        listing = keytool("-list", "-storetype", "PKCS12", "-keystore", java_store, "-storepass:file", password_file)
        if "trustedCertEntry" not in listing:
            raise Rejected("truststore-java.p12 does not list the CA certificate")
        put("truststore.p12", entries["certs/truststore.p12"])
        put("package.zip", package)
        if connect_host:
            order = list(FIXED) + [client]
            put("takx-package.zip", rewrite_package(entries, order, connect_host))
    except BaseException:
        for name in STAGED:
            path = os.path.join(out_dir, name)
            if os.path.lexists(path):
                os.chmod(path, 0o600)
                os.unlink(path)
        raise

    log(f"identity staged in {out_dir}: callsign store {client}, connect string {host}:{port}:ssl"
        + (f", takx-package.zip uses host {connect_host}" if connect_host else ""))
    for name in staged:
        path = os.path.join(out_dir, name)
        log(f"  {name} {os.path.getsize(path)} bytes mode {os.stat(path).st_mode & 0o777:04o}")
    log(f"  client certificate {facts[0]['owner']}; not after {facts[0]['not_after']:%Y-%m-%dT%H:%M:%SZ}; "
        f"SHA-256 {facts[0]['sha256']}")
    log(f"  CA certificate {facts[-1]['owner']}; not after {facts[-1]['not_after']:%Y-%m-%dT%H:%M:%SZ}; "
        f"SHA-256 {facts[-1]['sha256']}")
    log(f"  package.zip SHA-256 {hashlib.sha256(package).hexdigest()}")
    log("the import is manual: TAKX window, Data Packages or TAK Server; no connection was attempted")
    return staged


def main():
    os.umask(0o077)
    secret_dir = os.environ.get("TAKX_IDENTITY_SECRET_DIR", "/run/takx/secret")
    out_dir = os.environ.get("TAKX_IDENTITY_DIR", "/run/takx/identity")
    try:
        stage(secret_dir, out_dir,
              expect_host=os.environ.get("TAKX_IDENTITY_EXPECT_HOST") or None,
              connect_host=os.environ.get("TAKX_IDENTITY_CONNECT_HOST") or None,
              log=lambda text: print(f"stage_identity: {text}", flush=True))
    except Rejected as error:
        print(f"stage_identity: rejected: {error}", file=sys.stderr, flush=True)
        return 1
    except OSError as error:
        print(f"stage_identity: {error.__class__.__name__}: {error.strerror} ({error.filename})", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
