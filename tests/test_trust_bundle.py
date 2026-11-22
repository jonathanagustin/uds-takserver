# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Verify rendered trust mounts and Node TLS with two independent CAs."""

import http.server
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def render(chart):
    result = subprocess.run(["helm", "template", chart, str(ROOT / "charts" / chart)],
                            text=True, capture_output=True, check=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


@unittest.skipUnless(shutil.which("helm"), "helm CLI is not installed")
class TrustBundleChartTests(unittest.TestCase):
    def test_both_bundles_reload_only_their_consumers(self):
        for chart, selector in (("takserver", "uds.dev/oidc-reload=takserver"),
                                ("cloudtak", "app=cloudtak")):
            with self.subTest(chart=chart):
                package = next(d for d in render(chart) if d["kind"] == "Package")
                config = package["spec"]["caBundle"]["configMap"]
                self.assertEqual(config["name"], "uds-trust-bundle")
                self.assertEqual(config["key"], "ca-bundle.pem")
                self.assertEqual(config["labels"], {"uds.dev/pod-reload": "true"})
                self.assertEqual(config["annotations"], {"uds.dev/pod-reload-selector": selector})

    def test_cloudtak_reads_the_merged_file_without_admin_key_access(self):
        deployment = next(d for d in render("cloudtak")
                          if d["kind"] == "Deployment" and d["metadata"]["name"] == "cloudtak")
        spec = deployment["spec"]["template"]["spec"]
        bundle = next(v for v in spec["volumes"] if v["name"] == "uds-trust")
        self.assertEqual(bundle["configMap"], {"name": "uds-trust-bundle", "optional": True})
        init = spec["initContainers"][0]
        self.assertEqual(init["name"], "merge-trust")
        self.assertEqual(init["command"], ["node", "--input-type=module", "-e"])
        self.assertNotIn("tak-admin", {m["name"] for m in init["volumeMounts"]})
        for container in spec["containers"][:2]:
            env = {e["name"]: e.get("value") for e in container["env"]}
            self.assertEqual(env["NODE_EXTRA_CA_CERTS"], "/var/run/combined-trust/ca-bundle.pem")
            mount = next(m for m in container["volumeMounts"] if m["name"] == "combined-trust")
            self.assertTrue(mount["readOnly"])
            self.assertNotIn("tak-admin", {m["name"] for m in container["volumeMounts"]})
        sso = spec["containers"][1]
        self.assertEqual(sso["name"], "sso")
        probe = sso["readinessProbe"]
        self.assertTrue(all(isinstance(arg, str) for arg in probe["exec"]["command"]))
        self.assertEqual(probe["exec"]["command"][:2], ["node", "-e"])
        self.assertNotIn("wget", probe["exec"]["command"])
        self.assertGreaterEqual(probe["timeoutSeconds"], 3)
        self.assertEqual(init["args"][0].strip(),
                         (ROOT / "charts/cloudtak/files/merge-trust.mjs").read_text().strip())


class QuietHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args):
        pass


