#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Live Kubernetes qualification of signed Zarf v1beta1 and UDS Next-mode bundles.

Exercises five live cluster gates tracked by #712 against a disposable k3d
cluster using local ConfigMap fixtures (no container images or registry):
  1. Signed Next-mode bundle deploy with deploy-time `--set` values and
     `optional_components = ["optional-addon"]` (plus wrong-key rejection).
  2. Clean opt-out deploy with `optional_components = ["-optional-addon"]`.
  3. Live in-place upgrade from a `v1alpha1` package to a signed `v1beta1`
     Next-mode bundle.
  4. Signed direct `v1beta1` `uds zarf package deploy` optional-component
     default OFF vs `--components optional-addon` opt-in with `--set-values`,
     followed by clean `package remove`.
  5. Mixed signed Next-mode bundle combining a `v1alpha1` package and a
     `v1beta1` package with `--set` values and clean `bundle remove`.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from tests import zarf_beta_contract as zbc

FIXTURE_ROOT = Path(__file__).resolve().parent / 'fixtures/zarf-beta-contract'


def run_cmd(cmd, env=None, timeout=180):
    result = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
    if result.returncode != 0:
        tail = ' | '.join(result.stderr.decode(errors='replace').strip().splitlines()[-6:])[-500:]
        raise RuntimeError(f'command failed (exit={result.returncode}): {cmd[:4]}; stderr={tail}')
    return result.stdout


def get_configmap(cli, name, namespace='default'):
    raw = run_cmd([cli, 'zarf', 'tools', 'kubectl', 'get', 'configmap', name, '-n', namespace, '-o', 'json'])
    return json.loads(raw).get('data', {})


def assert_configmap_absent(cli, name, namespace='default'):
    result = subprocess.run(
        [cli, 'zarf', 'tools', 'kubectl', 'get', 'configmap', name, '-n', namespace],
        capture_output=True, timeout=30,
    )
    if result.returncode == 0:
        raise RuntimeError(f'expected ConfigMap/{name} in namespace {namespace} to be absent, but it exists')


def _write_bundle_scaffold(bundle_dir, name, pub_path, packages_hcl):
    bundle_dir.mkdir()
    shutil.copyfile(pub_path, bundle_dir / 'pkg.pub')
    (bundle_dir / 'defaults.uds.hcl').write_text(
        'variables = {\n'
        '  domain         = "default.example"\n'
        '  cloudtak_class = "default-ssd"\n'
        '}\n'
    )
    (bundle_dir / 'bundle-values.yaml').write_text(
        'global:\n'
        '  domain: "{{ .vars.domain }}"\n'
        'storage:\n'
        '  cloudtakClass: "{{ .vars.cloudtak_class }}"\n'
    )
    (bundle_dir / 'bundle.uds.hcl').write_text(
        'uds {\n'
        '  bundle_api_version = "uds.dev/v1alpha1"\n'
        '}\n\n'
        'metadata {\n'
        f'  name    = "{name}"\n'
        '  version = "0.1.0"\n'
        '}\n\n'
        f'{packages_hcl}'
    )


def _contract_package_hcl(pkg_path, selector):
    return (
        'package "contract" {\n'
        f'  source              = "{Path(pkg_path).as_posix()}"\n'
        '  values_files        = ["./bundle-values.yaml"]\n'
        f'  optional_components = ["{selector}"]\n'
        '  signature_verification {\n'
        '    public_key = file("pkg.pub")\n'
        '  }\n'
        '}\n'
    )


def write_live_bundle(bundle_dir, name, pkg_path, pub_path, selector):
    _write_bundle_scaffold(bundle_dir, name, pub_path, _contract_package_hcl(pkg_path, selector))


def build_bundle(cli, bundle_dir, signing_key, env):
    run_cmd([cli, 'bundle', 'create', str(bundle_dir), '--signing-key', str(signing_key),
             '--architecture', 'amd64'], env=env)
    archives = list(bundle_dir.glob('uds-bundle-*.tar.zst'))
    if len(archives) != 1:
        raise RuntimeError(f'expected 1 bundle archive in {bundle_dir}, found {len(archives)}')
    return archives[0]


