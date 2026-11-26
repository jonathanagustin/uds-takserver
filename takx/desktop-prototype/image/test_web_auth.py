# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Tests of the bridge's token check (web_auth.py).

Every token is signed here by the OpenSSL command line with an RSA or EC key
made for the test run (verify/token_tool.py), so the signer is another
implementation than the verifier. The JWKS document is handed to the key set
by a function: no network, no sleep. Skipped where `openssl` is missing.
Run: python3 -B -m unittest takx/desktop-prototype/image/test_web_auth.py
"""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "verify"))

import token_tool  # noqa: E402
from token_tool import b64, jwks  # noqa: E402
import web_auth  # noqa: E402

ISSUER = "https://sso.example.test/realms/uds"
AUDIENCE = "uds-takx-desktop"
GROUP = "/UDS Core/Admin"
NOW = 1_800_000_000.0

OPENSSL = shutil.which("openssl")
KEYS = None
RSA_KEY = RSA_OTHER = RSA_NEW = EC_KEY = EC384_KEY = None


def setUpModule():
    global KEYS, RSA_KEY, RSA_OTHER, RSA_NEW, EC_KEY, EC384_KEY  # noqa: PLW0603
    if not OPENSSL:
        raise unittest.SkipTest("the openssl command is needed to make keys and signatures")
    KEYS = tempfile.TemporaryDirectory(prefix="takx-web-auth-test-")
    for name in ("a", "b"):
        Path(KEYS.name, name).mkdir()
    RSA_KEY = token_tool.Signer(Path(KEYS.name, "a"), "rsa-1")
    RSA_OTHER = token_tool.Signer(Path(KEYS.name, "b"), "rsa-1")  # another key under the same id: a forger's
    RSA_NEW = token_tool.Signer(Path(KEYS.name, "a"), "rsa-2")
    EC_KEY = token_tool.Signer(Path(KEYS.name, "a"), "ec-1", "EC")
    EC384_KEY = token_tool.Signer(Path(KEYS.name, "a"), "ec-384", "EC", curve="P-384")


def tearDownModule():
    if KEYS is not None:
        KEYS.cleanup()


def number(text):
    return int.from_bytes(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)), "big")


def claims(**changes):
    value = {"iss": ISSUER, "aud": AUDIENCE, "azp": AUDIENCE, "sub": "8a5f0000-0000-4000-8000-00000000d0c5",
             "typ": "ID", "iat": NOW - 5, "exp": NOW + 55, "groups": [GROUP, "/Other"]}
    value.update(changes)
    return {name: item for name, item in value.items() if item is not None}


class Clock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


class Source:
    """Stands in for the JWKS endpoint: counts fetches, can fail."""

    def __init__(self, *signers):
        self.document, self.fetches, self.fail = jwks(*signers), 0, False

    def __call__(self):
        self.fetches += 1
        if self.fail:
            raise OSError("connection refused")
        return self.document


def verifier(source=None, algorithms=("RS256",), wall=NOW, **options):
    source = source or Source(RSA_KEY, EC_KEY, EC384_KEY)
    mono = Clock()
    keys = web_auth.KeySet(source, algorithms=algorithms, clock=mono,
                           **{name: options.pop(name) for name in ("refresh_after", "max_age", "retry_after")
                              if name in options})
    check = web_auth.Verifier(keys, issuer=ISSUER, audience=AUDIENCE, groups=[GROUP], clock=Clock(wall), **options)
    check.mono, check.source = mono, source
    return check


def bearer(token):
    return [f"Bearer {token}"]


class Accepted(unittest.TestCase):
    def test_rsa_token(self):
        identity = verifier().verify(bearer(RSA_KEY.token(claims())))
        self.assertEqual(identity.subject, "8a5f0000-0000-4000-8000-00000000d0c5")
        self.assertEqual(identity.expires_at, NOW + 55)
        self.assertEqual(identity.key_id, "rsa-1")

    def test_ec_tokens_when_the_algorithm_is_allowed(self):
        check = verifier(algorithms=("RS256", "ES256", "ES384"))
        self.assertEqual(check.verify(bearer(EC_KEY.token(claims()))).key_id, "ec-1")
        self.assertEqual(check.verify(bearer(EC384_KEY.token(claims(), alg="ES384"))).key_id, "ec-384")
        self.assertEqual(check.verify(bearer(RSA_KEY.token(claims()))).key_id, "rsa-1")

    def test_audience_list_and_scheme_in_any_case(self):
        check = verifier()
        token = RSA_KEY.token(claims(aud=["account", AUDIENCE]))
        self.assertTrue(check.verify(bearer(token)))
        self.assertTrue(check.verify([f"bearer {token}"]))

    def test_leeway_covers_a_small_clock_difference_only(self):
        token = RSA_KEY.token(claims(exp=NOW - 3, iat=NOW - 63))
        self.assertTrue(verifier(leeway=10).verify(bearer(token)))
        with self.assertRaisesRegex(web_auth.Denied, "expired"):
            verifier(leeway=2).verify(bearer(token))


class Rejected(unittest.TestCase):
    def denied(self, authorization, pattern, check=None, kind=web_auth.Denied):
        check = check or verifier()
        with self.assertRaisesRegex(kind, pattern) as raised:
            check.verify(authorization)
        self.assertIs(type(raised.exception), kind)
        return check

    def test_no_header_and_several_headers(self):
        self.denied([], "no Authorization header")
        token = RSA_KEY.token(claims())
        self.denied([f"Bearer {token}", f"Bearer {token}"], "more than one")

    def test_wrong_signature(self):
        token = RSA_KEY.token(claims())
        head, payload, signature = token.split(".")
        flipped = signature[:-2] + ("AA" if not signature.endswith("AA") else "BB")
        self.denied(bearer(f"{head}.{payload}.{flipped}"), "signature does not verify")
        # A genuine signature over other claims: the payload was swapped.
        other = RSA_KEY.token(claims(groups=["/Nobody"])).split(".")[2]
        self.denied(bearer(f"{head}.{payload}.{other}"), "signature does not verify")
        # Signed by another key that claims the same key id.
        self.denied(bearer(RSA_OTHER.token(claims())), "signature does not verify")
        self.denied(bearer(f"{head}.{payload}.{b64(b'short')}"), "signature does not verify")
        check = verifier(algorithms=("ES256",))
        head, payload, signature = EC_KEY.token(claims()).split(".")
        self.denied(bearer(f"{head}.{payload}.{b64(bytes(64))}"), "signature does not verify", check)
        self.denied(bearer(f"{head}.{payload}.{b64(base64.urlsafe_b64decode(signature + '==')[:-1])}"),
                    "signature does not verify", check)

    def test_wrong_issuer(self):
        self.denied(bearer(RSA_KEY.token(claims(iss="https://sso.example.test/realms/other"))), "wrong issuer")
        self.denied(bearer(RSA_KEY.token(claims(iss=ISSUER + "/"))), "wrong issuer")
        self.denied(bearer(RSA_KEY.token(claims(iss=None))), "wrong issuer")

    def test_wrong_audience(self):
        self.denied(bearer(RSA_KEY.token(claims(aud="uds-other-app", azp="uds-other-app"))), "wrong audience")
        self.denied(bearer(RSA_KEY.token(claims(aud=["account"]))), "wrong audience")
        self.denied(bearer(RSA_KEY.token(claims(aud=None))), "wrong audience")
        self.denied(bearer(RSA_KEY.token(claims(aud={AUDIENCE: True}))), "wrong audience")
        # Our audience was added to a token that was issued to another client.
        self.denied(bearer(RSA_KEY.token(claims(aud=[AUDIENCE, "x"], azp="uds-other-app"))), "another client")

    def test_expired(self):
        self.denied(bearer(RSA_KEY.token(claims(exp=NOW - 11))), "expired")
        self.denied(bearer(RSA_KEY.token(claims(exp=NOW - 86400))), "expired")
        self.denied(bearer(RSA_KEY.token(claims(exp=None))), "claim exp is missing")
        self.denied(bearer(RSA_KEY.token(claims(exp="never"))), "claim exp is missing")
        self.denied(bearer(RSA_KEY.token(claims(exp=True))), "claim exp is missing")

    def test_not_yet_valid(self):
        self.denied(bearer(RSA_KEY.token(claims(nbf=NOW + 30))), "not valid yet")
        self.denied(bearer(RSA_KEY.token(claims(iat=NOW + 30))), "issued in the future")
        self.denied(bearer(RSA_KEY.token(claims(nbf="soon"))), "claim nbf is missing or not a number")

    def test_missing_group_is_forbidden_not_unauthenticated(self):
        for groups in (["/CloudTAK/Users"], [], ["/UDS Core/Admin/Sub"], ["UDS Core/Admin"], ["/uds core/admin"]):
            self.denied(bearer(RSA_KEY.token(claims(groups=groups))), "in no required group", kind=web_auth.Forbidden)
        for groups in (None, GROUP, {GROUP: True}, [GROUP, 7]):
            self.denied(bearer(RSA_KEY.token(claims(groups=groups))), "no groups claim", kind=web_auth.Forbidden)
        self.assertEqual(web_auth.Forbidden.status, 403)
        self.assertEqual(web_auth.Denied.status, 401)

    def test_alg_none(self):
        payload = b64(json.dumps(claims()).encode())
        for alg in ("none", "None", "NONE", ""):
            head = b64(json.dumps({"alg": alg, "kid": "rsa-1"}).encode())
            for signature in ("", "AA", RSA_KEY.token(claims()).split(".")[2]):
                # Without a third segment it is not even a compact JWS here.
                self.denied(bearer(f"{head}.{payload}.{signature}"),
                            "algorithm not allowed" if signature else "not a Bearer token")
        for head in ({"kid": "rsa-1"}, {"alg": ["RS256"], "kid": "rsa-1"}, {"alg": None, "kid": "rsa-1"}):
            self.denied(bearer(f"{b64(json.dumps(head).encode())}.{payload}.AA"), "algorithm not allowed")

    def test_hmac_with_the_public_key_as_the_secret(self):
        pem = token_tool.openssl("rsa", "-in", RSA_KEY.path, "-pubout")
        secrets = [pem, pem.strip(), token_tool.openssl("rsa", "-in", RSA_KEY.path, "-pubout", "-outform", "DER"),
                   token_tool.openssl("rsa", "-in", RSA_KEY.path, "-RSAPublicKey_out"),
                   json.dumps(RSA_KEY.jwk).encode(), base64.urlsafe_b64decode(RSA_KEY.jwk["n"] + "==")]
        for alg, digest in (("HS256", hashlib.sha256), ("HS384", hashlib.sha384), ("HS512", hashlib.sha512)):
            signed = f"{b64(json.dumps({'alg': alg, 'kid': 'rsa-1'}).encode())}.{b64(json.dumps(claims()).encode())}"
            for secret in secrets:
                token = f"{signed}.{b64(hmac.new(secret, signed.encode(), digest).digest())}"
                self.denied(bearer(token), "algorithm not allowed")
        # No configuration can switch a symmetric algorithm or 'none' on.
        for name in ("HS256", "none", "PS256", ""):
            with self.assertRaises(ValueError):
                web_auth.KeySet(Source(RSA_KEY), algorithms=(name,) if name else ())

    def test_algorithm_and_key_must_belong_together(self):
        # An EC key id on an RS256 token, and the reverse: the key decides.
        both = verifier(algorithms=("RS256", "ES256", "ES384"))
        self.denied(bearer(RSA_KEY.token(claims(), header={"kid": "ec-1"})), "not for this algorithm", both)
        self.denied(bearer(EC_KEY.token(claims(), header={"kid": "rsa-1"})), "not for this algorithm", both)
        self.denied(bearer(EC384_KEY.token(claims(), alg="ES256")), "not for this algorithm", both)
        # RS256 is allowed, but the JWK says this key is for RS256 only.
        wide = verifier(algorithms=("RS256", "RS384"))
        self.denied(bearer(RSA_KEY.token(claims(), alg="RS384")), "not for this algorithm", wide)
        # ES256 exists in the key set but is not in the allow-list.
        self.denied(bearer(EC_KEY.token(claims())), "algorithm not allowed")

    def test_unknown_kid_and_keys_brought_by_the_token(self):
        check = self.denied(bearer(RSA_NEW.token(claims())), "unknown key id")
        self.assertEqual(check.source.fetches, 1)
        # The token names its own key in the header: not read.
        for extra in ({"jwk": RSA_NEW.jwk}, {"jku": "https://attacker.example.test/keys"},
                      {"x5u": "https://attacker.example.test/cert"}):
            self.denied(bearer(RSA_NEW.token(claims(), header=extra)), "unknown key id")
        self.denied(bearer(RSA_KEY.token(claims(), header={"kid": None})), "no key id")
        self.denied(bearer(RSA_KEY.token(claims(), header={"kid": "k" * 300})), "no key id")
        self.denied(bearer(RSA_KEY.token(claims(), header={"crit": ["exp"]})), "critical header")

    def test_oversized_header(self):
        token = RSA_KEY.token(claims(padding="x" * 20000))
        self.assertGreater(len(token), web_auth.MAX_TOKEN_CHARS)
        check = self.denied(bearer(token), "too long")
        self.assertEqual(check.source.fetches, 0)

    def test_non_bearer_scheme(self):
        token = RSA_KEY.token(claims())
        basic = base64.b64encode(b"doug:password").decode()
        for value in (f"Basic {basic}", f"Basic {token}", token, f"Token {token}", f"Bearer  {token}", f"Bearer {token} x",
                      f"Bearer {token},Bearer {token}", f" Bearer {token}", "Bearer", "Bearer ", f"Negotiate {token}",
                      f"Bearer {token}\n", f"Bearer {token}.{token}", "Bearer a.b", f"Bearer {token}="):
            check = self.denied([value], "not a Bearer token")
            self.assertEqual(check.source.fetches, 0, value)

    def test_malformed_segments(self):
        good_head, good_payload, signature = RSA_KEY.token(claims()).split(".")
        self.denied(bearer(f"{b64(b'[1]')}.{good_payload}.{signature}"), "not a JSON object")
        self.denied(bearer(f"{b64(b'{not json')}.{good_payload}.{signature}"), "not JSON")
        self.denied(bearer(f"{b64(bytes([255, 254]))}.{good_payload}.{signature}"), "not JSON")
        self.denied(bearer(f"AAAAA.{good_payload}.{signature}"), "not base64url")
        twice = b'{"alg":"none","alg":"RS256","kid":"rsa-1"}'
        self.denied(bearer(f"{b64(twice)}.{good_payload}.{signature}"), "not JSON")
        # Payloads that are no object, or nested beyond reason, correctly signed.
        deep = b'{"a":' + b"[" * 5000 + b"]" * 5000 + b"}"
        for payload, pattern in ((b"[]", "not a JSON object"), (b'{"exp": NaN}', "not JSON"), (deep, "")):
            signed = f"{good_head}.{b64(payload)}"
            self.denied(bearer(f"{signed}.{b64(RSA_KEY.sign(signed.encode(), 'RS256'))}"), pattern)

    def test_message_never_repeats_the_token(self):
        token = RSA_OTHER.token(claims())
        with self.assertRaises(web_auth.Denied) as raised:
            verifier().verify(bearer(token))
        for part in token.split("."):
            self.assertNotIn(part[:16], str(raised.exception))


class Keys(unittest.TestCase):
    def test_fetch_failure_fails_closed(self):
        source = Source(RSA_KEY)
        source.fail = True
        check = verifier(source)
        token = bearer(RSA_KEY.token(claims()))
        with self.assertRaises(web_auth.Unavailable):
            check.verify(token)
        self.assertEqual(web_auth.Unavailable.status, 503)
        self.assertFalse(issubclass(web_auth.Unavailable, web_auth.Denied))
        self.assertFalse(check.keys.loaded())
        # No fetch storm: the next attempt waits for retry_after.
        with self.assertRaises(web_auth.Unavailable):
            check.verify(token)
        self.assertEqual(source.fetches, 1)
        check.mono.now += 10
        source.fail = False
        self.assertTrue(check.verify(token))
        self.assertEqual(source.fetches, 2)

    def test_unusable_documents_fail_closed(self):
        token = bearer(RSA_KEY.token(claims()))
        weak = token_tool.Signer(Path(KEYS.name, "b"), "rsa-weak", bits=1024)
        weak.jwk["kid"] = "rsa-1"
        encryption = dict(RSA_KEY.jwk, use="enc", alg="RSA-OAEP")
        symmetric = {"kty": "oct", "kid": "rsa-1", "k": b64(b"0123456789abcdef0123456789abcdef"), "alg": "HS256"}
        for document in (b"", b"not json", b"[]", b'{"keys": {}}', b'{"keys": []}', jwks(weak),
                         json.dumps({"keys": [encryption, symmetric]}).encode(),
                         json.dumps({"keys": [RSA_KEY.jwk, RSA_OTHER.jwk]}).encode(),
                         json.dumps({"keys": [RSA_KEY.jwk] * (web_auth.MAX_JWKS_KEYS + 1)}).encode(),
                         json.dumps({"keys": [dict(RSA_KEY.jwk, n="!!")]}).encode(),
                         json.dumps({"keys": [dict(EC_KEY.jwk, kid="rsa-1", y=EC_KEY.jwk["x"])]}).encode()):
            source = Source()
            source.document = document
            with self.assertRaises(web_auth.Unavailable, msg=document[:40]):
                verifier(source, algorithms=("RS256", "ES256")).verify(token)

    def test_keys_expire(self):
        source = Source(RSA_KEY)
        check = verifier(source, refresh_after=300, max_age=900)
        token = bearer(RSA_KEY.token(claims()))
        check.verify(token)
        check.mono.now = 299
        check.verify(token)
        self.assertEqual(source.fetches, 1)
        # Due for a refresh; the endpoint is down: the keys held are still used...
        source.fail = True
        check.mono.now = 301
        check.verify(token)
        self.assertEqual(source.fetches, 2)
        check.mono.now = 899
        check.verify(token)
        self.assertTrue(check.keys.loaded())
        # ...but never past their maximum age.
        check.mono.now = 901
        with self.assertRaises(web_auth.Unavailable):
            check.verify(token)
        self.assertFalse(check.keys.loaded())
        source.fail = False
        check.mono.now = 950
        check.verify(token)

    def test_rotation_picks_up_a_new_kid_and_drops_the_old_one(self):
        source = Source(RSA_KEY)
        check = verifier(source)
        old, new = bearer(RSA_KEY.token(claims())), bearer(RSA_NEW.token(claims()))
        check.verify(old)
        self.assertEqual(source.fetches, 1)
        # The realm rotates: both keys are published, tokens come with the new id.
        source.document = jwks(RSA_KEY, RSA_NEW)
        check.mono.now = 20
        self.assertEqual(check.verify(new).key_id, "rsa-2")
        self.assertEqual(source.fetches, 2)
        check.verify(old)
        self.assertEqual(source.fetches, 2)
        # The old key is withdrawn: after the next refresh it is no longer accepted.
        source.document = jwks(RSA_NEW)
        check.mono.now = 400
        check.verify(new)
        self.assertEqual(source.fetches, 3)
        check.mono.now = 420
        with self.assertRaisesRegex(web_auth.Denied, "unknown key id"):
            check.verify(old)

    def test_unknown_kids_cannot_make_a_fetch_storm(self):
        source = Source(RSA_KEY)
        check = verifier(source)
        check.verify(bearer(RSA_KEY.token(claims())))
        for _ in range(50):
            with self.assertRaises(web_auth.Denied):
                check.verify(bearer(RSA_NEW.token(claims())))
        self.assertEqual(source.fetches, 1)
        check.mono.now = 10
        with self.assertRaises(web_auth.Denied):
            check.verify(bearer(RSA_NEW.token(claims())))
        self.assertEqual(source.fetches, 2)

    def test_refresh_and_settings(self):
        source = Source(RSA_KEY)
        keys = web_auth.KeySet(source, clock=Clock())
        self.assertFalse(keys.loaded())
        self.assertTrue(keys.refresh())
        self.assertTrue(keys.loaded())
        source.fail = True
        self.assertFalse(keys.refresh())
        self.assertTrue(keys.loaded())
        for bad in ({"refresh_after": 0}, {"refresh_after": 10, "max_age": 5}, {"retry_after": 0}):
            with self.assertRaises(ValueError):
                web_auth.KeySet(source, **bad)
        for bad in ({"issuer": ""}, {"audience": ""}, {"groups": []}, {"groups": [""]}, {"leeway": 301}):
            with self.assertRaises(ValueError):
                web_auth.Verifier(keys, **{"issuer": ISSUER, "audience": AUDIENCE, "groups": [GROUP], **bad})


class Primitives(unittest.TestCase):
    """The two signature checks on their own, against OpenSSL's signatures."""

    def test_rsa(self):
        modulus, exponent = number(RSA_KEY.jwk["n"]), number(RSA_KEY.jwk["e"])
        message = b"signed text"
        for alg, digest in (("RS256", "sha256"), ("RS384", "sha384"), ("RS512", "sha512")):
            signature = RSA_KEY.sign(message, alg)
            self.assertTrue(web_auth.rsa_verify(modulus, exponent, signature, message, digest))
            self.assertFalse(web_auth.rsa_verify(modulus, exponent, signature, message + b".", digest))
            other = "sha384" if digest == "sha256" else "sha256"
            self.assertFalse(web_auth.rsa_verify(modulus, exponent, signature, message, other))
        signature = RSA_KEY.sign(message, "RS256")
        value = int.from_bytes(signature, "big")
        size = len(signature)
        self.assertEqual(size, 256)
        # The same number plus the modulus, a shorter or longer encoding, zero, one.
        for forged in ((value + modulus).to_bytes(size + 1, "big"), b"\0" + signature, signature[1:], signature + b"\0",
                       bytes(size), (1).to_bytes(size, "big"), (modulus - 1).to_bytes(size, "big"), b""):
            self.assertFalse(web_auth.rsa_verify(modulus, exponent, forged, message, "sha256"))
        for at in (0, 1, 100, size - 1):
            flipped = bytearray(signature)
            flipped[at] ^= 1
            self.assertFalse(web_auth.rsa_verify(modulus, exponent, bytes(flipped), message, "sha256"))
        other = number(RSA_OTHER.jwk["n"])
        self.assertFalse(web_auth.rsa_verify(other, exponent, signature, message, "sha256"))

    def test_rsa_exponent_three_forgery_with_trailing_garbage(self):
        # Bleichenbacher 2006: with e = 3, a cube root of "00 01 ff 00
        # DigestInfo hash garbage" passes every check that parses the padding
        # instead of comparing the whole encoded message.
        modulus, message = number(RSA_KEY.jwk["n"]), b"forged"
        tail = web_auth._DIGEST_INFO["sha256"] + hashlib.sha256(message).digest()
        target = int.from_bytes(b"\x00\x01\xff\x00" + tail + b"\xff" * (256 - 4 - len(tail)), "big")
        low, high = 0, 1 << 700
        while low < high:  # the largest root whose cube is not above the target
            middle = (low + high + 1) // 2
            low, high = (middle, high) if middle ** 3 <= target else (low, middle - 1)
        forged = low.to_bytes(256, "big")
        self.assertTrue((low ** 3).to_bytes(256, "big").startswith(b"\x00\x01\xff\x00" + tail))
        self.assertLess(low ** 3, modulus)
        self.assertFalse(web_auth.rsa_verify(modulus, 3, forged, message, "sha256"))

    def test_ecdsa(self):
        message = b"signed text"
        for signer, alg, digest in ((EC_KEY, "ES256", "sha256"), (EC384_KEY, "ES384", "sha384")):
            curve, x, y = signer.jwk["crv"], number(signer.jwk["x"]), number(signer.jwk["y"])
            order = web_auth.CURVES[curve][4]
            size = (order.bit_length() + 7) // 8
            for _ in range(4):
                signature = signer.sign(message, alg)
                self.assertEqual(len(signature), 2 * size)
                self.assertTrue(web_auth.ec_verify(curve, x, y, signature, message, digest))
                self.assertFalse(web_auth.ec_verify(curve, x, y, signature, message + b".", digest))
            r, s = int.from_bytes(signature[:size], "big"), int.from_bytes(signature[size:], "big")
            pack = lambda first, second: first.to_bytes(size + 1, "big")[-size:] + second.to_bytes(size + 1, "big")[-size:]  # noqa: E731
            self.assertTrue(web_auth.ec_verify(curve, x, y, pack(r, s), message, digest))
            # (r, n - s) is the other valid signature of the same message; everything else is not one.
            self.assertTrue(web_auth.ec_verify(curve, x, y, pack(r, order - s), message, digest))
            for first, second in ((0, s), (r, 0), (0, 0), (order, s), (r, order), (s, r), (r, s + 1), (r + 1, s), (1, 1)):
                self.assertFalse(web_auth.ec_verify(curve, x, y, pack(first, second), message, digest), (first, second))
            for forged in (signature[:-1], signature + b"\0", b"", bytes(2 * size), token_tool.openssl(
                    "dgst", "-" + digest, "-sign", signer.path, data=message)):
                self.assertFalse(web_auth.ec_verify(curve, x, y, forged, message, digest))
            # Another key, a point that is not on the curve, the point at infinity as (0, 0).
            self.assertFalse(web_auth.ec_verify(curve, y, x, signature, message, digest))
            self.assertFalse(web_auth.ec_verify(curve, x, y + 1, signature, message, digest))
            self.assertFalse(web_auth.ec_verify(curve, 0, 0, signature, message, digest))
            generator = web_auth.CURVES[curve][2:4]
            self.assertFalse(web_auth.ec_verify(curve, *generator, signature, message, digest))
        self.assertFalse(web_auth.ec_verify("P-384", number(EC384_KEY.jwk["x"]), number(EC384_KEY.jwk["y"]),
                                            EC_KEY.sign(message, "ES256"), message, "sha256"))

    def test_curve_constants(self):
        for name, (prime, _, gx, gy, order) in web_auth.CURVES.items():
            curve = web_auth.CURVES[name]
            self.assertTrue(web_auth.ec_on_curve(name, gx, gy))
            self.assertIsNone(web_auth._ec_multiply(curve, order, (gx, gy)))
            self.assertEqual(web_auth._ec_multiply(curve, order - 1, (gx, gy)), (gx, prime - gy))
            self.assertEqual(web_auth._ec_multiply(curve, order + 1, (gx, gy)), (gx, gy))
            double = web_auth._ec_add(curve, (gx, gy), (gx, gy))
            self.assertTrue(web_auth.ec_on_curve(name, *double))
            self.assertEqual(web_auth._ec_multiply(curve, 2, (gx, gy)), double)
            self.assertIsNone(web_auth._ec_add(curve, (gx, gy), (gx, prime - gy)))

    def test_agrees_with_the_cryptography_package_where_that_is_installed(self):
        try:
            import _cffi_backend  # noqa: F401 - without it the package below aborts with a Rust panic
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
            from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        except BaseException:  # noqa: BLE001 - a broken installation panics instead of failing to import
            self.skipTest("no usable 'cryptography' package for this interpreter")
        import random
        rng = random.Random(20261126)

        def theirs(verify):
            try:
                verify()
            except (InvalidSignature, ValueError):
                return False
            return True

        public = rsa.RSAPublicNumbers(number(RSA_KEY.jwk["e"]), number(RSA_KEY.jwk["n"])).public_key()
        point = ec.EllipticCurvePublicNumbers(number(EC_KEY.jwk["x"]), number(EC_KEY.jwk["y"]), ec.SECP256R1()).public_key()
        agreed = 0
        for round_number in range(40):
            message = rng.randbytes(rng.randrange(0, 200))
            for signer in (RSA_KEY, EC_KEY):
                signature = bytearray(signer.sign(message, "RS256" if signer is RSA_KEY else "ES256"))
                if round_number % 2:
                    signature[rng.randrange(len(signature))] ^= 1 << rng.randrange(8)
                signature = bytes(signature)
                if signer is RSA_KEY:
                    mine = web_auth.rsa_verify(number(signer.jwk["n"]), 65537, signature, message, "sha256")
                    other = theirs(lambda: public.verify(signature, message, padding.PKCS1v15(), hashes.SHA256()))
                else:
                    mine = web_auth.ec_verify("P-256", number(signer.jwk["x"]), number(signer.jwk["y"]), signature, message,
                                              "sha256")
                    der = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
                    other = theirs(lambda: point.verify(der, message, ec.ECDSA(hashes.SHA256())))
                self.assertEqual(mine, other)
                self.assertEqual(mine, round_number % 2 == 0)
                agreed += 1
        self.assertEqual(agreed, 80)


