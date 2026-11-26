#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Scan every selected image against authenticated release SBOMs; report with uds-pk."""

import argparse
import base64
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[2]
JSON_LIMIT = 32 * 1024 * 1024
ARCHIVE_LIMIT = 128 * 1024 * 1024
SCAN_LIMIT = 64 * 1024 * 1024
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
REFERENCE = re.compile(
    r'(?P<host>[a-z0-9.-]+(?::[1-9][0-9]{0,4})?)/'
    r'(?P<path>[a-z0-9]+(?:[._-]+[a-z0-9]+)*(?:/[a-z0-9]+(?:[._-]+[a-z0-9]+)*)*)'
    r'(?P<version>:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|@sha256:[0-9a-f]{64})')
REPO = 'jonathanagustin/uds-takserver'
SUBJECT = 'ghcr.io/' + REPO + '/takserver'
RELEASE_TAG = re.compile(r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)')


class Rejected(ValueError):
    """Only fixed reason codes, never child output or input payloads, are public."""


def require(condition, reason):
    if not condition:
        raise Rejected(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'duplicate_json_key')
        value[key] = item
    return value


def decode(raw, limit=JSON_LIMIT):
    require(len(raw) <= limit, 'json_limit')
    return json.loads(raw, object_pairs_hook=unique)


def read(path, limit=JSON_LIMIT):
    require(not path.is_symlink() and path.is_file(), 'regular_input_required')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'input_limit')
    return raw


def reference(value):
    require(type(value) is str and len(value) <= 512, 'invalid_image_reference')
    match = REFERENCE.fullmatch(value)
    require(match is not None, 'invalid_image_reference')
    host = match['host']
    require('.' in host or ':' in host or host == 'localhost', 'explicit_registry_required')
    if ':' in host:
        require(int(host.rsplit(':', 1)[1]) <= 65535, 'invalid_registry_port')
    require(not host.startswith('.') and '..' not in host, 'invalid_registry_host')
    hostname = host.split(':', 1)[0]
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = False
    require(hostname != 'localhost', 'numeric_loopback_required')
    return {'ref': value, 'repository': host + '/' + match['path'],
            'version': match['version'], 'loopback': loopback}


class ManifestLoader(yaml.SafeLoader):
    pass


def unique_yaml(loader, node, deep=False):
    pairs = loader.construct_pairs(node, deep=deep)
    return unique(pairs)


ManifestLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_yaml)


def inventory(manifest, flavor, root=None):
    """Union selected/common components and exact local component imports (v1alpha1 & v1beta1)."""
    root = (root or manifest.parent).resolve()
    sources, images, documents = {}, set(), {}

    selected_manifest = manifest.resolve()

    def document(path):
        require(not path.is_symlink(), 'symlink_manifest')
        path = path.resolve()
        require(path == selected_manifest or path.is_relative_to(root), 'import_outside_source')
        relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else 'selected-manifest'
        if path not in documents:
            raw = read(path, 2 * 1024 * 1024)
            value = yaml.load(raw, Loader=ManifestLoader)
            require(type(value) is dict, 'invalid_manifest')
            api_version = value.get('apiVersion')
            kind = value.get('kind')
            if api_version == 'zarf.dev/v1beta1' and kind == 'ZarfComponentConfig':
                require(type(value.get('component')) is dict, 'invalid_manifest')
            else:
                require(kind in (None, 'ZarfPackageConfig') and type(value.get('components')) is list,
                        'invalid_manifest')
                names = [component.get('name') for component in value['components'] if type(component) is dict]
                require(len(names) == len(value['components']) and all(type(n) is str and n for n in names)
                        and len(names) == len(set(names)), 'ambiguous_components')
            documents[path] = value
            sources[relative] = sha(raw)
        return path, documents[path]

    def visit(path, component, stack, is_beta=False):
        identity = (path, component.get('name') or '<component>')
        require(identity not in stack and len(stack) < 16, 'cyclic_or_deep_import')
        if is_beta:
            require('only' not in component, 'unsupported_component_selector')
            selector = component.get('selector', {})
        else:
            require('selector' not in component, 'unsupported_component_selector')
            selector = component.get('only', {})
        require(type(selector) is dict and set(selector) <= {'flavor'}, 'unsupported_component_selector')
        selected = selector.get('flavor', '')
        require(type(selected) is str, 'invalid_flavor')
        if selected and selected != flavor:
            return
        declared = component.get('images', [])
        require(type(declared) is list, 'invalid_images')
        for item in declared:
            if is_beta:
                require(type(item) is dict and type(item.get('name')) is str and item['name'], 'invalid_images')
                ref = item['name']
            else:
                ref = item
            reference(ref)
            images.add(ref)
        imported = component.get('import')
        if imported is not None:
            if is_beta:
                require(type(imported) is dict and set(imported) <= {'local'}
                        and type(imported.get('local')) is list and len(imported['local']) > 0,
                        'unsupported_import')
                entries = imported['local']
            else:
                require(type(imported) is dict and set(imported) <= {'path', 'name'}, 'unsupported_import')
                entries = [imported]
            for entry in entries:
                require(type(entry) is dict and set(entry) <= {'path', 'name'}
                        and type(entry.get('path')) is str and entry['path'], 'unsupported_import')
                base = root if path == selected_manifest and not path.is_relative_to(root) else path.parent
                target = base / entry['path']
                require(not target.is_symlink(), 'symlink_import')
                if target.is_dir():
                    target /= 'zarf.yaml'
                target, value = document(target)
                target_beta = value.get('apiVersion') == 'zarf.dev/v1beta1'
                require(target_beta if is_beta else not target_beta, 'unsupported_import')
                if value.get('kind') == 'ZarfComponentConfig':
                    visit(target, value['component'], (*stack, identity), is_beta=True)
                else:
                    name = entry.get('name', component.get('name'))
                    require(type(name) is str and name, 'unresolved_component_import')
                    matches = [item for item in value['components'] if item['name'] == name]
                    require(len(matches) == 1, 'unresolved_component_import')
                    visit(target, matches[0], (*stack, identity), is_beta=target_beta)

    manifest, value = document(manifest)
    require(value.get('kind') != 'ZarfComponentConfig' and type(value.get('components')) is list,
            'invalid_manifest')
    is_beta = value.get('apiVersion') == 'zarf.dev/v1beta1'
    for component in value['components']:
        visit(manifest, component, (), is_beta=is_beta)
    require(0 < len(images) <= 128, 'empty_or_large_inventory')
    return sorted(images), sources


