#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Real CLI comparison of legacy variables and proposed package values."""
import argparse
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import tempfile
import time
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


def command(prefix, args, env=None):
    result = subprocess.run(prefix + args, capture_output=True, timeout=120, env=env)
    if result.returncode or len(result.stdout) > 1024*1024:
        tail = ' | '.join(result.stderr.decode(errors='replace').strip().splitlines()[-5:])[-400:]
        raise RuntimeError(f'contract command failed; exit={result.returncode}; stderr={tail}')
    return result.stdout


def expect_failure(prefix, args, message, env=None):
    result = subprocess.run(prefix + args, capture_output=True, timeout=120, env=env)
    if result.returncode == 0:
        raise RuntimeError(message)


def generate_keypair(prefix, directory, name):
    key_dir = directory / f'key-{name}'
    key_dir.mkdir()
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        prefix + ['tools', 'gen-key', '--no-color'],
        cwd=key_dir, stdin=slave, stdout=slave, stderr=slave, close_fds=True,
    )
    os.close(slave)
    buf = b''
    sent = 0
    deadline = time.monotonic() + 30
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                lower = buf.lower()
                if sent == 0 and b'password):' in lower:
                    os.write(master, b'\r')
                    sent = 1
                    buf = b''
                elif sent == 1 and b'again' in lower and b'password):' in lower:
                    os.write(master, b'\r')
                    sent = 2
                    buf = b''
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        os.close(master)
    if proc.returncode != 0:
        raise RuntimeError(f'gen-key failed; exit={proc.returncode}')
    key = key_dir / 'cosign.key'
    pub = key_dir / 'cosign.pub'
    if not key.is_file() or not pub.is_file():
        raise RuntimeError('gen-key did not create cosign.key and cosign.pub')
    return key, pub


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


def write_next_bundle(bundle_dir, name, pkg_path, pub_path, selector):
    bundle_dir.mkdir()
    shutil.copyfile(pub_path, bundle_dir / 'pkg.pub')
    (bundle_dir / 'bundle-values.yaml').write_text('global:\n  domain: bundle.example\n')
    (bundle_dir / 'bundle.uds.hcl').write_text(
        'uds {\n'
        '  bundle_api_version = "uds.dev/v1alpha1"\n'
        '}\n\n'
        'metadata {\n'
        f'  name    = "{name}"\n'
        '  version = "0.1.0"\n'
        '}\n\n'
        'package "contract" {\n'
        f'  source              = "{pkg_path}"\n'
        '  values_files        = ["./bundle-values.yaml"]\n'
        f'  optional_components = ["{selector}"]\n'
        '  signature_verification {\n'
        '    public_key = file("pkg.pub")\n'
        '  }\n'
        '}\n'
    )


