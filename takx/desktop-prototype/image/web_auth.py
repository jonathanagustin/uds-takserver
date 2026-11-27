#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Token check of the TAKX desktop bridge: who may reach the desktop.

The UDS tenant gateway puts Authservice in front of the pod. After a sign-in
Authservice adds the OpenID Connect ID token of the session to every request
it lets through, as `Authorization: Bearer <JWT>`, and the waypoint forwards
that header unchanged (RequestAuthentication, forwardOriginalToken). This
module is what the bridge itself verifies in that header, so that reaching
its port is not enough:

  signature   against the realm's JSON Web Key Set, by the `kid` of the token;
              only the algorithms in the allow-list, all of them asymmetric
  iss         equal to the configured issuer
  aud         contains the configured audience (the Keycloak client id of the
              package); `azp`, when present, equals it too
  exp         required and in the future; `nbf` and `iat`, when present, not
              in the future (a small leeway covers clock differences)
  groups      the claim is a list that names at least one required group

Nothing else is a credential: no cookie, no query parameter, no other scheme
of the Authorization header, and never a key the token brings along (`jwk`,
`jku`, `x5c`, `x5u` in its header are not read).

Keys are fetched from the JWKS URL, kept for `refresh_after` seconds, used for
at most `max_age` seconds when a refresh fails, and never after that: without
keys every request is answered 'unavailable', never let through. An unknown
`kid` triggers one fetch (key rotation), at most every `retry_after` seconds.

Standard library only. The two signature checks are the textbook ones and
work on public values alone, so they have nothing to keep secret and no
timing to protect: RSASSA-PKCS1-v1_5 by building the expected encoded message
and comparing all of it (RFC 8017, 8.2.2), ECDSA over P-256 and P-384 as in
FIPS 186-4, 6.4.2. test_web_auth.py checks both against signatures made by
the OpenSSL command line. Python 3.11 or later.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# Sizes are far above what Keycloak sends and far below what would hurt.
MAX_TOKEN_CHARS = 16384
MAX_JWKS_BYTES = 262144
MAX_JWKS_KEYS = 32
MIN_RSA_BITS = 2048

MAX_RSA_BITS = 8192

# name -> (key type, curve or None, hashlib name)
ALGORITHMS = {
    "RS256": ("RSA", None, "sha256"),
    "RS384": ("RSA", None, "sha384"),
    "RS512": ("RSA", None, "sha512"),
    "ES256": ("EC", "P-256", "sha256"),
    "ES384": ("EC", "P-384", "sha384"),
}

# DER prefix of DigestInfo for each hash (RFC 8017, 9.2, note 1).
_DIGEST_INFO = {
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}

# Curve: (p, b, Gx, Gy, n); a = -3 for both (FIPS 186-4, D.1.2).
CURVES = {
    "P-256": (0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff,
              0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b,
              0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
              0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5,
              0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551),
    "P-384": (0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffeffffffff0000000000000000ffffffff,
              0xb3312fa7e23ee7e4988e056be3f82d19181d9c6efe8141120314088f5013875ac656398d8a2ed19d2a85c8edd3ec2aef,
              0xaa87ca22be8b05378eb1c71ef320ad746e1d3b628ba79b9859f741e082542a385502f25dbf55296c3a545e3872760ab7,
              0x3617de4a96262c6f5d9e98bf9292dc29f8f41dbd289a147ce9da3113b5f0b8c00a60b1ce1d7e819d7a431d7c90ea0e5f,
              0xffffffffffffffffffffffffffffffffffffffffffffffffc7634d81f4372ddf581a0db248b0a77aecec196accc52973),
}

_BEARER = re.compile(r"\A[Bb][Ee][Aa][Rr][Ee][Rr] ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\Z")
_SEGMENT = re.compile(r"\A[A-Za-z0-9_-]+\Z")


class Denied(Exception):
    """The request carries no acceptable credential (HTTP 401)."""

    status = 401


