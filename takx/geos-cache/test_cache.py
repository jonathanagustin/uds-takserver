# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic parser/layout controls; these fixtures do not qualify native code."""
import copy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import sys
import subprocess
import tarfile
import tempfile
import unittest
import unicodedata
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cache as C
import producer as P


def ar(objects):
    raw = b'!<arch>\n'
    for name, body in objects:
        header = f'{name+"/":<16}{0:<12}{0:<6}{0:<6}{"644":<8}{len(body):<10}`\n'.encode()
        raw += header + body + (b'\n' if len(body) % 2 else b'')
    return raw


def elf(marker=0):
    raw = bytearray(64)
    raw[:6], raw[16:18], raw[18:20] = b'\x7fELF\x02\x01', b'\x01\x00', b'\x3e\x00'
    raw[-1] = marker
    return bytes(raw)


class LocalContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.lock = copy.deepcopy(P.LOCK)
        self.lock['component']['license_sha256'] = C.sha(b'synthetic test-only license')
        (self.root / 'inputs.lock.json').write_bytes(C.canonical(self.lock))
        authority = patch.object(C, 'HERE', self.root)
        authority.start(); self.addCleanup(authority.stop)
        t = {n: {'path': '/synthetic/' + n, 'sha256': 'b' * 64, 'version': 'fixture 14.2.0',
                  'version_command': ['/synthetic/' + n, '--version']}
             for n in ('gcc', 'g++', 'cc1', 'cc1plus', 'as', 'ld', 'ar', 'ranlib', 'nm', 'strip',
                       'make', 'patch', 'readelf', 'python3', 'sh', 'bash', 'ldd')}
        self.identity = {'schema': 'uds-geos-capi-inputs-v1', 'component': self.lock['component'],
            'base_image': self.lock['base_image'], 'execution': {'engine_host_arch': 'arm64',
            'container_machine': 'x86_64', 'mode': 'emulated', 'target_os': 'linux', 'target_arch': 'amd64'},
            'source_inventories': {'unpatched': 'a' * 64, 'patched': 'c' * 64},
            'recipes': {n: 'd' * 64 for n in C.RECIPE}, 'tools': t,
            'abi': {'target': 'x86_64-linux-gnu', 'pointer_bytes': '8', 'byte_order': '__ORDER_LITTLE_ENDIAN__',
                    'cxx11_abi': '1', 'exceptions': '1', 'rtti': '1', 'cxx_standard': '201103L',
                    'glibc': '2.36', 'providers': {'/synthetic/libc.so.6': 'e' * 64}},
            'environment': self.lock['environment'], 'configure': self.lock['configure'],
            'build_commands': self.lock['build_commands'],
            'configuration_files': {n:'f'*64 for n in ('Makefile','config.status','include/config.h',
                                                       'include/geos/platform.h','libtool')},
            'configured_features': {'int64_macro':'HAVE_LONG_INT_64','int64_bytes':8,
                                    'probe_source_sha256':C.sha(C.INT64_PROBE.encode())},
            'dependency_digests': [], 'constraints': self.lock['constraints']}
        self.prefix = self.root / 'prefix'; self.prefix.mkdir()
        self.payload = {name: b'fixture header\n' for name in C.FILES}
        self.payload['COPYING'] = b'synthetic test-only license'
        self.payload['lib/libgeos.a'] = ar([('repeated.o', elf(1)), ('repeated.o', elf(2))])
        self.payload['lib/libgeos_c.a'] = ar([('capi.o', elf(3))])
        for name, raw in self.payload.items():
            p = self.prefix / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw); p.chmod(0o644)
        archives = {name: {'objects': C.archive_objects(self.payload[name]),
                           'object_inventory_sha256': C.sha(C.canonical(C.archive_objects(self.payload[name])))}
                    for name in ('lib/libgeos.a', 'lib/libgeos_c.a')}
        self.proof = {'fresh_source_build': True, 'geometry': True, 'whole_archive_pic': True,
            'archives': archives, 'tested_payload': {n: {'bytes':len(v),'sha256':C.sha(v)} for n,v in self.payload.items()}, 'runtime_providers': {'/synthetic/libc.so.6': 'e' * 64},
            'registry_roundtrip': False, 'publisher_authenticated': False,
            'sdk_accepted': False, 'security_accepted': False}

    def packed(self, name='capsule.tar.gz'):
        path = self.root / name
        C.pack(self.prefix, self.identity, self.proof, path)
        return path, C.file_sha(path), C.identity_key(self.identity)

    def test_complete_five_file_projection_and_digest_restores_two_prefixes(self):
        capsule, digest, key = self.packed()
        subject = C.layout(capsule, digest, key, self.root / 'oci')
        self.assertEqual(set(C.verify_layout(self.root / 'oci', subject, key)[1]), C.FILES)
        for name in ('first', 'unrelated/deeper/second'):
            target = self.root / name
            C.restore(self.root / 'oci', subject, key, target)
            self.assertEqual({str(p.relative_to(target)): p.read_bytes()
                              for p in target.rglob('*') if p.is_file()}, self.payload)
        with self.assertRaises(ValueError):
            C.restore(self.root / 'oci', subject, key, self.root / 'first')

    def test_key_sensitivity_and_historical_unknown_or_incomplete_identity_refusal(self):
        original = C.identity_key(self.identity)
        for field in ('recipes', 'source_inventories', 'configuration_files'):
            changed = copy.deepcopy(self.identity); changed[field][next(iter(changed[field]))] = '1' * 64
            self.assertNotEqual(C.identity_key(changed), original)
        for tool in ('cc1', 'cc1plus', 'as', 'ld', 'python3', 'sh'):
            changed = copy.deepcopy(self.identity); changed['tools'][tool]['sha256'] = '1' * 64
            self.assertNotEqual(C.identity_key(changed), original)
        for mutation in ('schema', 'extra', 'patch', 'abi', 'execution', 'tool', 'secret-env', 'missing-recipe'):
            changed = copy.deepcopy(self.identity)
            if mutation == 'schema': changed['schema'] = 'uds-draco-inputs-v2'
            if mutation == 'extra': changed['historical_complete'] = True
            if mutation == 'patch': changed['component']['patch']['files']['ltmain.sh']['after'] = '1' * 64
            if mutation == 'abi': changed['abi']['target'] = 'aarch64-linux-gnu'
            if mutation == 'execution': changed['execution']['mode'] = 'native'
            if mutation == 'tool': del changed['tools']['cc1plus']
            if mutation == 'secret-env': changed['environment']['GH_TOKEN'] = 'private-fixture'
            if mutation == 'missing-recipe': changed['recipes'] = {'invented-only': 'a' * 64}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): C.identity_key(changed)
        for old in ({'schema': 'uds-draco-capsule-v2'}, {'schema_version': 1}, {'complete': True}):
            with self.assertRaises(ValueError): C.identity_key(old)
        self.assertTrue(all('README' not in n and '/test_' not in n for n in P.RECIPE))

    def test_object_order_repeated_names_and_actual_payload_closure_are_bound(self):
        objects = C.archive_objects(self.payload['lib/libgeos.a'])
        self.assertEqual([o['name'] for o in objects], ['repeated.o', 'repeated.o'])
        self.assertEqual([o['ordinal'] for o in objects], [0, 1])
        self.proof['archives']['lib/libgeos.a']['objects'].reverse()
        self.proof['archives']['lib/libgeos.a']['object_inventory_sha256'] = C.sha(C.canonical(
            self.proof['archives']['lib/libgeos.a']['objects']))
        with self.assertRaises(ValueError): self.packed()

    def test_tested_headers_and_final_payload_cannot_drift_or_be_relabelled(self):
        expected = P.payload_inventory(self.prefix)
        control = {'header_sha256': {n: expected['include/' + n]['sha256']
                                    for n in ('geos_c.h', 'geos/export.h')}}
        P.tested_payload(self.prefix, expected, control)
        control['header_sha256']['geos_c.h'] = '0' * 64
        with self.assertRaises(ValueError): P.tested_payload(self.prefix, expected, control)
        (self.prefix / 'include/geos_c.h').write_bytes(b'untested replacement')
        with self.assertRaises(ValueError): self.packed()
        with self.assertRaises(ValueError): P.tested_payload(self.prefix, expected, control)

    def test_thin_nested_nonrel_wrong_arch_and_short_object_archives_refuse(self):
        wrong_type = bytearray(elf()); wrong_type[16] = 3
        wrong_arch = bytearray(elf()); wrong_arch[18:20] = b'\xb7\x00'
        for raw in (b'!<thin>\n', ar([('../outside.o', elf())]), ar([('x.o', b'bad')]),
                    ar([('x.o', wrong_type)]), ar([('x.o', wrong_arch)]), ar([('x.o', elf())])[:-1]):
            with self.subTest(raw=raw[:8]), self.assertRaises((ValueError, UnicodeError)):
                C.archive_objects(raw)

    def test_tar_metadata_padding_duplicates_links_sparse_and_truncation_refuse(self):
        capsule, _, _ = self.packed()
        original = gzip.decompress(capsule.read_bytes())
        offset = 0; first = tarfile.TarInfo.frombuf(original[:512], 'utf-8', 'strict')
        padding = 512 + first.size
        end = 0
        while original[end:end+512] != bytes(512):
            member = tarfile.TarInfo.frombuf(original[end:end+512], 'utf-8', 'strict')
            end += 512 + ((member.size+511)//512)*512
        mutations = [original[:1024], original + b'private-trailing', original[:end]]
        if padding % 512:
            changed = bytearray(original); changed[padding] = 65; mutations.append(changed)
        mutations.append(original[:end] + original[:512 + ((first.size+511)//512)*512] + bytes(1024))
        for field in ('mtime', 'uname', 'type', 'sparse'):
            changed = bytearray(original); member = copy.copy(first)
            if field == 'mtime': member.mtime = 17
            if field == 'uname': member.uname = 'private-unrecorded'
            if field == 'type': member.type = tarfile.SYMTYPE
            if field == 'sparse': member.type = tarfile.GNUTYPE_SPARSE
            changed[:512] = member.tobuf(format=tarfile.USTAR_FORMAT); mutations.append(changed)
        for raw in mutations:
            with self.subTest(length=len(raw)), self.assertRaises((ValueError, tarfile.TarError)):
                C.tar_files(raw)

    def test_manifest_or_oci_corruption_wrong_key_and_extra_payload_refuse(self):
        capsule, digest, key = self.packed()
        for supplied_digest, supplied_key in (('0' * 64, key), (digest, 'geos-capi-local-v1-' + '0' * 64)):
            with self.assertRaises(ValueError): C.verify(capsule, supplied_digest, supplied_key)
        subject = C.layout(capsule, digest, key, self.root / 'oci')
        blob = self.root / 'oci/blobs/sha256' / subject[7:]
        original = blob.read_bytes(); blob.write_bytes(original + b' ')
        with self.assertRaises(ValueError): C.verify_layout(self.root / 'oci', subject, key)
        blob.write_bytes(original); (self.root / 'oci/private.log').write_text('private-fixture')
        with self.assertRaises(ValueError): C.verify_layout(self.root / 'oci', subject, key)
        (self.root / 'oci/private.log').unlink(); (self.root / 'oci/private-empty-directory').mkdir()
        with self.assertRaises(ValueError): C.verify_layout(self.root / 'oci', subject, key)
        with self.assertRaises(ValueError): C.decode(b'{"duplicate":1,"duplicate":2}')

    def test_gzip_private_metadata_concatenation_and_truncated_framing_refuse(self):
        capsule, _, key = self.packed(); original=capsule.read_bytes()
        changed=bytearray(original);changed[4:8]=(17).to_bytes(4,'little')
        stream=io.BytesIO()
        with gzip.GzipFile(fileobj=stream,filename='unrecorded-private-metadata',mtime=0,mode='wb') as gz:
            gz.write(gzip.decompress(original))
        for raw in (changed,stream.getvalue(),original+gzip.compress(b'',mtime=0),original[:-4],original+b'private'):
            target=self.root/'invalid-framing.tar.gz';target.write_bytes(raw)
            with self.assertRaises(ValueError):C.verify(target,C.file_sha(target),key)

    def test_projection_rejects_executable_symlink_and_claim_promotion(self):
        path = self.prefix / 'include/geos_c.h'; path.chmod(0o755)
        with self.assertRaises(ValueError): self.packed()
        path.unlink(); path.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError): self.packed()
        for claim in ('registry_roundtrip', 'publisher_authenticated', 'sdk_accepted', 'security_accepted'):
            changed = copy.deepcopy(self.proof); changed[claim] = True
            with self.assertRaises(ValueError): C.qualification(changed)

    def test_restore_and_layout_public_apis_reject_alias_or_input_output_overlap(self):
        capsule, digest, key = self.packed()
        subject = C.layout(capsule, digest, key, self.root / 'oci')
        for target in (self.root / 'oci/restore', P.ROOT / '__geos_restore_never_created__'):
            with self.assertRaises(ValueError): C.restore(self.root / 'oci', subject, key, target)
            self.assertFalse(target.exists())
        with self.assertRaises(ValueError): C.layout(capsule, digest, key, self.root)


class ProducerBoundaries(unittest.TestCase):
    def test_new_source_pins_and_secret_free_environment_preserve_old_profile(self):
        P.historical_helpers()
        self.assertEqual(P.ENV['CONFIG_SITE'], '/dev/null')
        self.assertEqual(P.ENV['CXXFLAGS'], '-O2 -fPIC -std=c++11 -march=x86-64 -mtune=generic')
        self.assertFalse(any(k in P.ENV for k in ('GH_TOKEN', 'GITHUB_TOKEN', 'SSH_AUTH_SOCK', 'BASH_ENV')))
        self.assertEqual(P.LOCK['constraints']['cpus'], 1)
        self.assertEqual(P.LOCK['constraints']['memory_bytes'], 3 * 1024**3)
        self.assertEqual(P.LOCK['budgets']['jobs'], 1)

    def test_active_64_bit_feature_requires_both_generated_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'include/geos').mkdir(parents=True)
            headers=[root/'include/config.h',root/'include/geos/platform.h']
            for p in headers:p.write_text('#define HAVE_LONG_INT_64 1\n')
            self.assertEqual(P.configured_features(root)['int64_macro'],'HAVE_LONG_INT_64')
            for raw in ('/* #undef HAVE_LONG_INT_64 */\n','#define HAVE_INT64_T_64 1\n',
                        '#define HAVE_LONG_INT_64 1\n#define HAVE_LONG_LONG_INT_64 1\n'):
                headers[1].write_text(raw)
                with self.assertRaises(ValueError):P.configured_features(root)
            self.assertIn('-std=gnu89',P.ENV['CFLAGS'])
            self.assertNotIn('-Wno',P.ENV['CFLAGS'])

    def test_external_paths_reject_repository_overlap_and_alias_before_acquisition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); first = root / 'first'; second = root / 'second'
            self.assertEqual(C.external_separate(first, second), [first, second])
            alias = root / 'alias'; alias.symlink_to(root, target_is_directory=True)
            for output, inputs in ((P.ROOT / 'private-output', second), (first, first / 'source'),
                                   (first / 'output', first), (first, first), (alias / 'output', second)):
                with self.subTest(output=output), self.assertRaises(ValueError):
                    C.external_separate(output, inputs)
            with patch.object(sys, 'argv', ['producer', '--output', str(P.ROOT / 'private-output'),
                    '--source-cache', str(second)]), patch.object(P, 'acquire') as acquire, \
                    patch.object(C, 'new_directory') as create, self.assertRaises(ValueError):
                P.main()
            acquire.assert_not_called(); create.assert_not_called()

    def test_owned_cleanup_is_independent_of_expired_runner_and_full_output(self):
        name = 'uds-geos-' + 'a' * 32
        with patch.object(P.Runner, '__call__', side_effect=ValueError('expired/quota')) as runner, \
                patch.object(P.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            P.cleanup_owned('/owned/engine', name, {'PATH': '/usr/bin:/bin'})
            runner.assert_not_called()
            self.assertEqual(run.call_args.args[0], ['/owned/engine', 'rm', '-f', name])
            self.assertEqual(run.call_args.kwargs['timeout'], 15)
            self.assertEqual(run.call_args.kwargs['stdout'], subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs['stderr'], subprocess.DEVNULL)
        with patch.object(P.subprocess, 'run') as run, self.assertRaises(ValueError):
            P.cleanup_owned('/owned/engine', 'unrelated-container', {})
        run.assert_not_called()

    def test_real_filesystem_repository_and_unicode_case_aliases_refuse_without_repo_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); unrelated = root / 'cache'
            alias = P.ROOT.with_name(P.ROOT.name.upper())
            if alias.exists() and alias.samefile(P.ROOT):
                target = alias / '__geos_apfs_alias_probe_never_created__'
                self.assertFalse(target.exists())
                with self.assertRaises(ValueError): C.external_separate(target, unrelated)
                self.assertFalse(target.exists())
            composed = root / 'Caf\u00e9'; composed.mkdir()
            decomposed = root / unicodedata.normalize('NFD', composed.name).upper()
            for first, second in ((composed / 'Cache', decomposed / 'cache/output'),
                                  (root / 'NEW-CACHE', root / 'new-cache/output')):
                with self.assertRaises(ValueError): C.external_separate(first, second)


if __name__ == '__main__':
    unittest.main()
