# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Strict inventory, source binding and fail-closed scanner adapter controls."""

import base64
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('strict_scan', ROOT / '.github/scripts/strict-scan-compare.py')
S = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(S)
IMAGE = 'registry.example/team/image:current'
CONFIG = json.dumps({'os': 'linux', 'architecture': 'amd64'}).encode()
NATIVE_MANIFEST = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
    'config': {'mediaType': 'application/vnd.oci.image.config.v1+json',
               'digest': 'sha256:' + S.sha(CONFIG), 'size': len(CONFIG)}, 'layers': []}).encode()
DIGEST = 'sha256:' + S.sha(NATIVE_MANIFEST)
MIRROR = '127.0.0.1:5000/team/image@' + DIGEST


def manifest(path, components, *, api_version=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {'kind': 'ZarfPackageConfig', 'components': components}
    if api_version is not None:
        doc['apiVersion'] = api_version
    path.write_text(yaml.safe_dump(doc))


def component_manifest(path, component):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({
        'apiVersion': 'zarf.dev/v1beta1',
        'kind': 'ZarfComponentConfig',
        'metadata': {'name': path.parent.name, 'version': '0.1.0'},
        'component': component,
    }))


def sbom(image='registry.example/team/image:released', arch='amd64'):
    config = json.dumps({'architecture': arch, 'os': 'linux'}).encode()
    return {'source': {'type': 'image', 'metadata': {'userInput': image,
        'config': base64.b64encode(config).decode(), 'imageID': 'sha256:' + S.sha(config)}},
        'artifacts': [{'id': 'opaque-package-id', 'name': 'fixture-package', 'version': '1',
                       'purl': 'pkg:generic/fixture-package@1'}]}


def catalog(image, *, catalog=True):
    identity = S.reference(image)
    result = {'bomFormat': 'CycloneDX', 'specVersion': '1.6', 'metadata': {'component': {
        'type': 'container', 'name': identity['repository'], 'version': identity['version'][1:],
        'bom-ref': 'opaque-root-image-id'}},
        'vulnerabilities': []}
    if catalog:
        result['components'] = [{'type': 'library', 'name': 'fixture-package', 'version': '1',
                                 'bom-ref': 'opaque-package-id',
                                 'purl': 'pkg:generic/fixture-package@1'}]
    return result


