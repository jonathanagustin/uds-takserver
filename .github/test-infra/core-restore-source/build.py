#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Closed Core source-package experiment; default verification launches nothing."""
import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LOCK_SHA = '4beec73e138295ad0f56e8243658c3d38bf5ef5fbe47a6316741b6fd64f42ad8'
ISSUER = 'https://token.actions.githubusercontent.com'
IDENTITY = 'https://github.com/jonathanagustin/uds-takserver/.github/workflows/staged-migrations.yaml@refs/heads/main'
IDENTITY_REGEXP = r'^https://github\.com/jonathanagustin/uds-takserver/\.github/workflows/staged-migrations\.yaml@refs/heads/main$'
VERSION = '1.16.0-restore-policy.1-upstream'


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for b in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def byte_digest(data):
    return hashlib.sha256(data).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON field')
        result[key] = value
    return result


def contract():
    lock = json.loads((HERE / 'inputs.lock.json').read_text(), object_pairs_hook=unique)
    require(byte_digest(json.dumps(lock, sort_keys=True).encode()) == LOCK_SHA, 'source lock differs')
    require(lock['schema'] == 1 and lock['candidate_version'] == VERSION and len(lock['source_files']) == 34,
            'source identity differs')
    require(digest(HERE / 'change.patch') == lock['patch_sha256'] and
            digest(HERE / 'LICENSE.md') == lock['core_license_sha256'], 'source patch/license differs')
    return lock


def context():
    expected = {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'GITHUB_REPOSITORY': 'jonathanagustin/uds-takserver', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_WORKFLOW_REF': IDENTITY.removeprefix('https://github.com/'),
                'SCENARIO': 'velero', 'ARCHITECTURE': 'amd64', 'CORE_RESTORE_POLICY_CANDIDATE': 'true'}
    require(all(os.environ.get(k) == v for k, v in expected.items()), 'candidate requires explicit reviewed main owner workflow')
    require(re.fullmatch('[a-f0-9]{40}', os.environ.get('GITHUB_SHA', '')) and
            all(re.fullmatch('[1-9][0-9]{0,19}', os.environ.get(k, '')) for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')),
            'candidate run/source identity differs')
    return {k: os.environ[v] for k, v in [('sourceSHA', 'GITHUB_SHA'), ('runID', 'GITHUB_RUN_ID'), ('runAttempt', 'GITHUB_RUN_ATTEMPT')]}


def regular(path, limit):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)) and path.is_file() and path.stat().st_size <= limit,
            'bounded regular file required')
    return path


def extract_source(archive, source, lock):
    found = set()
    total = 0
    prefix = 'uds-core-' + lock['core_commit'] + '/'
    with tarfile.open(archive, 'r:*') as t:
        for member in t:
            total += member.size
            require(total <= 512 * 1024**2, 'source archive expansion bound')
            name = member.name.removeprefix(prefix)
            require(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts, 'source archive path differs')
            if name not in lock['source_files']:
                continue
            require(member.isfile() and name not in found and member.size <= 4 * 1024**2, 'source archive entry differs')
            data = t.extractfile(member).read()
            require(byte_digest(data) == lock['source_files'][name], 'source image differs')
            dest = source / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open('xb') as stream:
                stream.write(data)
            found.add(name)
    require(found == set(lock['source_files']), 'source archive is incomplete')


def download_source(dest, lock):
    url = 'https://github.com/defenseunicorns/uds-core/archive/' + lock['core_commit'] + '.tar.gz'
    count = 0
    with urllib.request.urlopen(url, timeout=30) as response, dest.open('xb') as stream:
        while b := response.read(1024 * 1024):
            count += len(b)
            require(count <= 64 * 1024**2, 'source download bound')
            stream.write(b)


def inventory(root):
    return {str(p.relative_to(root)): digest(p) for p in root.rglob('*') if p.is_file()}