def candidate_mappings(images, overrides, state_path=None, state_sha256=None):
    mappings = {}
    for argument in overrides:
        parts = argument.split('=')
        require(len(parts) == 2, 'invalid_override')
        original, actual = map(reference, parts)
        require(not original['loopback'] and actual['loopback'] and actual['version'].startswith('@sha256:'),
                'override_requires_canonical_and_loopback_digest')
        require(original['ref'] not in mappings and actual['ref'] not in mappings.values(), 'duplicate_override')
        mappings[original['ref']] = actual['ref']
    loopbacks = {item for item in images if reference(item)['loopback']}
    require(set(mappings.values()) == loopbacks, 'missing_or_unused_override')
    if not loopbacks:
        require(state_path is None and state_sha256 is None, 'unused_candidate_state')
        return {}, None
    require(state_path is not None and type(state_sha256) is str
            and re.fullmatch(r'[0-9a-f]{64}', state_sha256), 'verified_candidate_state_required')
    raw = read(state_path, 4 * 1024 * 1024)
    require(sha(raw) == state_sha256, 'candidate_state_hash_mismatch')
    validated = validate_candidate_source(state_path)
    after = read(state_path, 4 * 1024 * 1024)
    require(after == raw and sha(after) == state_sha256, 'candidate_state_changed_during_validation')
    state = decode(raw, 4 * 1024 * 1024)
    require(state == validated and state.get('schema') == 'uds-takserver/candidate-consumer/v1',
            'candidate_source_policy_mismatch')
    require(type(state.get('groups')) is dict and state['groups'], 'invalid_candidate_groups')
    pairs = {}
    for group in state['groups'].values():
        require(type(group) is dict and type(group.get('images')) is list, 'invalid_candidate_images')
        for image in group['images']:
            require(type(image) is dict, 'invalid_candidate_image')
            original, mirror = reference(image['ref']), reference(image['mirror'])
            require(not original['loopback'] and mirror['loopback']
                    and DIGEST.fullmatch(image['mirror_manifest_digest'])
                    and DIGEST.fullmatch(image['config_digest']), 'invalid_candidate_digests')
            target = mirror['repository'] + '@' + image['mirror_manifest_digest']
            require(original['ref'] not in pairs and target not in pairs.values(), 'duplicate_candidate_image')
            pairs[original['ref']] = target
    require(pairs == mappings, 'candidate_mapping_mismatch')
    require(not (set(mappings) & set(images)), 'canonical_and_mirror_both_selected')
    return {value: key for key, value in mappings.items()}, {
        'sha256': state_sha256,
        'verification_authority': 'shared candidate-consumer.py validate_state replay; not independent producer verification'}