def verify_next_bundles(cli, directory, signed_pkg, unsigned_pkg, good_key, good_pub, wrong_pub):
    env = {**os.environ, 'CLI_FEATURES': 'NextMode=true', 'COSIGN_PASSWORD': ''}
    for label, selector in (('include', 'optional-addon'), ('exclude', '-optional-addon')):
        bundle_dir = directory / f'next-{label}'
        write_next_bundle(bundle_dir, f'contract-{label}', signed_pkg, good_pub, selector)
        command([cli], ['bundle', 'create', str(bundle_dir), '--signing-key', str(good_key),
                        '--architecture', 'amd64'], env=env)
        bundles = list(bundle_dir.glob('uds-bundle-*.tar.zst'))
        if len(bundles) != 1:
            raise RuntimeError(f'next bundle artifact missing for {label}')
        bundle_archive = bundles[0]
        command([cli], ['bundle', 'verify', str(bundle_archive), '--public-key', str(good_pub)], env=env)
        expect_failure([cli], ['bundle', 'verify', str(bundle_archive), '--public-key', str(wrong_pub)],
                       f'next bundle verify accepted wrong public key for {label}', env=env)
        inspected = json.loads(command(
            [cli], ['bundle', 'inspect', str(bundle_archive), '--public-key', str(good_pub), '-o', 'json'],
            env=env,
        ))
        if inspected.get('bundleSignature', {}).get('status') != 'verified':
            raise RuntimeError(f'next bundle inspect did not report verified signature for {label}')
        pkgs = {p['name']: p for p in inspected.get('packages', [])}
        pkg = pkgs.get('contract', {})
        if pkg.get('signature', {}).get('signed') != 'signed' or pkg.get('signature', {}).get('verification') != 'verified':
            raise RuntimeError(f'next bundle inspect lost verified package posture for {label}')
        if not pkg.get('valuesFiles'):
            raise RuntimeError(f'next bundle inspect missing embedded valuesFiles for {label}')
        # Confirm the OCI archive retained the exact optional_components selector in bundle.uds.hcl.
        unpacked = directory / f'unpacked-{label}'
        unpacked.mkdir()
        subprocess.run(['tar', '--zstd', '-xf', str(bundle_archive), '-C', str(unpacked)],
                       check=True, capture_output=True, timeout=60)
        selector_bytes = f'optional_components = ["{selector}"]'.encode()
        found_selector = any(
            selector_bytes in blob.read_bytes()
            for blob in unpacked.rglob('*')
            if blob.is_file() and blob.stat().st_size <= 1024 * 1024
        )
        if not found_selector:
            raise RuntimeError(f'next bundle OCI archive lost optional_components selector {selector}')
    # Substituting an unsigned package must fail bundle create under public_key policy.
    unsigned_dir = directory / 'next-unsigned'
    write_next_bundle(unsigned_dir, 'contract-unsigned', unsigned_pkg, good_pub, 'optional-addon')
    expect_failure([cli], ['bundle', 'create', str(unsigned_dir), '--signing-key', str(good_key),
                           '--architecture', 'amd64'],
                   'next bundle create accepted an unsigned package under public_key policy', env=env)


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
    expect_failure(
        prefix,
        [
            'dev', 'inspect', 'manifests', str(ROOT / 'beta'),
            '--no-color', '--log-level', 'warn',
            '--set-values', 'backup.filesystem.owner=invalid',
        ],
        'beta schema failed to reject invalid backup.filesystem.owner',
    )
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        good_key, good_pub = generate_keypair(prefix, tmp, 'good')
        _wrong_key, wrong_pub = generate_keypair(prefix, tmp, 'wrong')
        sign_env = {**os.environ, 'COSIGN_PASSWORD': ''}
        unsigned_beta = None
        for mode in ('alpha', 'beta'):
            output = tmp / mode
            output.mkdir()
            command(prefix, ['package', 'create', str(ROOT / mode), '--confirm', '--skip-sbom',
                             '--output', str(output), '--architecture', 'amd64'])
            archives = list(output.glob('zarf-package-*.tar.zst'))
            if len(archives) != 1:
                raise RuntimeError('package artifact missing')
            if mode == 'beta':
                unsigned_beta = archives[0]
                defn = yaml.safe_load(command(prefix, ['package', 'inspect', 'definition', str(unsigned_beta),
                                                       '--no-color', '--log-level', 'warn']))
                by_comp = {c['name']: c for c in defn.get('components', [])}
                if by_comp.get('optional-addon', {}).get('optional') is not True:
                    raise RuntimeError('beta built package lost optional: true on optional-addon')
        # Signed v1beta1 package control: matching key passes; wrong key and unsigned fail.
        signed_out = tmp / 'beta-signed'
        signed_out.mkdir()
        command(prefix, ['package', 'create', str(ROOT / 'beta'), '--confirm', '--skip-sbom',
                         '--signing-key', str(good_key),
                         '--output', str(signed_out), '--architecture', 'amd64'], env=sign_env)
        signed_archives = list(signed_out.glob('zarf-package-*.tar.zst'))
        if len(signed_archives) != 1:
            raise RuntimeError('signed beta package artifact missing')
        signed_beta = signed_archives[0]
        signed_defn = yaml.safe_load(command(
            prefix, ['package', 'inspect', 'definition', str(signed_beta),
                     '--key', str(good_pub), '--verify=always', '--no-color', '--log-level', 'warn'],
        ))
        if {c['name']: c for c in signed_defn.get('components', [])}.get('optional-addon', {}).get('optional') is not True:
            raise RuntimeError('signed beta package lost optional: true on optional-addon')
        expect_failure(
            prefix, ['package', 'inspect', 'definition', str(signed_beta),
                     '--key', str(wrong_pub), '--verify=always', '--no-color', '--log-level', 'warn'],
            'signed beta package verify accepted wrong public key',
        )
        expect_failure(
            prefix, ['package', 'inspect', 'definition', str(unsigned_beta),
                     '--key', str(good_pub), '--verify=always', '--no-color', '--log-level', 'warn'],
            'beta package verify accepted an unsigned package',
        )
        if args.uds:
            verify_next_bundles(args.cli, tmp, signed_beta, unsigned_beta, good_key, good_pub, wrong_pub)
    print('PASS: alpha/beta renders, chartOverrides, optional component definition, schema rejection, signed package, and Next bundle verification')


if __name__ == '__main__':
    main()