def apply_metadata(source, binding=None):
    path = source / 'packages/backup-restore/zarf.yaml'
    text = path.read_text()
    before = yaml.safe_load(text)
    require(text.count('  version: "1.16.0"') == 1 and
            text.count('  description: "UDS Core (Backup and Restore)"') == 1, 'package metadata differs')
    text = text.replace('  version: "1.16.0"', '  version: "' + VERSION + '"').replace(
        '  description: "UDS Core (Backup and Restore)"',
        '  description: "Local UDS Core source derivative for restore-policy qualification; not an upstream release"')
    if binding:
        require(set(binding) == {'sourceSHA', 'runID', 'runAttempt'} and
                re.fullmatch('[a-f0-9]{40}', binding['sourceSHA']) and
                all(re.fullmatch('[1-9][0-9]{0,19}', binding[k]) for k in ('runID', 'runAttempt')), 'signed annotation identity differs')
        require(text.count('  annotations:\n') == 1, 'annotation source differs')
        added = ''.join('    takserver.qualification/' + key + ': \"' + binding[field] + '\"\n'
                        for key, field in [('source-sha', 'sourceSHA'), ('run-id', 'runID'), ('run-attempt', 'runAttempt')])
        text = text.replace('  annotations:\n', '  annotations:\n' + added, 1)
    after = yaml.safe_load(text)
    if binding:
        for key in ('source-sha', 'run-id', 'run-attempt'):
            after['metadata']['annotations'].pop('takserver.qualification/' + key)
    after['metadata']['version'] = before['metadata']['version']
    after['metadata']['description'] = before['metadata']['description']
    require(after == before, 'unexpected package metadata change')
    path.write_text(text)


def inspect_package(path, lock, signed, binding=None):
    import zstandard
    hashes, small, total = {}, {}, 0
    with path.open('rb') as stream, zstandard.ZstdDecompressor().stream_reader(stream) as decoded, tarfile.open(fileobj=decoded, mode='r|') as t:
        for member in t:
            name = member.name.removeprefix('./')
            require(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts and
                    (member.isdir() or member.isfile()), 'package entry differs')
            if member.isdir():
                continue
            total += member.size
            require(name not in hashes and total <= 1024**3 and member.size <= 512 * 1024**2 and len(hashes) < 512,
                    'package expansion bound')
            src = t.extractfile(member)
            h, data = hashlib.sha256(), bytearray()
            while b := src.read(1024 * 1024):
                h.update(b)
                if member.size <= 256 * 1024:
                    data.extend(b)
            hashes[name] = h.hexdigest()
            if member.size <= 256 * 1024:
                small[name] = bytes(data)
            if name.startswith('images/blobs/sha256/'):
                require(name.split('/')[-1] == hashes[name], 'OCI blob differs')
    required = {'checksums.txt', 'zarf.yaml', 'components/velero.tar', 'images/index.json'}
    require(required <= set(small), 'package metadata is incomplete')
    checksums = {}
    for line in small['checksums.txt'].decode().splitlines():
        pin, name = line.split(None, 1)
        name = name.lstrip('*').removeprefix('./')
        require(name not in checksums and hashes.get(name) == pin, 'package checksum differs')
        checksums[name] = pin
    excluded = {'checksums.txt', 'zarf.yaml'} | ({'zarf.bundle.sig'} if signed else set())
    require(set(hashes) - set(checksums) == excluded, 'untracked package payload')
    meta = yaml.safe_load(small['zarf.yaml'])
    require(meta['metadata']['aggregateChecksum'] == byte_digest(small['checksums.txt']) and
            meta['metadata']['name'] == 'core-backup-restore' and meta['metadata']['version'] == VERSION and
            meta['metadata']['description'].startswith('Local UDS Core source derivative') and
            meta['build']['architecture'] == 'amd64' and meta['build']['flavor'] == 'upstream' and
            meta['build'].get('signed', False) is signed and
            set(meta['build'].get('provenanceFiles') or []) == ({'checksums.txt', 'zarf.bundle.sig'} if signed else {'checksums.txt'}), 'package identity differs')
    annotations = meta['metadata'].get('annotations', {})
    signed_fields = {k: v for k, v in annotations.items() if k.startswith('takserver.qualification/')}
    expected_fields = ({'takserver.qualification/source-sha': binding['sourceSHA'],
                        'takserver.qualification/run-id': binding['runID'],
                        'takserver.qualification/run-attempt': binding['runAttempt']} if binding else {})
    require(signed_fields == expected_fields and (not signed or bool(binding)), 'signed package source/run differs')
    manifests = []
    for descriptor in json.loads(small['images/index.json'])['manifests']:
        pin = descriptor['digest'].removeprefix('sha256:')
        require(pin in lock['image_manifest_sha256'], 'unreviewed image manifest')
        image = json.loads(small['images/blobs/sha256/' + pin])
        config = json.loads(small['images/blobs/sha256/' + image['config']['digest'].removeprefix('sha256:')])
        require(config['architecture'] == 'amd64' and config['os'] == 'linux', 'image platform differs')
        manifests.append(pin)
    require(sorted(manifests) == lock['image_manifest_sha256'], 'image inventory differs')
    with tarfile.open(fileobj=io.BytesIO(small['components/velero.tar'])) as t:
        chart = t.extractfile('velero/charts/uds-velero-config-0.1.0.tgz').read()
    with tarfile.open(fileobj=io.BytesIO(chart), mode='r:gz') as t:
        crds = [m for m in t if m.name.endswith('/templates/crds/restores.yaml')]
        require(len(crds) == 1 and byte_digest(t.extractfile(crds[0]).read()) == lock['crd_after_sha256'], 'embedded Restore CRD differs')
    require(any('sbom' in name for name in hashes), 'SBOM payload missing')
    return {'packageSHA256': digest(path), 'imageManifestSHA256': manifests, 'embeddedRestoreCrdSHA256': lock['crd_after_sha256']}


