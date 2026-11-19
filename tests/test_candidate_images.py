# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Candidate identity and archive corruption must fail before a Docker load."""

import copy
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('candidate_images', ROOT / '.github/scripts/candidate-images.py')
CANDIDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CANDIDATE)


class CandidateArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.expected = {'producer': {'repository': 'upstream/repository', 'head_sha': 'a' * 40, 'checkout_sha': 'd' * 40,
                                       'workflow': '.github/workflows/test.yaml', 'run_id': 42, 'run_attempt': 1},
                         'group': 'postgis', 'architecture': 'arm64', 'inputs': 'b' * 40,
                         'tag': '18-3.6-candidate'}

    def archive(self, mutate=None, compressed=False, extra=None):
        config = {'architecture': self.expected['architecture'], 'os': 'linux',
                  'config': {'Labels': {CANDIDATE.INPUTS_LABEL: self.expected['inputs']}}}
        inner = io.BytesIO()
        with tarfile.open(fileobj=inner, mode='w') as layer:
            member = tarfile.TarInfo('candidate-marker')
            raw = b'candidate content\n'
            member.size = len(raw)
            layer.addfile(member, io.BytesIO(raw))
        layer = inner.getvalue()
        config['rootfs'] = {'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(layer).hexdigest()]}
        values = {'config': config, 'layer': gzip.compress(layer) if compressed else layer,
                  'ref': next(iter(CANDIDATE.refs(self.expected).values()))}
        if mutate:
            mutate(values)
        raw_config = json.dumps(values['config']).encode()
        config_path = hashlib.sha256(raw_config).hexdigest() + '.json'
        tags = list(CANDIDATE.refs(self.expected).values()) if len(CANDIDATE.GROUPS[self.expected['group']]) > 1 else [values['ref']]
        manifest = [{'Config': config_path, 'Layers': ['layer/layer.tar'], 'RepoTags': tags}]
        with tarfile.open(self.directory / 'images.tar', mode='w') as archive:
            for name, raw in [('manifest.json', json.dumps(manifest).encode()), (config_path, raw_config),
                              ('layer/layer.tar', values['layer'])]:
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
            if extra:
                archive.addfile(extra)

    def test_both_native_platforms_and_compressed_layers_preserve_exact_identity(self):
        for arch in ('amd64', 'arm64'):
            for compressed in (False, True):
                with self.subTest(arch=arch, compressed=compressed):
                    self.expected['architecture'] = arch
                    self.archive(compressed=compressed)
                    receipt = CANDIDATE.seal(self.directory, self.expected)
                    self.assertEqual(CANDIDATE.verify(self.directory, self.expected), receipt)
                    self.assertEqual(len(receipt['images']), 1)

    def test_wrong_source_run_attempt_workflow_group_inputs_arch_or_tag_is_rejected(self):
        self.archive()
        CANDIDATE.seal(self.directory, self.expected)
        for key, value in [('repository', 'attacker/repository'), ('head_sha', 'c' * 40), ('checkout_sha', 'e' * 40),
                           ('workflow', '.github/workflows/postgis-image.yaml'), ('run_id', 43), ('run_attempt', 2)]:
            expected = copy.deepcopy(self.expected)
            expected['producer'][key] = value
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, 'identity mismatch'):
                CANDIDATE.verify(self.directory, expected)

    def test_all_six_tak_names_can_share_layers_and_a_config_without_extra_images(self):
        self.expected['group'] = 'takserver'
        self.expected['inputs'] += '-' + 'c' * 40
        self.archive()
        receipt = CANDIDATE.seal(self.directory, self.expected)
        self.assertEqual({image['name'] for image in receipt['images']}, set(CANDIDATE.GROUPS['takserver']))
        self.assertEqual(len(receipt['images']), 6)
        self.assertEqual(CANDIDATE.verify(self.directory, self.expected), receipt)
        for key, value in [('group', 'cloudtak'), ('architecture', 'amd64'), ('inputs', 'c' * 40), ('tag', 'other')]:
            expected = {**self.expected, key: value}
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, 'identity mismatch'):
                CANDIDATE.verify(self.directory, expected)

    def test_tampered_archive_is_rejected_even_when_its_receipt_hash_is_rewritten(self):
        self.archive()
        CANDIDATE.seal(self.directory, self.expected)
        path = self.directory / 'images.tar'
        raw = path.read_bytes()
        self.assertIn(b'candidate content', raw)
        path.write_bytes(raw.replace(b'candidate content', b'tampered! content'))
        with self.assertRaisesRegex(ValueError, 'archive integrity mismatch'):
            CANDIDATE.verify(self.directory, self.expected)
        with self.assertRaisesRegex(ValueError, 'layer content digest mismatch'):
            CANDIDATE.seal(self.directory, self.expected)

    def test_wrong_image_config_or_extra_tag_cannot_be_sealed(self):
        def arch(values):
            values['config']['architecture'] = 'amd64'
        def inputs(values):
            values['config']['config']['Labels'][CANDIDATE.INPUTS_LABEL] = 'c' * 40
        def ref(values):
            values['ref'] = 'ghcr.io/other/image:main'
        for mutate in (arch, inputs, ref):
            with self.subTest(mutate=mutate.__name__):
                self.archive(mutate)
                with self.assertRaises(ValueError):
                    CANDIDATE.seal(self.directory, self.expected)

    def test_archive_paths_links_duplicates_and_dirty_input_receipts_are_rejected(self):
        for name in ('../escape', '/absolute', 'manifest.json', 'repositories', 'unexpected'):
            with self.subTest(name=name):
                self.archive(extra=tarfile.TarInfo(name))
                with self.assertRaises(ValueError):
                    CANDIDATE.seal(self.directory, self.expected)
        member = tarfile.TarInfo('symlink')
        member.type = tarfile.SYMTYPE
        member.linkname = '/etc/passwd'
        self.archive(extra=member)
        with self.assertRaisesRegex(ValueError, 'link or special file'):
            CANDIDATE.seal(self.directory, self.expected)
        self.expected['inputs'] += '-dirty'
        with self.assertRaisesRegex(ValueError, 'committed image inputs'):
            CANDIDATE.validate_expected(self.expected)


if __name__ == '__main__':
    unittest.main()
