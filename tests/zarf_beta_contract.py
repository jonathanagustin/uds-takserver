#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Real CLI comparison of legacy variables and proposed package values."""
import argparse
from pathlib import Path
import subprocess
import tempfile
import yaml

ROOT = Path(__file__).resolve().parent / 'fixtures/zarf-beta-contract'
DEFAULTS = {'DOMAIN': 'uds.dev', 'TAK_IMAGE_TAG': '5.7-RELEASE-14', 'IN_CLUSTER_DNS': 'true',
    'PASSTHROUGH_HOSTS': 'takserver-api takserver-stream', 'PLUGINS_MEMORY_REQUEST': '320Mi',
    'PLUGINS_MEMORY_LIMIT': '512Mi', 'FILESYSTEM_BACKUP': 'false', 'FILESYSTEM_BACKUP_OWNER': 'local',
    'POSTGRES_STORAGE_CLASS': '', 'CLOUDTAK_STORAGE_CLASS': ''}
PATHS = ('global.domain', 'tak.imageTag', 'dns.inCluster', 'dns.passthroughHosts',
    'tak.plugins.memoryRequest', 'tak.plugins.memoryLimit', 'backup.filesystem.enabled',
    'backup.filesystem.owner', 'storage.postgresClass', 'storage.cloudtakClass')
OVERRIDES = dict(zip(DEFAULTS, ('example.test', 'reviewed-tag', 'false', 'api stream', '256Mi',
    '768Mi', 'true', 'core', 'postgres-test', 'cloudtak-test')))


def command(prefix, args):
    result = subprocess.run(prefix + args, capture_output=True, timeout=120)
    if result.returncode or len(result.stdout) > 1024*1024:
        raise RuntimeError('contract command failed; exit=' + str(result.returncode))
    return result.stdout


def render(prefix, mode, override):
    args = ['dev', 'inspect', 'manifests', str(ROOT / mode), '--no-color', '--log-level', 'warn']
    for (name, value), path in zip(OVERRIDES.items(), PATHS):
        if override:
            args += (['--deploy-set-variables', name + '=' + value] if mode == 'alpha'
                     else ['--set-values', path + '=' + value])
    docs = [x for x in yaml.safe_load_all(command(prefix, args)) if isinstance(x, dict)]
    if len(docs) != 1 or docs[0].get('kind') != 'ConfigMap':
        raise RuntimeError('unexpected contract manifests')
    return docs[0]['data']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', required=True)
    parser.add_argument('--uds', action='store_true')
    args = parser.parse_args()
    # Fail when production adds or changes a legacy input without updating this fixture.
    production = yaml.safe_load((ROOT.parents[2] / 'zarf.yaml').read_text())
    actual = {item['name']: item.get('default', '') for item in production['variables']}
    if actual != DEFAULTS:
        raise RuntimeError('production variable defaults changed; review the migration fixture')
    prefix = [args.cli] + (['zarf'] if args.uds else [])
    for override in (False, True):
        expected = OVERRIDES if override else DEFAULTS
        for mode in ('alpha', 'beta'):
            if render(prefix, mode, override) != expected:
                raise RuntimeError('rendered contract differs: ' + mode)
    with tempfile.TemporaryDirectory() as directory:
        for mode in ('alpha', 'beta'):
            output = Path(directory) / mode
            output.mkdir()
            command(prefix, ['package', 'create', str(ROOT / mode), '--confirm', '--skip-sbom',
                             '--output', str(output), '--architecture', 'amd64'])
            if len(list(output.glob('zarf-package-*.tar.zst'))) != 1:
                raise RuntimeError('package artifact missing')
    print('PASS: four default/override renders and both fixture package creations')


if __name__ == '__main__':
    main()
