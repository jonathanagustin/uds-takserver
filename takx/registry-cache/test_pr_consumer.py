# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Read-only selection guards; fixture attestations are not cryptographic proof."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr_consumer as P


class PrConsumerGuards(unittest.TestCase):
    def fixture(self):
        identity = {'recipes': P.producer.recipes(), 'source': P.producer.LOCK['source'],
                    'base_image': P.producer.LOCK['image'], 'base_image_config': P.producer.LOCK['image_config']}
        pin = P.contract()
        pin['input_key'] = 'registry-v2-' + P.local.sha(P.local.canonical(identity))
        q = {k: pin[k] for k in ('source_commit', 'run_id', 'subject_digest', 'input_key', 'capsule_sha256')}
        q.update(schema='uds-draco-qualification-v2', repository=P.transport.REPO,
                 workflow=P.transport.WORKFLOW, identity=identity)
        statement = {'predicateType': P.fresh.PREDICATE, 'subject': [
            {'digest': {'sha256': pin['subject_digest'].split(':')[1]}}], 'predicate': q}
        return pin, q, [{'verificationResult': {'statement': statement}}]

    def test_wrong_producer_subject_key_and_ambiguous_predicates_refused(self):
        pin, q, results = self.fixture()
        self.assertEqual(P.qualification(results, pin), q)
        for field in ('source_commit', 'run_id', 'subject_digest', 'input_key', 'capsule_sha256', 'workflow'):
            changed = copy.deepcopy(results)
            changed[0]['verificationResult']['statement']['predicate'][field] = 'foreign'
            with self.subTest(field=field), self.assertRaises(ValueError):
                P.qualification(changed, pin)
        changed = copy.deepcopy(results)
        changed[0]['verificationResult']['statement']['predicate']['identity']['recipes'] = {}
        with self.assertRaises(ValueError): P.qualification(changed, pin)
        for bad in ([], results + results):
            with self.assertRaises(ValueError): P.qualification(bad, pin)

    def test_each_consumed_build_identity_change_misses(self):
        _, q, _ = self.fixture()
        self.assertTrue(P.compatible(q))
        for field in ('recipes', 'source', 'base_image', 'base_image_config'):
            changed = copy.deepcopy(q); changed['identity'][field] = 'different'
            with self.subTest(field=field): self.assertFalse(P.compatible(changed))

    def test_hit_verifies_before_readback_and_only_mismatch_is_a_miss(self):
        pin, q, results = self.fixture()
        calls = []
        def verify(args, output, name, env):
            calls.append('verify')
            self.assertIn('--deny-self-hosted-runners', args)
            self.assertEqual(args[args.index('--source-digest') + 1], pin['source_commit'])
            self.assertEqual(json.loads((Path(env['DOCKER_CONFIG']) / 'config.json').read_text()), {'auths': {}})
            return P.local.canonical(results)
        with tempfile.TemporaryDirectory() as temp, patch.object(P, 'contract', return_value=pin), \
             patch.object(P.transport, 'command', side_effect=verify), \
             patch.object(P.transport, 'readback', side_effect=lambda *a: calls.append('readback')) as read:
            root = Path(temp)
            self.assertTrue(P.restore(root / 'hit')['cacheHit'])
            self.assertEqual(calls, ['verify', 'readback'])
            with patch.object(P, 'compatible', return_value=False):
                self.assertFalse(P.restore(root / 'miss')['cacheHit'])
            self.assertEqual(read.call_count, 1)
            with patch.object(P.transport, 'command', side_effect=ValueError('authentication failed')):
                with self.assertRaises(ValueError): P.restore(root / 'bad-auth')
                self.assertFalse((root / 'bad-auth/result.json').exists())
            read.side_effect = ValueError('corrupt capsule')
            with self.assertRaises(ValueError): P.restore(root / 'bad-payload')
            self.assertFalse((root / 'bad-payload/result.json').exists())

    def test_existing_or_symlink_output_refused_before_transport(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(P.transport, 'command') as command:
            root = Path(temp); (root / 'link').symlink_to(root, target_is_directory=True)
            for output in (root, root / 'link/child'):
                with self.assertRaises(ValueError): P.restore(output)
            command.assert_not_called()

    def test_workflow_is_ordinary_pr_read_only_and_hit_miss_paths_are_exclusive(self):
        value = yaml.safe_load((P.local.ROOT / '.github/workflows/native-cache-pr.yaml').read_text())
        triggers = value.get('on', value.get(True))
        self.assertEqual(set(triggers), {'pull_request', 'workflow_dispatch'})
        self.assertEqual(value['permissions'], {'contents': 'read', 'attestations': 'read'})
        steps = value['jobs']['draco']['steps']
        self.assertFalse(any('permissions' in s for s in steps))
        self.assertFalse(any('login-action' in s.get('uses', '') or 'actions/attest@' in s.get('uses', '') for s in steps))
        runs = [s for s in steps if 'producer.py consume' in s.get('run', '') or 'producer.py build' in s.get('run', '')]
        self.assertEqual(len(runs), 2)
        self.assertEqual({s['if'] for s in runs}, {"steps.cache.outputs.hit == 'true'", "steps.cache.outputs.hit == 'false'"})
        for step in steps:
            if step.get('uses', '').startswith('actions/checkout@'):
                self.assertFalse(step['with']['persist-credentials'])
