#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Scan every layer of a saved image for things that must not be in it.

  docker save IMAGE | scan_image.py [--ca-bundle FILE] [--must-not-contain FILE ...] [--report FILE]

Reads the 'docker save' stream (OCI layout or the older Docker layout) into a
temporary file (TMPDIR), unpacks one layer at a time in memory and looks at
every file of every layer, so a file deleted by a later layer is still seen.
Reported:

  private_ca       every certificate of a --must-not-contain file, and every
                   certificate of --ca-bundle that is not a public root (not
                   in the JDK cacerts and not shipped by Debian's
                   ca-certificates in the image), searched as DER bytes and as
                   PEM text in every file of every layer. This is how a
                   build-time proxy CA would show up
  private_keys     files with a PEM private key (header and base64 body).
                   A file that is byte-identical to what a Debian package
                   ships (dpkg md5sums) is listed separately and does not
                   fail the scan: GnuTLS carries self-test keys in its library
  key_stores       files named *.p12, *.pfx, *.jks, *.key or *.keystore
  build_paths      entries below var/lib/takx-proto, the build work root
  layers, files, bytes

Exit status 1 when a private CA, a PEM private key or a build path is found.
Key stores are listed for a person to judge: the TAKX tree and the JDK ship
some on purpose. Files inside archives (JARs) are searched as stored bytes
only, so compressed members are not seen.
"""
import argparse
import base64
import gzip
import hashlib
import io
import json
import re
import shutil
import sys
import tarfile
import tempfile

# A header alone is a constant of some parser (GnuTLS, GLib, Xtigervnc carry
# it); a key is the header followed by a base64 body.
KEY_HEADER = re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE" rb" KEY-----\r?\n"
                        rb"(?:[A-Za-z-]+: [^\n]*\n)*\r?\n?[A-Za-z0-9+/=]{40,}")
STORE_NAME = re.compile(r"\.(p12|pfx|jks|key|keystore)$", re.I)


def bundle_certificates(path):
    with open(path, encoding="ascii", errors="replace") as stream:
        text = stream.read()
    found = []
    for body in re.findall(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", text, re.S):
        lines = [line.strip() for line in body.strip().splitlines() if line.strip()]
        found.append({"der": base64.b64decode("".join(lines)), "pem_line": lines[2].encode() if len(lines) > 3 else None})
    return found


def subject_hint(der):
    """Printable strings of the certificate, enough to recognise it in a report."""
    return " / ".join(text.decode() for text in re.findall(rb"[A-Za-z0-9 ()._-]{6,}", der[:400])[:4])


def layer_streams(save):
    """Yield (name, tarfile) for every layer inside a 'docker save' stream."""
    for member in save:
        if not member.isfile():
            continue
        data = save.extractfile(member).read()
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        if len(data) > 512 and data[257:262] == b"ustar":
            yield member.name, tarfile.open(fileobj=io.BytesIO(data))


def trust_stores(save):
    """Return (public trust store bytes, {path: (package, md5)} of Debian package files)."""
    stores, packaged = [], {}
    for _layer, archive in layer_streams(save):
        for member in archive:
            if not member.isfile():
                continue
            if member.name.endswith("lib/security/cacerts"):
                stores.append(archive.extractfile(member).read())
            elif member.name.startswith("usr/share/ca-certificates/") and member.name.endswith(".crt"):
                text = archive.extractfile(member).read().decode("ascii", "replace")
                for body in re.findall(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", text, re.S):
                    stores.append(base64.b64decode("".join(body.split())))
            elif member.name.startswith("var/lib/dpkg/info/") and member.name.endswith(".md5sums"):
                package = member.name.rsplit("/", 1)[1][:-len(".md5sums")]
                for line in archive.extractfile(member).read().decode("utf-8", "replace").splitlines():
                    digest, _, path = line.partition("  ")
                    packaged[path] = (package, digest)
    return stores, packaged


def scan(save, private, packaged):
    """Return the report fields for one pass over all layers."""
    layers, files, total = 0, 0, 0
    hits, key_files, debian_keys, stores, build_paths = [], [], [], [], []
    for layer, archive in layer_streams(save):
        layers += 1
        for member in archive:
            if member.name == "var/lib/takx-proto" or member.name.startswith("var/lib/takx-proto/"):
                build_paths.append({"layer": layer, "path": member.name})
            if not member.isfile():
                continue
            files += 1
            total += member.size
            if STORE_NAME.search(member.name):
                stores.append({"layer": layer, "path": member.name, "bytes": member.size})
            data = archive.extractfile(member).read()
            if KEY_HEADER.search(data):
                package, digest = packaged.get(member.name, (None, None))
                if digest == hashlib.md5(data).hexdigest():  # noqa: S324 - dpkg's own checksum format
                    debian_keys.append({"path": member.name, "package": package})
                else:
                    key_files.append({"layer": layer, "path": member.name})
            for item in private:
                if item["der"] in data or (item["pem_line"] and item["pem_line"] in data):
                    hits.append({"layer": layer, "path": member.name, "certificate": subject_hint(item["der"])})
    return {"private_ca_hits": hits, "layers": layers, "files": files, "bytes": total, "private_keys": key_files,
            "pem_keys_in_unmodified_debian_package_files": debian_keys, "key_stores": stores,
            "build_paths": build_paths}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ca-bundle")
    parser.add_argument("--must-not-contain", action="append", default=[],
                        help="file of certificates that may be nowhere in the image, trust stores included")
    parser.add_argument("--report")
    options = parser.parse_args(argv)
    certificates = bundle_certificates(options.ca_bundle) if options.ca_bundle else []
    # Two passes are needed (the JDK trust store has to be known before any
    # file is judged), so the stream is spooled to a temporary file first.
    with tempfile.TemporaryFile() as stream:
        shutil.copyfileobj(sys.stdin.buffer, stream, 1 << 20)
        stream.seek(0)
        with tarfile.open(fileobj=stream) as save:
            cacerts, packaged = trust_stores(save)
        private = [item for item in certificates if not any(item["der"] in store for store in cacerts)]
        known = {item["der"] for item in private}
        for path in options.must_not_contain:
            private += [item for item in bundle_certificates(path) if item["der"] not in known]
        stream.seek(0)
        with tarfile.open(fileobj=stream) as save:
            found = scan(save, private, packaged)
    report = {"ca_bundle_certificates": len(certificates),
              "bundle_certificates_that_are_public_roots_of_the_image": len(certificates) - len(known),
              "private_ca_searched_for": [subject_hint(item["der"]) for item in private], **found}
    report["status"] = "failed" if found["private_ca_hits"] or found["private_keys"] or found["build_paths"] else "ok"
    text = json.dumps(report, indent=1)
    if options.report:
        with open(options.report, "w", encoding="utf-8") as output:
            output.write(text + "\n")
    print(text)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
