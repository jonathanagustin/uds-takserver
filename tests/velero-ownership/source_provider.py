#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Same-run signed Core provider for the opt-in native owner trial only."""
import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('owner_core_build', ROOT / '.github/test-infra/core-restore-source/build.py')
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)
HELPER = 'tests/backup-restore-controller-trial.sh'
HELPERS = (HELPER, '.github/scripts/velero-controller-state.py', 'tests/velero-controller-restore-policy.yaml',
           'tests/velero-ownership/source_provider.py', 'tests/velero-ownership/candidate.py',
           'tests/velero-ownership/prepare.py', 'tests/velero-ownership/source_bundle.py',
           '.github/scripts/staged-migrations.sh', '.github/scripts/staged-migrations.py',
           'tests/ephemeral-cluster.py', 'tests/velero-ownership/drive.py',
           'tests/velero-ownership/transfer.py', 'tests/velero-ownership/observe.py',
           'tests/backup-restore.sh', 'tests/backup-names.sh', 'tests/api-smoke.sh',
           'tests/extract-certs.sh', 'tests/keystore-pass.sh', 'tests/db-dump-secret.sh',
           'tests/ensure-tak-group.py', 'tasks/test.yaml', '.github/scripts/pin-image-tags.sh',
           '.github/scripts/checked-pipe.sh', '.github/scripts/redact-then-filter.sh',
           '.github/scripts/redact-logs.py', '.github/scripts/node-agent-ready.sh')


