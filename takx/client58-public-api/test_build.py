# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline profile guards; real Java controls are explicit and separately bound."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('vehicle_profile', Path(__file__).with_name('build.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class Guards(unittest.TestCase):
    def test_default_verification_cannot_start_a_process(self):
        with patch('subprocess.Popen', side_effect=AssertionError('no process')), patch('sys.argv', ['build.py']):
            P.main()
        self.assertFalse(P.contract()['full_client_accepted'])

    def test_acceptance_and_foreign_source_membership_are_refused(self):
        for mutation in ('acceptance', 'membership', 'source'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for name in [*P.contract()['owned'], 'inputs.lock.json']:
                    dest = root / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(P.HERE / name, dest)
                lock = json.loads((root / 'inputs.lock.json').read_text())
                if mutation == 'acceptance':
                    lock['full_client_accepted'] = True
                elif mutation == 'membership':
                    lock['owned']['foreign.java'] = '0' * 64
                else:
                    (root / P.SOURCE).write_text('unreviewed source')
                (root / 'inputs.lock.json').write_text(json.dumps(lock))
                with patch.object(P, 'HERE', root), self.assertRaises(ValueError):
                    P.contract()

    def test_changed_or_symlinked_java_proof_is_refused_before_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'receipt.json').write_text('{}')
            args = SimpleNamespace(java_proof=root, java_proof_sha256='0' * 64,
                                   java_home=root / 'missing-jdk', output=root / 'output')
            with patch('subprocess.Popen', side_effect=AssertionError('no process')), self.assertRaises(ValueError):
                P.controls(args)
            self.assertFalse(args.output.exists())
            alias = root / 'alias'
            alias.symlink_to(root, target_is_directory=True)
            args.java_proof = alias
            with self.assertRaises(ValueError):
                P.controls(args)

    def test_path_digest_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'real').write_bytes(b'input')
            (root / 'link').symlink_to(root / 'real')
            with self.assertRaises(ValueError):
                P.sha(root / 'link')


class ArtifactGuards(unittest.TestCase):
    def classes(self, root):
        for name in P.LIBRARY_CLASSES:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'owned packaging fixture '+name.encode())

    def test_archive_is_deterministic_source_carrying_and_excludes_controls(self):
        import zipfile
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);classes=root/'classes';self.classes(classes)
            first=P.package_library(classes,root/'first.jar',P.contract())
            second=P.package_library(classes,root/'second.jar',P.contract())
            self.assertEqual((root/'first.jar').read_bytes(),(root/'second.jar').read_bytes())
            self.assertEqual(first['sha256'],second['sha256'])
            with zipfile.ZipFile(root/'first.jar') as archive:
                self.assertEqual({n for n in archive.namelist() if n.endswith('.class')},P.LIBRARY_CLASSES)
                self.assertEqual(archive.read('META-INF/LICENSE'),(P.REPO/'LICENSE').read_bytes())
                source='META-INF/source/gov/tak/api/cot/detail/VehicleModelDetailHandler.java'
                self.assertEqual(archive.read(source),(P.HERE/P.SOURCE).read_bytes())
                self.assertTrue(all(i.date_time==(1980,1,1,0,0,0) for i in archive.infolist()))

    def test_missing_foreign_or_symlinked_class_cannot_acquire_an_artifact(self):
        for mutation in ('missing','foreign','symlink'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);classes=root/'classes';self.classes(classes)
                path=classes/sorted(P.LIBRARY_CLASSES)[0]
                if mutation=='missing':path.unlink()
                elif mutation=='foreign':(classes/'Controls.class').write_bytes(b'not a library class')
                else:
                    original=root/'outside';path.rename(original);path.symlink_to(original)
                with self.assertRaises(ValueError):P.package_library(classes,root/'api.jar',P.contract())
                self.assertFalse((root/'api.jar').exists())

    def test_artifact_build_requires_explicit_reviewed_inputs(self):
        with patch('subprocess.Popen',side_effect=AssertionError('no process')),patch('sys.argv',['build.py','--phase','build']):
            with self.assertRaisesRegex(ValueError,'explicit reviewed inputs'):P.main()
