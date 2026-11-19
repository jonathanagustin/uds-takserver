# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Selected candidates cannot silently become a cache hit or another source."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('candidate_consumer', ROOT / '.github/scripts/candidate-consumer.py')
CANDIDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CANDIDATE)


class CandidateConsumerTests(unittest.TestCase):
    def setUp(self):
        self.context = {'repository': 'upstream/repo', 'head_repository': 'contributor/repo',
                        'head_sha': 'a' * 40, 'checkout_sha': 'd' * 40, 'base_sha': 'e' * 40, 'pull_number': 5}
        self.expected = {'group': 'postgis', 'architecture': 'arm64', 'inputs': 'b' * 40, 'tag': '18-3.6-fixture',
                         'producer': {'repository': 'upstream/repo', 'head_sha': 'a' * 40, 'checkout_sha': 'd' * 40,
                                      'workflow': '.github/workflows/postgis-image.yaml', 'run_id': 42, 'run_attempt': 1}}
        self.api = MagicMock()

    def test_pending_or_failed_scheduled_producer_cannot_fall_back_to_a_published_key(self):
        for error in (ValueError('canonical build failed'), ValueError('canonical candidate needs approval')):
            with self.subTest(error=str(error)), patch.object(CANDIDATE, 'identity', return_value=self.expected), \
                 patch.object(CANDIDATE.ARTIFACTS, 'GitHub', return_value=self.api), \
                 patch.object(CANDIDATE.ARTIFACTS, 'select_run', return_value={'id': 42}), \
                 patch.object(CANDIDATE.ARTIFACTS, 'resolve', side_effect=error), \
                 patch.object(CANDIDATE, 'published_images', return_value=['stale cached image']) as published:
                with self.assertRaises(ValueError):
                    CANDIDATE.acquire('postgis', 'arm64', self.context, Path('/unused'))
                published.assert_not_called()

    def test_newly_scheduled_run_waits_instead_of_using_an_existing_key(self):
        with patch.object(CANDIDATE, 'identity', return_value=self.expected), \
             patch.object(CANDIDATE.ARTIFACTS, 'GitHub', return_value=self.api), \
             patch.object(CANDIDATE.ARTIFACTS, 'select_run', side_effect=CANDIDATE.ARTIFACTS.Pending()), \
             patch.object(CANDIDATE.ARTIFACTS, 'canonical_triggered', return_value=True), \
             patch.object(CANDIDATE.ARTIFACTS, 'resolve', return_value=({'id': 56, 'name': 'candidate-postgis-arm64-1'}, self.expected)) as resolve, \
             patch.object(CANDIDATE, 'published_images') as published:
            _, _, origin = CANDIDATE.acquire('postgis', 'arm64', self.context, Path('/unused'))
            self.assertEqual(origin['artifact_id'], 56)
            resolve.assert_called_once()
            published.assert_not_called()

    def test_ready_checks_failed_arm_before_waiting_for_pending_amd(self):
        calls = []
        def acquire(group, arch, context, directory, load=False, timeout=10800, wait=True, api=None):
            self.assertIs(api, self.api)
            calls.append(arch)
            if arch == 'amd64':
                if wait:
                    raise AssertionError('blocked on pending amd64 before inspecting failed arm64')
                raise CANDIDATE.ARTIFACTS.Pending('canonical native candidate build is still running')
            raise ValueError('canonical arm64 native candidate build failed')
        with patch.object(sys, 'argv', ['candidate-consumer.py', 'ready', '--group', 'takserver']), \
             patch.object(CANDIDATE.ARTIFACTS, 'context_from_environment', return_value=self.context), \
             patch.object(CANDIDATE.ARTIFACTS, 'GitHub', return_value=self.api), \
             patch.object(CANDIDATE, 'acquire', side_effect=acquire), \
             patch.object(CANDIDATE.time, 'sleep') as sleep:
            with self.assertRaisesRegex(ValueError, 'arm64 native candidate build failed'):
                CANDIDATE.main()
            self.assertEqual(calls, ['amd64', 'arm64'])
            sleep.assert_not_called()

    def test_published_immutable_reuse_requires_verified_producer_path_exclusion(self):
        with patch.object(CANDIDATE, 'identity', return_value=self.expected), \
             patch.object(CANDIDATE.ARTIFACTS, 'GitHub', return_value=self.api), \
             patch.object(CANDIDATE.ARTIFACTS, 'select_run', side_effect=CANDIDATE.ARTIFACTS.Pending()), \
             patch.object(CANDIDATE.ARTIFACTS, 'canonical_triggered', return_value=False), \
             patch.object(CANDIDATE, 'published_images', return_value=[{'name': 'postgis'}]):
            _, images, origin = CANDIDATE.acquire('postgis', 'arm64', self.context, Path('/unused'))
            self.assertEqual(images, [{'name': 'postgis'}])
            self.assertEqual(origin['kind'], 'published-immutable')

    def test_archive_corruption_is_rejected_before_docker_load(self):
        artifact = {'id': 56, 'name': 'candidate-postgis-arm64-1'}
        with patch.object(CANDIDATE, 'identity', return_value=self.expected), \
             patch.object(CANDIDATE.ARTIFACTS, 'GitHub', return_value=self.api), \
             patch.object(CANDIDATE.ARTIFACTS, 'select_run', return_value={'id': 42}), \
             patch.object(CANDIDATE.ARTIFACTS, 'resolve', return_value=(artifact, self.expected)), \
             patch.object(CANDIDATE.IMAGES, 'verify', side_effect=ValueError('layer content digest mismatch')), \
             patch.object(CANDIDATE.subprocess, 'run') as runtime:
            with self.assertRaisesRegex(ValueError, 'layer content'):
                CANDIDATE.acquire('postgis', 'arm64', self.context, Path('/unused'), load=True)
            runtime.assert_not_called()

    def test_registry_override_injection_includes_csv_quoting_prefixes_equals_and_repeated_flags(self):
        state = {'registry': '127.0.0.1:5000'}
        bad = [
            '--registry-override ghcr.io=elsewhere.invalid',
            '--registry-override=ghcr.io/jonathanagustin/uds-takserver=elsewhere.invalid',
            '--registry-override docker.io=mirror --registry-override ghcr.io=evil',
            '--registry-override docker.io=mirror,ghcr.io/path=evil',
            '--registry-override g=evil',
            '''--registry-override '"ghcr.io/jonathanagustin/uds-takserver=elsewhere.invalid/other"' ''',
            '''--registry-override='"docker.io=mirror","ghcr.io/path=evil"' ''',
            '--registry-override GHCR.IO=evil',
        ]
        with patch.object(CANDIDATE, 'validate_state', return_value=state):
            for extra in bad:
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    CANDIDATE.create_options(Path('/unused'), extra)
            options = CANDIDATE.create_options(Path('/unused'), '--registry-override docker.io=mirror.gcr.io --skip-sbom')
            self.assertEqual(shlex.split(options), ['--registry-override', 'docker.io=mirror.gcr.io', '--skip-sbom',
                                                   '--registry-override', 'ghcr.io=127.0.0.1:5000'])
        for registry in ('evil.invalid:5000', '0.0.0.0:5000', '127.0.0.1:65536', '127.0.0.1:5000/x'):
            with self.subTest(registry=registry), self.assertRaises(ValueError):
                CANDIDATE.validate_registry(registry)

    def test_registry_inspection_and_readiness_failures_remove_only_the_created_named_container(self):
        for failure in ('start', 'interrupt', 'inspect', 'readiness'):
            with self.subTest(failure=failure), patch.dict(os.environ, {'GITHUB_RUN_ID': '42'}), \
                 patch.object(CANDIDATE.subprocess, 'run') as runtime, \
                 patch.object(CANDIDATE, 'registry_details') as inspect, \
                 patch.object(CANDIDATE, 'urlopen', side_effect=OSError('not ready')), \
                 patch.object(CANDIDATE.time, 'sleep'):
                inspect.return_value = {'Config': {'Labels': {CANDIDATE.RUN_LABEL: '42', CANDIDATE.HEAD_LABEL: 'a' * 40}},
                                        'NetworkSettings': {'Ports': {'5000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '5000'}]}}}
                if failure == 'inspect':
                    inspect.side_effect = ValueError('bad inspection')
                elif failure in ('start', 'interrupt'):
                    runtime.side_effect = [subprocess.CalledProcessError(1, ['docker', 'run']) if failure == 'start' else KeyboardInterrupt(), None]
                with self.assertRaises((ValueError, subprocess.CalledProcessError, KeyboardInterrupt)):
                    CANDIDATE.start_registry(self.context)
                created = runtime.call_args_list[0].args[0]
                name = created[created.index('--name') + 1]
                self.assertTrue(name.startswith('uds-candidate-42-'))
                self.assertEqual(runtime.call_args_list[-1].args[0], ['docker', 'rm', '-f', name])
                self.assertEqual(len(runtime.call_args_list), 2)

    def test_mirror_manifest_change_is_rejected_even_when_the_config_is_identical(self):
        image = {'name': 'postgis', 'ref': 'ghcr.io/jonathanagustin/uds-takserver/postgis:18-3.6-fixture',
                 'mirror': '127.0.0.1:5000/jonathanagustin/uds-takserver/postgis:18-3.6-fixture',
                 'config_digest': 'sha256:' + '1' * 64, 'mirror_manifest_digest': 'sha256:' + '2' * 64}
        state = {'schema': CANDIDATE.STATE_SCHEMA, **self.context, 'consumer_run_id': '42', 'consumer_run_attempt': '1', 'architecture': 'arm64',
                 'registry': '127.0.0.1:5000', 'registry_name': 'uds-candidate-42-' + 'a' * 12,
                 'groups': {'postgis': {'inputs': 'b' * 40, 'tag': '18-3.6-fixture', 'images': [image],
                                         'origin': {'kind': 'published-immutable'}}}}
        details = {'Config': {'Labels': {CANDIDATE.RUN_LABEL: '42', CANDIDATE.HEAD_LABEL: 'a' * 40}},
                   'State': {'Running': True}, 'NetworkSettings': {'Ports': {'5000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '5000'}]}}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.json'
            path.write_text(json.dumps(state))
            with patch.dict(os.environ, {'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '1'}), patch.object(CANDIDATE, 'native_architecture', return_value='arm64'), \
                 patch.object(CANDIDATE, 'registry_details', return_value=details), \
                 patch.dict(CANDIDATE.ARTIFACTS.PRODUCERS, {'postgis': 'postgis-image.yaml'}, clear=True), \
                 patch.object(CANDIDATE, 'identity', return_value=self.expected), \
                 patch.object(CANDIDATE, 'loopback_manifest', return_value=({'config': {'digest': image['config_digest']}}, 'sha256:' + '3' * 64)):
                with self.assertRaisesRegex(ValueError, 'mirror bytes changed'):
                    CANDIDATE.validate_state(path, self.context)

    def test_current_scan_manifest_uses_pinned_mirror_bytes_and_leaves_canonical_zarf_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = 'images:\n  - "ghcr.io/jonathanagustin/uds-takserver/postgis:fixture"\n  - "docker.io/library/nats:version"\n'
            (root / 'zarf.yaml').write_text(original)
            state = {'groups': {'postgis': {'images': [{'ref': 'ghcr.io/jonathanagustin/uds-takserver/postgis:fixture',
                      'mirror': '127.0.0.1:5000/jonathanagustin/uds-takserver/postgis:fixture',
                      'mirror_manifest_digest': 'sha256:' + '1' * 64}]}}}
            with patch.object(CANDIDATE, 'validate_state', return_value=state), patch.object(CANDIDATE, 'ROOT', root):
                CANDIDATE.scan_manifest(root / 'state.json', root / 'scan.yaml')
            self.assertEqual((root / 'zarf.yaml').read_text(), original)
            self.assertIn('127.0.0.1:5000/jonathanagustin/uds-takserver/postgis@sha256:' + '1' * 64, (root / 'scan.yaml').read_text())
            self.assertIn('docker.io/library/nats:version', (root / 'scan.yaml').read_text())


if __name__ == '__main__':
    unittest.main()