class InventoryTests(unittest.TestCase):
    def test_actual_package_includes_all_eleven_images_and_local_import(self):
        images, sources = S.inventory(ROOT / 'zarf.yaml', 'upstream')
        self.assertEqual(len(images), 11)
        self.assertEqual(set(sources), {'zarf.yaml', 'common/zarf.yaml', 'common/cloudtak/zarf.yaml'})
        for name in ('takserver-api', 'takserver-config', 'takserver-ignite', 'cloudtak', 'postgis'):
            self.assertTrue(any('/' + name + ':' in image for image in images))

    def test_same_flavor_union_common_import_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest(root / 'common/zarf.yaml', [{'name': 'first', 'images': ['registry.example/common:1']}])
            manifest(root / 'zarf.yaml', [
                {'name': 'first', 'only': {'flavor': 'upstream'}, 'import': {'path': 'common'}, 'images': [IMAGE]},
                {'name': 'second', 'only': {'flavor': 'upstream'}, 'images': [IMAGE, 'registry.example/second:1']},
                {'name': 'common', 'images': ['registry.example/all:1']},
                {'name': 'other', 'only': {'flavor': 'other'}, 'images': ['registry.example/excluded:1']},
            ])
            images, _ = S.inventory(root / 'zarf.yaml', 'upstream')
            self.assertEqual(set(images), {IMAGE, 'registry.example/common:1',
                                         'registry.example/second:1', 'registry.example/all:1'})

    def test_temporary_rewritten_manifest_keeps_imports_relative_to_source_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'; temporary = Path(directory) / 'rewritten.yaml'
            manifest(root / 'common/zarf.yaml', [{'name': 'first', 'images': [IMAGE]}])
            manifest(temporary, [{'name': 'first', 'import': {'path': 'common'}}])
            self.assertEqual(S.inventory(temporary, 'upstream', root)[0], [IMAGE])

    def test_unresolved_remote_cycle_and_unsupported_selectors_refuse(self):
        for component in (
            {'name': 'first', 'images': [IMAGE], 'import': {'path': 'missing'}},
            {'name': 'first', 'images': [IMAGE], 'import': {'url': 'oci://example/image:1'}},
            {'name': 'first', 'images': [IMAGE], 'import': {'path': '.'}},
            {'name': 'first', 'images': [IMAGE], 'only': {'localOS': 'linux'}},
        ):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'zarf.yaml'; manifest(path, [component])
                with self.assertRaises((S.Rejected, OSError)):
                    S.inventory(path, 'upstream')

    def test_ambiguous_components_duplicate_yaml_and_empty_inventory_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'zarf.yaml'
            for components in ([], [{'name': 'same', 'images': [IMAGE]}] * 2):
                manifest(path, components)
                with self.assertRaises(S.Rejected):
                    S.inventory(path, 'upstream')
            path.write_text('components: []\ncomponents: []\n')
            with self.assertRaisesRegex(S.Rejected, 'duplicate'):
                S.inventory(path, 'upstream')

    def test_v1beta1_package_and_component_config_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            component_manifest(root / 'common/zarf.yaml', {'images': [{'name': 'registry.example/common:1'}]})
            manifest(root / 'zarf.yaml', [
                {'name': 'first', 'selector': {'flavor': 'upstream'},
                 'import': {'local': [{'path': 'common/zarf.yaml'}]},
                 'images': [{'name': IMAGE}]},
                {'name': 'second', 'selector': {'flavor': 'upstream'},
                 'images': [{'name': IMAGE}, {'name': 'registry.example/second:1'}]},
                {'name': 'common', 'images': [{'name': 'registry.example/all:1'}]},
                {'name': 'other', 'selector': {'flavor': 'other'},
                 'images': [{'name': 'registry.example/excluded:1'}]},
            ], api_version='zarf.dev/v1beta1')
            images, sources = S.inventory(root / 'zarf.yaml', 'upstream')
            self.assertEqual(set(images), {IMAGE, 'registry.example/common:1',
                                           'registry.example/second:1', 'registry.example/all:1'})
            self.assertEqual(set(sources), {'zarf.yaml', 'common/zarf.yaml'})

    def test_v1beta1_staged_production_inventory_matches_v1alpha1(self):
        from tests import test_zarf_beta_production_parity as parity
        expected_images, expected_sources = S.inventory(ROOT / 'zarf.yaml', 'upstream')
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory)
            parity.stage_beta_tree(dest)
            beta_images, sources = S.inventory(dest / 'zarf.yaml', 'upstream')
            self.assertEqual(beta_images, expected_images)
            self.assertEqual(len(beta_images), 11)
            self.assertEqual(set(sources), set(expected_sources))

    def test_v1beta1_rejects_invalid_images_selectors_and_imports(self):
        cases = (
            {'name': 'first', 'images': [IMAGE]},
            {'name': 'first', 'only': {'flavor': 'upstream'}, 'images': [{'name': IMAGE}]},
            {'name': 'first', 'selector': {'architecture': 'amd64'}, 'images': [{'name': IMAGE}]},
            {'name': 'first', 'import': {'path': 'common'}, 'images': [{'name': IMAGE}]},
            {'name': 'first', 'import': {'oci': 'oci://example/pkg:1'}, 'images': [{'name': IMAGE}]},
            {'name': 'first', 'import': {'local': []}, 'images': [{'name': IMAGE}]},
            {'name': 'first', 'import': {'local': [{'path': 'zarf.yaml'}]}, 'images': [{'name': IMAGE}]},
        )
        for component in cases:
            with self.subTest(component=component), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'zarf.yaml'
                manifest(path, [component], api_version='zarf.dev/v1beta1')
                with self.assertRaises(S.Rejected):
                    S.inventory(path, 'upstream')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'zarf.yaml'
            manifest(path, [{'name': 'first', 'selector': {'flavor': 'upstream'}, 'images': [IMAGE]}])
            with self.assertRaises(S.Rejected):
                S.inventory(path, 'upstream')


class CandidateTests(unittest.TestCase):
    def state(self):
        return {'schema': 'uds-takserver/candidate-consumer/v1', 'groups': {'fixture': {'images': [{
            'ref': IMAGE, 'mirror': '127.0.0.1:5000/team/image:current',
            'mirror_manifest_digest': DIGEST, 'config_digest': 'sha256:' + 'b' * 64}]}}}

    def test_hash_and_shared_policy_replay_precede_exact_full_reference_bijection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'; state = self.state()
            path.write_text(json.dumps(state)); digest = S.sha(path.read_bytes())
            with patch.object(S, 'validate_candidate_source', return_value=state) as validator:
                mappings, receipt = S.candidate_mappings([MIRROR], [IMAGE + '=' + MIRROR], path, digest)
            validator.assert_called_once_with(path)
            self.assertEqual(mappings, {MIRROR: IMAGE})
            self.assertEqual(receipt['sha256'], digest)
            self.assertIn('not independent', receipt['verification_authority'])

    def test_missing_wrong_hash_validator_refusal_and_post_validation_mutation_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'; state = self.state()
            path.write_text(json.dumps(state)); digest = S.sha(path.read_bytes())
            for bad in (None, '0' * 64):
                with self.subTest(hash=bad), patch.object(S, 'validate_candidate_source') as validator:
                    with self.assertRaises(S.Rejected):
                        S.candidate_mappings([MIRROR], [IMAGE + '=' + MIRROR], path, bad)
                    validator.assert_not_called()
            with patch.object(S, 'validate_candidate_source', side_effect=ValueError('source policy refused')):
                with self.assertRaises(ValueError):
                    S.candidate_mappings([MIRROR], [IMAGE + '=' + MIRROR], path, digest)
            def changed(_path):
                path.write_text(json.dumps(state) + '\n')
                return state
            with patch.object(S, 'validate_candidate_source', side_effect=changed):
                with self.assertRaisesRegex(S.Rejected, 'changed_during_validation'):
                    S.candidate_mappings([MIRROR], [IMAGE + '=' + MIRROR], path, digest)

    def test_mutable_nonnumeric_missing_duplicate_and_unused_overrides_refuse(self):
        for images, overrides in (
            ([MIRROR], []), ([MIRROR], [IMAGE + '=' + MIRROR] * 2),
            ([MIRROR], ['image=' + MIRROR]),
            (['127.0.0.1:5000/team/image:mutable'], [IMAGE + '=127.0.0.1:5000/team/image:mutable']),
            ([MIRROR], [IMAGE + '=localhost:5000/team/image@' + DIGEST]),
            ([IMAGE], [IMAGE + '=' + MIRROR]),
        ):
            with self.subTest(overrides=overrides), self.assertRaises(S.Rejected):
                S.candidate_mappings(images, overrides)

    def test_direct_registry_mode_does_not_import_candidate_validator(self):
        with patch.object(S, 'validate_candidate_source') as validator:
            self.assertEqual(S.candidate_mappings([IMAGE], []), ({}, None))
            validator.assert_not_called()