def direct_deploy_args(zarf_prefix, pkg_path, pub_path, *, components=(), set_values=()):
    args = [
        *zarf_prefix, 'package', 'deploy', str(pkg_path),
        '--confirm', '--key', str(pub_path), '--verify=always',
    ]
    if components:
        args += ['--components', ','.join(components)]
    for pair in set_values:
        args += ['--set-values', pair]
    return args


def write_alpha_standin_package(pkg_dir):
    pkg_dir.mkdir()
    (pkg_dir / 'core-cm.yaml').write_text(
        'apiVersion: v1\n'
        'kind: ConfigMap\n'
        'metadata:\n'
        '  name: core-standin\n'
        '  namespace: default\n'
        'data:\n'
        '  CORE_SETTING: "###ZARF_VAR_CORE_SETTING###"\n'
    )
    (pkg_dir / 'zarf.yaml').write_text(
        'kind: ZarfPackageConfig\n'
        'metadata:\n'
        '  name: core-standin\n'
        '  version: 0.1.0\n'
        'variables:\n'
        '  - name: CORE_SETTING\n'
        '    default: core-default\n'
        'components:\n'
        '  - name: core-baseline\n'
        '    required: true\n'
        '    manifests:\n'
        '      - name: core-standin\n'
        '        files:\n'
        '          - core-cm.yaml\n'
    )