def request_oidc():
    binding = context()
    url = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_URL')
    bearer = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN')
    require(url and bearer, 'job identity capability missing')
    split = urllib.parse.urlsplit(url)
    require(split.scheme == 'https' and split.hostname and split.hostname.endswith('.actions.githubusercontent.com') and
            not split.username and not split.password and split.port in (None, 443), 'job identity endpoint differs')
    query = urllib.parse.parse_qsl(split.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k != 'audience'] + [('audience', 'sigstore')]
    url = urllib.parse.urlunsplit(split._replace(query=urllib.parse.urlencode(query)))
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + bearer})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(64 * 1024 + 1)
    require(len(raw) <= 64 * 1024, 'job identity response bound')
    token = json.loads(raw)['value']
    parts = token.split('.')
    require(len(parts) == 3 and len(token) <= 32 * 1024, 'job identity token shape differs')
    claims = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
    require(claims.get('iss') == ISSUER and claims.get('aud') == 'sigstore' and
            claims.get('repository') == 'jonathanagustin/uds-takserver' and claims.get('sha') == binding['sourceSHA'] and
            claims.get('ref') == 'refs/heads/main' and claims.get('event_name') == 'workflow_dispatch' and
            claims.get('workflow_ref') == IDENTITY.removeprefix('https://github.com/'), 'job identity context differs')
    return token


def public_provider(binding, package, lock, source_archive):
    return {'schema': 1, 'scope': 'local-core-source-candidate', **binding, 'coreCommit': lock['core_commit'],
            'patchSHA256': lock['patch_sha256'], 'sourceLockSHA256': digest(HERE / 'inputs.lock.json'),
            'cliSHA256': lock['cli_sha256'], 'version': VERSION, 'certificateIdentity': IDENTITY,
            'certificateOIDCIssuer': ISSUER, 'signatureVerified': True, 'correspondingSourceSHA256': digest(source_archive), **package}


def validate_provider(value, binding, lock):
    keys = {'schema', 'scope', 'sourceSHA', 'runID', 'runAttempt', 'coreCommit', 'patchSHA256', 'sourceLockSHA256',
            'cliSHA256', 'version', 'certificateIdentity', 'certificateOIDCIssuer', 'signatureVerified',
            'packageSHA256', 'imageManifestSHA256', 'embeddedRestoreCrdSHA256', 'correspondingSourceSHA256'}
    require(set(value) == keys and type(value['schema']) is int and value['schema'] == 1 and value['scope'] == 'local-core-source-candidate' and
            all(value[k] == binding[k] for k in binding) and value['coreCommit'] == lock['core_commit'] and
            value['patchSHA256'] == lock['patch_sha256'] and value['sourceLockSHA256'] == digest(HERE / 'inputs.lock.json') and
            value['cliSHA256'] == lock['cli_sha256'] and value['version'] == VERSION and
            value['certificateIdentity'] == IDENTITY and value['certificateOIDCIssuer'] == ISSUER and
            value['signatureVerified'] is True and
            all(isinstance(value[k], str) and re.fullmatch('[a-f0-9]{64}', value[k]) for k in ('packageSHA256', 'correspondingSourceSHA256')) and
            sorted(value['imageManifestSHA256']) == lock['image_manifest_sha256'] and
            value['embeddedRestoreCrdSHA256'] == lock['crd_after_sha256'], 'candidate provider identity differs')


