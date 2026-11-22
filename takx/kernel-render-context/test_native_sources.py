# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Source projection guards never compile, configure, load or acquire providers."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('complete_native_sources', HERE / 'native_sources.py')
N = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(N)


class NativeSourceGuards(unittest.TestCase):
    def test_default_verification_never_launches_a_process(self):
        with patch.object(N.P.subprocess, 'Popen', side_effect=AssertionError('must not launch')):
            N.main([])
            with self.assertRaises(ValueError):
                N.main(['--source', '/missing'])
        context, base, sources, _, composed, inputs = N.contract()
        self.assertEqual(len(sources), 1396)
        self.assertEqual(len(set(sources) | set(composed)), 1399)
        self.assertEqual(base['source_counts'], {'core': 483, 'jni': 159, 'cesium': 173})
        self.assertEqual(inputs[str(HERE / 'tests/BindingStateTest.java')],
                         hashlib.sha256((HERE / 'tests/BindingStateTest.java').read_bytes()).hexdigest())
        self.assertTrue(all(context[key] is False for key in ('native_builder_integration', 'gpu_qualified', 'full_sdk_accepted')))

    def test_path_and_official_bytes_identity_refuse_changes(self):
        for name in ('../escape', '/absolute', 'takkernel/engine/../escape', 'bad\\path', 'unrelated/source.cpp'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                N.relative(name)
        data = b'locked source'
        entry = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                 'git_blob': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}
        N.check_bytes(data, entry)
        for key in entry:
            changed = dict(entry, **{key: 0 if key == 'bytes' else '0' * len(entry[key])})
            with self.subTest(key=key), self.assertRaises(ValueError):
                N.check_bytes(data, changed)

    def test_sparse_hash_membership_and_output_overlap_fail_before_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, sparse = root / 'source', root / 'sparse'
            source.mkdir(); sparse.mkdir()
            args = SimpleNamespace(source=source, prepared_source=sparse, output=root / 'output', git=sys.executable)
            with patch.object(N.P.subprocess, 'Popen', side_effect=AssertionError('must not launch')):
                with self.assertRaisesRegex(ValueError, 'exact current sparse'):
                    N.prepare(args)
                sibling = root / 'sibling'
                sibling.mkdir()
                outputs = [source / 'out', sibling / '../source/out', Path('/' + str(source / 'out'))]
                case_alias = root / 'SOURCE'
                if case_alias.exists() and case_alias.samefile(source):
                    outputs.append(case_alias / 'out')
                for output in outputs:
                    args.output = output
                    with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'disjoint'):
                        N.prepare(args)
                    self.assertFalse((source / 'out').exists())
            self.assertFalse((root / 'output').exists())
            (sparse / 'alias').symlink_to(source)
            with self.assertRaisesRegex(ValueError, 'nonregular'):
                N.source_inventory(sparse)

    def test_aggregate_disk_stream_and_stalled_child_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            runner = N.Runner(root)
            with patch.object(N, 'CAP', 10), self.assertRaisesRegex(ValueError, 'output cap'):
                runner.check(11)
            runner.deadline = time.monotonic() - 1
            with self.assertRaisesRegex(ValueError, 'aggregate deadline'):
                runner.check()
            runner = N.Runner(root)
            with self.assertRaisesRegex(ValueError, 'stream exceeded'):
                runner.run([sys.executable, '-c', 'print("x"*2000)'], limit=100)
            pid_file = root / 'source-child.pid'
            runner.deadline = time.monotonic() + .4
            started = time.monotonic()
            with self.assertRaises((ValueError, TimeoutError)):
                runner.run([sys.executable, '-c', 'import os,time;from pathlib import Path;'
                            'Path(' + repr(str(pid_file)) + ').write_text(str(os.getpid()));time.sleep(10)'])
            self.assertLess(time.monotonic() - started, 2)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid_file.read_text()), 0)

    def test_unexpected_sparse_member_is_not_read_and_inventory_has_one_deadline(self):
        expected = {name: pin for name, pin in N.contract()[0]['stages']['client_jni'].items() if pin}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            foreign = root / 'unexpected-large-file'
            with foreign.open('wb') as stream:
                stream.truncate(N.SPARSE_CAP + 1)
            with patch.object(N.P, 'regular', side_effect=AssertionError('unexpected member must never be read')):
                with self.assertRaisesRegex(ValueError, 'unexpected'):
                    N.sparse_inventory(root, expected, time.monotonic() + 1)
            with self.assertRaisesRegex(ValueError, 'aggregate deadline'):
                N.sparse_inventory(root, expected, time.monotonic() - 1)
            runner = N.Runner(root, time.monotonic() - 1)
            with self.assertRaisesRegex(ValueError, 'aggregate deadline'):
                runner.check()

    def test_failed_blob_reader_keeps_failure_without_success_receipt(self):
        context = N.contract()[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, sparse, output = root / 'source', root / 'sparse', root / 'output'
            source.mkdir(); sparse.mkdir()
            expected = {name: {'sha256': pin, 'bytes': 0} for name, pin in context['stages']['client_jni'].items() if pin}
            args = SimpleNamespace(source=source, prepared_source=sparse, output=output, git=sys.executable)
            with patch.object(N, 'sparse_inventory', return_value=expected), \
                 patch.object(N.Runner, 'run', side_effect=ValueError('cached Git blob unavailable')):
                with self.assertRaisesRegex(ValueError, 'unavailable'):
                    N.prepare(args)
            self.assertFalse((output / 'receipt.json').exists())
            failed = json.loads((output / 'failed.json').read_text())
            self.assertFalse(failed['completed'])
            self.assertTrue(all(failed[name] is False for name in N.FLAGS))


if __name__ == '__main__':
    unittest.main()
