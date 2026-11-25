# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Read-only GEOS PR cache selection guards; fixture attestations are not cryptographic proof."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr_consumer as P


class GeosPrConsumerGuards(unittest.TestCase):
    def fixture(self):
        lock = P.producer.LOCK
        identity = {
            'schema': 'uds-geos-capi-inputs-v1',
            'recipes': P.producer.recipes(),
            'component': lock['component'],
            'base_image': lock['base_image'],
            'constraints': lock['constraints'],
            'environment': lock['environment'],
            'configure': lock['configure'],
            'build_commands': lock['build_commands'],
            'execution': {
                'engine_host_arch': 'amd64',
                'container_machine': 'x86_64',
                'mode': 'native',
                'target_os': 'linux',
                'target_arch': 'amd64',
            },
        }
        pin = P.contract()
        pin['input_key'] = 'geos-capi-local-v1-' + P.cache.sha(P.cache.canonical(identity))
        q = {
            'schema': 'uds-geos-capi-publication-v1',
            'repository': P.transport.REPOSITORY,
            'workflow': P.transport.WORKFLOW,
            'registry': P.transport.REGISTRY,
            'source_commit': pin['source_commit'],
            'run_id': pin['run_id'],
            'run_attempt': pin['run_attempt'],
            'subject': pin['subject_digest'],
            'input_key': pin['input_key'],
            'capsule_sha256': pin['capsule_sha256'],
            'identity': identity,
        }
        statement = {
            'predicateType': P.transport.PREDICATE,
            'subject': [{'name': P.transport.REGISTRY, 'digest': {'sha256': pin['subject_digest'][7:]}}],
            'predicate': q,
        }
        return pin, q, [{'verificationResult': {'statement': statement}}]

    def test_wrong_producer_subject_key_and_ambiguous_predicates_refused(self):
        pin, q, results = self.fixture()
        self.assertEqual(P.qualification(results, pin), q)
        for field in ('source_commit', 'run_id', 'run_attempt', 'subject', 'input_key',
                      'capsule_sha256', 'workflow', 'registry', 'repository'):
            changed = copy.deepcopy(results)
            changed[0]['verificationResult']['statement']['predicate'][field] = 'foreign'
            with self.subTest(field=field), self.assertRaises(ValueError):
                P.qualification(changed, pin)
        # Wrong subject registry name or emulated execution mode must be refused.
        wrong_subject = copy.deepcopy(results)
        wrong_subject[0]['verificationResult']['statement']['subject'][0]['name'] = 'ghcr.io/other/repo'
        with self.assertRaises(ValueError):
            P.qualification(wrong_subject, pin)
        emulated = copy.deepcopy(results)
        emulated[0]['verificationResult']['statement']['predicate']['identity']['execution']['mode'] = 'emulated'
        with self.assertRaises(ValueError):
            P.qualification(emulated, pin)
        changed = copy.deepcopy(results)
        changed[0]['verificationResult']['statement']['predicate']['identity']['recipes'] = {}
        with self.assertRaises(ValueError):
            P.qualification(changed, pin)
        for bad in ([], results + results):
            with self.assertRaises(ValueError):
                P.qualification(bad, pin)

    def test_each_consumed_build_identity_change_misses(self):
        _, q, _ = self.fixture()
        self.assertTrue(P.compatible(q))
        for field in ('recipes', 'component', 'base_image', 'constraints',
                      'environment', 'configure', 'build_commands'):
            changed = copy.deepcopy(q)
            changed['identity'][field] = 'different'
            with self.subTest(field=field):
                self.assertFalse(P.compatible(changed))

    def test_hit_verifies_before_readback_and_only_mismatch_is_a_miss(self):
        pin, _, results = self.fixture()
        calls = []

        def verify(args, output, name, env):
            calls.append('verify')
            self.assertIn('--deny-self-hosted-runners', args)
            self.assertEqual(args[args.index('--source-digest') + 1], pin['source_commit'])
            self.assertEqual(args[args.index('--signer-digest') + 1], pin['source_commit'])
            self.assertEqual(args[args.index('--source-ref') + 1], 'refs/heads/main')
            self.assertEqual(json.loads((Path(env['DOCKER_CONFIG']) / 'config.json').read_text()), {'auths': {}})
            return P.cache.canonical(results)

        with tempfile.TemporaryDirectory() as temp, patch.object(P, 'contract', return_value=pin), \
             patch.object(P.transport, 'command', side_effect=verify), \
             patch.object(P.transport, 'readback', side_effect=lambda *a: calls.append('readback')) as read:
            root = Path(temp)
            hit_result = P.restore(root / 'hit')
            self.assertTrue(hit_result['cacheHit'])
            self.assertFalse(hit_result['sdkAcceptance'])
            self.assertFalse(hit_result['securityAcceptance'])
            self.assertEqual(calls, ['verify', 'readback'])
            with patch.object(P, 'compatible', return_value=False):
                miss_result = P.restore(root / 'miss')
                self.assertFalse(miss_result['cacheHit'])
                self.assertEqual(miss_result['reason'], 'build-inputs-differ')
            self.assertEqual(read.call_count, 1)
            with patch.object(P.transport, 'command', side_effect=ValueError('authentication failed')):
                with self.assertRaises(ValueError):
                    P.restore(root / 'bad-auth')
                self.assertFalse((root / 'bad-auth/result.json').exists())
            read.side_effect = ValueError('corrupt capsule')
            with self.assertRaises(ValueError):
                P.restore(root / 'bad-payload')
            self.assertFalse((root / 'bad-payload/result.json').exists())

    def test_existing_symlink_or_in_repo_output_refused_before_transport(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(P.transport, 'command') as command:
            root = Path(temp)
            (root / 'link').symlink_to(root, target_is_directory=True)
            for output in (root, root / 'link/child', P.cache.REPO_ROOT / 'geos-pr-in-repo'):
                with self.assertRaises(ValueError):
                    P.restore(output)
            command.assert_not_called()

    def test_workflow_is_ordinary_pr_read_only_and_hit_miss_paths_are_exclusive(self):
        value = yaml.safe_load((P.cache.REPO_ROOT / '.github/workflows/native-cache-pr.yaml').read_text())
        triggers = value.get('on', value.get(True))
        self.assertEqual(set(triggers), {'pull_request', 'workflow_dispatch'})
        self.assertIn('takx/geos-cache/**', triggers['pull_request']['paths'])
        self.assertIn('takx/native-geos/**', triggers['pull_request']['paths'])
        self.assertEqual(value['permissions'], {'contents': 'read', 'attestations': 'read'})
        job = value['jobs']['geos']
        self.assertNotIn('permissions', job)
        steps = job['steps']
        self.assertFalse(any('permissions' in s for s in steps))
        self.assertFalse(any('login-action' in s.get('uses', '') or 'actions/attest@' in s.get('uses', '') for s in steps))
        hit_steps = [s for s in steps if s.get('if') == "steps.cache.outputs.hit == 'true'"]
        miss_steps = [s for s in steps if s.get('if') == "steps.cache.outputs.hit == 'false'"]
        self.assertTrue(any('qualify.py' in s.get('run', '') for s in hit_steps))
        self.assertTrue(any('producer.py' in s.get('run', '') for s in miss_steps))
        self.assertTrue(any('qualify.py' in s.get('run', '') for s in miss_steps))
        for step in steps:
            if step.get('uses', '').startswith('actions/checkout@'):
                self.assertFalse(step['with']['persist-credentials'])


if __name__ == '__main__':
    unittest.main()