class Forbidden(Denied):
    """The token is genuine, but its holder is in no required group (HTTP 403)."""

    status = 403


class Unavailable(Exception):
    """No usable keys: nobody can be let in (HTTP 503)."""

    status = 503


@dataclass(frozen=True)
class Identity:
    """What a verified token says, as far as the bridge uses it."""

    subject: str
    expires_at: float
    key_id: str


@dataclass(frozen=True)
class _Key:
    kty: str
    curve: str | None
    alg: str | None
    numbers: tuple  # RSA: (n, e); EC: (x, y)


def rsa_verify(modulus, exponent, signature, message, digest):
    """RSASSA-PKCS1-v1_5: the signature, raised to the public exponent, must
    be exactly the encoded message for this digest. Nothing is parsed."""
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    value = int.from_bytes(signature, "big")
    if value >= modulus:
        return False
    tail = _DIGEST_INFO[digest] + hashlib.new(digest, message).digest()
    expected = b"\x00\x01" + b"\xff" * (size - len(tail) - 3) + b"\x00" + tail
    return hmac.compare_digest(pow(value, exponent, modulus).to_bytes(size, "big"), expected)


def _ec_add(curve, first, second):
    """Sum of two points in affine coordinates; None is the point at infinity."""
    if first is None:
        return second
    if second is None:
        return first
    prime = curve[0]
    (x1, y1), (x2, y2) = first, second
    if x1 == x2:
        if (y1 + y2) % prime == 0:
            return None
        slope = (3 * x1 * x1 - 3) * pow(2 * y1, -1, prime) % prime
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, prime) % prime
    x3 = (slope * slope - x1 - x2) % prime
    return x3, (slope * (x1 - x3) - y1) % prime


def _ec_multiply(curve, scalar, point):
    result = None
    while scalar:
        if scalar & 1:
            result = _ec_add(curve, result, point)
        point = _ec_add(curve, point, point)
        scalar >>= 1
    return result


def ec_on_curve(name, x, y):
    prime, b = CURVES[name][0], CURVES[name][1]
    return 0 <= x < prime and 0 <= y < prime and (y * y - (x * x * x - 3 * x + b)) % prime == 0


def ec_verify(name, x, y, signature, message, digest):
    """ECDSA with the signature as r || s, each as long as the curve order
    (RFC 7518, 3.4). The digests used here are as long as the order."""
    curve = CURVES[name]
    order = curve[4]
    size = (order.bit_length() + 7) // 8
    if len(signature) != 2 * size or not ec_on_curve(name, x, y):
        return False
    r, s = int.from_bytes(signature[:size], "big"), int.from_bytes(signature[size:], "big")
    if not (1 <= r < order and 1 <= s < order):
        return False
    hashed = int.from_bytes(hashlib.new(digest, message).digest(), "big")
    inverse = pow(s, -1, order)
    point = _ec_add(curve, _ec_multiply(curve, hashed * inverse % order, (curve[2], curve[3])),
                    _ec_multiply(curve, r * inverse % order, (x, y)))
    return point is not None and point[0] % order == r


def _decode(segment, what):
    if not _SEGMENT.match(segment) or len(segment) % 4 == 1:
        raise Denied(f"{what} is not base64url")
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError):
        raise Denied(f"{what} is not base64url") from None


def _unique(pairs):
    seen = {}
    for name, value in pairs:
        if name in seen:
            raise ValueError(f"member {name!r} twice")
        seen[name] = value
    return seen


def _constant(name):
    raise ValueError(f"{name} is not a JSON number")


def _json_object(data, what):
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
    except (ValueError, RecursionError):
        raise Denied(f"{what} is not JSON") from None
    if not isinstance(value, dict):
        raise Denied(f"{what} is not a JSON object")
    return value


