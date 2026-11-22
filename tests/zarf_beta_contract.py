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


def render_docs(prefix, mode, extra_args=()):
    args = ['dev', 'inspect', 'manifests', str(ROOT / mode), '--no-color', '--log-level', 'warn']
    args += list(extra_args)
    docs = [x for x in yaml.safe_load_all(command(prefix, args)) if isinstance(x, dict)]
    if not docs or any(d.get('kind') != 'ConfigMap' for d in docs):
        raise RuntimeError('unexpected contract manifests')
    return {d['metadata']['name']: d['data'] for d in docs}


def render(prefix, mode, override):
    extra = []
    for (name, value), path in zip(OVERRIDES.items(), PATHS):
        if override:
            extra += (['--deploy-set-variables', name + '=' + value] if mode == 'alpha'
                      else ['--set-values', path + '=' + value])
    by_name = render_docs(prefix, mode, extra)
    expected_names = {'contract'} if mode == 'alpha' else {'contract', 'optional-addon'}
    if set(by_name) != expected_names:
        raise RuntimeError('unexpected components for ' + mode + ': ' + str(set(by_name)))
    return by_name['contract']


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
    # Verify chartOverrides whole-chart overlay propagates across import.local.
    overlaid = render_docs(prefix, 'beta', ['--set-values', 'chartOverrides.contract.extra=override-ok'])
    if overlaid.get('contract', {}).get('EXTRA') != 'override-ok':
        raise RuntimeError('beta chartOverrides did not propagate')
    # Verify optional-addon receives mapped package values.
    addon_docs = render_docs(
        prefix,
        'beta',
        [
            '--set-values', 'global.domain=opt.example',
            '--set-values', 'storage.cloudtakClass=fast-ssd',
        ],
    )
    if addon_docs.get('optional-addon') != {'DOMAIN': 'opt.example', 'CLOUDTAK_STORAGE_CLASS': 'fast-ssd'}:
        raise RuntimeError('beta optional component values differed')
    # Verify values.schema rejects invalid values before rendering.
    invalid = subprocess.run(
        prefix + [
            'dev', 'inspect', 'manifests', str(ROOT / 'beta'),
            '--no-color', '--log-level', 'warn',
            '--set-values', 'backup.filesystem.owner=invalid',
        ],
        capture_output=True,
        timeout=120,
    )
    if invalid.returncode == 0:
        raise RuntimeError('beta schema failed to reject invalid backup.filesystem.owner')
    with tempfile.TemporaryDirectory() as directory:
        for mode in ('alpha', 'beta'):
            output = Path(directory) / mode
            output.mkdir()
            command(prefix, ['package', 'create', str(ROOT / mode), '--confirm', '--skip-sbom',
                             '--output', str(output), '--architecture', 'amd64'])
            archives = list(output.glob('zarf-package-*.tar.zst'))
            if len(archives) != 1:
                raise RuntimeError('package artifact missing')
            if mode == 'beta':
                defn = yaml.safe_load(command(prefix, ['package', 'inspect', 'definition', str(archives[0]), '--no-color', '--log-level', 'warn']))
                by_comp = {c['name']: c for c in defn.get('components', [])}
                if by_comp.get('optional-addon', {}).get('optional') is not True:
                    raise RuntimeError('beta built package lost optional: true on optional-addon')
    print('PASS: alpha/beta renders, chartOverrides, optional component definition, schema rejection, and package creation')


if __name__ == '__main__':
    main()