@unittest.skipUnless(shutil.which("node") and shutil.which("openssl"), "Node and OpenSSL are required")
class TrustBundleTLS(unittest.TestCase):
    def test_node_trusts_both_cas_and_slim_core_still_works(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            servers = []
            try:
                for name in ("tak", "core"):
                    cert, key = root / f"{name}.pem", root / f"{name}.key"
                    subprocess.run([
                        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1",
                        "-subj", f"/CN={name}", "-addext", "subjectAltName=DNS:localhost",
                    ], check=True, capture_output=True)
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.minimum_version = ssl.TLSVersion.TLSv1_2
                    context.load_cert_chain(cert, key)
                    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
                    server.socket = context.wrap_socket(server.socket, server_side=True)
                    threading.Thread(target=server.serve_forever, daemon=True).start()
                    servers.append(server)
                merge = ROOT / "charts/cloudtak/files/merge-trust.mjs"
                merge_script = merge.read_text()
                output = root / "combined.pem"
                request = """const https = require('node:https');
https.get({host: '127.0.0.1', servername: 'localhost', port: +process.argv[1]},
  response => { response.resume(); process.exitCode = response.statusCode === 200 ? 0 : 1; })
  .on('error', () => { process.exitCode = 1; });"""

                def connects(server):
                    return subprocess.run(
                        ["node", "-e", request, str(server.server_port)],
                        env={**os.environ, "NODE_EXTRA_CA_CERTS": str(output)},
                        capture_output=True, timeout=10,
                    ).returncode == 0

                def assemble(bundle, tak_ca=None):
                    if output.exists():
                        output.chmod(0o644)
                    subprocess.run([
                        "node", "--input-type=module", "-e", merge_script,
                        "merge-trust", str(tak_ca or (root / "tak.pem")),
                        str(bundle), str(output),
                    ], check=True)

                assemble(root / "core.pem")
                self.assertTrue(all(connects(server) for server in servers))
                for bundle in (root / "absent.pem", root / "empty.pem"):
                    if bundle.name == "empty.pem":
                        bundle.touch()
                    assemble(bundle)
                    self.assertTrue(connects(servers[0]))
                    self.assertFalse(connects(servers[1]))
                # A missing or empty TAK CA must fail the init container.
                (root / "empty-tak.pem").touch()
                for bad_tak in (root / "missing.pem", root / "empty-tak.pem"):
                    result = subprocess.run([
                        "node", "--input-type=module", "-e", merge_script,
                        "merge-trust", str(bad_tak),
                        str(root / "core.pem"), str(output),
                    ], capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()



@unittest.skipUnless(all(shutil.which(tool) for tool in ('openssl','keytool','timeout','bash')), 'TLS/JVM deadline tools required')
class TrustImportDeadline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=tempfile.TemporaryDirectory();cls.root=Path(cls.fixture.name)
        cls.cert=cls.root/'ca.pem';cls.key=cls.root/'ca.key'
        subprocess.run(['openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256',
                        '-nodes','-keyout',str(cls.key),'-out',str(cls.cert),'-days','1','-subj','/CN=localhost'],
                       capture_output=True,check=True,timeout=10)
        cls.key.chmod(0o600)
        cls.java_home=Path(shutil.which('keytool')).resolve().parents[1]

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def run_import(self,mode,source=None,deadline=30):
        import contextlib
        import signal
        import socket
        import time
        @contextlib.contextmanager
        def peer(tls):
            server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1);server.settimeout(0.1)
            stop=threading.Event()
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version=ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(self.cert,self.key)
            def serve():
                while not stop.is_set():
                    try:conn,_=server.accept()
                    except socket.timeout:continue
                    except OSError:break
                    with conn:
                        conn.settimeout(2 if tls else 0.1)
                        if tls:
                            try:
                                wrapped=context.wrap_socket(conn,server_side=True)
                                with wrapped:
                                    try:wrapped.unwrap()
                                    except (ssl.SSLError,OSError):pass
                            except (ssl.SSLError,OSError):pass
                        else:
                            while not stop.is_set():
                                try:
                                    if not conn.recv(4096):break
                                except socket.timeout:continue
                                except OSError:break
            thread=threading.Thread(target=serve,daemon=True);thread.start()
            try:yield server.getsockname()[1]
            finally:stop.set();server.close();thread.join(timeout=3)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'trust').mkdir();(root/'bundle').mkdir();(root/'bin').mkdir()
            # A present non-PEM file avoids the unchanged optional-bundle polling.
            # No certificate is imported until the real TLS fetch succeeds.
            (root/'bundle/ca-bundle.pem').write_text('no certificate in this owned fixture\n')
            if mode=='partial-bundle':(root/'bundle/ca-bundle.pem').write_bytes(self.cert.read_bytes())
            # Keep the resolved JDK available after narrowing the child PATH.
            (root/'bin/keytool').symlink_to(Path(shutil.which('keytool')).resolve())
            if mode=='missing-timeout':
                for tool in ('bash','cp','chmod','rm','seq','awk','basename','openssl'):
                    (root/'bin'/tool).symlink_to(Path(shutil.which(tool)).resolve())
            if mode in ('partial','partial-bundle','oversize'):
                body='#!/bin/sh\ncat '+str(self.cert)+'\n'
                body+=('head -c 262144 /dev/zero\n' if mode=='oversize' else 'exit 1\n')
                wrapper=root/'bin/openssl';wrapper.write_text(body);wrapper.chmod(0o700)
            network=peer(mode=='tls') if mode in ('tls','stall') else contextlib.nullcontext(1)
            with network as port:
                original=(ROOT/'charts/takserver/files/scripts/import-trust.sh').read_text() if source is None else source
                # Project only fixed filesystem/network locations into the owned
                # fixture. The import/deadline/error control flow is unchanged.
                script=original.replace('/trust/cacerts',str(root/'trust/cacerts')) \
                    .replace('/bundle/ca-bundle.pem',str(root/'bundle/ca-bundle.pem')) \
                    .replace('/tmp/uds-ca-',str(root/'uds-ca-')) \
                    .replace('/tmp/issuer.txt',str(root/'issuer.txt')) \
                    .replace('/tmp/uds-issuer.',str(root/'uds-issuer.')) \
                    .replace(':443',':'+str(port))
                path=root/'import.sh';path.write_text(script)
                env={'PATH':str(root/'bin')+('' if mode=='missing-timeout' else ':'+os.defpath),'JAVA_HOME':str(self.java_home),
                     'ISSUER_HOST':'127.0.0.1','LC_ALL':'C'}
                started=time.monotonic();child=subprocess.Popen(['bash',str(path)],env=env,
                    stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
                expired=False
                try:out,err=child.communicate(timeout=deadline)
                except subprocess.TimeoutExpired:
                    expired=True;os.killpg(child.pid,signal.SIGKILL);out,err=child.communicate()
                finally:
                    try:os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                elapsed=time.monotonic()-started
            query=subprocess.run(['keytool','-list','-alias','issuer-uds-ca-1','-keystore',str(root/'trust/cacerts'),
                                  '-storepass','changeit'],capture_output=True,timeout=10)
            return {'exit':child.returncode,'outer_timeout':expired,'seconds':elapsed,
                    'issuer_imported':query.returncode==0,'temporary_fetches':len(list(root.glob('uds-issuer.*'))),
                    'stdout':out,'stderr':err}

    def test_real_tls_certificate_import_still_succeeds(self):
        result=self.run_import('tls')
        self.assertFalse(result['outer_timeout']);self.assertEqual(result['exit'],0,result['stderr'])
        self.assertTrue(result['issuer_imported']);self.assertEqual(result['temporary_fetches'],0)

    def test_tcp_peer_that_never_speaks_tls_is_bounded_and_fails_closed(self):
        result=self.run_import('stall')
        self.assertFalse(result['outer_timeout']);self.assertNotEqual(result['exit'],0)
        self.assertLess(result['seconds'],30);self.assertFalse(result['issuer_imported'])
        self.assertEqual(result['temporary_fetches'],0)

    def test_failed_fetch_cannot_import_a_valid_partial_transcript(self):
        result=self.run_import('partial')
        self.assertFalse(result['outer_timeout']);self.assertNotEqual(result['exit'],0)
        self.assertFalse(result['issuer_imported']);self.assertEqual(result['temporary_fetches'],0)

    def test_oversized_fetch_is_discarded_and_cleaned(self):
        result=self.run_import('oversize')
        self.assertFalse(result['outer_timeout']);self.assertNotEqual(result['exit'],0)
        self.assertFalse(result['issuer_imported']);self.assertEqual(result['temporary_fetches'],0)

    def test_valid_bundle_remains_usable_when_issuer_fetch_fails(self):
        result=self.run_import('partial-bundle')
        self.assertFalse(result['outer_timeout']);self.assertEqual(result['exit'],0,result['stderr'])
        self.assertFalse(result['issuer_imported']);self.assertEqual(result['temporary_fetches'],0)

    def test_missing_deadline_tool_refuses_unbounded_fetch(self):
        result=self.run_import('missing-timeout')
        self.assertFalse(result['outer_timeout']);self.assertNotEqual(result['exit'],0)
        self.assertIn(b'timeout is required',result['stderr'])
        self.assertFalse(result['issuer_imported']);self.assertEqual(result['temporary_fetches'],0)


if __name__ == "__main__":
    unittest.main()