class ArchiveTests(unittest.TestCase):
    def archive(self, root, members, *, truncate=False):
        path = root / 'sboms.tar'
        with tarfile.open(path, 'w', format=tarfile.PAX_FORMAT) as archive:
            for member, data in members:
                archive.addfile(member, io.BytesIO(data) if data else None)
        if truncate:
            path.write_bytes(path.read_bytes()[:1024])
        return path

    def member(self, name='image.json', image='registry.example/team/image:released', arch='amd64'):
        raw = json.dumps(sbom(image, arch)).encode()
        member = tarfile.TarInfo(name); member.size = len(raw)
        return member, raw

    def test_real_regular_catalog_is_mapped_by_full_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); out = root / 'out'; out.mkdir()
            result = S.released_sboms(self.archive(root, [self.member()]), out, 'amd64')
            self.assertEqual(set(result), {'registry.example/team/image'})

    def test_traversal_links_sparse_duplicate_wrong_arch_and_missing_end_blocks_refuse(self):
        cases = []
        cases.append(([self.member('../image.json')], False))
        link = tarfile.TarInfo('link.json'); link.type = tarfile.SYMTYPE; link.linkname = 'image.json'
        cases.append(([(link, b'')], False))
        sparse, raw = self.member()
        sparse.pax_headers = {'GNU.sparse.map': '0,' + str(len(raw)), 'GNU.sparse.size': str(len(raw))}
        cases.append(([(sparse, raw)], False))
        cases.append(([self.member(), self.member()], False))
        cases.append(([self.member(arch='arm64')], False))
        cases.append(([self.member()], True))
        cases.append(([self.member(), self.member('second.json', 'registry.example/team/image:other')], False))
        for index, (members, truncate) in enumerate(cases):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); out = root / 'out'; out.mkdir()
                with self.assertRaises((S.Rejected, tarfile.TarError)):
                    S.released_sboms(self.archive(root, members, truncate=truncate), out, 'amd64')

    def test_config_os_image_id_and_catalog_are_consistent(self):
        for failure in ('os', 'image-id', 'catalog', 'duplicate-id'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); out = root / 'out'; out.mkdir()
                value = sbom()
                if failure == 'os':
                    config = json.dumps({'os': 'windows', 'architecture': 'amd64'}).encode()
                    value['source']['metadata'].update(config=base64.b64encode(config).decode(),
                                                       imageID='sha256:' + S.sha(config))
                if failure == 'image-id': value['source']['metadata']['imageID'] = 'sha256:' + 'c' * 64
                if failure == 'catalog': value['artifacts'] = []
                if failure == 'duplicate-id':
                    value['artifacts'].append({**value['artifacts'][0], 'name': 'other-package'})
                raw = json.dumps(value).encode(); member = tarfile.TarInfo('image.json'); member.size = len(raw)
                with self.assertRaises(S.Rejected):
                    S.released_sboms(self.archive(root, [(member, raw)]), out, 'amd64')


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.config = CONFIG
        configuration = patch.object(S, 'current_configuration', side_effect=lambda *_: self.config)
        configuration.start()
        self.addCleanup(configuration.stop)

    def fixture(self, root, failure=''):
        db = root / 'db'; db.mkdir(); (db / 'snapshot').write_bytes(b'frozen database')
        scans = root / 'scans'; scans.mkdir()
        baseline = {'registry.example/team/image': {'ref': 'registry.example/team/image:released',
                    'path': root / 'source-sbom.json', 'sha256': 'b' * 64}}
        calls = []
        self.config = CONFIG
        if failure == 'wrong-platform':
            self.config = json.dumps({'os': 'windows', 'architecture': 'amd64'}).encode()
        def run(command, **kwargs):
            calls.append((command, kwargs))
            if command[:5] == ['uds', 'zarf', 'tools', 'registry', 'digest']:
                count = sum(call[0][:5] == command[:5] for call in calls)
                return (('sha256:' + 'c' * 64) if failure == 'tag-race' and count > 1 else DIGEST).encode()
            if command[:5] == ['uds', 'zarf', 'tools', 'registry', 'manifest']:
                return NATIVE_MANIFEST
            if command[0] == 'grype':
                if failure == 'scanner-error':
                    raise S.Rejected('tool_failed')
                source = command[-1]
                expected = 'registry.example/team/image:released' if source.startswith('sbom:') else source[9:]
                if failure == 'wrong-image':
                    expected = 'other.example/team/image:wrong'
                result = catalog(expected, catalog=failure != 'empty-catalog')
                if failure == 'files-only':
                    result['components'] = [{'type': 'file', 'name': '/fixture'}]
                if failure == 'duplicate-id':
                    result['components'].append({**result['components'][0], 'name': 'other-package'})
                if failure != 'missing-result':
                    Path(command[command.index('--file') + 1]).write_text(json.dumps(result))
                if failure == 'database-race':
                    (db / 'snapshot').write_bytes(b'changed database')
                return b''
            return b'' if failure == 'empty-comparator' else (
                b'### fixture\nNew vulnerabilities: 0\nFixed vulnerabilities: 0\nExisting vulnerabilities: 0\n')
        return db, scans, baseline, calls, run

    def test_complete_independent_scan_pair_with_zero_vulnerabilities_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db, scans, baseline, calls, run = self.fixture(root)
            markdown, receipt = S.compare_inventory([IMAGE], {}, baseline, scans, db, 'amd64', run)
            self.assertIn('New vulnerabilities: 0', markdown)
            self.assertEqual(len(receipt['comparisons']), 1)
            self.assertEqual(len([call for call in calls if call[0][0] == 'grype']), 2)
            self.assertEqual(len([call for call in calls if call[0][0] == 'uds']), 3)
            for command, options in calls:
                if command[0] == 'grype':
                    self.assertEqual(options['environment']['GRYPE_DB_AUTO_UPDATE'], 'false')
                    self.assertEqual(options['environment']['GRYPE_REGISTRY_INSECURE_USE_HTTP'], 'false')
                    self.assertIn('linux/amd64', command)

    def test_missing_invalid_empty_changed_database_and_changed_native_payload_fail(self):
        for failure in ('scanner-error', 'missing-result', 'wrong-image', 'empty-catalog',
                        'empty-comparator', 'database-race', 'tag-race', 'wrong-platform', 'files-only',
                        'duplicate-id'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); db, scans, baseline, _, run = self.fixture(root, failure)
                with self.assertRaises(S.Rejected):
                    S.compare_inventory([IMAGE], {}, baseline, scans, db, 'amd64', run)

    def test_absent_new_or_duplicate_repository_fails_before_scanner_execution(self):
        for images, baseline_missing in (([IMAGE], True),
            (['registry.example/new/image:1'], False), ([IMAGE, 'registry.example/team/image:second'], False)):
            with self.subTest(images=images), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); db, scans, baseline, calls, run = self.fixture(root)
                with self.assertRaises(S.Rejected):
                    S.compare_inventory(images, {}, {} if baseline_missing else baseline, scans, db, 'amd64', run)
                self.assertEqual(calls, [])

    def test_http_is_enabled_only_for_the_exact_loopback_digest_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db, scans, baseline, calls, run = self.fixture(root)
            S.compare_inventory([MIRROR], {MIRROR: IMAGE}, baseline, scans, db, 'amd64', run)
            grype = [call for call in calls if call[0][0] == 'grype']
            self.assertEqual(grype[0][1]['environment']['GRYPE_REGISTRY_INSECURE_USE_HTTP'], 'false')
            self.assertEqual(grype[1][1]['environment']['GRYPE_REGISTRY_INSECURE_USE_HTTP'], 'true')
            self.assertEqual(grype[1][0][-1], 'registry:' + MIRROR)