def verify_corresponding_source(path, lock, binding):
    found, total = set(), 0
    with tarfile.open(path, 'r:gz') as t:
        for member in t:
            name = member.name.removeprefix('source/')
            require(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts and
                    (member.isdir() or member.isfile()), 'corresponding source entry differs')
            if member.isdir():
                continue
            total += member.size
            require(name in lock['source_files'] and name not in found and member.size <= 4 * 1024**2 and
                    total <= 128 * 1024**2, 'corresponding source inventory differs')
            data = t.extractfile(member).read()
            expected = lock['source_files'][name]
            if name == 'src/velero/chart/templates/crds/restores.yaml':
                expected = lock['crd_after_sha256']
            elif name == 'src/velero/tasks.yaml':
                expected = lock['tasks_after_sha256']
            elif name == 'packages/backup-restore/zarf.yaml':
                text = data.decode()
                for key, field in [('source-sha', 'sourceSHA'), ('run-id', 'runID'), ('run-attempt', 'runAttempt')]:
                    line = '    takserver.qualification/' + key + ': "' + binding[field] + '"\n'
                    require(text.count(line) == 1, 'corresponding source binding differs')
                    text = text.replace(line, '', 1)
                version = '  version: "' + VERSION + '"'
                description = '  description: "Local UDS Core source derivative for restore-policy qualification; not an upstream release"'
                require(text.count(version) == text.count(description) == 1, 'corresponding metadata differs')
                text = text.replace(version, '  version: "1.16.0"', 1).replace(description, '  description: "UDS Core (Backup and Restore)"', 1)
                data = text.encode()
            require(byte_digest(data) == expected, 'corresponding source image differs')
            found.add(name)
    require(found == set(lock['source_files']), 'corresponding source incomplete')