def _number(claims, name, required):
    value = claims.get(name)
    if value is None and not required:
        return None
    # bool is an int in Python; a token saying "exp": true is not a time.
    # Neither is a number no clock can reach (1e400 reads as infinity).
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not -1e12 < value < 1e12:
        raise Denied(f"claim {name} is missing or not a number")
    return float(value)


def _uint(text, what):
    return int.from_bytes(_decode(text, what), "big") if isinstance(text, str) else None


def parse_jwks(data, algorithms):
    """The signing keys of a JWKS document, by kid. Raises ValueError when the
    document is not one; keys of other use or kind are left out."""
    document = json.loads(data.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise ValueError("no 'keys' list")
    if len(document["keys"]) > MAX_JWKS_KEYS:
        raise ValueError("too many keys")
    keys = {}
    for entry in document["keys"]:
        if not isinstance(entry, dict) or entry.get("use", "sig") != "sig":
            continue
        kid, kty, alg = entry.get("kid"), entry.get("kty"), entry.get("alg")
        if not isinstance(kid, str) or not kid or len(kid) > 256:
            continue
        if alg is not None and (alg not in algorithms or ALGORITHMS[alg][0] != kty):
            continue
        if not any(ALGORITHMS[name][:2] == (kty, entry.get("crv") if kty == "EC" else None) for name in algorithms):
            continue
        try:
            if kty == "RSA":
                modulus, exponent = _uint(entry.get("n"), "n"), _uint(entry.get("e"), "e")
                if (modulus is None or exponent is None or not MIN_RSA_BITS <= modulus.bit_length() <= MAX_RSA_BITS
                        or not 3 <= exponent < 1 << 32 or exponent % 2 == 0 or modulus % 2 == 0):
                    continue
                key = _Key("RSA", None, alg, (modulus, exponent))
            elif kty == "EC" and entry.get("crv") in CURVES:
                x, y = _uint(entry.get("x"), "x"), _uint(entry.get("y"), "y")
                if x is None or y is None or not ec_on_curve(entry["crv"], x, y):
                    continue
                key = _Key("EC", entry["crv"], alg, (x, y))
            else:
                continue
        except Denied:
            continue
        if kid in keys:
            raise ValueError("a key id occurs twice")
        keys[kid] = key
    return keys


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102, PLR0913
        return None


def fetch_url(url, timeout=5.0, ca_file=None):
    """GET url and return the body. http and https only, no proxy, no
    redirect, certificate verification always on, bounded size."""
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme not in ("http", "https"):
        raise ValueError("the JWKS URL must be http or https")
    handlers = [urllib.request.ProxyHandler({}), _NoRedirect()]
    if scheme == "https":
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file or None)))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "takx-desktop-bridge"})
    with opener.open(request, timeout=timeout) as reply:
        if reply.status != 200:
            raise ValueError(f"HTTP {reply.status}")
        body = reply.read(MAX_JWKS_BYTES + 1)
    if len(body) > MAX_JWKS_BYTES:
        raise ValueError("the JWKS document is too large")
    return body