def helper_inventory(binding):
    """Freeze the complete runtime helper inventory to this workflow's source."""
    # tasks.yaml deliberately carries the reviewed pin-image-tags transform
    # until restore-task-default validates and restores it after archive binding.
    # That existing guard is itself in HELPERS; Driver.verify_helpers then
    # requires exact tasks.yaml bytes before any owner deployment.
    env = {'PATH': '/usr/bin:/bin', 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1', 'GIT_OPTIONAL_LOCKS': '0'}
    if 'HOME' in os.environ:
        env['HOME'] = os.environ['HOME']
    result = {}
    for relative in HELPERS:
        path = B.regular(ROOT / relative, 4 * 1024**2)
        reviewed = subprocess.run(['/usr/bin/git', '--no-replace-objects', '-C', str(ROOT), 'show',
                                   binding['sourceSHA'] + ':' + relative], env=env, timeout=20,
                                  check=True, capture_output=True)
        B.require(len(reviewed.stdout) <= 4 * 1024**2 and reviewed.stdout == path.read_bytes(),
                  'owner runtime helper differs from workflow source')
        result[relative] = B.byte_digest(reviewed.stdout)
    return result


def bundle_reader():
    spec = importlib.util.spec_from_file_location('owner_bundle_reader', Path(__file__).with_name('source_bundle.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def manifest_digest(package, cli, verification):
    """Read canonical identity only AFTER verify_provider's real signature check."""
    cli = B.regular(Path(cli).resolve(), 256 * 1024**2)
    B.require(B.digest(cli) == B.contract()['cli_sha256'], 'unreviewed UDS CLI payload')
    spec = importlib.util.spec_from_file_location('owner_digest_runner', ROOT / 'takx/native-commoncommo/runner.py')
    runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
    env = {k: os.environ[k] for k in ('HOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
           'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if k in os.environ}
    env.update(PATH='/usr/bin:/bin', LC_ALL='C', KUBECONFIG=str(verification / 'empty-kubeconfig'))
    # UDS0.41 digest does not support signature flags. Its stderr warning would
    # contaminate the bounded merged output; the supported quiet flags suppress
    # that warning. This command cannot substitute for verify_provider.
    raw = runner.execute([str(cli), 'zarf', 'package', 'inspect', 'digest', str(package),
                          '--tmpdir', str(verification / 'tmp'), '--log-level=error', '--no-color'],
                         verification, 'package-manifest', 60, env, capture=True,
                         disk_limit=1024**3, log_limit=1024)
    digest = raw.decode().strip()
    B.require(re.fullmatch('sha256:[a-f0-9]{64}', digest), 'canonical package manifest digest differs')
    return digest


def enabled():
    value = os.environ.get('CORE_RESTORE_OWNER_TRIAL', 'false')
    B.require(value in ('true', 'false'), 'invalid Core owner trial selection')
    if value == 'true':
        B.context()
        B.native_host()
        B.require(os.environ.get('IDENTITY_METHOD', 'manual') == 'manual' and
                  os.environ.get('FRESH_STRICT', 'false') == 'false', 'Core owner trial is Velero only')
    return value == 'true'


def paths():
    B.require(enabled(), 'explicit Core owner trial required')
    temporary = Path(os.environ['RUNNER_TEMP']).absolute()
    B.require(temporary.is_dir() and not any(p.is_symlink() for p in (temporary, *temporary.parents)) and
              not temporary.is_relative_to(ROOT) and not ROOT.is_relative_to(temporary), 'private runner directory required')
    return temporary / 'core-source-provider', temporary / 'core-owner-verification'


def inventory(directory):
    name = 'zarf-package-core-backup-restore-' + B.architecture() + '-' + B.VERSION + '-upstream.tar.zst'
    expected = {name, 'provider.json', 'change.patch', 'LICENSE.md', 'inputs.lock.json', 'corresponding-source.tar.gz'}
    B.require(directory.is_dir() and not directory.is_symlink() and {p.name for p in directory.iterdir()} == expected,
              'same-run provider inventory differs')
    return {p.name: B.digest(B.regular(p, 512 * 1024**2)) for p in directory.iterdir()}


def receipt():
    """Recheck provider and signature evidence before bundling, binding and deployment."""
    directory, verification = paths()
    binding, lock = B.context(), B.contract()
    B.verify_recipe(binding)
    helpers = helper_inventory(binding)
    pins = inventory(directory)
    value = json.loads(B.regular(directory / 'provider.json', 256 * 1024).read_bytes(), object_pairs_hook=B.unique)
    B.validate_provider(value, binding, lock)
    verified = json.loads(B.regular(verification / 'verified.json', 256 * 1024).read_text(), object_pairs_hook=B.unique)
    B.require(set(verified) == {'schema', 'providerSHA256', 'packageSHA256', 'signatureVerified', 'certificateIdentity', 'logSHA256'}
              and verified['schema'] == 1 and verified['signatureVerified'] is True and
              verified['certificateIdentity'] == B.IDENTITY and verified['providerSHA256'] == pins['provider.json'] and
              verified['packageSHA256'] == value['packageSHA256'] and
              B.digest(B.regular(verification / 'verify-signature.log', 4 * 1024**2)) == verified['logSHA256'],
              'same-run signature verification differs')
    captured = json.loads(B.regular(verification / 'owner-inputs.json', 256 * 1024).read_text(), object_pairs_hook=B.unique)
    manifest = B.regular(verification / 'package-manifest.log', 1024).read_text().strip()
    B.require(re.fullmatch('sha256:[a-f0-9]{64}', manifest), 'captured package manifest differs')
    result = {'schema': 2, **binding, 'architecture': B.architecture(), 'files': pins,
              'packageSHA256': value['packageSHA256'], 'providerSHA256': pins['provider.json'],
              'restoreHelperSHA256': helpers[HELPER], 'helperSHA256': helpers, 'packageManifestDigest': manifest}
    B.require(captured == result, 'same-run provider changed after verification')
    return result


def verify(cli):
    directory, verification = paths()
    binding = B.context()
    B.verify_recipe(binding)
    helpers = helper_inventory(binding)
    before = inventory(directory)
    value = B.verify_provider(directory, cli, verification)
    package = next(directory / name for name in before if name.startswith('zarf-package-'))
    manifest = manifest_digest(package, cli, verification)
    B.require(inventory(directory) == before and helper_inventory(binding) == helpers,
              'same-run provider or helpers changed during verification')
    result = {'schema': 2, **binding, 'architecture': B.architecture(), 'files': before,
              'packageSHA256': value['packageSHA256'], 'providerSHA256': before['provider.json'],
              'restoreHelperSHA256': helpers[HELPER], 'helperSHA256': helpers, 'packageManifestDigest': manifest}
    with (verification / 'owner-inputs.json').open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    receipt()


def verify_bundle(path, expected):
    B.require(receipt() == expected, 'Core provider changed before bundle use')
    directory, _ = paths()
    digest = bundle_reader().inspect_bundle(path, expected, directory)
    B.require(receipt() == expected, 'Core provider changed during bundle inspection')
    return digest


def replace_package(package, destination):
    """Replace only the backup/restore source in a disposable bundle copy."""
    receipt()
    directory, _ = paths()
    B.require(package['name'] == 'core-backup-restore' and package['ref'] == '1.16.0-upstream' and
              'repository' in package and 'path' not in package, 'unexpected Core trial input')
    package.pop('repository')
    package['path'] = os.path.relpath(directory, destination)
    package['ref'] = B.VERSION + '-upstream'
    package['keylessVerification'] = {'certificateIdentityRegexp': '^' + re.escape(B.IDENTITY) + '$',
                                        'certificateOIDCIssuer': B.ISSUER}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, required=True)
    verify(parser.parse_args().cli)
