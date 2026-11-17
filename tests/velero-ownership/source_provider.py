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

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('owner_core_build', ROOT / '.github/test-infra/core-restore-source/build.py')
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)
HELPER = 'tests/backup-restore-controller-trial.sh'
HELPERS = (HELPER, '.github/scripts/velero-controller-state.py', 'tests/velero-controller-restore-policy.yaml',
           'tests/velero-ownership/source_provider.py', 'tests/velero-ownership/candidate.py',
           'tests/velero-ownership/prepare.py', '.github/scripts/staged-migrations.sh')


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
    pins = inventory(directory)
    value = json.loads((directory / 'provider.json').read_text(), object_pairs_hook=B.unique)
    B.validate_provider(value, binding, lock)
    verified = json.loads(B.regular(verification / 'verified.json', 256 * 1024).read_text(), object_pairs_hook=B.unique)
    B.require(set(verified) == {'schema', 'providerSHA256', 'packageSHA256', 'signatureVerified', 'certificateIdentity', 'logSHA256'}
              and verified['schema'] == 1 and verified['signatureVerified'] is True and
              verified['certificateIdentity'] == B.IDENTITY and verified['providerSHA256'] == pins['provider.json'] and
              verified['packageSHA256'] == value['packageSHA256'] and
              B.digest(B.regular(verification / 'verify-signature.log', 4 * 1024**2)) == verified['logSHA256'],
              'same-run signature verification differs')
    captured = json.loads(B.regular(verification / 'owner-inputs.json', 256 * 1024).read_text(), object_pairs_hook=B.unique)
    result = {'schema': 1, **binding, 'architecture': B.architecture(), 'files': pins,
              'packageSHA256': value['packageSHA256'], 'providerSHA256': pins['provider.json'],
              'restoreHelperSHA256': B.digest(ROOT / HELPER)}
    B.require(captured == result, 'same-run provider changed after verification')
    return result


def verify(cli):
    directory, verification = paths()
    binding = B.context()
    B.verify_recipe(binding)
    before = inventory(directory)
    value = B.verify_provider(directory, cli, verification)
    B.require(inventory(directory) == before, 'same-run provider changed during verification')
    result = {'schema': 1, **binding, 'architecture': B.architecture(), 'files': before,
              'packageSHA256': value['packageSHA256'], 'providerSHA256': before['provider.json'],
              'restoreHelperSHA256': B.digest(ROOT / HELPER)}
    with (verification / 'owner-inputs.json').open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    receipt()


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
