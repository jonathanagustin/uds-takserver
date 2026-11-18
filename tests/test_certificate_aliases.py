# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise certificate bundle selection with synthetic backup aliases."""
import base64
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT / '.github/container-and-chart/docker/publish-cert-configmap.sh'
SPLIT = ROOT / 'charts/takserver/files/scripts/split-key-material.sh'
ALIASES = {'ca-do-not-share.key', 'root-ca-do-not-share.key'}


def encoded(value):
    return base64.b64encode(value.encode()).decode()


class CertificateAliasTests(unittest.TestCase):
    def test_fresh_publication_omits_both_ca_names_and_keeps_application_files(self):
        text = PUBLISH.read_text()
        start = text.index('\n{\n', text.index('secrets_api=')) + 1
        end = text.index('} > /tmp/takserver-certs.json', start) + 1
        # Execute the real production serialization block, rather than a copy
        # of its filtering rules. Source-only mode defines its helpers.
        command = 'PUBLISH_CERT_SOURCE_ONLY=1; source "$1";\n' + text[start:end]
        names = ALIASES | {'admin.key', 'takserver.jks', 'ca-signing.jks', 'ca.pem',
                           'root-ca.pem', 'truststore-root.jks', 'custom-ca.key'}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'files').mkdir()
            for name in names:
                (directory / 'files' / name).write_text('synthetic-' + name)
            result = subprocess.run(['bash', '-eu', '-c', command, 'bash', str(PUBLISH)],
                                    cwd=directory, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            body = json.loads(result.stdout)
            self.assertEqual(set(body['data']), names - ALIASES)
            self.assertEqual(body['metadata']['name'], 'takserver-certs')

    def test_real_migration_removes_aliases_from_old_bundles_without_changing_ca_secret(self):
        public = {'ca.pem': encoded('synthetic-certificate'), 'root-ca.pem': encoded('public-backup')}
        private = {name: encoded('synthetic-private-' + name) for name in ALIASES}
        state = {'cm': {'binaryData': {**public, **private, 'admin.key': encoded('current-admin')}},
                 'secret': {'data': {**private, 'takserver.jks': encoded('application-store'),
                                    'custom-ca.key': encoded('application-key')}}}
        calls = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, value):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                calls.append(('GET', self.path))
                if self.path.endswith('/configmaps/cert-migration'):
                    self.reply(200, state['cm'])
                elif self.path.endswith('/secrets/takserver-certs'):
                    self.reply(200, state['secret'])
                else:
                    self.reply(404, {})

            def do_POST(self):
                calls.append(('POST', self.path))
                self.rfile.read(int(self.headers['Content-Length']))
                self.reply(409, {})

            def do_PUT(self):
                calls.append(('PUT', self.path))
                self.server.test.assertTrue(self.path.endswith('/secrets/takserver-certs'))
                state['secret'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.reply(200, state['secret'])

            def do_PATCH(self):
                calls.append(('PATCH', self.path))
                self.server.test.assertTrue(self.path.endswith('/configmaps/cert-migration'))
                patch = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                for name, value in patch['binaryData'].items():
                    self.server.test.assertIsNone(value)
                    state['cm']['binaryData'].pop(name, None)
                self.reply(200, state['cm'])

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.test = self
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                for name, value in [('namespace', 'takserver'), ('token', 'synthetic-test-token'), ('ca.crt', '')]:
                    (directory / name).write_text(value)
                env = {**os.environ, 'SA_DIR': str(directory),
                       'KUBE_API': 'http://127.0.0.1:' + str(server.server_port)}
                result = subprocess.run(['bash', str(SPLIT)], env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(set(state['secret']['data']), set(public) | {'admin.key', 'takserver.jks', 'custom-ca.key'})
        self.assertEqual(state['secret']['data']['admin.key'], encoded('current-admin'))
        self.assertEqual(state['cm']['binaryData'], public)
        self.assertFalse(any('/secrets/takserver-ca-key' in path for _, path in calls))
        self.assertIn(('PUT', '/api/v1/namespaces/takserver/secrets/takserver-certs'), calls)
