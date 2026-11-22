# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline refusal checks for the explicit caller-slice recipe."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('contour_style_build', HERE / 'build.py')
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)


class StyleRecipeGuards(unittest.TestCase):
    def test_default_never_imports_executor(self):
        with patch.object(B, 'module', side_effect=AssertionError('must not execute')):
            B.main([])
            for args in (['--phase', 'controls'], ['--takx', '/missing']):
                with self.assertRaises(ValueError):
                    B.main(args)

    def test_changed_sources_and_client_claim_refuse(self):
        for change in (*B.OWNED, 'acceptance', 'external-inventory'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'profile'
                shutil.copytree(HERE, root)
                if change in B.OWNED:
                    path = root / change
                    path.write_bytes(path.read_bytes() + b'\n// changed\n')
                else:
                    path = root / 'inputs.lock.json'
                    lock = json.loads(path.read_text())
                    if change == 'acceptance':
                        lock['full_client_accepted'] = True
                    else:
                        lock['takx']['../unreviewed.java'] = '0' * 64
                    path.write_text(json.dumps(lock))
                with patch.object(B, 'HERE', root), self.assertRaises(ValueError):
                    B.contract()

    def args(self, root, output):
        return SimpleNamespace(java_home=root / 'jdk', takx=root / 'takx', kernel=root / 'kernel',
                               annotations=root / 'annotations.jar', output=output)

    def test_unpinned_inputs_refuse_before_output_or_executor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'output'
            with patch.object(B, 'module', side_effect=AssertionError('must not import')):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    B.controls(self.args(root, output))
            self.assertFalse(output.exists())

    def test_overlap_and_symlink_refuse_before_executor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'takx').mkdir()
            (root / 'alias').symlink_to(root / 'takx', target_is_directory=True)
            with patch.object(B, 'module', side_effect=AssertionError('must not import')):
                for output in (root / 'takx/output', root / 'alias/output', B.REPO / '.invalid-style-proof'):
                    with self.subTest(output=output), self.assertRaises(ValueError):
                        B.controls(self.args(root, output))
                    self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