class KeySet:
    """The realm's signing keys, with an expiry. Thread-safe.

    fetch() returns the JWKS document as bytes or raises; clock() is a
    monotonic time in seconds. Both are parameters so that tests need no
    network and no sleep.
    """

    def __init__(self, fetch, algorithms=("RS256",), refresh_after=300.0, max_age=900.0, retry_after=10.0,
                 clock=time.monotonic, log=None):
        unknown = [name for name in algorithms if name not in ALGORITHMS]
        if unknown or not algorithms:
            raise ValueError(f"unsupported algorithm: {', '.join(unknown) or 'none given'}")
        if not 0 < refresh_after <= max_age or retry_after <= 0:
            raise ValueError("need 0 < refresh_after <= max_age and retry_after > 0")
        self.algorithms = tuple(algorithms)
        self._fetch, self._clock, self._log = fetch, clock, log or (lambda message: None)
        self._refresh_after, self._max_age, self._retry_after = refresh_after, max_age, retry_after
        self._lock = threading.Lock()
        self._keys = None
        self._fetched = self._attempted = None

    def _load(self, now):
        self._attempted = now
        try:
            keys = parse_jwks(self._fetch(), self.algorithms)
            if not keys:
                raise ValueError("no usable signing key in the document")
        except Exception as error:  # noqa: BLE001 - any failure means: no new keys
            self._log(f"jwks: fetch failed: {type(error).__name__}: {str(error)[:200]}")
            return False
        if self._keys is None or set(keys) != set(self._keys):
            self._log(f"jwks: {len(keys)} signing key(s) loaded")
        self._keys, self._fetched = keys, self._clock()
        return True

    def _may_fetch(self, now):
        return self._attempted is None or now - self._attempted >= self._retry_after

    def refresh(self):
        """Fetch now, whatever the age of the keys. True when keys were loaded."""
        with self._lock:
            return self._load(self._clock())

    def loaded(self):
        """Whether usable keys are held right now."""
        with self._lock:
            return self._keys is not None and self._clock() - self._fetched < self._max_age

    def key(self, kid):
        """The key with this id. Unavailable without usable keys, Denied for an unknown id."""
        with self._lock:
            now = self._clock()
            if self._keys is not None and now - self._fetched >= self._max_age:
                self._keys = None
                self._log("jwks: the keys are older than the maximum age and are no longer used")
            stale = self._keys is None or now - self._fetched >= self._refresh_after
            if (stale or kid not in self._keys) and self._may_fetch(now):
                self._load(now)
            if self._keys is None:
                raise Unavailable("no signing keys")
            key = self._keys.get(kid)
            if key is None:
                raise Denied("unknown key id")
            return key


class Verifier:
    """Decides about one Authorization header. See the module text for the rules."""

    def __init__(self, keys, issuer, audience, groups, groups_claim="groups", leeway=10.0, clock=time.time):  # noqa: PLR0913
        if not issuer or not audience or not groups_claim:
            raise ValueError("issuer, audience and the groups claim name must not be empty")
        if not groups or any(not isinstance(group, str) or not group for group in groups):
            raise ValueError("at least one required group, and none of them empty")
        if not 0 <= leeway <= 300:
            raise ValueError("leeway must be between 0 and 300 seconds")
        self.keys, self.issuer, self.audience = keys, issuer, audience
        self.groups, self.groups_claim, self.leeway, self._clock = frozenset(groups), groups_claim, leeway, clock

    def verify(self, authorization):
        """authorization: every value of the request's Authorization header,
        as a list of str. Returns an Identity or raises Denied, Forbidden or
        Unavailable. The exception text never contains part of the token."""
        if not authorization:
            raise Denied("no Authorization header")
        if len(authorization) != 1:
            raise Denied("more than one Authorization header")
        if len(authorization[0]) > MAX_TOKEN_CHARS:
            raise Denied("Authorization header too long")
        match = _BEARER.match(authorization[0])
        if not match:
            raise Denied("not a Bearer token")
        header_text, payload_text, signature_text = match.group(1).split(".")
        header = _json_object(_decode(header_text, "token header"), "token header")
        alg, kid = header.get("alg"), header.get("kid")
        if not isinstance(alg, str) or alg not in self.keys.algorithms:
            raise Denied("algorithm not allowed")
        if "crit" in header:
            raise Denied("critical header parameters are not supported")
        if not isinstance(kid, str) or not kid or len(kid) > 256:
            raise Denied("no key id")
        signature = _decode(signature_text, "signature")
        key = self.keys.key(kid)
        kty, curve, digest = ALGORITHMS[alg]
        if key.kty != kty or key.curve != curve or (key.alg is not None and key.alg != alg):
            raise Denied("the key is not for this algorithm")
        signed = f"{header_text}.{payload_text}".encode("ascii")
        if kty == "RSA":
            genuine = rsa_verify(*key.numbers, signature, signed, digest)
        else:
            genuine = ec_verify(curve, *key.numbers, signature, signed, digest)
        if not genuine:
            raise Denied("signature does not verify")
        # Only now is the payload trusted enough to be read at all.
        claims = _json_object(_decode(payload_text, "token payload"), "token payload")
        return self._claims(claims, kid)

    def _claims(self, claims, kid):
        now = self._clock()
        if claims.get("iss") != self.issuer:
            raise Denied("wrong issuer")
        audience = claims.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience
        if not isinstance(audiences, list) or self.audience not in audiences:
            raise Denied("wrong audience")
        if "azp" in claims and claims["azp"] != self.audience:
            raise Denied("issued to another client")
        expires = _number(claims, "exp", required=True)
        if now >= expires + self.leeway:
            raise Denied("expired")
        not_before, issued = _number(claims, "nbf", required=False), _number(claims, "iat", required=False)
        if not_before is not None and now + self.leeway < not_before:
            raise Denied("not valid yet")
        if issued is not None and now + self.leeway < issued:
            raise Denied("issued in the future")
        groups = claims.get(self.groups_claim)
        if not isinstance(groups, list) or not all(isinstance(group, str) for group in groups):
            raise Forbidden("no groups claim")
        if self.groups.isdisjoint(groups):
            raise Forbidden("in no required group")
        subject = claims.get("sub")
        return Identity(subject=subject if isinstance(subject, str) else "", expires_at=expires, key_id=kid)


