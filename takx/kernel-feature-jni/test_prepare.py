# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""No compiler/native launch; reject unqualified source-parent/output contracts."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('feature_callback_prepare', HERE / 'prepare.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class SourceGuards(unittest.TestCase):
    def test_default_never_prepares_or_launches(self):
        with patch.object(P, 'prepare', side_effect=AssertionError('must not prepare')):
            P.main([])
        for args in (['--source', '/missing'], ['--phase', 'prepare'], ['--parent-receipt', '/missing']):
            with self.assertRaises(ValueError):
                P.main(args)

    def test_contract_rejects_scope_parent_and_acceptance_change(self):
        for field, value in (('files', {'../foreign.cpp': {}}), ('parent_native_sha256', 'a'*64),
                             ('strict_callback_accepted', True)):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'profile'
                shutil.copytree(HERE, root)
                path = root / 'inputs.lock.json'
                lock = json.loads(path.read_text())
                lock[field] = value
                path.write_text(json.dumps(lock))
                with patch.object(P, 'HERE', root), self.assertRaises(ValueError):
                    P.contract()

    def test_unqualified_parent_refuses_before_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / 'parent/receipt.json'
            parent.parent.mkdir()
            parent.write_text('{"accepted":true}')
            source = root / 'source'
            source.mkdir()
            output = root / 'output'
            with self.assertRaisesRegex(ValueError, 'parent receipt differs'):
                P.prepare(source, output, parent)
            self.assertFalse(output.exists())

    def test_overlap_and_symlink_refuse_before_parent_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            alias = root / 'alias'
            alias.symlink_to(source, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                P.prepare(alias, root / 'output', root / 'missing/receipt.json')
            with self.assertRaisesRegex(ValueError, 'overlaps'):
                P.prepare(source, source / 'output', root / 'missing/receipt.json')
            with self.assertRaisesRegex(ValueError, 'overlaps'):
                P.prepare(source, root / 'parent/output', root / 'parent/receipt.json')

    def test_owned_diagnostic_tamper_refuses(self):
        for name in sorted(P.CONTROLS):
            with self.subTest(control=name),tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'profile'
                shutil.copytree(HERE, root)
                path = root / name
                path.write_bytes(path.read_bytes() + b'\n// changed\n')
                with patch.object(P, 'HERE', root), self.assertRaisesRegex(ValueError, 'control source'):
                    P.contract()