def validate_candidate_source(path):
    """Replay the shared consumer authority with its real environment context."""
    source = ROOT / '.github/scripts/candidate-consumer.py'
    require(source.is_file() and not source.is_symlink(), 'canonical_candidate_validator_unavailable')
    spec = importlib.util.spec_from_file_location('strict_scan_candidate_consumer', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # No supplied context and no preparation: the canonical validator binds
    # the actual event/checkout/producer/registry before returning its state.
    return module.validate_state(path)


def released_sboms(archive_path, destination, architecture):
    """Read a previously digest-verified archive without filesystem extraction."""
    require(not archive_path.is_symlink() and archive_path.is_file(), 'regular_sbom_archive_required')
    require(1024 <= archive_path.stat().st_size <= ARCHIVE_LIMIT
            and archive_path.stat().st_size % 512 == 0, 'sbom_archive_limit')
    result, names = {}, set()
    with tarfile.open(archive_path, 'r:') as archive:
        for member in archive:
            require(member.name not in names and len(names) < 256, 'duplicate_or_large_archive')
            names.add(member.name)
            if member.isdir() and member.name == '.':
                continue
            require(member.isfile() and not member.issparse()
                    and not any('sparse' in key.lower() for key in member.pax_headers)
                    and PurePosixPath(member.name).name == member.name
                    and member.name.endswith('.json') and 0 < member.size <= JSON_LIMIT, 'unsafe_sbom_member')
            raw = archive.extractfile(member).read(JSON_LIMIT + 1)
            require(len(raw) == member.size, 'truncated_sbom')
            value = decode(raw)
            packages = value.get('artifacts')
            require(type(packages) is list and packages and all(type(package) is dict
                    and type(package.get('purl')) is str and package['purl'].startswith('pkg:')
                    for package in packages), 'empty_or_unidentified_sbom_catalog')
            package_ids = [package.get('id') for package in packages]
            require(all(type(identity) is str and identity for identity in package_ids)
                    and len(package_ids) == len(set(package_ids)), 'ambiguous_sbom_package_ids')
            source = value['source']
            require(source['type'] == 'image', 'non_image_sbom')
            metadata = source['metadata']
            image = reference(metadata['userInput'])
            require(not image['loopback'], 'noncanonical_released_image')
            config_raw = base64.b64decode(metadata['config'], validate=True)
            config = decode(config_raw, JSON_LIMIT)
            require(config['architecture'] == architecture and config.get('os') == 'linux',
                    'wrong_released_image_platform')
            require(metadata.get('imageID') == 'sha256:' + sha(config_raw)
                    and metadata.get('architecture') in (None, '', architecture)
                    and metadata.get('os') in (None, '', 'linux'), 'inconsistent_sbom_image_identity')
            repository = image['repository']
            require(repository not in result, 'ambiguous_released_repository')
            digest = sha(raw)
            target = destination / (digest + '.json')
            with target.open('xb') as output:
                output.write(raw)
            result[repository] = {'ref': image['ref'], 'path': target, 'sha256': digest,
                                  'image_config_sha256': sha(config_raw)}
        archive.fileobj.seek(archive.offset)
        tail = archive.fileobj.read(ARCHIVE_LIMIT + 1)
        require(len(tail) >= 1024 and not any(tail), 'incomplete_or_trailing_tar_data')
    require(result, 'empty_released_sboms')
    return result


def child(command, *, environment=None, timeout=300, limit=SCAN_LIMIT):
    """Private, bounded capture. No child exception or output is a diagnostic."""
    def limits():
        # The scanner also writes image layers and the vulnerability database.
        # Those legitimate files exceed its much smaller report/output bound.
        resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024**3, 4 * 1024**3))
    if Path(command[0]).name == 'gh':
        environment = dict(os.environ if environment is None else environment)
        environment['GH_HOST'] = 'github.com'
        environment['GH_REPO'] = REPO
    deadline = time.monotonic() + timeout
    output = bytearray()
    counts = {'stdout': 0, 'stderr': 0}
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=environment, start_new_session=True, preexec_fn=limits) as process:
        try:
            with selectors.DefaultSelector() as selector:
                for stream, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, name)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'tool_timeout')
                    for key, _ in selector.select(min(remaining, 1)):
                        name = key.data
                        block = os.read(key.fd, min(65536, limit - counts[name] + 1))
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        counts[name] += len(block)
                        require(counts[name] <= limit, 'tool_output_limit')
                        if name == 'stdout':
                            output.extend(block)
            try:
                result = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise Rejected('tool_timeout') from None
            require(result == 0, 'tool_failed')
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
    return bytes(output)


