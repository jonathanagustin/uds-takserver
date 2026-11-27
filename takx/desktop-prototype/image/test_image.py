# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline tests for the scripts of the TAKX desktop image.

No container, no network, nothing written outside a temporary directory.
Tests that need keytool and openssl are skipped where those are missing.
Run: python3 -B -m unittest takx/desktop-prototype/image/test_image.py
"""
import datetime
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
import zlib

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "verify"))

import check_native_closure  # noqa: E402
import gen_notices  # noqa: E402
import rfb_probe  # noqa: E402
import scan_image  # noqa: E402
import stage_identity  # noqa: E402
import verify_tree  # noqa: E402


def elf(machine=62, soname=None, needed=(), runpath=None):
    """A minimal little-endian ELF64 shared object with a dynamic section."""
    strings = b"\0"
    offsets = {}
    for text in list(needed) + [soname, runpath]:
        if text is not None and text not in offsets:
            offsets[text] = len(strings)
            strings += text.encode() + b"\0"
    tags = [(1, offsets[name]) for name in needed]
    if soname:
        tags.append((14, offsets[soname]))
    if runpath:
        tags.append((29, offsets[runpath]))
    header, program = 64, 56
    strtab_at = header + 2 * program
    dynamic_at = strtab_at + len(strings)
    tags += [(5, strtab_at), (0, 0)]
    dynamic = b"".join(struct.pack("<qQ", tag, value) for tag, value in tags)
    total = dynamic_at + len(dynamic)
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8)
    head = ident + struct.pack("<HHIQQQIHHHHHH", 3, machine, 1, 0, header, 0, 0, header, program, 2, 0, 0, 0)
    load = struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, total, total, 0x1000)
    dyn = struct.pack("<IIQQQQQQ", 2, 6, dynamic_at, dynamic_at, dynamic_at, len(dynamic), len(dynamic), 8)
    return head + load + dyn + strings + dynamic


class VerifyTreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "boot").mkdir()
        (self.tmp / "boot/a.jar").write_bytes(b"alpha")
        (self.tmp / "b.xml").write_bytes(b"beta")
        files = [{"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                 for path, data in (("boot/a.jar", b"alpha"), ("b.xml", b"beta"))]
        (self.tmp / "manifest.json").write_text(json.dumps({"files": files, "takx": {"source_revision": "r"}}))
        self.digest = hashlib.sha256((self.tmp / "manifest.json").read_bytes()).hexdigest()

    def test_matching_tree_passes_with_and_without_the_pinned_digest(self):
        self.assertEqual(verify_tree.verify(str(self.tmp))[1], [])
        facts, problems = verify_tree.verify(str(self.tmp), self.digest)
        self.assertEqual((problems, facts["files"], facts["bytes"]), ([], 2, 9))

    def test_wrong_manifest_digest_is_a_problem(self):
        self.assertIn("expected " + "0" * 64, verify_tree.verify(str(self.tmp), "0" * 64)[1][0])

    def test_changed_extra_missing_and_linked_files_are_problems(self):
        (self.tmp / "b.xml").write_bytes(b"BETA")
        (self.tmp / "extra.txt").write_bytes(b"x")
        (self.tmp / "boot/a.jar").unlink()
        os.symlink("b.xml", self.tmp / "link.xml")
        text = "\n".join(verify_tree.verify(str(self.tmp))[1])
        for expected in ("SHA-256 of b.xml differs", "file not in the manifest: extra.txt",
                         "missing file: boot/a.jar", "symbolic link in the tree: link.xml"):
            self.assertIn(expected, text)

    def test_command_line_exit_status(self):
        script = str(HERE / "verify_tree.py")
        good = subprocess.run([sys.executable, "-I", script, str(self.tmp), self.digest], capture_output=True, text=True)
        self.assertEqual((good.returncode, json.loads(good.stdout)["status"]), (0, "ok"))
        bad = subprocess.run([sys.executable, "-I", script, str(self.tmp), "f" * 64], capture_output=True, text=True)
        self.assertEqual((bad.returncode, json.loads(bad.stdout)["status"]), (1, "failed"))


class NativeClosureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def tree(self, engine_runpath="/build/dir/lib", extra=()):
        with zipfile.ZipFile(self.tmp / "kernel.jar", "w") as archive:
            archive.writestr("libs/linux64/libdep.so", elf(soname="libdep.so.4", needed=["libc.so.6"]))
            archive.writestr("libs/linux64/libengine.so",
                             elf(soname="libengine.so", needed=["libdep.so.4", "libjawt.so"] + list(extra),
                                 runpath=engine_runpath))
            archive.writestr("Android/x86_64/libserial.so", elf(needed=["liblog.so"]))
            archive.writestr("Linux/armv8_64/libserial.so", elf(machine=183, needed=["libc.so.6"]))
            archive.writestr("notes/readme.so.txt", b"not a library")
        (self.tmp / "native").mkdir(exist_ok=True)
        (self.tmp / "native/libloose.so").write_bytes(elf(needed=["libc.so.6", "libm.so.6"]))
        return str(self.tmp)

    def check(self, tree, exists=lambda path: False):
        return check_native_closure.check(tree, {"libc.so.6": "/lib/libc.so.6", "libm.so.6": "/lib/libm.so.6"},
                                          {"libjawt.so": "/jdk/lib/libjawt.so"}, ("kernel.jar!/Android/",), exists=exists)

    def test_parse_elf_reads_the_dynamic_section(self):
        facts = check_native_closure.parse_elf(elf(soname="libx.so.1", needed=["liby.so.2"], runpath="$ORIGIN:/abs"))
        self.assertEqual((facts["machine"], facts["soname"], facts["needed"], facts["runpath"]),
                         ("x86-64", "libx.so.1", ["liby.so.2"], ["$ORIGIN", "/abs"]))
        with self.assertRaises(check_native_closure.NotElf):
            check_native_closure.parse_elf(b"PK\x03\x04" + bytes(80))

    def test_bundled_soname_jvm_and_system_resolve(self):
        report = self.check(self.tree())
        self.assertEqual(report["status"], "ok", report["problems"])
        engine = next(item for item in report["libraries"] if item["library"].endswith("libengine.so"))
        self.assertEqual(engine["needed"], {"libdep.so.4": "bundled:libdep.so", "libjawt.so": "jvm:/jdk/lib/libjawt.so"})
        self.assertEqual(report["counts"]["x86_64"], 3)

    def test_other_machine_and_other_os_are_foreign_not_checked(self):
        reasons = {item["library"]: item["reason"] for item in self.check(self.tree())["foreign"]}
        self.assertEqual(reasons, {"kernel.jar!/Android/x86_64/libserial.so": "another operating system",
                                   "kernel.jar!/Linux/armv8_64/libserial.so": "machine aarch64"})

    def test_unresolved_needed_fails(self):
        report = self.check(self.tree(extra=["libmissing.so.9"]))
        self.assertEqual(report["status"], "failed")
        self.assertIn("DT_NEEDED libmissing.so.9", report["problems"][0])

    def test_dead_runpath_is_recorded_and_an_existing_one_fails(self):
        dead = self.check(self.tree())
        self.assertEqual([item["path"] for item in dead["dead_search_paths"]], ["/build/dir/lib"])
        live = self.check(self.tree(), exists=lambda path: path == "/build/dir/lib")
        self.assertEqual(live["status"], "failed")
        self.assertIn("RUNPATH directory /build/dir/lib exists", live["problems"][0])

    def test_origin_runpath_is_neither_dead_nor_a_problem(self):
        report = self.check(self.tree(engine_runpath="$ORIGIN"))
        self.assertEqual((report["status"], report["dead_search_paths"]), ("ok", []))

    def test_ldconfig_listing_is_parsed_for_x86_64_only(self):
        listing = ("2 libs found\n\tlibm.so.6 (libc6,x86-64) => /lib/x86_64-linux-gnu/libm.so.6\n"
                   "\tlibm.so.6 (libc6) => /lib32/libm.so.6\n\tlibold.so.1 (libc6) => /lib32/libold.so.1\n")
        self.assertEqual(check_native_closure.system_sonames(listing), {"libm.so.6": "/lib/x86_64-linux-gnu/libm.so.6"})


def package(entries):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return data.getvalue()


def pref(connect="takserver-stream.uds.dev:8089:ssl", count=1):
    entry = f'<entry key="connectString0" class="class java.lang.String">{connect}</entry>\n'
    return ("<preferences><preference>\n" + entry * count + "</preference></preferences>\n").encode()


GOOD = {"MANIFEST/manifest.xml": b"<m/>", "certs/server.pref": pref(), "certs/truststore.p12": b"t",
        "certs/alpha.p12": b"c"}


class IdentityPackageTest(unittest.TestCase):
    def test_good_package_is_read(self):
        entries, client = stage_identity.read_package(package(GOOD))
        self.assertEqual((client, sorted(entries)), ("certs/alpha.p12", sorted(GOOD)))

    def test_bad_packages_are_rejected(self):
        cases = {
            "not a zip archive": b"hello",
            "lacks certs/truststore.p12": package({k: v for k, v in GOOD.items() if k != "certs/truststore.p12"}),
            "unexpected entries": package({**GOOD, "../evil": b"x"}),
            "exactly one client store": package({**GOOD, "certs/beta.p12": b"c"}),
            "larger than 262144 bytes": package({**GOOD, "certs/alpha.p12": bytes(300000)}),
            "larger than 1048576 bytes": bytes(stage_identity.MAX_PACKAGE + 1),
        }
        for message, data in cases.items():
            with self.subTest(message), self.assertRaisesRegex(stage_identity.Rejected, message):
                stage_identity.read_package(data)

    def test_connect_string(self):
        self.assertEqual(stage_identity.connect_string(pref()), ("takserver-stream.uds.dev", 8089))
        self.assertEqual(stage_identity.connect_string(pref(), "TAKSERVER-stream.uds.dev")[1], 8089)
        for text, message in ((pref("takserver:8089:tcp"), "not HOST:PORT:ssl"), (pref("a b:1:ssl"), "not HOST:PORT:ssl"),
                              (pref("takserver:0:ssl"), "no valid port"), (pref(count=2), "exactly one"),
                              (pref(count=0), "exactly one")):
            with self.subTest(text), self.assertRaisesRegex(stage_identity.Rejected, message):
                stage_identity.connect_string(text)
        with self.assertRaisesRegex(stage_identity.Rejected, "expected 'other.example'"):
            stage_identity.connect_string(pref(), "other.example")

    def test_rewrite_changes_only_the_host(self):
        order = list(stage_identity.FIXED) + ["certs/alpha.p12"]
        data = stage_identity.rewrite_package(GOOD, order, "takserver")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.namelist(), order)
            self.assertEqual(archive.read("certs/server.pref"), pref("takserver:8089:ssl"))
            self.assertEqual(archive.read("certs/alpha.p12"), b"c")
        with self.assertRaisesRegex(stage_identity.Rejected, "not a host name"):
            stage_identity.rewrite_package(GOOD, order, "bad host<")

    def test_no_secret_stages_nothing_and_one_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret, out, lines = Path(tmp, "secret"), Path(tmp, "out"), []
            out.mkdir()
            self.assertEqual(stage_identity.stage(str(secret), str(out), log=lines.append), [])
            self.assertIn("no identity staged", lines[0])
            secret.mkdir()
            (secret / "password").write_text("0123456789abcdef")
            with self.assertRaisesRegex(stage_identity.Rejected, "not both"):
                stage_identity.stage(str(secret), str(out), log=lines.append)
            self.assertEqual(list(out.iterdir()), [])


@unittest.skipUnless(shutil.which("keytool") and shutil.which("openssl"), "needs keytool and openssl")
class IdentityStageTest(unittest.TestCase):
    PASSWORD = "0123456789abcdef0123456789abcdef"

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.tmp)
        run = lambda *args: subprocess.run(args, check=True, capture_output=True, cwd=cls.tmp)
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=unit-ca",
            "-addext", "basicConstraints=critical,CA:TRUE", "-keyout", "ca.key", "-out", "ca.pem")
        run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=unit-client", "-keyout", "c.key", "-out", "c.csr")
        run("openssl", "x509", "-req", "-in", "c.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
            "-days", "1", "-out", "c.pem")
        run("openssl", "pkcs12", "-export", "-name", "unit-client", "-inkey", "c.key", "-in", "c.pem",
            "-certfile", "ca.pem", "-passout", "pass:" + cls.PASSWORD, "-out", "client.p12")
        run("openssl", "pkcs12", "-export", "-name", "unit-client", "-inkey", "c.key", "-in", "c.pem",
            "-passout", "pass:" + cls.PASSWORD, "-out", "client-no-ca.p12")
        run("openssl", "pkcs12", "-export", "-nokeys", "-name", "tak-ca", "-in", "ca.pem",
            "-passout", "pass:" + cls.PASSWORD, "-out", "truststore.p12")

    def secret(self, name, client="client.p12", password=None):
        folder = self.tmp / name
        folder.mkdir()
        entries = {**GOOD, "certs/truststore.p12": (self.tmp / "truststore.p12").read_bytes()}
        del entries["certs/alpha.p12"]
        entries["certs/unit-client.p12"] = (self.tmp / client).read_bytes()
        (folder / "package.zip").write_bytes(package(entries))
        (folder / "password").write_text(password or self.PASSWORD)
        out = self.tmp / (name + "-out")
        out.mkdir()
        return str(folder), out

    def test_valid_identity_is_staged_read_only_without_secrets_in_the_log(self):
        secret, out = self.secret("valid")
        lines = []
        staged = stage_identity.stage(secret, str(out), expect_host="takserver-stream.uds.dev",
                                      connect_host="takserver", log=lines.append)
        self.assertEqual(sorted(staged), sorted(stage_identity.STAGED))
        for name in staged:
            self.assertEqual(os.stat(out / name).st_mode & 0o777, 0o400, name)
        self.assertEqual((out / "package.zip").read_bytes(), Path(secret, "package.zip").read_bytes())
        text = "\n".join(lines)
        self.assertNotIn(self.PASSWORD, text)
        self.assertNotIn("BEGIN", text)
        self.assertIn("CN=unit-client", text)
        with zipfile.ZipFile(out / "takx-package.zip") as archive:
            self.assertIn(b">takserver:8089:ssl<", archive.read("certs/server.pref"))
        listing = subprocess.run(["keytool", "-list", "-storetype", "PKCS12", "-keystore", str(out / "truststore-java.p12"),
                                  "-storepass", self.PASSWORD], capture_output=True, text=True).stdout
        self.assertIn("trustedCertEntry", listing)

    def test_rejections_leave_nothing_staged(self):
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
        cases = (("wrong-password", {"password": "ffffffffffffffff0000000000000000"}, {}, "cannot be opened"),
                 ("no-ca", {"client": "client-no-ca.p12"}, {}, "does not carry the self-signed CA"),
                 ("expired", {}, {"now": future}, "outside its validity period"),
                 ("wrong-host", {}, {"expect_host": "elsewhere.example"}, "expected 'elsewhere.example'"))
        for name, secret_options, stage_options, message in cases:
            with self.subTest(name):
                secret, out = self.secret(name, **secret_options)
                with self.assertRaisesRegex(stage_identity.Rejected, message) as caught:
                    stage_identity.stage(secret, str(out), log=lambda text: None, **stage_options)
                self.assertNotIn(self.PASSWORD, str(caught.exception))
                self.assertEqual(list(out.iterdir()), [])


class NoticesTest(unittest.TestCase):
    def test_every_known_origin_maps_to_a_component(self):
        cases = {
            "public atak-civ 9f6893dd atak/ATAK/app/src/main/assets": "atak-civ-assets",
            "public Maven artifact org.json:json:20240303 (https://repo1.maven.org/maven2)": "maven",
            "official TAKX 5.7 deployment/bin": "takx",
            "takx/desktop-prototype client stage (official TAKX 5.7 sources)": "takx",
            "Kernel 8.30.5 dependency JAR frozen by takx/kernel-java": "kernel-dependency-jars",
            "Kernel 8.30.5 + project patches (texture, render-context), unqualified": "kernel",
            "desktop-prototype Kernel Java overlay: 7 Kernel classes": "kernel-java-overlay",
            "CommonCommo 1.17.2 patched JNI library (takx/native-commoncommo)": "commoncommo",
            "NASA WorldWind Java v2.2.1 + interop profile (takx/worldwind-java --interop)": "worldwind",
            "public source rebuild by takx/ui-java (substitute for a private artifact)": "ui-java",
            "hand-written JBoss Modules descriptor": "uds-takserver",
        }
        for origin, component in cases.items():
            self.assertEqual(gen_notices.component_of({"path": "p", "origin": origin}), component)
            self.assertIn(component, gen_notices.COMPONENTS)

    def test_unknown_origin_is_an_error_not_a_default(self):
        with self.assertRaisesRegex(ValueError, "no component rule for x.jar"):
            gen_notices.component_of({"path": "x.jar", "origin": "somewhere else"})

    def test_labels_name_the_public_versions_and_never_the_private_ones(self):
        dockerfile = (HERE / "Dockerfile").read_text()
        labels = dockerfile[dockerfile.index("LABEL "):]
        for text in (gen_notices.LABEL, labels, (HERE / "NOTICE.md").read_text()):
            self.assertIn("Kernel 8.30.5 + project patches", text)
            self.assertIn("CommonCommo 1.17.2 patched", text)
            for private in ("8.57", "1.20.0", "TAKX-4"):
                self.assertNotIn(private, text)

    def test_patch_is_found_in_the_working_tree_only_with_the_recorded_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.patch").write_bytes(b"--- a\n+++ b\n")
            digest = hashlib.sha256(b"--- a\n+++ b\n").hexdigest()
            self.assertEqual(gen_notices.find_patch(tmp, "a.patch", digest), (b"--- a\n+++ b\n", "working tree"))
            self.assertEqual(gen_notices.find_patch(tmp, "a.patch", "0" * 64), (None, None))

    def test_licence_index_has_one_row_per_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = []
            target = str(Path(tmp, "LICENSES", "providers", "p", "LICENSE"))
            source = Path(tmp, "LICENSE.in")
            source.write_bytes(b"one text")
            gen_notices.copy_licence(str(source), target, index, "provider p (source/LICENSE)")
            # The same bytes under the same name again: one file, both origins.
            gen_notices.write_licence(b"one text", target, index, "provider p (licenses/LICENSE)")
            # Other bytes under that name: a second file, nothing overwritten.
            gen_notices.write_licence(b"another text", target, index, "provider p (other/LICENSE)")
            gen_notices.write_licence(b"another text", target, index, "provider p (third/LICENSE)")
            self.assertEqual([path for path, _ in index], [target, target + ".2"])
            self.assertEqual(index[0][1], "provider p (source/LICENSE); provider p (licenses/LICENSE)")
            self.assertEqual(index[1][1], "provider p (other/LICENSE); provider p (third/LICENSE)")
            self.assertEqual(Path(target).read_bytes(), b"one text")
            self.assertEqual(Path(target + ".2").read_bytes(), b"another text")
            self.assertEqual(sorted(path.name for path in Path(target).parent.iterdir()), ["LICENSE", "LICENSE.2"])


class ProbeTest(unittest.TestCase):
    def test_png_round_trip_and_luma(self):
        frame = bytes([10, 20, 30, 0]) * 4 + bytes([255, 255, 255, 0]) * 4  # BGRX, 4x2
        data = rfb_probe.png(frame, 4, 2)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", data[16:24]), (4, 2))
        length, = struct.unpack(">I", data[33:37])
        rows = zlib.decompress(data[41:41 + length])
        self.assertEqual(rows[:4], b"\0" + bytes([30, 20, 10]))
        facts = rfb_probe.luma_facts(frame, stride=1)
        self.assertEqual((facts["luma_min"], facts["luma_max"], facts["distinct_colours_in_sample"]), (21, 255, 2))

    def test_websocket_frames_are_reassembled_and_pings_answered(self):
        class Fake:
            def __init__(self, data):
                self.data, self.sent = data, b""

            def recv(self, count):
                block, self.data = self.data[:count], self.data[count:]
                return block

            def sendall(self, data):
                self.sent += data

        payload = bytes(range(200))
        wire = bytes([0x82, 3]) + b"RFB" + bytes([0x89, 2]) + b"hi" + bytes([0x82, 126]) + struct.pack(">H", 200) + payload
        sock = Fake(wire)
        stream = rfb_probe.WebSocket(sock)
        self.assertEqual(stream.recv(5), b"RFB" + payload[:2])
        self.assertEqual(sock.sent[0], 0x8A)
        stream.send(b"x")
        self.assertEqual(sock.sent[-7] & 0x0F, 2)
        self.assertTrue(sock.sent[-6] & 0x80, "client frames must be masked")


def tar_bytes(files, compress=False):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return gzip.compress(data.getvalue()) if compress else data.getvalue()


class ScanImageTest(unittest.TestCase):
    PUBLIC, PRIVATE = bytes(range(200)) * 2, bytes(reversed(range(200))) * 2

    def save(self, extra):
        base = {"tools/jdk/lib/security/cacerts": b"JKS" + self.PUBLIC + b"tail",
                "var/lib/dpkg/info/libgnutls30.md5sums":
                    (hashlib.md5(self.key()).hexdigest() + "  usr/lib/libgnutls.so.30\n").encode(),
                "usr/lib/libgnutls.so.30": self.key(),
                "usr/bin/parser": b"-----BEGIN PRIVATE" + b" KEY-----\x00constant only"}
        return tar_bytes({"blobs/sha256/aaa": tar_bytes(base, compress=True), "blobs/sha256/bbb": tar_bytes(extra),
                          "index.json": b"{}"})

    @staticmethod
    def key():
        return b"x-----BEGIN RSA PRIVATE" + b" KEY-----\n" + b"A" * 64 + b"\n"

    def run_scan(self, extra):
        with tarfile.open(fileobj=io.BytesIO(self.save(extra))) as save:
            stores, packaged = scan_image.trust_stores(save)
        certificates = [{"der": self.PUBLIC, "pem_line": None}, {"der": self.PRIVATE, "pem_line": b"UFJJVkFURQ"}]
        private = [item for item in certificates if not any(item["der"] in store for store in stores)]
        with tarfile.open(fileobj=io.BytesIO(self.save(extra))) as save:
            return private, scan_image.scan(save, private, packaged)

    def test_clean_image_passes_and_public_roots_are_not_searched_for(self):
        private, found = self.run_scan({"opt/app/readme.txt": b"hello"})
        self.assertEqual([item["der"] for item in private], [self.PRIVATE])
        self.assertEqual((found["private_ca_hits"], found["private_keys"], found["build_paths"], found["layers"]),
                         ([], [], [], 2))
        self.assertEqual(found["pem_keys_in_unmodified_debian_package_files"],
                         [{"path": "usr/lib/libgnutls.so.30", "package": "libgnutls30"}])

    def test_proxy_ca_key_store_private_key_and_build_path_are_found(self):
        _private, found = self.run_scan({
            "etc/ssl/extra.der": b"..." + self.PRIVATE, "etc/ssl/extra.pem": b"line\nUFJJVkFURQ\n",
            "opt/app/id.p12": b"p", "opt/app/server.pem": self.key(), "var/lib/takx-proto/P/x": b"x"})
        self.assertEqual(sorted(hit["path"] for hit in found["private_ca_hits"]), ["etc/ssl/extra.der", "etc/ssl/extra.pem"])
        self.assertEqual([item["path"] for item in found["private_keys"]], ["opt/app/server.pem"])
        self.assertEqual([item["path"] for item in found["key_stores"]], ["opt/app/id.p12"])
        self.assertEqual([item["path"] for item in found["build_paths"]], ["var/lib/takx-proto/P/x"])


class ShellTest(unittest.TestCase):
    def run_script(self, name, *args, env=None):
        return subprocess.run(["bash", str(HERE / name), *args], capture_output=True, text=True, timeout=30,
                              env={"PATH": os.environ["PATH"], **(env or {})})

    def test_entrypoint_refuses_an_unknown_mode(self):
        done = self.run_script("entrypoint.sh", "shell")
        self.assertEqual(done.returncode, 64)
        self.assertIn("usage: entrypoint.sh desktop|web|identity", done.stderr)

    def test_healthcheck_fails_without_a_jvm_and_with_a_dead_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"TAKX_RUN_DIR": tmp}
            for mode in ("started", "ready", "live"):
                done = self.run_script("healthcheck.sh", mode, env=env)
                self.assertEqual(done.returncode, 1, mode)
                self.assertIn("no " + tmp + "/session/jvm.pid", done.stderr)
            os.mkdir(os.path.join(tmp, "session"))
            child = subprocess.Popen(["true"])
            child.wait()
            Path(tmp, "session/jvm.pid").write_text(str(child.pid))
            done = self.run_script("healthcheck.sh", "live", env=env)
            self.assertEqual(done.returncode, 1)
            self.assertIn("is not a running JVM", done.stderr)
            self.assertEqual(self.run_script("healthcheck.sh", "bogus", env=env).returncode, 64)

    def test_web_mode_and_its_probe_need_the_bridge_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"TAKX_RUN_DIR": tmp}
            os.mkdir(os.path.join(tmp, "vnc"))
            done = self.run_script("entrypoint.sh", "web", env=env)
            self.assertEqual(done.returncode, 64)
            self.assertIn(tmp + "/web is not a writable directory", done.stderr)
            for name, value in (("TAKX_WEB_LISTEN", "everywhere"), ("TAKX_WEB_HEARTBEAT", "often")):
                done = self.run_script("entrypoint.sh", "web", env={**env, name: value})
                self.assertEqual(done.returncode, 64, name)
                self.assertIn(name, done.stderr)
            done = self.run_script("healthcheck.sh", "web", env=env)
            self.assertEqual(done.returncode, 1)
            self.assertIn("no websockify socket", done.stderr)

    def test_the_bridge_never_starts_websockify_on_a_tcp_port(self):
        text = (HERE / "entrypoint.sh").read_text()
        (command,) = [line for line in text.splitlines() if line.lstrip().startswith("websockify ")]
        self.assertIn('--unix-listen="${socket}" --unix-listen-mode=0600', command)
        self.assertEqual(text.count('python3 -I "${app}/web_gate.py"'), 1)
        # The probe asks the gate for the one path that needs no token.
        self.assertIn("/healthz", (HERE / "healthcheck.sh").read_text())
        self.assertNotIn("vnc.html", (HERE / "healthcheck.sh").read_text())

    def seed(self, tree, session, mode=None):
        """Run seed_program_properties of entrypoint.sh on its own."""
        text = (HERE / "entrypoint.sh").read_text()
        function = text[text.index("seed_program_properties() {"):text.index("\ndesktop() {")]
        script = ('set -euo pipefail\nlog() { echo "log: $*" >&2; }\ndie() { echo "die: $*" >&2; exit 64; }\n'
                  f'tree={tree}\nsession={session}\n{function}\nseed_program_properties\n')
        env = {"PATH": os.environ["PATH"], **({"TAKX_LOCAL_INPUTS": mode} if mode else {})}
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, env=env)

    def test_default_inputs_are_switched_off_in_a_new_session_only(self):
        inputs = "".join(f'<node name="In {n}"><map><entry key="uri" value="{uri}"/>'
                         f'<entry key="enabled" value="true"/></map></node>\n'
                         for n, uri in enumerate(("udp://0.0.0.0:4242", "tcp://0.0.0.0:4242", "tcp://0.0.0.0:8087")))
        defaults = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE preferences SYSTEM "x.dtd">\n<preferences><root>\n'
                    '<node name="Other"><map><entry key="enabled" value="true"/></map></node>\n'
                    f'<node name="DefaultInputs">\n{inputs}</node>\n<node name="DefaultOutputs">\n'
                    '<node name="Out"><map><entry key="uri" value="udp://224.10.10.1:17012"/>'
                    '<entry key="enabled" value="true"/></map></node>\n</node></root></preferences>\n')
        with tempfile.TemporaryDirectory() as tmp:
            tree, session = Path(tmp, "tree"), Path(tmp, "session")
            (tree / "conf").mkdir(parents=True)
            (tree / "conf/DefaultProgramProperties.xml").write_text(defaults)
            target = session / "ProjectData/ProjectData/LocalProgramProperties.xml"
            done = self.seed(tree, session)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("3 default inputs switched off", done.stderr)
            # The three inputs and nothing else: the outputs and other nodes keep their value.
            self.assertEqual(target.read_text(), defaults.replace(
                'value="true"/></map></node>\n<node name="In', 'value="false"/></map></node>\n<node name="In').replace(
                'value="true"/></map></node>\n</node>\n<node name="DefaultOutputs">',
                'value="false"/></map></node>\n</node>\n<node name="DefaultOutputs">'))
            self.assertEqual(target.read_text().count('value="false"'), 3)
            self.assertTrue(target.read_text().startswith(defaults[:80]))
            # A second start, or a volume with a project: the file is the user's.
            target.write_text("edited by the user")
            done = self.seed(tree, session)
            self.assertEqual((done.returncode, target.read_text()), (0, "edited by the user"))
            self.assertIn("left as it is", done.stderr)
            target.unlink()
            done = self.seed(tree, session, mode="default")
            self.assertEqual(done.returncode, 0)
            self.assertFalse(target.exists())
            self.assertEqual(self.seed(tree, session, mode="maybe").returncode, 64)
            # A defaults file of another shape is an error, not a guess.
            (tree / "conf/DefaultProgramProperties.xml").write_text(defaults.replace("DefaultOutputs", "Outputs"))
            done = self.seed(tree, session)
            self.assertEqual(done.returncode, 64)
            self.assertFalse(target.exists())

    def test_probe_sends_the_token_only_when_one_is_given(self):
        saved = os.environ.pop("TAKX_PROBE_BEARER", None)
        try:
            self.assertEqual(rfb_probe.bearer(), b"")
            os.environ["TAKX_PROBE_BEARER"] = "a.b.c"
            self.assertEqual(rfb_probe.bearer(), b"Authorization: Bearer a.b.c\r\n")
        finally:
            os.environ.pop("TAKX_PROBE_BEARER", None)
            if saved is not None:
                os.environ["TAKX_PROBE_BEARER"] = saved

    def test_no_script_disables_tls_verification_or_embeds_key_material(self):
        for path in sorted(HERE.rglob("*")):
            if path.is_file() and path.name != "test_image.py" and "__pycache__" not in path.parts:
                text = path.read_text(errors="replace")
                for banned in ("--insecure", "curl -k", "verify=False", "NODE_TLS_REJECT_UNAUTHORIZED",
                               "PRIVATE KEY-----", "--no-check-certificate"):
                    self.assertNotIn(banned, text, f"{path.name} contains {banned}")

    def test_dockerfile_never_copies_the_ca_and_ends_as_a_non_root_user(self):
        text = (HERE / "Dockerfile").read_text()
        self.assertNotIn("ca-bundle", text)
        self.assertIn("--mount=type=secret,id=ca,required=false", text)
        self.assertNotIn("patchelf", text.replace("no patchelf", ""))
        instructions = [line for line in text.splitlines() if line and not line.startswith(("#", " "))]
        self.assertEqual([line for line in instructions if line.startswith("USER")], ["USER 10001:10001"])
        self.assertTrue(instructions[-2].startswith("ENTRYPOINT [\"/usr/bin/tini\""))


if __name__ == "__main__":
    unittest.main()