def _seconds(environ, name, default, low, high):
    text = environ.get(name, "")
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{name} must be a number of seconds") from None
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}")
    return value


def from_environment(environ=None, log=None):
    """Build the Verifier the image runs with. Every value is required unless
    a default is named in ../image/README.md; a missing one is an error, so
    that a bridge without a complete configuration does not start."""
    environ = os.environ if environ is None else environ
    missing = [name for name in ("TAKX_AUTH_ISSUER", "TAKX_AUTH_AUDIENCE", "TAKX_AUTH_JWKS_URL", "TAKX_AUTH_GROUPS")
               if not environ.get(name)]
    if missing:
        raise ValueError(f"not set: {', '.join(missing)}")
    try:
        groups = json.loads(environ["TAKX_AUTH_GROUPS"])
    except ValueError:
        raise ValueError("TAKX_AUTH_GROUPS must be a JSON list of group paths") from None
    if not isinstance(groups, list):
        raise ValueError("TAKX_AUTH_GROUPS must be a JSON list of group paths")
    url = environ["TAKX_AUTH_JWKS_URL"]
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("TAKX_AUTH_JWKS_URL must be http or https")
    ca_file = environ.get("TAKX_AUTH_JWKS_CA_FILE") or None
    timeout = _seconds(environ, "TAKX_AUTH_JWKS_TIMEOUT_SECONDS", 5.0, 0.5, 30.0)
    refresh = _seconds(environ, "TAKX_AUTH_JWKS_REFRESH_SECONDS", 300.0, 1.0, 86400.0)
    keys = KeySet(lambda: fetch_url(url, timeout=timeout, ca_file=ca_file),
                  algorithms=tuple(name for name in environ.get("TAKX_AUTH_ALGORITHMS", "RS256").split(",") if name),
                  refresh_after=refresh,
                  max_age=_seconds(environ, "TAKX_AUTH_JWKS_MAX_AGE_SECONDS", max(900.0, refresh), refresh, 604800.0),
                  log=log)
    return Verifier(keys, issuer=environ["TAKX_AUTH_ISSUER"], audience=environ["TAKX_AUTH_AUDIENCE"], groups=groups,
                    groups_claim=environ.get("TAKX_AUTH_GROUPS_CLAIM") or "groups",
                    leeway=_seconds(environ, "TAKX_AUTH_LEEWAY_SECONDS", 10.0, 0.0, 300.0))