def database_snapshot(directory):
    require(directory.is_dir(), 'missing_database')
    files, total = {}, 0
    for path in sorted(directory.rglob('*')):
        require(not path.is_symlink(), 'symlink_database')
        if path.is_file():
            total += path.stat().st_size
            require(len(files) < 32 and total <= 4 * 1024**3, 'database_limit')
            with path.open('rb') as stream:
                files[str(path.relative_to(directory))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    require(files, 'empty_database')
    return files


def database_state(directory):
    return {str(path.relative_to(directory)): (path.stat().st_size, path.stat().st_mtime_ns,
            path.stat().st_ctime_ns, path.stat().st_ino)
            for path in sorted(directory.rglob('*')) if path.is_file() and not path.is_symlink()}


def scan_environment(db_directory, loopback=False):
    environment = dict(os.environ)
    for prefix in ('GRYPE', 'SYFT'):
        environment[prefix + '_REGISTRY_INSECURE_USE_HTTP'] = 'true' if loopback else 'false'
        environment[prefix + '_REGISTRY_INSECURE_SKIP_TLS_VERIFY'] = 'false'
    environment['GRYPE_DB_CACHE_DIR'] = str(db_directory)
    environment['GRYPE_DB_AUTO_UPDATE'] = 'false'
    environment['GRYPE_CHECK_FOR_APP_UPDATE'] = 'false'
    return environment


def checked_scan(path, expected):
    value = decode(read(path, SCAN_LIMIT), SCAN_LIMIT)
    require(value.get('bomFormat') == 'CycloneDX' and type(value.get('metadata')) is dict,
            'invalid_scan_format')
    component = value['metadata'].get('component')
    require(type(component) is dict and component.get('type') == 'container' and type(component.get('name')) is str
            and component['name'] and type(component.get('version')) is str and component['version'],
            'missing_scan_image_identity')
    identity = reference(expected)
    require(component['name'] == identity['repository']
            and component['version'] == identity['version'][1:], 'scan_image_identity_mismatch')
    catalog = value.get('components')
    require(type(catalog) is list and catalog and all(type(item) is dict
            and type(item.get('name')) is str and item['name'] for item in catalog), 'empty_scan_catalog')
    package_count = sum(item.get('type') in ('library', 'application', 'framework')
                        and type(item.get('purl')) is str and item['purl'].startswith('pkg:') for item in catalog)
    require(package_count > 0, 'missing_scan_package_catalog')
    identities = [item.get('bom-ref') for item in [component, *catalog]]
    require(all(type(identity) is str and identity for identity in identities)
            and len(identities) == len(set(identities)), 'ambiguous_scan_component_ids')
    vulnerabilities = value.get('vulnerabilities', [])
    require(type(vulnerabilities) is list, 'invalid_scan_vulnerabilities')
    for vulnerability in vulnerabilities:
        require(type(vulnerability) is dict and type(vulnerability.get('id')) is str
                and vulnerability.get('affects') and vulnerability.get('ratings')
                and (vulnerability.get('source', {}).get('url') or vulnerability.get('advisories')),
                'incomplete_scan_vulnerability')
        require(type(vulnerability['affects']) is list and all(type(affected) is dict
                and affected.get('ref') in identities for affected in vulnerability['affects']),
                'unresolved_vulnerability_component')
    return {'name': component['name'], 'version': component['version'],
            'sha256': sha(read(path, SCAN_LIMIT)), 'vulnerability_count': len(vulnerabilities),
            'catalog_package_count': package_count}


def immutable_current(image, architecture, run):
    parsed = reference(image)
    if parsed['loopback']:
        require(parsed['version'].startswith('@sha256:'), 'mutable_loopback_image')
        return image
    digest = run(['uds', 'zarf', 'tools', 'registry', 'digest', image,
                  '--platform', 'linux/' + architecture], timeout=90, limit=1024 * 1024).decode().strip()
    require(DIGEST.fullmatch(digest), 'invalid_current_image_digest')
    return parsed['repository'] + '@' + digest


def current_platform(image, architecture, run):
    parsed = reference(image)
    require(parsed['version'].startswith('@sha256:'), 'immutable_platform_input_required')
    command = ['uds', 'zarf', 'tools', 'registry', 'manifest', image, '--platform', 'linux/' + architecture]
    if reference(image)['loopback']:
        command.append('--insecure')
    manifest_raw = run(command, timeout=90, limit=1024 * 1024)
    require('sha256:' + sha(manifest_raw) == parsed['version'][1:], 'current_manifest_digest_mismatch')
    manifest = decode(manifest_raw, 1024 * 1024)
    require(type(manifest.get('schemaVersion')) is int and manifest['schemaVersion'] == 2
            and manifest.get('mediaType') in ('application/vnd.oci.image.manifest.v1+json',
                'application/vnd.docker.distribution.manifest.v2+json')
            and type(manifest.get('config')) is dict, 'invalid_current_manifest')
    descriptor = manifest['config']
    require(DIGEST.fullmatch(descriptor['digest']) and type(descriptor['size']) is int
            and 0 < descriptor['size'] <= 1024 * 1024
            and descriptor.get('mediaType') in ('application/vnd.oci.image.config.v1+json',
                'application/vnd.docker.container.image.v1+json'), 'invalid_current_config_descriptor')
    raw = current_configuration(image, descriptor)
    require(len(raw) == descriptor['size'] and 'sha256:' + sha(raw) == descriptor['digest'],
            'current_config_digest_mismatch')
    config = decode(raw, 1024 * 1024)
    require(config.get('os') == 'linux' and config.get('architecture') == architecture,
            'wrong_current_image_platform')
    return descriptor['digest']


def current_configuration(image, descriptor):
    """Read public config bytes only; no inherited registry credentials or challenges."""
    parsed = reference(image)
    host, repository = parsed['repository'].split('/', 1)
    headers, redirect = {}, False
    if parsed['loopback']:
        require(host.startswith('127.0.0.1:'), 'unsupported_loopback_registry')
        base = 'http://' + host
    else:
        require(host in ('docker.io', 'ghcr.io'), 'unsupported_current_registry')
        if host == 'docker.io':
            base, token_url, service = 'https://registry-1.docker.io', 'https://auth.docker.io/token', 'registry.docker.io'
            redirect = 'docker'
        else:
            base, token_url, service = 'https://ghcr.io', 'https://ghcr.io/token', 'ghcr.io'
            redirect = True
        query = urllib.parse.urlencode({'service': service, 'scope': 'repository:' + repository + ':pull'})
        token = decode(fetch(token_url + '?' + query, 32768), 32768)['token']
        require(type(token) is str and 0 < len(token) <= 16384 and '\r' not in token and '\n' not in token,
                'invalid_current_registry_token')
        headers['Authorization'] = 'Bearer ' + token
    try:
        return fetch(base + '/v2/' + repository + '/blobs/' + descriptor['digest'],
                     1024 * 1024, headers, redirect=redirect)
    except RegistryRedirectRejected as error:
        error.diagnostics.update(blob_stage='current-image-config',
                                 blob_source='loopback' if parsed['loopback'] else
                                 'docker' if host == 'docker.io' else 'ghcr')
        raise


def compare_inventory(images, mappings, baseline, directory, db_directory, architecture, run=child):
    """Every selected ref must have its own real current and released scan."""
    pairs, repositories = [], set()
    for actual in images:
        canonical = mappings.get(actual, actual)
        repository = reference(canonical)['repository']
        require(repository not in repositories, 'ambiguous_current_repository')
        repositories.add(repository)
        require(repository in baseline, 'unsupported_new_image_repository')
        pairs.append((canonical, actual, baseline[repository]))
    require(pairs, 'empty_comparison_inventory')
    frozen = database_snapshot(db_directory)
    state = database_state(db_directory)
    sections, receipts, leases = [], [], []
    for index, (canonical, actual, released) in enumerate(pairs):
        immutable = immutable_current(actual, architecture, run)
        config_sha256 = current_platform(immutable, architecture, run)
        if reference(actual)['version'].startswith(':'):
            leases.append((actual, immutable))
        outputs = []
        for label, source, expected, loopback in (
            ('released', 'sbom:' + str(released['path']), released['ref'], False),
            ('current', 'registry:' + immutable, immutable, reference(immutable)['loopback']),
        ):
            output = directory / (str(index) + '-' + label + '.json')
            run(['grype', '--add-cpes-if-none', '--output', 'cyclonedx-json',
                 '--platform', 'linux/' + architecture, '--file', str(output), source],
                environment=scan_environment(db_directory, loopback), timeout=600)
            outputs.append((output, checked_scan(output, expected)))
            require(database_state(db_directory) == state, 'database_changed_during_scan')
        markdown = run(['uds-pk', 'compare-scans', str(outputs[0][0]), str(outputs[1][0]),
                        '--allow-different-images'], timeout=90, limit=16 * 1024 * 1024).decode()
        require(markdown.strip().startswith('### ') and all(label in markdown for label in (
            'New vulnerabilities:', 'Fixed vulnerabilities:', 'Existing vulnerabilities:')), 'empty_comparator')
        sections.append(markdown)
        receipts.append({'canonical_image': canonical, 'scanned_image': actual,
                         'immutable_scanned_image': immutable,
                         'image_config_digest': config_sha256,
                         'released_image': released['ref'], 'released_sbom_sha256': released['sha256'],
                         'released_scan': outputs[0][1], 'current_scan': outputs[1][1],
                         'markdown_sha256': sha(markdown.encode())})
    require(database_snapshot(db_directory) == frozen, 'database_changed_during_comparison')
    require(all(immutable_current(actual, architecture, run) == digest for actual, digest in leases),
            'current_image_tag_changed')
    return '\n\n'.join(sections), {'database_files': frozen, 'comparisons': receipts}


class RegistryRedirectRejected(Rejected):
    """Fixed failure reason with bounded, non-payload redirect diagnostics."""

    def __init__(self, target):
        super().__init__('unexpected_registry_redirect')
        self.diagnostics = {'redirect_provider': 'invalid', 'redirect_transport': 'other'}
        try:
            parsed = urllib.parse.urlsplit(target)
            host = parsed.hostname or ''
        except ValueError:
            return
        if parsed.scheme in ('https', 'http'):
            self.diagnostics['redirect_transport'] = parsed.scheme
        # Never retain the target URL, userinfo, path, query or fragment. Invalid
        # and oversized hostnames contribute no input bytes to the diagnostics.
        if not (0 < len(host) <= 253 and all(
                re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                for label in host.split('.'))):
            return
        provider = 'other'
        for family, suffixes in (
                ('github', ('.githubusercontent.com',)),
                ('cloudfront', ('.cloudfront.net', '.cloudfront.docker.com')),
                ('cloudflare', ('.cloudflarestorage.com', '.cloudflare.docker.com')),
                ('azure', ('.blob.core.windows.net',))):
            if any(host.endswith(suffix) for suffix in suffixes):
                provider = family
                break
        self.diagnostics.update(redirect_provider=provider, redirect_host_sha256=sha(host.encode('ascii')))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts):
        super().__init__()
        self.hosts = hosts

    def redirect_request(self, request, response, code, message, headers, target):
        try:
            parsed = urllib.parse.urlsplit(target)
            allowed = (parsed.scheme == 'https' and parsed.hostname in self.hosts
                       and parsed.port in (None, 443) and not parsed.username and not parsed.password)
        except ValueError:
            allowed = False
        if not allowed:
            raise RegistryRedirectRejected(target) from None
        redirected = super().redirect_request(request, response, code, message, headers, target)
        redirected.remove_header('Authorization')
        return redirected


