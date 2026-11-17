# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline guards only: no compiler, source download, JVM or native execution."""
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('context_prepare', Path(__file__).with_name('prepare.py'))
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


class SourceGuards(unittest.TestCase):
    def source_arguments(self, root, output):
        source = root/'source'
        source.mkdir(exist_ok=True)
        (root/'texture').mkdir(exist_ok=True)
        return SimpleNamespace(source=source, output=output,
                               baseline_profile=p.HERE.parent/'kernel-native',
                               texture_profile=root/'texture',
                               client_profile=p.HERE.parent/'kernel-client-interop',
                               git=sys.executable, patch=sys.executable)

    def test_output_case_alias_is_refused_before_mutation_or_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            args = self.source_arguments(root, root/'SOURCE'/'out')
            alias = root/'SOURCE'
            if not alias.exists() or not alias.samefile(args.source):
                self.skipTest('temporary filesystem does not alias source and SOURCE')
            # Keep the owned source contract real and isolate external profile
            # checks from this filesystem guard. No source tool may execute.
            with patch.object(p, 'profile_inputs', return_value=({}, [], b'', b'')), \
                 patch.object(p.Runner, 'run', side_effect=ValueError('source command intercepted')) as command, \
                 patch.object(p.subprocess, 'Popen', side_effect=AssertionError('process forbidden')):
                with self.assertRaisesRegex(ValueError, 'disjoint'):
                    p.prepare(args)
                command.assert_not_called()
            self.assertFalse((args.source/'out').exists())

    def test_output_direct_overlap_and_disjoint_entrypoint_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            args = self.source_arguments(root, root/'source'/'out')
            with patch.object(p, 'profile_inputs', return_value=({}, [], b'', b'')), \
                 patch.object(p.Runner, 'run', side_effect=ValueError('source command intercepted')) as command, \
                 patch.object(p.subprocess, 'Popen', side_effect=AssertionError('process forbidden')):
                with self.assertRaisesRegex(ValueError, 'disjoint'):
                    p.prepare(args)
                command.assert_not_called()
                self.assertFalse(args.output.exists())
                args.output = root/'disjoint-output'
                with self.assertRaisesRegex(ValueError, 'source command intercepted'):
                    p.prepare(args)
                command.assert_called_once()
            failure = json.loads((args.output/'first-failure.json').read_text())
            self.assertTrue(failure['source_only'])
            self.assertFalse((args.output/'prepared.json').exists())

    def test_cpu_control_extracts_one_unchanged_production_class_only(self):
        spec = importlib.util.spec_from_file_location('worker_control', Path(__file__).with_name('worker_retirement_control.py'))
        control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(control)
        start = '    class RetirableWorker final : public ControlWorker {'
        end = '    struct ContextWorkers {'
        body = start+'\npublic: int actual_value = 17;\n    };\n'
        self.assertEqual(control.wrapper_source('prefix\n'+body+end+'suffix'), body)
        for invalid in (body, end+body, body+body+end, body+end+end):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                control.wrapper_source(invalid)

    def test_owned_contract_and_exact_order(self):
        lock, data = p.load_contract()
        self.assertEqual(p.digest(data), lock['context_patch_sha256'])
        self.assertEqual(lock['stage_order'], ['official', 'baseline', 'texture', 'context', 'client_jni'])
        self.assertEqual(len(lock['stages']['official']), 61)
        self.assertFalse(lock['native_builder_integration'])
        self.assertFalse(lock['gpu_qualified'])

    def test_changed_binding_control_refuses_default_verification(self):
        original = p.regular
        def read(path, *args, **kwargs):
            data = original(path, *args, **kwargs)
            return data + b'\n// changed\n' if Path(path).name == 'BindingStateTest.java' else data
        with patch.object(p, 'regular', side_effect=read), self.assertRaisesRegex(ValueError, 'binding state control differs'):
            p.load_contract()

    def test_client_jni_requires_its_paired_java_options_image(self):
        original = p.regular
        for mutation in ('missing-java', 'old-java-image'):
            with self.subTest(mutation=mutation):
                lock = json.loads(original(p.HERE/'inputs.lock.json'))
                name = 'takkernel/engine/src/main/java/com/atakmap/map/layer/feature/FeatureLayer3.java'
                if mutation == 'missing-java':
                    del lock['client_jni']['files'][name]
                else:
                    lock['stages']['client_jni'][name] = lock['client_jni']['files'][name]['before']
                def read(path, *args, **kwargs):
                    return json.dumps(lock).encode() if Path(path).name == 'inputs.lock.json' else original(path, *args, **kwargs)
                with patch.object(p, 'regular', side_effect=read), self.assertRaisesRegex(ValueError, 'paired client JNI and Java'):
                    p.load_contract()

    def test_stage_before_and_after_cannot_be_substituted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = 'takkernel/engine/src/main/cpp/example.cpp'
            target = root/name
            target.parent.mkdir(parents=True)
            target.write_bytes(b'before')
            p.verify_stage(root, {name: p.digest(b'before')})
            with self.assertRaises(ValueError):
                p.verify_stage(root, {name: p.digest(b'after')})
            target.write_bytes(b'after')
            p.verify_stage(root, {name: p.digest(b'after')})
            with self.assertRaises(ValueError):
                p.verify_stage(root, {name: p.digest(b'before')})

    def test_new_source_must_not_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = 'takkernel/engine/src/new.h'
            p.verify_stage(root, {name: None})
            target = root/name
            target.parent.mkdir(parents=True)
            target.write_bytes(b'not absent')
            with self.assertRaises(ValueError):
                p.verify_stage(root, {name: None})

    def test_traversal_and_other_components_refused(self):
        for name in ['../x', '/takkernel/engine/src/x', 'takkernel/engine/src/../../x',
                     'commoncommo/src/x', 'takkernel\\engine\\src\\x',
                     'takkernel/port/src/jre/java/gov/tak/platform/commons/opengl/GLES30.java']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                p.relative(name)

    def test_symlink_file_and_parent_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root/'outside'
            outside.write_bytes(b'outside')
            target = root/'takkernel/engine/src/file.cpp'
            target.parent.mkdir(parents=True)
            target.symlink_to(outside)
            with self.assertRaises(ValueError):
                p.verify_stage(root, {'takkernel/engine/src/file.cpp': p.digest(b'outside')})
            target.unlink()
            target.parent.rmdir()
            target.parent.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                p.target(root, 'takkernel/engine/src/file.cpp')

    def test_client_selection_excludes_unconsumed_java(self):
        java = b'--- a/unrelated.java\n+++ b/unrelated.java\n@@ -1 +1 @@\n-old\n+new\n'
        native = b'--- a/takkernel/engine/src/main/jni/a.cpp\n+++ b/takkernel/engine/src/main/jni/a.cpp\n@@ -1 +1 @@\n-old\n+new\n'
        name = 'takkernel/engine/src/main/jni/a.cpp'
        self.assertEqual(p.selected_client_patch(java+native, {name}), native)
        self.assertEqual(p.selected_client_patch(native+java, {name}), native)
        with self.assertRaises(ValueError):
            p.selected_client_patch(java, {name})
        with self.assertRaises(ValueError):
            p.selected_client_patch(native+native, {name})
        with self.assertRaises(ValueError):
            p.selected_client_patch(b'diff --git a/x b/x\n'+native, {name})

    def test_source_size_and_missing_input_are_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'input'
            with self.assertRaises(FileNotFoundError):
                p.regular(path)
            path.write_bytes(b'12345')
            with self.assertRaises(ValueError):
                p.regular(path, limit=4)

    def test_default_verify_invokes_no_process(self):
        with patch('sys.argv', ['prepare.py']), patch.object(p.subprocess, 'Popen', side_effect=AssertionError('process forbidden')):
            p.main()
        with patch('sys.argv', ['prepare.py', '--output', '/unused']), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            p.main()


if __name__ == '__main__':
    unittest.main()