class CurrentPlatformTests(unittest.TestCase):
    def test_supported_manifest_command_and_hash_bound_native_configuration(self):
        for platform in (('linux', 'amd64'), ('linux', 'arm64'), ('windows', 'amd64')):
            with self.subTest(platform=platform):
                config = json.dumps({'os': platform[0], 'architecture': platform[1]}).encode()
                manifest = json.loads(NATIVE_MANIFEST)
                manifest['config'].update(digest='sha256:' + S.sha(config), size=len(config))
                raw = json.dumps(manifest).encode()
                image = 'docker.io/library/nats@sha256:' + S.sha(raw)
                def run(command, **_options):
                    self.assertEqual(command, ['uds', 'zarf', 'tools', 'registry', 'manifest', image,
                                               '--platform', 'linux/amd64'])
                    return raw
                with patch.object(S, 'current_configuration', return_value=config):
                    if platform == ('linux', 'amd64'):
                        self.assertEqual(S.current_platform(image, 'amd64', run), 'sha256:' + S.sha(config))
                    else:
                        with self.assertRaisesRegex(S.Rejected, 'wrong_current_image_platform'):
                            S.current_platform(image, 'amd64', run)
        for manifest, config in ((NATIVE_MANIFEST + b'\n', CONFIG), (NATIVE_MANIFEST, CONFIG + b' ')):
            with patch.object(S, 'current_configuration', return_value=config), self.assertRaises(S.Rejected):
                S.current_platform('docker.io/library/nats@' + DIGEST, 'amd64', lambda *_a, **_k: manifest)

    def test_public_config_routes_are_scoped_and_unknown_registries_refuse(self):
        for host, endpoint, redirect in (
            ('docker.io', 'https://registry-1.docker.io', 'docker'),
            ('ghcr.io', 'https://ghcr.io', True), ('127.0.0.1:5000', 'http://127.0.0.1:5000', False)):
            with self.subTest(host=host):
                calls = []
                def fetch(url, _limit, headers=None, **options):
                    calls.append((url, headers, options))
                    return b'{"token":"public-scoped-token"}' if '/token?' in url else CONFIG
                with patch.object(S, 'fetch', side_effect=fetch):
                    result = S.current_configuration(host + '/team/image@' + DIGEST,
                        json.loads(NATIVE_MANIFEST)['config'])
                self.assertEqual(result, CONFIG)
                self.assertEqual(calls[-1][0], endpoint + '/v2/team/image/blobs/sha256:' + S.sha(CONFIG))
                self.assertEqual(calls[-1][2]['redirect'], redirect)
                if host.startswith('127.'):
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[-1][1], {})
                else:
                    self.assertIn('scope=repository%3Ateam%2Fimage%3Apull', calls[0][0])
        with patch.object(S, 'fetch') as fetch, self.assertRaisesRegex(S.Rejected, 'unsupported_current_registry'):
            S.current_configuration(IMAGE.replace(':current', '@' + DIGEST), {})
        fetch.assert_not_called()

    def test_blob_redirects_strip_authorization_and_reject_unapproved_destinations(self):
        import urllib.request
        request = urllib.request.Request('https://registry-1.docker.io/v2/team/image/blobs/' + DIGEST,
                                         headers={'Authorization': 'Bearer private-sentinel'})
        handler = S.SafeRedirect({'production.cloudfront.docker.com'})
        redirected = handler.redirect_request(request, None, 302, '', {},
                                               'https://production.cloudfront.docker.com/public-config')
        self.assertFalse(redirected.has_header('Authorization'))
        for target in ('http://production.cloudfront.docker.com/x', 'https://evil.example/x',
                       'https://production.cloudfront.docker.com:8443/x',
                       'https://user@production.cloudfront.docker.com/x'):
            with self.subTest(target=target), self.assertRaises(S.Rejected):
                handler.redirect_request(request, None, 302, '', {}, target)