def fetch(url, limit, headers=None, *, redirect=False):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            raise Rejected('unexpected_api_redirect')
    hosts = ({'production.cloudfront.docker.com', 'production.cloudflare.docker.com',
              'docker-images-prod.s3.dualstack.us-east-1.amazonaws.com'} if redirect == 'docker'
             else {'pkg-containers.githubusercontent.com'})
    opener = urllib.request.build_opener(SafeRedirect(hosts) if redirect else NoRedirect())
    try:
        with opener.open(urllib.request.Request(url, headers=headers or {}), timeout=60) as response:
            raw = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        error.close()
        raise Rejected('baseline_http_unavailable') from None
    require(len(raw) <= limit, 'baseline_response_limit')
    return raw


def api(path):
    require(path.startswith('/') and '\n' not in path and len(path) <= 512, 'invalid_api_path')
    # Use the existing CLI authentication context without copying tokens into
    # Python state, temporary files, diagnostics or redirected HTTP requests.
    return decode(child(['gh', 'api', '--hostname', 'github.com', 'repos/' + REPO + path],
                        timeout=60, limit=1024 * 1024), 1024 * 1024)


def load_policy(name):
    path = ROOT / '.github/test-infra' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def acquire_release(architecture, directory, run=child, release_tag=None):
    """Acquire only the SBOM layer bound by the verified public release index.

    Historical backfills using a different workflow commit are deliberately
    unsupported in this first adapter, rather than treated as ordinary releases.
    """
    require(release_tag is None or RELEASE_TAG.fullmatch(release_tag), 'invalid_release_tag')
    release = api('/releases/tags/' + release_tag if release_tag else '/releases/latest')
    tag = release['tag_name']
    require(type(tag) is str and RELEASE_TAG.fullmatch(tag), 'invalid_published_release')
    readiness = load_policy('release-readiness')
    # This existing policy validates draft/public state, exact tag/ID/API URL,
    # and peeled immutable Git source; target_commitish is never an authority.
    checked = readiness.check_release(api, tag)
    require(checked['id'] == release['id'] and checked['draft'] is False, 'release_identity_changed')
    source = readiness.source_at_tag(api, tag)
    # GitHub returns file patches only on comparison page 1. An ancestry check
    # needs the fixed comparison status, not unbounded historical file patches.
    comparison = api('/compare/' + source + '...main?per_page=1&page=2')
    require(comparison.get('status') in ('ahead', 'identical'), 'release_not_reachable_from_main')
    asset_name = 'takserver-' + tag[1:] + '-upstream.release-binding.sigstore.json'
    assets = [asset for asset in checked['assets'] if asset.get('name') == asset_name]
    require(len(assets) == 1 and type(assets[0].get('size')) is int
            and 0 < assets[0]['size'] <= 1024 * 1024, 'missing_release_binding')
    # gh follows the public asset redirect without passing an API token to a CDN.
    asset = directory / asset_name
    run(['gh', 'release', 'download', tag, '--repo', REPO, '--pattern', asset_name,
         '--dir', str(directory)], timeout=90, limit=1024 * 1024)
    candidate = decode(read(asset, 1024 * 1024), 1024 * 1024)
    statement = decode(base64.b64decode(candidate['dsseEnvelope']['payload'], validate=True), 1024 * 1024)
    predicate = statement['predicate']
    require(predicate['repository'] == REPO and predicate['release'] == {
        'tag': tag, 'id': str(checked['id']), 'applicationCommit': source}, 'wrong_release_binding_identity')
    workflow = predicate['workflow']
    require(workflow['commit'] == source, 'unsupported_historical_backfill')
    require(workflow['ref'] in ('refs/heads/main', 'refs/tags/' + tag)
            and re.fullmatch(r'[1-9][0-9]*', workflow['runID'])
            and re.fullmatch(r'[1-9][0-9]*', workflow['runAttempt']), 'invalid_release_workflow')
    invocation = api('/actions/runs/' + workflow['runID'] + '/attempts/' + workflow['runAttempt'])
    require(invocation['head_sha'] == source and invocation['conclusion'] == 'success'
            and invocation['path'].split('@', 1)[0] in (
                '.github/workflows/release.yaml', '.github/workflows/release-please.yaml')
            and invocation['event'] in ('push', 'release', 'workflow_dispatch'), 'wrong_release_invocation')
    subjects = statement['subject']
    require(type(subjects) is list and len(subjects) == 1 and subjects[0]['name'] == SUBJECT,
            'wrong_release_subject')
    digest = 'sha256:' + subjects[0]['digest']['sha256']
    require(DIGEST.fullmatch(digest), 'invalid_release_index_digest')
    query = urllib.parse.urlencode({'service': 'ghcr.io', 'scope': 'repository:' + REPO + '/takserver:pull'})
    token = decode(fetch('https://ghcr.io/token?' + query, 32768), 32768)['token']
    require(type(token) is str and 0 < len(token) <= 16384 and '\r' not in token and '\n' not in token,
            'invalid_registry_token')
    base = 'https://ghcr.io/v2/' + REPO + '/takserver/'
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.oci.image.index.v1+json'}
    raw_index = fetch(base + 'manifests/' + tag[1:] + '-upstream', 1024 * 1024, headers)
    require('sha256:' + sha(raw_index) == digest, 'release_index_mismatch')
    policy = load_policy('release-binding')
    metadata = {'repository': REPO, 'release': predicate['release'], 'workflow': workflow, 'indexDigest': digest}
    # Unverified public asset fields above are only candidates. Cryptographic
    # verification and exact existing policy both succeed before SBOM use.
    verified_raw = run(['gh', 'attestation', 'verify', 'oci://' + SUBJECT + '@' + digest,
        '--owner', 'jonathanagustin', '--signer-workflow', REPO + '/.github/workflows/release.yaml',
        '--signer-digest', workflow['commit'], '--source-digest', workflow['commit'],
        '--cert-oidc-issuer', 'https://token.actions.githubusercontent.com',
        '--predicate-type', policy.PREDICATE, '--bundle-from-oci', '--format=json'],
        timeout=120, limit=1024 * 1024)
    policy.verify_verified_output(decode(verified_raw, 1024 * 1024), metadata, raw_index)
    manifests = policy.architectures(raw_index, digest)
    native = next(item['digest'] for item in manifests if item['architecture'] == architecture)
    descriptor = next(item for item in decode(raw_index)['manifests'] if item['digest'] == native)
    headers['Accept'] = 'application/vnd.oci.image.manifest.v1+json'
    raw_native = fetch(base + 'manifests/' + native, 1024 * 1024, headers)
    require(len(raw_native) == descriptor['size'] and 'sha256:' + sha(raw_native) == native,
            'native_manifest_mismatch')
    manifest = decode(raw_native, 1024 * 1024)
    require(type(manifest.get('schemaVersion')) is int and manifest['schemaVersion'] == 2
            and manifest.get('mediaType') == policy.MANIFEST_TYPE
            and type(manifest.get('layers')) is list, 'invalid_native_manifest')
    layers = [item for item in manifest['layers']
              if item.get('annotations', {}).get('org.opencontainers.image.title') == 'sboms.tar']
    require(len(layers) == 1, 'missing_or_duplicate_sbom_layer')
    layer = layers[0]
    require(DIGEST.fullmatch(layer['digest']) and type(layer['size']) is int
            and layer.get('mediaType') == 'application/vnd.zarf.layer.v1.blob'
            and 0 < layer['size'] <= ARCHIVE_LIMIT, 'invalid_sbom_layer')
    try:
        archive = fetch(base + 'blobs/' + layer['digest'], ARCHIVE_LIMIT, headers, redirect=True)
    except RegistryRedirectRejected as error:
        error.diagnostics.update(blob_stage='released-sbom-layer', blob_source='ghcr')
        raise
    require(len(archive) == layer['size'] and 'sha256:' + sha(archive) == layer['digest'], 'sbom_layer_mismatch')
    archive_path = directory / 'sboms.tar'
    with archive_path.open('xb') as output:
        output.write(archive)
    return archive_path, {'release': metadata['release'], 'workflow': workflow, 'architecture': architecture,
                         'index_digest': digest, 'native_manifest_digest': native,
                         'sbom_layer_digest': layer['digest'], 'sbom_layer_bytes': layer['size'],
                         'cryptographic_verified_output_sha256': sha(verified_raw),
                         'selection': 'pinned' if release_tag else 'latest',
                         'backfill_policy': 'different workflow/application commits explicitly unsupported'}