class Fetch(unittest.TestCase):
    def test_only_http_and_https(self):
        for url in ("file:///etc/passwd", "ftp://example.test/keys", "/etc/passwd", "data:application/json,{}"):
            with self.assertRaises(ValueError):
                web_auth.fetch_url(url)

    def test_never_follows_a_redirect_and_uses_no_proxy(self):
        handler = web_auth._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "http://elsewhere.example.test/"))
        text = (HERE / "web_auth.py").read_text()
        self.assertIn("urllib.request.ProxyHandler({})", text)
        self.assertIn("ssl.create_default_context(", text)
        for banned in ("CERT_NONE", "check_hostname = False", "_create_unverified_context", "import cryptography",
                       "from cryptography"):
            self.assertNotIn(banned, text)


class Environment(unittest.TestCase):
    BASE = {"TAKX_AUTH_ISSUER": ISSUER, "TAKX_AUTH_AUDIENCE": AUDIENCE,
            "TAKX_AUTH_JWKS_URL": "http://keycloak-http.keycloak.svc.cluster.local:8080/realms/uds/protocol/openid-connect/certs",
            "TAKX_AUTH_GROUPS": json.dumps([GROUP])}

    def test_complete_settings(self):
        check = web_auth.from_environment(self.BASE)
        self.assertEqual((check.issuer, check.audience, check.groups, check.groups_claim, check.leeway),
                         (ISSUER, AUDIENCE, frozenset([GROUP]), "groups", 10.0))
        self.assertEqual(check.keys.algorithms, ("RS256",))
        check = web_auth.from_environment({**self.BASE, "TAKX_AUTH_ALGORITHMS": "RS256,ES256",
                                           "TAKX_AUTH_LEEWAY_SECONDS": "0", "TAKX_AUTH_GROUPS_CLAIM": "roles"})
        self.assertEqual((check.keys.algorithms, check.leeway, check.groups_claim), (("RS256", "ES256"), 0.0, "roles"))

    def test_incomplete_or_wrong_settings_do_not_start(self):
        for name in self.BASE:
            with self.assertRaisesRegex(ValueError, name):
                web_auth.from_environment({key: value for key, value in self.BASE.items() if key != name})
        for change in ({"TAKX_AUTH_GROUPS": "/UDS Core/Admin"}, {"TAKX_AUTH_GROUPS": "[]"}, {"TAKX_AUTH_GROUPS": '"x"'},
                       {"TAKX_AUTH_JWKS_URL": "file:///keys.json"}, {"TAKX_AUTH_ALGORITHMS": "HS256"},
                       {"TAKX_AUTH_ALGORITHMS": "none"}, {"TAKX_AUTH_LEEWAY_SECONDS": "3600"},
                       {"TAKX_AUTH_JWKS_REFRESH_SECONDS": "soon"}, {"TAKX_AUTH_JWKS_MAX_AGE_SECONDS": "1"}):
            with self.assertRaises(ValueError, msg=change):
                web_auth.from_environment({**self.BASE, **change})


if __name__ == "__main__":
    unittest.main()