class RedirectDiagnosticTests(unittest.TestCase):
    def test_docker_storage_redirect_is_exact_scoped_and_strips_authorization(self):
        host = 'docker-images-prod.s3.dualstack.us-east-1.amazonaws.com'
        self.assertEqual(S.sha(host.encode()),
                         '045e09cab0f0c6072ce2eb29bf5da5e431de3e34c53c8d2a62fffc414455b1f6')
        request = S.urllib.request.Request('https://registry-1.docker.io/v2/team/image/blobs/' + DIGEST,
                                          headers={'Authorization': 'Bearer private-token'})
        for mode in ('docker', True, False):
            with self.subTest(mode=mode), patch.object(S.urllib.request, 'build_opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = CONFIG
                self.assertEqual(S.fetch(request.full_url, 1024, redirect=mode), CONFIG)
                handler = opener.call_args.args[0]
                target = 'https://' + host + '/private-path?token=private-token'
                if mode == 'docker':
                    redirected = handler.redirect_request(request, None, 307, '', {}, target)
                    self.assertFalse(redirected.has_header('Authorization'))
                    for bad in ('http://' + host + '/x', 'https://' + host + ':8443/x',
                                'https://user:password@' + host + '/x',
                                'https://' + host + '.evil.example/x',
                                'https://other-bucket.s3.dualstack.us-east-1.amazonaws.com/x'):
                        with self.subTest(target=bad), self.assertRaises(S.RegistryRedirectRejected):
                            handler.redirect_request(request, None, 307, '', {}, bad)
                else:
                    with self.assertRaises(S.Rejected):
                        handler.redirect_request(request, None, 307, '', {}, target)

    def test_current_config_rejection_adds_only_fixed_stage_and_source(self):
        for host, source in (('docker.io', 'docker'), ('ghcr.io', 'ghcr'),
                             ('127.0.0.1:5000', 'loopback')):
            with self.subTest(source=source):
                error = S.RegistryRedirectRejected('https://cdn.example/private?token=private-token')
                replies = [error] if source == 'loopback' else [b'{"token":"private-token"}', error]
                with patch.object(S, 'fetch', side_effect=replies), self.assertRaises(
                        S.RegistryRedirectRejected) as caught:
                    S.current_configuration(host + '/team/private-image@' + DIGEST, {'digest': DIGEST})
                self.assertIs(caught.exception, error)
                self.assertEqual(error.diagnostics['blob_stage'], 'current-image-config')
                self.assertEqual(error.diagnostics['blob_source'], source)
                for private in ('private-token', 'private-image', 'cdn.example', host):
                    self.assertNotIn(private, json.dumps(error.diagnostics))

    def test_failure_retains_only_hostname_fingerprint_and_fixed_categories(self):
        import urllib.request
        target = ('https://private-user:private-password@objects.githubusercontent.com/'
                  'private-path?token=private-token#private-fragment')
        request = urllib.request.Request('https://ghcr.io/v2/public/image/blobs/' + DIGEST)
        with self.assertRaises(S.RegistryRedirectRejected) as caught:
            S.SafeRedirect({'pkg-containers.githubusercontent.com'}).redirect_request(
                request, None, 307, '', {}, target)
        error = caught.exception
        self.assertEqual(str(error), 'unexpected_registry_redirect')
        self.assertEqual(error.diagnostics, {
            'redirect_provider': 'github', 'redirect_transport': 'https',
            'redirect_host_sha256': S.sha(b'objects.githubusercontent.com')})
        public = json.dumps(error.diagnostics) + repr(error)
        for secret in ('private-user', 'private-password', 'private-path', 'private-token',
                       'private-fragment', 'objects.githubusercontent.com'):
            self.assertNotIn(secret, public)

    def test_fingerprint_does_not_depend_on_signed_url_payload(self):
        first = S.RegistryRedirectRejected('https://cdn.example/first?sig=one')
        second = S.RegistryRedirectRejected('https://cdn.example/second?sig=two')
        self.assertEqual(first.diagnostics, second.diagnostics)
        self.assertEqual(first.diagnostics['redirect_provider'], 'other')

    def test_invalid_and_oversized_hosts_have_no_fingerprint(self):
        for target in ('https://[broken', 'https://' + 'a' * 254 + '/private',
                       'https://' + 'a' * 64 + '.example/private',
                       'https://-invalid.example/private', 'https://\u2603.example/private'):
            with self.subTest(target=target):
                error = S.RegistryRedirectRejected(target)
                self.assertNotIn('redirect_host_sha256', error.diagnostics)
                self.assertEqual(error.diagnostics['redirect_provider'], 'invalid')
                self.assertLess(len(json.dumps(error.diagnostics)), 256)

    def test_invalid_port_is_rejected_without_echoing_input(self):
        import urllib.request
        request = urllib.request.Request('https://ghcr.io/v2/public/image/blobs/' + DIGEST)
        with self.assertRaises(S.RegistryRedirectRejected) as caught:
            S.SafeRedirect({'pkg-containers.githubusercontent.com'}).redirect_request(
                request, None, 307, '', {},
                'https://pkg-containers.githubusercontent.com:private-port/private')
        self.assertNotIn('private-port', str(caught.exception))

    def test_main_reports_fingerprint_but_still_fails_without_success_receipt(self):
        error = S.RegistryRedirectRejected('https://cdn.example/private?token=private-token')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            argv = ['scan', '--source-root', str(root), '--zarf-yaml-path', str(root / 'zarf.yaml'),
                    '--architecture', 'amd64', '--output-directory', str(root / 'result'),
                    '--markdown', str(root / 'comment.md')]
            output = io.StringIO()
            with patch.object(sys, 'argv', argv), patch.object(S, 'inventory', side_effect=error), \
                    patch.object(S, 'tool_identities') as later, contextlib.redirect_stderr(output):
                self.assertEqual(S.main(), 1)
            later.assert_not_called()
            lines = output.getvalue().splitlines()
            self.assertEqual(lines[0], 'Strict image comparison rejected: unexpected_registry_redirect')
            self.assertEqual(json.loads(lines[1]), error.diagnostics)
            self.assertNotIn('private-token', output.getvalue())
            self.assertFalse((root / 'result/receipt.json').exists())
            self.assertFalse((root / 'comment.md').exists())


class ReleaseProviderTests(unittest.TestCase):
    """Network/crypto process doubles test ordering; real crypto is qualified separately."""

    def fixture(self, root, failure=''):
        binding, readiness = S.load_policy('release-binding'), S.load_policy('release-readiness')
        raw_sbom = json.dumps(sbom()).encode()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            member = tarfile.TarInfo('image.json'); member.size = len(raw_sbom)
            archive.addfile(member, io.BytesIO(raw_sbom))
        archive_bytes = stream.getvalue()
        layer = {'digest': 'sha256:' + S.sha(archive_bytes), 'size': len(archive_bytes),
                 'mediaType': 'application/vnd.zarf.layer.v1.blob',
                 'annotations': {'org.opencontainers.image.title': 'sboms.tar'}}
        layers = [] if failure == 'missing-sbom' else [layer, layer] if failure == 'duplicate-sbom' else [layer]
        native = json.dumps({'schemaVersion': 2, 'mediaType': binding.MANIFEST_TYPE,
                             'layers': layers}).encode()
        native_digest = 'sha256:' + S.sha(native)
        index = json.dumps({'schemaVersion': 2, 'mediaType': binding.INDEX_TYPE, 'manifests': [
            {'mediaType': binding.MANIFEST_TYPE, 'digest': digest, 'size': len(native),
             'platform': {'architecture': arch, 'os': 'multi'}}
            for arch, digest in [('amd64', native_digest), ('arm64', 'sha256:' + 'f' * 64)]]}).encode()
        metadata = {'repository': S.REPO, 'release': {'tag': 'v1.2.3', 'id': '123',
                    'applicationCommit': '1' * 40}, 'workflow': {'commit': '1' * 40, 'runID': '456',
                    'runAttempt': '1', 'ref': 'refs/heads/main'}, 'indexDigest': 'sha256:' + S.sha(index)}
        predicate = binding.predicate(metadata, index)
        statement = {'_type': 'https://in-toto.io/Statement/v1', 'subject': [
            {'name': S.SUBJECT, 'digest': {'sha256': S.sha(index)}}],
            'predicateType': binding.PREDICATE, 'predicate': predicate}
        candidate = copy.deepcopy(statement)
        if failure == 'wrong-namespace':
            candidate['subject'][0]['name'] = 'ghcr.io/other/repository/takserver'
        if failure == 'wrong-source':
            candidate['predicate']['release']['applicationCommit'] = '2' * 40
        if failure == 'backfill':
            candidate['predicate']['workflow']['commit'] = '2' * 40
        asset = {'dsseEnvelope': {'payload': base64.b64encode(json.dumps(candidate).encode()).decode()}}
        certificate = {'issuer': 'https://token.actions.githubusercontent.com',
            'subjectAlternativeName': binding.WORKFLOW + '@refs/heads/main',
            'buildSignerURI': binding.WORKFLOW + '@refs/heads/main', 'buildSignerDigest': '1' * 40,
            'sourceRepositoryURI': binding.REPOSITORY, 'sourceRepositoryDigest': '1' * 40,
            'sourceRepositoryIdentifier': '1427088300', 'sourceRepositoryOwnerIdentifier': '5193877',
            'runInvocationURI': binding.REPOSITORY + '/actions/runs/456/attempts/1'}
        verified = [{'verificationResult': {'signature': {'certificate': certificate},
                     'verifiedTimestamps': [{'type': 'transparency-log'}], 'statement': statement}}]
        release = {'id': 123, 'url': 'https://api.github.com/repos/' + S.REPO + '/releases/123',
            'tag_name': 'v1.2.3', 'prerelease': False, 'draft': False,
            'assets': [{'name': 'takserver-1.2.3-upstream.release-binding.sigstore.json', 'size': 1234}]}
        controls = {'source': '1' * 40, 'release_id': 123, 'index': index}
        events = []

        def api(path):
            if failure == 'denied':
                raise S.Rejected('baseline_http_unavailable')
            if path in ('/releases/latest', '/releases/tags/v1.2.3'):
                return {**copy.deepcopy(release), 'id': controls['release_id']}
            if path == '/git/ref/tags/v1.2.3':
                return {'ref': 'refs/tags/v1.2.3', 'object': {'type': 'commit', 'sha': controls['source']}}
            if path.startswith('/compare/'):
                return {'status': 'ahead'}
            if path == '/actions/runs/456/attempts/1':
                return {'head_sha': '1' * 40, 'conclusion': 'success',
                        'path': '.github/workflows/release-please.yaml', 'event': 'push'}
            self.fail('Unexpected API route')

        def fetch(url, limit, headers=None, redirect=False):
            if url.startswith('https://ghcr.io/token?'):
                return b'{"token":"fixture-scoped-token"}'
            if url.endswith('/manifests/1.2.3-upstream'):
                return index + b'\n' if failure == 'wrong-index' else controls['index']
            if url.endswith('/manifests/' + native_digest):
                return native + b'\n' if failure == 'wrong-native' else native
            if '/blobs/' in url:
                events.append('sbom-fetch')
                return archive_bytes + b'x' if failure == 'wrong-blob' else archive_bytes
            self.fail('Unexpected registry route')

        def run(command, **kwargs):
            if command[:3] == ['gh', 'release', 'download']:
                path = root / 'takserver-1.2.3-upstream.release-binding.sigstore.json'
                path.write_text(json.dumps(asset))
                return b''
            self.assertEqual(command[:3], ['gh', 'attestation', 'verify'])
            self.assertIn('--bundle-from-oci', command)
            self.assertEqual(command[command.index('--source-digest') + 1], '1' * 40)
            self.assertEqual(command[command.index('--signer-digest') + 1], '1' * 40)
            self.assertFalse(any('insecure' in item for item in command))
            events.append('crypto-verify')
            if failure == 'crypto':
                raise S.Rejected('tool_failed')
            return json.dumps(verified).encode()

        def registry_index(digest, tag):
            self.assertEqual((digest, tag), (metadata['indexDigest'], 'v1.2.3'))
            binding.architectures(controls['index'], digest)
            return controls['index']
        binding.registry_index = registry_index
        policies = {'release-binding': binding, 'release-readiness': readiness}
        return api, fetch, run, policies, controls, events

    def test_published_source_crypto_native_and_sbom_digest_all_bind_before_use(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); api, fetch, run, policies, _, events = self.fixture(root)
            with patch.object(S, 'api', side_effect=api), patch.object(S, 'fetch', side_effect=fetch), \
                    patch.object(S, 'load_policy', side_effect=policies.__getitem__):
                archive, receipt = S.acquire_release('amd64', root, run)
                S.revalidate_release(receipt)
            self.assertEqual(events, ['crypto-verify', 'sbom-fetch'])
            self.assertEqual(receipt['architecture'], 'amd64')
            self.assertEqual(receipt['release']['applicationCommit'], '1' * 40)
            self.assertEqual('sha256:' + S.sha(archive.read_bytes()), receipt['sbom_layer_digest'])

    def test_released_sbom_rejection_adds_fixed_context_without_writing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); api, fetch, run, policies, _, events = self.fixture(root)
            error = S.RegistryRedirectRejected('https://cdn.example/private?token=private-token')

            def reject_blob(url, *args, **kwargs):
                if '/blobs/' in url:
                    raise error
                return fetch(url, *args, **kwargs)

            with patch.object(S, 'api', side_effect=api), patch.object(S, 'fetch', side_effect=reject_blob), \
                    patch.object(S, 'load_policy', side_effect=policies.__getitem__):
                with self.assertRaises(S.RegistryRedirectRejected) as caught:
                    S.acquire_release('amd64', root, run)
            self.assertIs(caught.exception, error)
            self.assertEqual(error.diagnostics['blob_stage'], 'released-sbom-layer')
            self.assertEqual(error.diagnostics['blob_source'], 'ghcr')
            self.assertEqual(events, ['crypto-verify'])
            self.assertNotIn('private-token', json.dumps(error.diagnostics))
            self.assertFalse((root / 'sboms.tar').exists())

    def test_missing_denied_wrong_source_namespace_hash_and_failed_crypto_refuse(self):
        for failure in ('denied', 'wrong-namespace', 'wrong-source', 'backfill', 'wrong-index',
                        'wrong-native', 'missing-sbom', 'duplicate-sbom', 'wrong-blob', 'crypto'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); api, fetch, run, policies, _, events = self.fixture(root, failure)
                with patch.object(S, 'api', side_effect=api), patch.object(S, 'fetch', side_effect=fetch), \
                        patch.object(S, 'load_policy', side_effect=policies.__getitem__):
                    with self.assertRaises((S.Rejected, ValueError)):
                        S.acquire_release('amd64', root, run)
                self.assertFalse((root / 'sboms.tar').exists())
                if failure == 'crypto':
                    self.assertNotIn('sbom-fetch', events)

    def test_final_lease_rejects_new_latest_release_changed_tag_source_or_index(self):
        for failure in ('latest-release', 'tag-source', 'index'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); api, fetch, run, policies, controls, _ = self.fixture(root)
                with patch.object(S, 'api', side_effect=api), patch.object(S, 'fetch', side_effect=fetch), \
                        patch.object(S, 'load_policy', side_effect=policies.__getitem__):
                    _, receipt = S.acquire_release('amd64', root, run)
                    if failure == 'latest-release': controls['release_id'] = 124
                    if failure == 'tag-source': controls['source'] = '2' * 40
                    if failure == 'index': controls['index'] += b'\n'
                    with self.assertRaises((S.Rejected, ValueError)):
                        S.revalidate_release(receipt)