def revalidate_release(receipt):
    release = receipt['release']
    if receipt['selection'] == 'latest':
        require(api('/releases/latest')['id'] == int(release['id']), 'latest_release_changed')
    readiness = load_policy('release-readiness')
    record = readiness.check_release(api, release['tag'], checked_source=release['applicationCommit'])
    require(str(record['id']) == release['id'] and record['draft'] is False, 'release_identity_changed')
    policy = load_policy('release-binding')
    policy.registry_index(receipt['index_digest'], release['tag'])


def tool_identities(run=child):
    grype = decode(run(['grype', 'version', '-o', 'json'], timeout=30, limit=1024 * 1024))
    require(grype.get('version') == '0.120.1', 'unsupported_grype_version')
    require(run(['uds-pk', 'version'], timeout=30, limit=1024 * 1024).decode().strip() == '0.2.0',
            'unsupported_comparator_version')
    require(run(['uds', 'version'], timeout=30, limit=1024 * 1024).decode().strip() == 'v0.42.0',
            'unsupported_uds_version')
    require(run(['uds', 'zarf', 'version'], timeout=30, limit=1024 * 1024).decode().strip() == 'v1.0.1',
            'unsupported_zarf_version')
    result = {}
    for name in ('grype', 'uds-pk', 'uds', 'gh'):
        path = shutil.which(name)
        require(path is not None, 'missing_tool')
        with Path(path).resolve().open('rb') as stream:
            result[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'binary_sha256': result, 'grype_version': grype['version'],
            'grype_build_commit': grype.get('gitCommit'), 'grype_platform': grype.get('platform'),
            'comparator_version': '0.2.0', 'uds_version': '0.42.0', 'embedded_zarf_version': '1.0.1'}


