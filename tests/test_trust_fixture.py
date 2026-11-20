# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Exercise the real private-PKI fixture without deploying a cluster."""

import base64
import importlib.util
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("trust_fixture", Path(__file__).with_name("prepare-trust-fixture.py"))
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


class PrivateGatewayFixtureTests(unittest.TestCase):
    def test_private_chain_config_and_cleanup(self):
        saved_root = "existing-public-certificate-data\n"
        original = {"variables": {"takserver": {"DOMAIN": "uds.dev", "TAK_IMAGE_TAG": "pinned-image"},
                                  "core-base": {"CA_BUNDLE_CERTS": base64.b64encode(saved_root.encode()).decode()}}}
        masks = []
        with tempfile.TemporaryDirectory() as parent:
            result = fixture.prepare(original, parent, masks.append)
            directory = Path(result["directory"])
            config = json.loads(Path(result["config"]).read_text())
            core = config["variables"]["core-base"]
            self.assertEqual(original["variables"]["takserver"], config["variables"]["takserver"])
            self.assertNotIn("TENANT_TLS_KEY", original["variables"]["core-base"])
            self.assertEqual(core["DOMAIN"], "uds.dev")
            root = Path(result["ca"]).read_text()
            self.assertEqual(base64.b64decode(core["CA_BUNDLE_CERTS"]).decode(), saved_root + root)
            key = base64.b64decode(core["TENANT_TLS_KEY"]).decode()
            self.assertIn(core["TENANT_TLS_KEY"], masks)
            self.assertTrue(set(key.splitlines()).issubset(masks))
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            for name in ("uds-config.yaml", "tenant.key"):
                self.assertEqual((directory / name).stat().st_mode & 0o777, 0o600)
            self.assertFalse((directory / "ca.key").exists())
            cert = directory / "tenant.pem"
            args = ["openssl", "verify", "-verify_hostname", "sso.uds.dev"]
            subprocess.run(args + ["-CAfile", result["ca"], str(cert)], check=True, capture_output=True)
            public = subprocess.run(args + ["-CAfile", ssl.get_default_verify_paths().cafile, str(cert)], capture_output=True)
            self.assertNotEqual(public.returncode, 0)
            self.assertIn(root, Path(result["host-ca"]).read_text())
            self.assertNotIn(key, json.dumps(result))
            fixture.cleanup(directory, parent)
            self.assertFalse(directory.exists())

    def test_hosted_python_without_a_cafile_keeps_native_roots_and_private_ca(self):
        defaults = ssl.get_default_verify_paths()
        public_bundle = Path(defaults.cafile)
        with tempfile.TemporaryDirectory() as parent, \
                patch.dict(os.environ, dict(os.environ)), \
                patch.object(fixture.ssl, "get_default_verify_paths", return_value=defaults._replace(cafile=None)), \
                patch.object(fixture, "SYSTEM_CA_BUNDLES", (public_bundle,), create=True):
            os.environ.pop(defaults.openssl_cafile_env, None)
            result = fixture.prepare({"variables": {}}, parent)
            host = Path(result["host-ca"]).read_text()
            self.assertTrue(host.startswith(public_bundle.read_text() + "\n"))
            self.assertTrue(host.endswith(Path(result["ca"]).read_text()))
            subprocess.run(["openssl", "verify", "-verify_hostname", "sso.uds.dev", "-CAfile",
                            result["host-ca"], str(Path(result["directory"]) / "tenant.pem")],
                           check=True, capture_output=True)
            fixture.cleanup(result["directory"], parent)
            self.assertEqual(list(Path(parent).iterdir()), [])

    def test_explicit_missing_empty_or_invalid_roots_cannot_select_system_fallback(self):
        defaults = ssl.get_default_verify_paths()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty, invalid = root / 'empty.pem', root / 'invalid.pem'
            empty.write_text('')
            invalid.write_text('password=PRIVATE_INVALID_CA_CONTENT')
            parent = root / 'fixtures'
            parent.mkdir()
            for selected in ('', str(root / 'missing.pem'), str(empty), str(invalid)):
                with self.subTest(selected=selected), \
                        patch.dict(os.environ, {defaults.openssl_cafile_env: selected}), \
                        patch.object(fixture.ssl, 'get_default_verify_paths',
                                     return_value=defaults._replace(cafile=None)):
                    with self.assertRaises(ValueError) as error:
                        fixture.prepare({'variables': {}}, parent)
                    self.assertNotIn('PRIVATE_INVALID_CA_CONTENT', str(error.exception))
                    self.assertEqual(list(parent.iterdir()), [])

    def test_missing_system_roots_fail_before_private_fixture_creation(self):
        defaults = ssl.get_default_verify_paths()
        with tempfile.TemporaryDirectory() as parent, patch.dict(os.environ, dict(os.environ)), \
                patch.object(fixture.ssl, 'get_default_verify_paths', return_value=defaults._replace(cafile=None)), \
                patch.object(fixture, 'SYSTEM_CA_BUNDLES', ()):
            os.environ.pop(defaults.openssl_cafile_env, None)
            with self.assertRaisesRegex(ValueError, 'system CA file is required'):
                fixture.prepare({'variables': {}}, parent)
            self.assertEqual(list(Path(parent).iterdir()), [])

    def test_existing_python_roots_take_priority_and_invalid_selected_roots_fail_closed(self):
        defaults = ssl.get_default_verify_paths()
        original = Path(defaults.cafile).read_text()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, dict(os.environ)):
            os.environ.pop(defaults.openssl_cafile_env, None)
            invalid = Path(directory) / 'invalid.pem'
            invalid.write_text('invalid certificate data')
            with patch.object(fixture.ssl, 'get_default_verify_paths', return_value=defaults), \
                    patch.object(fixture, 'SYSTEM_CA_BUNDLES', (invalid,)):
                self.assertEqual(fixture.host_public_roots(), original)
            with patch.object(fixture.ssl, 'get_default_verify_paths',
                              return_value=defaults._replace(cafile=str(invalid))), \
                    patch.object(fixture, 'SYSTEM_CA_BUNDLES', (Path(defaults.cafile),)):
                with self.assertRaisesRegex(ValueError, 'cannot be read or parsed'):
                    fixture.host_public_roots()

    def test_rejects_invalid_domains_before_creating_files(self):
        with tempfile.TemporaryDirectory() as parent:
            for domain in ("localhost", "bad/domain", "-bad.example", "example..test"):
                with self.subTest(domain=domain), self.assertRaises(ValueError):
                    fixture.prepare({"variables": {"takserver": {"DOMAIN": domain}}}, parent)
            self.assertEqual(list(Path(parent).iterdir()), [])

    def test_cleanup_rejects_unowned_directories_and_symlinks(self):
        with tempfile.TemporaryDirectory() as parent, tempfile.TemporaryDirectory() as other:
            for target in (parent, other):
                with self.assertRaises(ValueError):
                    fixture.cleanup(target, parent)
            link = Path(parent) / (fixture.PREFIX + "link")
            link.symlink_to(other, target_is_directory=True)
            with self.assertRaises(ValueError):
                fixture.cleanup(link, parent)
            self.assertTrue(Path(other).exists())

    def test_opt_in_workflow_and_config_plumbing(self):
        repo = Path(__file__).resolve().parents[1]
        workflow = (repo / ".github/workflows/test.yaml").read_text()
        self.assertIn("--set TEST_BUNDLE_CONFIG=", workflow)
        self.assertIn("&& !(github.event_name == 'workflow_dispatch' && inputs.trust_bundle)", workflow)
        self.assertIn("config: \"${TEST_BUNDLE_CONFIG}\"", (repo / "tasks.yaml").read_text())
        self.assertIn("sensitive: true", (repo / "bundle/uds-bundle.yaml").read_text())


if __name__ == "__main__":
    unittest.main()