class HostAndPrivacyTests(unittest.TestCase):
    def test_github_calls_pin_host_without_mutating_parent_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / 'gh'
            helper.write_text('#!' + sys.executable + '\nimport json,os\n'
                              'print(json.dumps([os.environ["GH_HOST"],os.environ["GH_REPO"]]))\n')
            helper.chmod(0o700)
            with patch.dict(os.environ, {'GH_HOST': 'other.example', 'GH_REPO': 'other/repo'}):
                self.assertEqual(json.loads(S.child([str(helper)])), ['github.com', S.REPO])
                self.assertEqual(os.environ['GH_HOST'], 'other.example')

    def test_capture_overflow_stops_child_before_later_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            for stream in (1, 2):
                with self.subTest(stream=stream):
                    pid = Path(directory) / f'pid-{stream}'
                    marker = Path(directory) / f'late-{stream}'
                    code = ('from pathlib import Path;import os,time;'
                            f'Path({str(pid)!r}).write_text(str(os.getpid()));'
                            f'os.write({stream}, b"x"*8192);time.sleep(2);'
                            f'Path({str(marker)!r}).write_text("must not execute")')
                    with self.assertRaisesRegex(S.Rejected, '^tool_output_limit$'):
                        S.child([sys.executable, '-c', code], limit=1024, timeout=5)
                    self.assertFalse(marker.exists())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(pid.read_text()), 0)

    def test_timeout_applies_after_both_capture_streams_close(self):
        code = 'import os,time;os.close(1);os.close(2);time.sleep(5)'
        with self.assertRaisesRegex(S.Rejected, '^tool_timeout$'):
            S.child([sys.executable, '-c', code], timeout=0.2)

    def test_scanner_data_file_is_separate_from_capture_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'scanner-data'
            code = ('from pathlib import Path;'
                    f'Path({str(data)!r}).write_bytes(b"x"*1048576);print("ok")')
            self.assertEqual(S.child([sys.executable, '-c', code], limit=16), b'ok\n')
            self.assertEqual(data.stat().st_size, 1048576)

    def test_invalid_manifest_diagnostics_do_not_echo_source_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'zarf.yaml'
            sentinel = 'private-payload-sentinel-do-not-echo'
            source.write_text('components:\n  - name: first\n    images: [\n      ' + sentinel)
            argv = ['scan', '--source-root', str(root), '--zarf-yaml-path', str(source),
                    '--architecture', 'amd64', '--output-directory', str(root / 'result'),
                    '--markdown', str(root / 'comment.md')]
            output = io.StringIO()
            with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(output), \
                    contextlib.redirect_stderr(output):
                self.assertEqual(S.main(), 1)
            self.assertNotIn(sentinel, output.getvalue())
            self.assertNotIn('Traceback', output.getvalue())
            self.assertFalse((root / 'comment.md').exists())


if __name__ == '__main__':
    unittest.main()
