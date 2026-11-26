#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Test keys, a JWKS document and signed tokens, made with the OpenSSL
command line. For the unit tests of ../web_auth.py and for the image
self-test, where this stands in for Keycloak. Never for a real deployment:
there the tokens come from the realm.

  token_tool.py new DIR [--kid ID] [--kind RSA|EC]
        make a key (DIR/ID.pem, mode 0600) and write DIR/jwks.json with the
        public halves of every key in DIR
  token_tool.py token DIR --kid ID --claims JSON [--lifetime SECONDS]
        print a compact JWS. `iat` and `exp` are added from the clock unless
        the claims name them

  token_tool.py cases DIR --issuer URL --audience ID --group PATH
        make the keys the cases need in DIR (the realm's stand-in 'realm-1',
        an EC key, and a forger's key under the same id in DIR/forger), write
        DIR/jwks.json and print the request variants auth_probe.py tries, as
        JSON: what a client might send, and what the bridge has to answer

The private keys are test material. Keep DIR outside the repository and
delete it afterwards.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time

CURVES = {"P-256": ("prime256v1", 32), "P-384": ("secp384r1", 48)}
DIGESTS = {"256": "-sha256", "384": "-sha384", "512": "-sha512"}


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def uint(value, length=None):
    return b64(value.to_bytes(length or (value.bit_length() + 7) // 8, "big"))


def openssl(*arguments, data=None):
    return subprocess.run(("openssl", *arguments), input=data, capture_output=True, check=True).stdout


def der_integers(signature):
    """r and s of a DER-encoded ECDSA signature."""
    if signature[0] != 0x30:
        raise ValueError("not a DER sequence")
    at = 2 if signature[1] < 0x80 else 2 + (signature[1] & 0x7f)
    values = []
    for _ in range(2):
        if signature[at] != 0x02:
            raise ValueError("not a DER integer")
        length = signature[at + 1]
        values.append(int.from_bytes(signature[at + 2:at + 2 + length], "big"))
        at += 2 + length
    return values


class Signer:
    """One key pair in a PEM file, its JWK and compact JWS made with it."""

    def __init__(self, directory, kid, kind="RSA", bits=2048, curve="P-256"):
        self.kid, self.kind, self.curve = kid, kind, curve if kind == "EC" else None
        self.path = os.path.join(directory, f"{kid}.pem")
        if not os.path.exists(self.path):
            previous = os.umask(0o077)
            try:
                if kind == "RSA":
                    openssl("genrsa", "-out", self.path, str(bits))
                else:
                    openssl("ecparam", "-name", CURVES[curve][0], "-genkey", "-noout", "-out", self.path)
            finally:
                os.umask(previous)
        if kind == "RSA":
            modulus = openssl("rsa", "-in", self.path, "-noout", "-modulus").decode().strip().split("=", 1)[1]
            self.jwk = {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": uint(int(modulus, 16)), "e": uint(65537)}
        else:
            size = CURVES[curve][1]
            point = openssl("ec", "-in", self.path, "-pubout", "-outform", "DER")[-(2 * size + 1):]
            if point[0] != 4:
                raise ValueError("expected an uncompressed point")
            self.jwk = {"kty": "EC", "kid": kid, "use": "sig", "crv": curve,
                        "x": b64(point[1:1 + size]), "y": b64(point[1 + size:])}

    def sign(self, signed, alg):
        signature = openssl("dgst", DIGESTS[alg[2:]], "-sign", self.path, data=signed)
        if self.kind == "RSA":
            return signature
        size = CURVES[self.curve][1]
        r, s = der_integers(signature)
        return r.to_bytes(size, "big") + s.to_bytes(size, "big")

    def token(self, claims, alg=None, header=None):
        alg = alg or ("RS256" if self.kind == "RSA" else {"P-256": "ES256", "P-384": "ES384"}[self.curve])
        head = {"alg": alg, "typ": "JWT", "kid": self.kid, **(header or {})}
        signed = f"{b64(json.dumps(head).encode())}.{b64(json.dumps(claims).encode())}"
        return f"{signed}.{b64(self.sign(signed.encode(), alg))}"


def jwks(*signers):
    return json.dumps({"keys": [signer.jwk for signer in signers]}).encode()


def load(directory):
    """Every key of DIR, by kid. The kind is read from the PEM header."""
    signers = {}
    for name in sorted(os.listdir(directory)):
        if name.endswith(".pem"):
            with open(os.path.join(directory, name), encoding="ascii") as stream:
                first = stream.readline()
            kind = "EC" if "EC PRIVATE" in first else "RSA"
            signers[name[:-4]] = Signer(directory, name[:-4], kind)
    return signers


def cases(directory, issuer, audience, group, lifetime=3600, short=20):
    """Request variants for auth_probe.py. `headers` are sent as they are,
    `query` is appended to the path, `expect` is 'open' or the HTTP status."""
    os.makedirs(os.path.join(directory, "forger"), mode=0o700, exist_ok=True)
    realm = Signer(directory, "realm-1")
    curve = Signer(directory, "realm-ec", "EC")
    forger = Signer(os.path.join(directory, "forger"), "realm-1")
    with open(os.path.join(directory, "jwks.json"), "wb") as stream:
        stream.write(jwks(realm, curve))
    now = int(time.time())

    def claims(**changes):
        value = {"iss": issuer, "aud": audience, "azp": audience, "sub": "00000000-0000-4000-8000-5e1f7e570001",
                 "typ": "ID", "iat": now, "exp": now + lifetime, "groups": [group]}
        value.update(changes)
        return {name: item for name, item in value.items() if item is not None}

    def bearer(token):
        return [f"Authorization: Bearer {token}"]

    good = realm.token(claims())
    head, payload, signature = good.split(".")
    other_payload = realm.token(claims(groups=["/Nobody"])).split(".")[1]
    none = f"{b64(json.dumps({'alg': 'none', 'kid': 'realm-1'}).encode())}.{payload}."
    hs_signed = f"{b64(json.dumps({'alg': 'HS256', 'kid': 'realm-1'}).encode())}.{payload}"
    public_pem = openssl("rsa", "-in", realm.path, "-pubout")
    hs256 = f"{hs_signed}.{b64(hmac.new(public_pem, hs_signed.encode(), hashlib.sha256).digest())}"
    basic = base64.b64encode(b"doug:not-a-real-password").decode()
    return [
        {"name": "good-token", "headers": bearer(good), "expect": "open"},
        {"name": "no-token", "headers": [], "expect": 401},
        {"name": "wrong-signature", "headers": bearer(forger.token(claims())), "expect": 401},
        {"name": "payload-swapped", "headers": bearer(f"{head}.{other_payload}.{signature}"), "expect": 401},
        {"name": "wrong-issuer", "headers": bearer(realm.token(claims(iss=issuer + "-other"))), "expect": 401},
        {"name": "wrong-audience", "headers": bearer(realm.token(claims(aud="uds-other-app", azp="uds-other-app"))),
         "expect": 401},
        {"name": "expired", "headers": bearer(realm.token(claims(iat=now - 600, exp=now - 300))), "expect": 401},
        {"name": "not-yet-valid", "headers": bearer(realm.token(claims(nbf=now + 600))), "expect": 401},
        {"name": "missing-group", "headers": bearer(realm.token(claims(groups=["/CloudTAK/Users"]))), "expect": 403},
        {"name": "no-groups-claim", "headers": bearer(realm.token(claims(groups=None))), "expect": 403},
        {"name": "alg-none", "headers": bearer(none + "AA"), "expect": 401},
        {"name": "hs256-with-the-public-key", "headers": bearer(hs256), "expect": 401},
        {"name": "unknown-kid", "headers": bearer(realm.token(claims(), header={"kid": "realm-0"})), "expect": 401},
        {"name": "es256-not-in-the-allow-list", "headers": bearer(curve.token(claims())), "expect": 401},
        {"name": "oversized-header", "headers": bearer(realm.token(claims(padding="x" * 20000))), "expect": 431},
        {"name": "basic-scheme", "headers": [f"Authorization: Basic {basic}"], "expect": 401},
        {"name": "bearer-token-as-basic", "headers": [f"Authorization: Basic {good}"], "expect": 401},
        {"name": "two-authorization-headers", "headers": [f"Authorization: Basic {basic}", *bearer(good)], "expect": 401},
        {"name": "token-in-query-string", "headers": [], "query": f"?token={good}&access_token={good}", "expect": 401},
        {"name": "token-in-cookie", "headers": [f"Cookie: token={good}; id_token={good}"], "expect": 401},
        {"name": "short-lived-token", "headers": bearer(realm.token(claims(exp=now + short))), "expect": "open",
         "expires_at": now + short},
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new")
    new.add_argument("directory")
    new.add_argument("--kid", default="test-1")
    new.add_argument("--kind", choices=("RSA", "EC"), default="RSA")
    token = commands.add_parser("token")
    token.add_argument("directory")
    token.add_argument("--kid", default="test-1")
    token.add_argument("--claims", required=True)
    token.add_argument("--lifetime", type=float, default=60.0)
    token.add_argument("--alg", default=None)
    case = commands.add_parser("cases")
    case.add_argument("directory")
    case.add_argument("--issuer", required=True)
    case.add_argument("--audience", required=True)
    case.add_argument("--group", required=True)
    case.add_argument("--short", type=int, default=20)
    options = parser.parse_args(argv)
    if options.command == "cases":
        print(json.dumps(cases(options.directory, options.issuer, options.audience, options.group, short=options.short)))
        return 0
    if options.command == "new":
        os.makedirs(options.directory, mode=0o700, exist_ok=True)
        Signer(options.directory, options.kid, options.kind)
        path = os.path.join(options.directory, "jwks.json")
        with open(path + ".tmp", "wb") as stream:
            stream.write(jwks(*load(options.directory).values()))
        os.replace(path + ".tmp", path)
        return 0
    claims = json.loads(options.claims)
    now = int(time.time())
    claims.setdefault("iat", now)
    claims.setdefault("exp", now + int(options.lifetime))
    print(load(options.directory)[options.kid].token(claims, alg=options.alg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