def write_mixed_live_bundle(bundle_dir, name, alpha_pkg, beta_pkg, pub_path):
    core_hcl = (
        'package "core-standin" {\n'
        f'  source = "{Path(alpha_pkg).as_posix()}"\n'
        '  signature_verification {\n'
        '    public_key = file("pkg.pub")\n'
        '  }\n'
        '}\n\n'
    )
    _write_bundle_scaffold(
        bundle_dir, name, pub_path,
        core_hcl + _contract_package_hcl(beta_pkg, 'optional-addon'),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', default='uds')
    args = parser.parse_args()
    cli = args.cli
    zarf_prefix = [cli, 'zarf']
    next_env = {**os.environ, 'CLI_FEATURES': 'NextMode=true', 'COSIGN_PASSWORD': ''}
    sign_env = {**os.environ, 'COSIGN_PASSWORD': ''}

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        good_key, good_pub = zbc.generate_keypair(zarf_prefix, tmp, 'good')
        _wrong_key, wrong_pub = zbc.generate_keypair(zarf_prefix, tmp, 'wrong')

        # Build unsigned v1alpha1 package and signed v1beta1 package.
        alpha_out = tmp / 'alpha'
        beta_out = tmp / 'beta-signed'
        alpha_out.mkdir()
        beta_out.mkdir()
        run_cmd(zarf_prefix + ['package', 'create', str(FIXTURE_ROOT / 'alpha'), '--confirm',
                               '--skip-sbom', '--output', str(alpha_out), '--architecture', 'amd64'])
        run_cmd(zarf_prefix + ['package', 'create', str(FIXTURE_ROOT / 'beta'), '--confirm',
                               '--skip-sbom', '--signing-key', str(good_key),
                               '--output', str(beta_out), '--architecture', 'amd64'], env=sign_env)
        alpha_pkg = next(alpha_out.glob('zarf-package-*.tar.zst'))
        beta_pkg = next(beta_out.glob('zarf-package-*.tar.zst'))

        # Build signed Next-mode include and exclude bundles.
        include_dir = tmp / 'next-include'
        exclude_dir = tmp / 'next-exclude'
        write_live_bundle(include_dir, 'contract-include', beta_pkg, good_pub, 'optional-addon')
        write_live_bundle(exclude_dir, 'contract-exclude', beta_pkg, good_pub, '-optional-addon')
        include_bundle = build_bundle(cli, include_dir, good_key, next_env)
        exclude_bundle = build_bundle(cli, exclude_dir, good_key, next_env)

        # Gate 1a: wrong public key must be rejected before touching the cluster.
        zbc.expect_failure(
            [cli], ['bundle', 'deploy', str(include_bundle), '--public-key', str(wrong_pub)],
            'bundle deploy accepted wrong public key', env=next_env,
        )
        assert_configmap_absent(cli, 'contract')

        # Gate 1b: signed Next-mode deploy applies --set values and includes optional-addon.
        run_cmd([
            cli, 'bundle', 'deploy', str(include_bundle),
            '--public-key', str(good_pub),
            '--set', 'domain=live-next.example',
            '--set', 'cloudtak_class=live-ssd',
        ], env=next_env)
        contract_cm = get_configmap(cli, 'contract')
        addon_cm = get_configmap(cli, 'optional-addon')
        if contract_cm.get('DOMAIN') != 'live-next.example' or contract_cm.get('CLOUDTAK_STORAGE_CLASS') != 'live-ssd':
            raise RuntimeError(f'Gate 1 contract ConfigMap mismatch: {contract_cm}')
        if addon_cm != {'DOMAIN': 'live-next.example', 'CLOUDTAK_STORAGE_CLASS': 'live-ssd'}:
            raise RuntimeError(f'Gate 1 optional-addon ConfigMap mismatch: {addon_cm}')

        # Clean remove before Gate 2.
        run_cmd([cli, 'bundle', 'remove', str(include_bundle), '--public-key', str(good_pub)], env=next_env)
        assert_configmap_absent(cli, 'contract')
        assert_configmap_absent(cli, 'optional-addon')

        # Gate 2: signed Next-mode opt-out bundle omits optional-addon.
        run_cmd([
            cli, 'bundle', 'deploy', str(exclude_bundle),
            '--public-key', str(good_pub),
            '--set', 'domain=optout.example',
        ], env=next_env)
        optout_cm = get_configmap(cli, 'contract')
        if optout_cm.get('DOMAIN') != 'optout.example':
            raise RuntimeError(f'Gate 2 opt-out contract ConfigMap mismatch: {optout_cm}')
        assert_configmap_absent(cli, 'optional-addon')

        # Clean remove before Gate 3.
        run_cmd([cli, 'bundle', 'remove', str(exclude_bundle), '--public-key', str(good_pub)], env=next_env)
        assert_configmap_absent(cli, 'contract')

        # Gate 3: deploy v1alpha1 package, then upgrade in place via signed v1beta1 Next-mode bundle.
        run_cmd(zarf_prefix + [
            'package', 'deploy', str(alpha_pkg), '--confirm',
            '--set-variables', 'DOMAIN=alpha-before.example',
        ])
        before_cm = get_configmap(cli, 'contract')
        if before_cm.get('DOMAIN') != 'alpha-before.example':
            raise RuntimeError(f'Gate 3 alpha baseline mismatch: {before_cm}')
        assert_configmap_absent(cli, 'optional-addon')

        run_cmd([
            cli, 'bundle', 'deploy', str(include_bundle),
            '--public-key', str(good_pub),
            '--set', 'domain=beta-after.example',
            '--set', 'cloudtak_class=upgraded-ssd',
        ], env=next_env)
        after_cm = get_configmap(cli, 'contract')
        after_addon = get_configmap(cli, 'optional-addon')
        if after_cm.get('DOMAIN') != 'beta-after.example' or after_cm.get('CLOUDTAK_STORAGE_CLASS') != 'upgraded-ssd':
            raise RuntimeError(f'Gate 3 upgraded contract ConfigMap mismatch: {after_cm}')
        if after_addon != {'DOMAIN': 'beta-after.example', 'CLOUDTAK_STORAGE_CLASS': 'upgraded-ssd'}:
            raise RuntimeError(f'Gate 3 upgraded optional-addon ConfigMap mismatch: {after_addon}')

        # Clean remove before Gate 4.
        run_cmd([cli, 'bundle', 'remove', str(include_bundle), '--public-key', str(good_pub)], env=next_env)
        assert_configmap_absent(cli, 'contract')
        assert_configmap_absent(cli, 'optional-addon')

        # Gate 4a: direct v1beta1 deploy keeps optional components OFF by default.
        run_cmd(direct_deploy_args(
            zarf_prefix, beta_pkg, good_pub,
            set_values=('global.domain=direct-default.example',),
        ))
        direct_default_cm = get_configmap(cli, 'contract')
        if direct_default_cm.get('DOMAIN') != 'direct-default.example':
            raise RuntimeError(f'Gate 4a direct default contract ConfigMap mismatch: {direct_default_cm}')
        assert_configmap_absent(cli, 'optional-addon')

        # Gate 4b: direct v1beta1 deploy with --components optional-addon opts in cleanly.
        run_cmd(direct_deploy_args(
            zarf_prefix, beta_pkg, good_pub,
            components=('optional-addon',),
            set_values=('global.domain=direct-opt-in.example', 'storage.cloudtakClass=direct-ssd'),
        ))
        direct_opt_in_cm = get_configmap(cli, 'contract')
        direct_addon_cm = get_configmap(cli, 'optional-addon')
        if (
            direct_opt_in_cm.get('DOMAIN') != 'direct-opt-in.example'
            or direct_opt_in_cm.get('CLOUDTAK_STORAGE_CLASS') != 'direct-ssd'
        ):
            raise RuntimeError(f'Gate 4b direct opt-in contract ConfigMap mismatch: {direct_opt_in_cm}')
        if direct_addon_cm != {'DOMAIN': 'direct-opt-in.example', 'CLOUDTAK_STORAGE_CLASS': 'direct-ssd'}:
            raise RuntimeError(f'Gate 4b direct opt-in optional-addon ConfigMap mismatch: {direct_addon_cm}')

        # Clean remove of direct v1beta1 package.
        run_cmd(zarf_prefix + [
            'package', 'remove', str(beta_pkg), '--confirm', '--key', str(good_pub), '--verify=always',
        ])
        assert_configmap_absent(cli, 'contract')
        assert_configmap_absent(cli, 'optional-addon')

        # Gate 5: mixed signed Next-mode bundle (v1alpha1 + v1beta1) deploys with --set and removes cleanly.
        alpha_standin_src = tmp / 'core-standin-src'
        alpha_standin_out = tmp / 'core-standin-signed'
        alpha_standin_out.mkdir()
        write_alpha_standin_package(alpha_standin_src)
        run_cmd(zarf_prefix + [
            'package', 'create', str(alpha_standin_src), '--confirm', '--skip-sbom',
            '--signing-key', str(good_key), '--output', str(alpha_standin_out), '--architecture', 'amd64',
        ], env=sign_env)
        alpha_standin_pkg = next(alpha_standin_out.glob('zarf-package-*.tar.zst'))

        mixed_dir = tmp / 'next-mixed'
        write_mixed_live_bundle(mixed_dir, 'contract-mixed', alpha_standin_pkg, beta_pkg, good_pub)
        mixed_bundle = build_bundle(cli, mixed_dir, good_key, next_env)

        run_cmd([
            cli, 'bundle', 'deploy', str(mixed_bundle),
            '--public-key', str(good_pub),
            '--set', 'domain=mixed-live.example',
            '--set', 'cloudtak_class=mixed-ssd',
        ], env=next_env)
        mixed_core_cm = get_configmap(cli, 'core-standin')
        if mixed_core_cm != {'CORE_SETTING': 'core-default'}:
            raise RuntimeError(f'Gate 5 core-standin ConfigMap mismatch: {mixed_core_cm}')
        mixed_contract_cm = get_configmap(cli, 'contract')
        if (
            mixed_contract_cm.get('DOMAIN') != 'mixed-live.example'
            or mixed_contract_cm.get('CLOUDTAK_STORAGE_CLASS') != 'mixed-ssd'
        ):
            raise RuntimeError(f'Gate 5 contract ConfigMap mismatch: {mixed_contract_cm}')
        mixed_addon_cm = get_configmap(cli, 'optional-addon')
        if mixed_addon_cm != {'DOMAIN': 'mixed-live.example', 'CLOUDTAK_STORAGE_CLASS': 'mixed-ssd'}:
            raise RuntimeError(f'Gate 5 optional-addon ConfigMap mismatch: {mixed_addon_cm}')

        run_cmd([cli, 'bundle', 'remove', str(mixed_bundle), '--public-key', str(good_pub)], env=next_env)
        assert_configmap_absent(cli, 'core-standin')
        assert_configmap_absent(cli, 'contract')
        assert_configmap_absent(cli, 'optional-addon')

    print(
        'PASS: live k3d signed Next-mode deploy, optional-component opt-out, '
        'v1alpha1 -> v1beta1 upgrade, direct v1beta1 --components opt-in, '
        'and mixed v1alpha1 + v1beta1 bundle qualification'
    )


if __name__ == '__main__':
    main()