def source_identity(root):
    checkout = child(['git', '-C', str(root), 'rev-parse', 'HEAD'], timeout=10,
                     limit=1024 * 1024).decode().strip()
    require(re.fullmatch(r'[0-9a-f]{40}', checkout), 'invalid_checkout_source')
    event_sha = os.environ.get('GITHUB_SHA')
    require(event_sha is None or event_sha == checkout, 'checkout_event_mismatch')
    files = [Path(__file__).resolve(), ROOT / '.github/test-infra/release-binding.py',
             ROOT / '.github/test-infra/release-readiness.py']
    return {'checkout_sha': checkout, 'source_sha256': {
        str(path.relative_to(ROOT)): sha(read(path, 1024 * 1024)) for path in files}}


def prepare_database(directory, run=child):
    environment = scan_environment(directory)
    run(['grype', 'db', 'update'], environment=environment, timeout=300, limit=1024 * 1024)
    status = decode(run(['grype', 'db', 'status', '-o', 'json'], environment=environment,
                        timeout=30, limit=1024 * 1024), 1024 * 1024)
    require(status.get('valid') is True and type(status.get('built')) is str
            and type(status.get('schemaVersion')) is str, 'invalid_vulnerability_database')
    source = urllib.parse.urlsplit(status['from'])
    query = urllib.parse.parse_qs(source.query, strict_parsing=True)
    require(source.scheme == 'https' and source.netloc == 'grype.anchore.io'
            and source.path.startswith('/databases/v6/') and not source.fragment
            and set(query) == {'checksum'} and len(query['checksum']) == 1
            and DIGEST.fullmatch(query['checksum'][0]), 'unsupported_database_source')
    return {'built': status['built'], 'schema_version': status['schemaVersion'],
            'download_url': status['from'], 'download_checksum': query['checksum'][0]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zarf-yaml-path', type=Path, default=Path('zarf.yaml'))
    parser.add_argument('--source-root', type=Path, default=Path.cwd())
    parser.add_argument('--canonical-manifest', type=Path)
    parser.add_argument('--flavor', choices=['upstream'], default='upstream')
    parser.add_argument('--architecture', choices=['amd64', 'arm64'], required=True)
    parser.add_argument('--image-name-override', action='append', default=[])
    parser.add_argument('--candidate-state', type=Path)
    parser.add_argument('--candidate-state-sha256')
    parser.add_argument('--release-tag')
    parser.add_argument('--output-directory', type=Path, required=True)
    parser.add_argument('--markdown', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    phase = 'input inventory and scanner identities'
    try:
        output = args.output_directory
        output.mkdir(parents=True, exist_ok=False)
        canonical_file = args.canonical_manifest or args.source_root / 'zarf.yaml'
        canonical, original_sources = inventory(canonical_file, args.flavor, args.source_root)
        images, selected_sources = inventory(args.zarf_yaml_path, args.flavor, args.source_root)
        mappings, candidate = candidate_mappings(images, args.image_name_override,
                                                args.candidate_state, args.candidate_state_sha256)
        require({mappings.get(image, image) for image in images} == set(canonical), 'rewritten_inventory_mismatch')
        tools = tool_identities()
        source = source_identity(args.source_root)
        with tempfile.TemporaryDirectory(prefix='strict-scan-') as temporary:
            work = Path(temporary)
            phase = 'released package and SBOM acquisition'
            archive, release = acquire_release(args.architecture, work, release_tag=args.release_tag)
            sboms = work / 'sboms'; sboms.mkdir()
            baseline = released_sboms(archive, sboms, args.architecture)
            # Validate every pair before database acquisition or any scan.
            require(all(reference(image)['repository'] in baseline for image in canonical),
                    'unsupported_new_image_repository')
            database = work / 'db'; database.mkdir()
            phase = 'vulnerability database acquisition'
            db_identity = prepare_database(database)
            scans = work / 'scans'; scans.mkdir()
            phase = 'image scanning and comparison'
            markdown, scans_receipt = compare_inventory(images, mappings, baseline, scans,
                                                        database, args.architecture)
            phase = 'release and candidate revalidation'
            revalidate_release(release)
            require(inventory(canonical_file, args.flavor, args.source_root) == (canonical, original_sources)
                    and inventory(args.zarf_yaml_path, args.flavor, args.source_root) == (images, selected_sources),
                    'source_manifest_changed')
            require(candidate_mappings(images, args.image_name_override, args.candidate_state,
                    args.candidate_state_sha256) == (mappings, candidate), 'candidate_source_changed')
            require(tool_identities() == tools, 'scanner_tool_changed')
            require(source_identity(args.source_root) == source, 'adapter_source_changed')
            phase = 'comparison receipt writing'
            receipt = {'schema': 1, 'flavor': args.flavor, 'architecture': args.architecture,
                       'source_files': original_sources, 'selected_source_files': selected_sources,
                       'canonical_inventory': canonical, 'selected_inventory': images,
                       'candidate_source': candidate, 'release': release, 'tools': tools,
                       'source': source,
                       'database': db_identity, **scans_receipt,
                       'policy': 'unchanged uds-pk compare-scans reporting; no fail-on-CVE threshold',
                       'new_repository_policy': 'explicit refusal; no synthetic/self baseline',
                       'markdown_sha256': sha(markdown.encode())}
            serialized = json.dumps(receipt, sort_keys=True, indent=2)
            require(len(serialized.encode()) <= 1024 * 1024, 'receipt_limit')
            require(len(markdown.encode()) <= 16 * 1024 * 1024, 'markdown_limit')
            (output / 'receipt.json').write_text(serialized + '\n')
            with args.markdown.open('x') as report:
                report.write(markdown)
        print('Strict image comparison: all selected images have authenticated release pairs')
        return 0
    except RegistryRedirectRejected as error:
        print('Strict image comparison rejected: ' + str(error), file=sys.stderr)
        print(json.dumps(error.diagnostics, sort_keys=True), file=sys.stderr)
    except Rejected as error:
        print('Strict image comparison rejected: ' + str(error), file=sys.stderr)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OSError, RuntimeError,
            StopIteration, subprocess.SubprocessError, tarfile.TarError, urllib.error.URLError, yaml.YAMLError):
        print(f'Strict image comparison rejected: unavailable or invalid input during {phase}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