def verify_provider(directory, cli, verification):
    lock, binding = contract(), context()
    directory = Path(directory).absolute()
    require(not any(p.is_symlink() for p in (directory, *directory.parents)) and directory.is_dir(), 'provider directory differs')
    entries = list(directory.iterdir())
    require(len(entries) == 6, 'provider file count differs')
    initial = {p.name: digest(regular(p, 512 * 1024**2)) for p in entries}
    provider_path = regular(directory / 'provider.json', 256 * 1024)
    value = json.loads(provider_path.read_text(), object_pairs_hook=unique)
    validate_provider(value, binding, lock)
    name = 'zarf-package-core-backup-restore-amd64-' + VERSION + '.tar.zst'
    expected = {name, 'provider.json', 'change.patch', 'LICENSE.md', 'inputs.lock.json', 'corresponding-source.tar.gz'}
    require({p.name for p in directory.iterdir()} == expected, 'provider file inventory differs')
    for p in directory.iterdir():
        regular(p, 512 * 1024**2)
    package = directory / name
    require(digest(package) == value['packageSHA256'] and
            digest(directory / 'corresponding-source.tar.gz') == value['correspondingSourceSHA256'] and
            digest(directory / 'change.patch') == lock['patch_sha256'] and
            digest(directory / 'LICENSE.md') == lock['core_license_sha256'] and
            digest(directory / 'inputs.lock.json') == digest(HERE / 'inputs.lock.json'), 'provider payload differs')
    verify_corresponding_source(directory / 'corresponding-source.tar.gz', lock, binding)
    observed = inspect_package(package, lock, True, binding)
    require(all(value[k] == v for k, v in observed.items()), 'provider package differs')
    cli = regular(cli, 256 * 1024**2)
    require(digest(cli) == lock['cli_sha256'], 'unreviewed UDS CLI payload')
    verification = Path(verification).absolute()
    require(not any(p.is_symlink() for p in (verification, *verification.parents)) and verification.parent.is_dir() and
            not verification.exists() and not verification.is_relative_to(ROOT) and not ROOT.is_relative_to(verification) and
            not verification.is_relative_to(directory) and not directory.is_relative_to(verification), 'fresh private verification required')
    verification.mkdir(mode=0o700)
    (verification / 'empty-kubeconfig').write_text('apiVersion: v1\nkind: Config\nclusters: []\ncontexts: []\nusers: []\n')
    (verification / 'empty-kubeconfig').chmod(0o600)
    (verification / 'tmp').mkdir(mode=0o700)
    spec = importlib.util.spec_from_file_location('core_candidate_verify_bounded', ROOT / 'takx/native-commoncommo/runner.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    env = {k: os.environ[k] for k in ('HOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
           'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if k in os.environ}
    env.update(PATH='/usr/bin:/bin', LC_ALL='C', KUBECONFIG=str(verification / 'empty-kubeconfig'))
    require({p.name: digest(p) for p in directory.iterdir()} == initial, 'provider changed before signature verification')
    before = initial
    argv = [str(cli), 'zarf', 'package', 'inspect', 'definition', str(package), '--verify=always',
            '--certificate-identity', IDENTITY, '--certificate-oidc-issuer', ISSUER, '--insecure-ignore-tlog=false',
            '--tmpdir', str(verification / 'tmp'), '--no-color']
    runner.execute(argv, verification, 'verify-signature', 90, env, capture=True,
                   disk_limit=1024**3, log_limit=4 * 1024**2)
    require({p.name: digest(p) for p in directory.iterdir()} == before, 'provider changed during verification')
    (verification / 'verified.json').write_text(json.dumps({'schema': 1, 'providerSHA256': digest(provider_path),
        'packageSHA256': value['packageSHA256'], 'signatureVerified': True, 'certificateIdentity': IDENTITY,
        'logSHA256': digest(verification / 'verify-signature.log')}, indent=2) + '\n')
    return value


def verify_recipe(binding):
    env = {'PATH': '/usr/bin:/bin', 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1', 'GIT_OPTIONAL_LOCKS': '0'}
    if 'HOME' in os.environ:
        env['HOME'] = os.environ['HOME']
    for path in (Path(__file__), HERE / 'inputs.lock.json', HERE / 'change.patch', HERE / 'LICENSE.md',
                 ROOT / 'takx/native-commoncommo/runner.py'):
        relative = str(path.relative_to(ROOT))
        result = subprocess.run(['/usr/bin/git', '--no-replace-objects', '-C', str(ROOT), 'show', binding['sourceSHA'] + ':' + relative],
                                env=env, timeout=20, check=True, capture_output=True)
        require(result.stdout == path.read_bytes(), 'producer recipe differs from workflow source')


def build(output, cli, source_archive=None, signed=False):
    lock = contract()
    binding = context() if signed else {}
    if signed:
        verify_recipe(binding)
    cli = regular(cli, 256 * 1024**2)
    require(digest(cli) == lock['cli_sha256'], 'unreviewed UDS CLI payload')
    output = Path(output).absolute()
    require(not any(p.is_symlink() for p in (output, *output.parents)) and output.parent.is_dir() and not output.exists() and
            output != ROOT and not output.is_relative_to(ROOT) and not ROOT.is_relative_to(output), 'fresh disjoint output required')
    require(shutil.disk_usage(output.parent).free >= 5 * 1024**3, 'output/free-space floor')
    output.mkdir(mode=0o700)
    spec = importlib.util.spec_from_file_location('core_candidate_bounded', ROOT / 'takx/native-commoncommo/runner.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    env = {k: os.environ[k] for k in ('HOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
           'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if k in os.environ}
    env.update(PATH='/usr/bin:/bin', LC_ALL='C', TMPDIR=str(output / 'tmp'), KUBECONFIG=str(output / 'empty-kubeconfig'))
    for name in ('tmp', 'cache', 'artifacts', 'source'):
        (output / name).mkdir(mode=0o700)
    (output / 'empty-kubeconfig').write_text('apiVersion: v1\nkind: Config\nclusters: []\ncontexts: []\nusers: []\n')
    (output / 'empty-kubeconfig').chmod(0o600)
    started = time.monotonic()
    record = {'completed': False, 'accepted': False, 'scope': 'source package only; no API/restore acceptance', 'commands': [],
              'inputs': {str(p): digest(p) for p in (Path(__file__), HERE / 'inputs.lock.json', HERE / 'change.patch',
                         HERE / 'LICENSE.md', Path(runner.__file__), cli)}}
    write = lambda: (output / 'PRIVATE-build-receipt.json').write_text(json.dumps(record, indent=2) + '\n')
    write()
    def command(label, args, seconds):
        item = {'label': label, 'argv': list(map(str, args))}
        record['commands'].append(item)
        write()
        try:
            runner.execute(item['argv'], output, label, min(seconds, 1800 - (time.monotonic() - started)), env,
                           capture=True, disk_limit=3 * 1024**3, log_limit=4 * 1024**2)
            item['exit'] = 0
        finally:
            p = output / (label + '.log')
            if p.exists():
                item['logSHA256'] = digest(p)
            write()
    def alarm(*unused):
        raise TimeoutError('candidate aggregate deadline')
    signal.signal(signal.SIGALRM, alarm)
    signal.alarm(1800)
    try:
        archive = Path(source_archive) if source_archive else output / 'core-source.tar.gz'
        if source_archive:
            regular(archive, 64 * 1024**2)
        else:
            download_source(archive, lock)
        extract_source(archive, output / 'source', lock)
        record['sourceArchiveSHA256'] = digest(archive)
        record['inputs'][str(archive)] = digest(archive)
        command('source-patch', ['patch', '--directory', output / 'source', '--fuzz=0', '-p1', '-i', HERE / 'change.patch'], 20)
        apply_metadata(output / 'source', binding if signed else None)
        source_images = inventory(output / 'source')
        changed = {n for n in source_images if source_images[n] != lock['source_files'][n]}
        require(changed == {'src/velero/chart/templates/crds/restores.yaml', 'src/velero/tasks.yaml', 'packages/backup-restore/zarf.yaml'} and
                source_images['src/velero/chart/templates/crds/restores.yaml'] == lock['crd_after_sha256'], 'derived source membership differs')
        record['sourceAfterSHA256'] = source_images
        args = [cli, 'zarf', 'package', 'create', output / 'source/packages/backup-restore', '--confirm', '--flavor', 'upstream',
                '--architecture', 'amd64', '--cache', output / 'cache', '--tmpdir', output / 'tmp', '--output', output / 'artifacts', '--no-color']
        if signed:
            with tempfile.TemporaryDirectory(prefix='core-oidc-', dir=output.parent) as private:
                token_file = Path(private) / 'identity.jwt'
                with token_file.open('x', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
                    stream.write(request_oidc())
                command('create-signed', [*args, '--keyless', '--identity-token', token_file], 1500)
        else:
            command('create-local', args, 1500)
        require(inventory(output / 'source') == source_images, 'source changed during build')
        packages = list((output / 'artifacts').glob('zarf-package-core-backup-restore-amd64-*.tar.zst'))
        require(len(packages) == 1, 'candidate archive inventory differs')
        package = inspect_package(packages[0], lock, signed, binding if signed else None)
        require(all(digest(p) == pin for p, pin in record['inputs'].items()), 'builder input changed')
        if signed:
            command('verify-signature', [cli, 'zarf', 'package', 'inspect', 'definition', packages[0], '--verify=always',
                    '--certificate-identity', IDENTITY, '--certificate-oidc-issuer', ISSUER, '--insecure-ignore-tlog=false',
                    '--tmpdir', output / 'tmp', '--no-color'], 90)
        shutil.copyfile(HERE / 'change.patch', output / 'artifacts/change.patch')
        shutil.copyfile(HERE / 'LICENSE.md', output / 'artifacts/LICENSE.md')
        shutil.copyfile(HERE / 'inputs.lock.json', output / 'artifacts/inputs.lock.json')
        with tarfile.open(output / 'artifacts/corresponding-source.tar.gz', 'x:gz', format=tarfile.PAX_FORMAT) as t:
            t.add(output / 'source', arcname='source')
        if signed:
            value = public_provider(binding, package, lock, output / 'artifacts/corresponding-source.tar.gz')
            validate_provider(value, binding, lock)
            (output / 'artifacts/provider.json').write_text(json.dumps(value, indent=2) + '\n')
        require(all(digest(p) == pin for p, pin in record['inputs'].items()), 'builder input changed')
        record.update(completed=True, package=package, signed=signed)
        write()
    except BaseException as error:
        record.update(failed=True, failureType=type(error).__name__)
        write()
        raise
    finally:
        signal.alarm(0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('verify', 'local', 'build', 'verify-provider'), default='verify')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cli', type=Path)
    parser.add_argument('--source-archive', type=Path)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--verification', type=Path)
    args = parser.parse_args(argv)
    if args.phase == 'verify':
        require(all(v is None for v in (args.output, args.cli, args.source_archive, args.directory, args.verification)), 'explicit phase required')
        contract()
        return
    if args.phase == 'verify-provider':
        require(args.directory is not None and args.verification is not None and args.cli is not None and
                args.output is args.source_archive is None, 'complete provider verification arguments required')
        verify_provider(args.directory, args.cli, args.verification)
        return
    require(args.directory is args.verification is None, 'provider arguments require their own phase')
    require(args.output is not None and args.cli is not None, 'CLI and fresh output required')
    require(args.phase != 'build' or args.source_archive is None, 'signed CI requires its own source acquisition')
    build(args.output, args.cli, args.source_archive, args.phase == 'build')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Core source candidate failed; private bounded evidence retained when available')
