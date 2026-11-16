#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Observe a Core-owned Restore CRD upgrade using server dry runs only."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tarfile
import yaml

HERE = Path(__file__).resolve().parent

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

B = module('core_source_api_builder', HERE / 'build.py')
E = module('core_source_api_ephemeral', B.ROOT / 'tests/ephemeral-cluster.py')
R = module('core_source_api_runner', B.ROOT / 'takx/native-commoncommo/runner.py')
BASELINE_DIGEST = 'sha256:e3728d6a8430dbd9a1127cbce7cf474026f8e393135ecb24cbe0616d70e69fa6'
UPSTREAM_IDENTITY = 'https://github.com/defenseunicorns/uds-core/.github/workflows/publish.yaml@refs/tags/v1.16.0'


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def crd_spec(value):
    """Permit only the two Kubernetes defaults absent from the source CRD."""
    value = copy.deepcopy(value)
    if value.get('conversion') == {'strategy': 'None'}:
        value.pop('conversion')
    if value.get('preserveUnknownFields') is False:
        value.pop('preserveUnknownFields')
    return value


def expected_specs(directory, lock):
    path = directory / 'corresponding-source.tar.gz'
    with tarfile.open(path, 'r:gz') as t:
        data = t.extractfile('source/src/velero/chart/templates/crds/restores.yaml').read()
    B.require(B.byte_digest(data) == lock['crd_after_sha256'], 'candidate CRD source differs')
    candidate = yaml.safe_load(data)['spec']
    B.require(len(candidate['versions']) == 1 and candidate['versions'][0]['name'] == 'v1', 'CRD version differs')
    baseline = copy.deepcopy(candidate)
    policy = baseline['versions'][0]['schema']['openAPIV3Schema']['properties']['spec']['properties'].pop('resourcePolicy')
    B.require(set(policy['properties']) == {'apiGroup', 'kind', 'name'} and
              set(policy['required']) == {'kind', 'name'}, 'policy schema differs')
    return {'baseline': baseline, 'candidate': candidate}


def crd_owner_facts(value):
    """Fixed ownership predicates; never expose unknown metadata values."""
    meta = value.get('metadata', {})
    uid = meta.get('uid', '')
    return {'nameMatches': meta.get('name') == 'restores.velero.io',
            'notDeleting': not bool(meta.get('deletionTimestamp')),
            'uidShapeMatches': isinstance(uid, str) and bool(re.fullmatch('[a-f0-9-]{36}', uid)),
            'managedByHelm': meta.get('labels', {}).get('app.kubernetes.io/managed-by') == 'Helm',
            'releaseMatchesCoreConfig': meta.get('annotations', {}).get('meta.helm.sh/release-name') == 'uds-velero-config',
            'releaseNamespaceMatches': meta.get('annotations', {}).get('meta.helm.sh/release-namespace') == 'velero',
            'ownerReferencesAbsent': not bool(meta.get('ownerReferences'))}


def validate_crd(value, expected):
    B.require(value.get('kind') == 'CustomResourceDefinition' and
              value.get('apiVersion') == 'apiextensions.k8s.io/v1', 'CRD API differs')
    meta = value.get('metadata', {})
    B.require(all(crd_owner_facts(value).values()), 'CRD ownership differs')
    B.require(crd_spec(value['spec']) == expected, 'CRD schema differs beyond the reviewed policy')
    conditions = {c['type']: c['status'] for c in value.get('status', {}).get('conditions', [])}
    B.require(conditions.get('Established') == 'True' and conditions.get('NamesAccepted') == 'True',
              'CRD is not established')
    return hashlib.sha256(meta['uid'].encode()).hexdigest()


def bootstrap_crd(value, expected):
    """Recognize only the exact baseline with correct or absent tracking fields."""
    B.require(value.get('kind') == 'CustomResourceDefinition' and
              value.get('apiVersion') == 'apiextensions.k8s.io/v1', 'bootstrap CRD API differs')
    facts = crd_owner_facts(value)
    B.require(all(facts[key] for key in ('nameMatches', 'notDeleting', 'uidShapeMatches', 'ownerReferencesAbsent')),
              'bootstrap CRD identity differs')
    meta = value['metadata']
    B.require(meta.get('labels', {}).get('component') == 'velero' and crd_spec(value['spec']) == expected,
              'bootstrap CRD source differs')
    fields = [(meta.get('labels', {}), 'app.kubernetes.io/managed-by', 'Helm'),
              (meta.get('annotations', {}), 'meta.helm.sh/release-name', 'uds-velero-config'),
              (meta.get('annotations', {}), 'meta.helm.sh/release-namespace', 'velero')]
    B.require(all(key not in data or data[key] == required for data, key, required in fields),
              'bootstrap CRD has conflicting tracking metadata')
    return {'crdUidSHA256': hashlib.sha256(meta['uid'].encode()).hexdigest(),
            'trackingFieldsPresent': sum(key in data for data, key, _ in fields)}


def probe(policy=True, unknown=False, missing=None):
    spec = {'backupName': 'schema-validation-only'}
    if policy:
        spec['resourcePolicy'] = {'kind': 'configmap', 'name': 'schema-validation-only'}
        if missing:
            spec['resourcePolicy'].pop(missing)
    if unknown:
        spec['schemaProbeUnexpectedField'] = True
    return {'apiVersion': 'velero.io/v1', 'kind': 'Restore',
            'metadata': {'generateName': 'uds-restore-policy-schema-', 'namespace': 'velero'}, 'spec': spec}


def rejected(output, field, required=False):
    text = output.decode('utf-8', errors='strict')
    if required:
        return ('spec.resourcePolicy.' + field + ': Required value') in text
    # Kubectl's server-side strict decoder reports the full JSON path.
    return ('unknown field "spec.' + field + '"') in text


def bundle(variant, directory):
    B.require(variant in ('baseline', 'candidate'), 'bundle variant differs')
    package = {'name': 'core-backup-restore', 'path': str(directory),
               'ref': ('1.16.0' if variant == 'baseline' else B.VERSION) + '-upstream',
               'keylessVerification': {'certificateIdentityRegexp':
                   '^' + re.escape(UPSTREAM_IDENTITY if variant == 'baseline' else B.IDENTITY) + '$',
                   'certificateOIDCIssuer': B.ISSUER},
               'overrides': {'velero': {'velero': {'values': [
                   {'path': 'deployNodeAgent', 'value': False},
                   {'path': 'schedules.udsbackup.paused', 'value': True}]}}}}
    return {'kind': 'UDSBundle', 'metadata': {'name': 'core-restore-api-' + variant,
            'version': 'dev', 'description': 'Disposable Core Restore API schema smoke only'}, 'packages': [package]}


def failure_diagnostic(error, observer=None):
    """Emit fixed classifications and known source frames, never private text."""
    summary = B.failure_summary(error)
    categories = {
        'CRD ownership differs': 'crd-owner',
        'CRD schema differs beyond the reviewed policy': 'crd-schema',
        'CRD is not established': 'crd-not-established',
        'server dry-run result differs': 'dry-run-outcome',
        'API rejection reason differs': 'dry-run-reason',
        'a Restore persisted during schema smoke': 'restore-persisted',
        'API smoke requires no existing Restore objects': 'restore-inventory',
        'CRD was replaced during Core upgrade': 'crd-replaced',
        'CRD changed during observation': 'crd-changed',
        'API bundle inventory differs': 'bundle-inventory',
        'API trial cluster changed': 'cluster-identity',
        'bootstrap CRD API differs': 'bootstrap-api',
        'bootstrap CRD identity differs': 'bootstrap-identity',
        'bootstrap CRD source differs': 'bootstrap-source',
        'bootstrap CRD has conflicting tracking metadata': 'bootstrap-foreign-owner',
        'baseline reconciliation requires no backup or restore objects': 'bootstrap-active-operations',
        'baseline reconciliation replaced the CRD': 'bootstrap-crd-replaced',
        'deployment bundle changed': 'deployment-input',
    }
    files = {str(Path(path).resolve()): str(Path(path).resolve().relative_to(B.ROOT))
             for path in (__file__, B.__file__, E.__file__, R.__file__)}
    frames, current = [], error.__traceback__
    while current:
        path = str(Path(current.tb_frame.f_code.co_filename).resolve())
        if path in files:
            frames.append({'file': files[path], 'line': current.tb_lineno, 'sourceSHA256': B.digest(path)})
        current = current.tb_next
    result = {'schema': 1, 'scope': 'source-callsite-only',
              'category': categories.get(str(error), summary['category']),
              'exceptionType': summary['exceptionType'], 'frames': frames[-8:]}
    if observer is not None:
        allowed = {'checkpoint', 'verified-official-pull', 'baseline-bootstrap-crd', 'baseline-empty-backups',
                   'baseline-empty-restores', 'baseline-reconcile', 'baseline-reconciled-crd'}
        for variant in ('baseline', 'candidate'):
            allowed.update(variant + '-' + suffix for suffix in ('create', 'deploy', 'valid', 'unknown', 'kind', 'name'))
            allowed.update(prefix + '-' + variant for prefix in ('read-crd', 'reread-crd', 'before-restores', 'after-restores'))
        result['command'] = observer.last_label if observer.last_label in allowed else 'unclassified'
        patterns = {
            'unknown-flag': rb'unknown flag:',
            'missing-api': rb'no matches for kind|resource mapping not found',
            'helm-timeout': rb'timed out waiting|context deadline exceeded',
            'helm-owner': rb'invalid ownership metadata|cannot be imported into the current release',
            'field-conflict': rb'field manager conflict|conflict with',
            'schema-rejection': rb'unknown field|strict decoding error|Required value',
            'image-pull': rb'ImagePullBackOff|ErrImagePull',
            'resource-pressure': rb'Insufficient cpu|Insufficient memory|FailedScheduling',
            'network-error': rb'no such host|connection refused|i/o timeout|TLS handshake timeout|x509:',
            'api-denied': rb'\bunauthorized\b|\bforbidden\b|access denied',
            'admission-rejected': rb'admission webhook.*(?:denied|rejected)|policy.*violation|violates PodSecurity',
        }
        data = b''
        if observer.last_log and observer.last_log.is_file():
            with observer.last_log.open('rb') as stream:
                stream.seek(max(0, observer.last_log.stat().st_size - 64 * 1024))
                data = stream.read(64 * 1024)
        result['patterns'] = [name for name, pattern in patterns.items() if re.search(pattern, data, re.I)]
    return result


class Observer:
    def __init__(self, cli, output, guard, env):
        self.cli, self.output, self.guard, self.env = cli, output, guard, env
        self.index = 0
        self.last_label = None
        self.last_log = None

    def command(self, argv, label, timeout=45, failure=False):
        self.index += 1
        self.last_label = label
        label = str(self.index) + '-' + label
        self.last_log = self.output / (label + '.log')
        try:
            return True, R.execute([str(v) for v in argv], self.output, label, timeout, self.env,
                                  capture=True, disk_limit=8 * 1024**3, log_limit=4 * 1024**2)
        except ValueError as error:
            # Only a completed nonzero exit may be an expected API rejection.
            # Timeouts, output quotas and other failures never qualify.
            if not failure or str(error) != 'owned command failed; private bounded log retained':
                raise
            return False, (self.output / (label + '.log')).read_bytes()

    def k(self, *args, label, failure=False):
        self.guard()
        return self.command([self.cli, 'zarf', 'tools', 'kubectl', '--context', E.CONTEXT,
                             '--request-timeout=30s', *args], label, failure=failure)

    def deploy_bundle(self, path, pin, label):
        B.require(label in ('baseline-deploy', 'baseline-reconcile', 'candidate-deploy'),
                  'deployment role differs')
        self.guard()
        B.require(B.digest(B.regular(path, 512 * 1024**2)) == pin, 'deployment bundle changed')
        return self.command([self.cli, 'deploy', path, '--packages', 'core-backup-restore', '--confirm',
            '--retries', '1', '--tmpdir', self.output / 'tmp', '--uds-cache', self.output / 'uds-cache',
            '--no-progress', '--no-log-file'], label, timeout=1500)

    def reconcile_baseline(self, path, pin, expected):
        _, raw = self.k('get', 'crd', 'restores.velero.io', '-o', 'json', label='baseline-bootstrap-crd')
        before = bootstrap_crd(json.loads(raw), expected)
        performed = before['trackingFieldsPresent'] < 3
        if performed:
            for resource in ('backups.velero.io', 'restores.velero.io'):
                _, raw = self.k('get', resource, '-n', 'velero', '-o', 'json',
                                label='baseline-empty-' + resource.split('.')[0])
                B.require(json.loads(raw)['items'] == [], 'baseline reconciliation requires no backup or restore objects')
            self.deploy_bundle(path, pin, 'baseline-reconcile')
        _, raw = self.k('get', 'crd', 'restores.velero.io', '-o', 'json', label='baseline-reconciled-crd')
        B.require(validate_crd(json.loads(raw), expected) == before['crdUidSHA256'],
                  'baseline reconciliation replaced the CRD')
        return {'performed': performed, 'initialTrackingFieldsPresent': before['trackingFieldsPresent'],
                'sameSignedBundle': True, 'crdUidStable': True}

    def failure_status(self):
        """Read only the owned trial; emit fixed counters/patterns, not object names."""
        try:
            self.guard()
        except Exception:
            return {'status': 'cluster-guard-refused'}
        try:
            _, raw = self.k('get', 'crd', 'restores.velero.io', '--ignore-not-found',
                            '-o', 'json', label='diagnostic-crd')
            crd = json.loads(raw) if raw.strip() else None
            result = {'status': 'captured', 'crdPresent': crd is not None}
            if crd is not None:
                B.require(crd.get('kind') == 'CustomResourceDefinition' and
                          crd.get('metadata', {}).get('name') == 'restores.velero.io', 'diagnostic CRD differs')
                result['crdOwnership'] = crd_owner_facts(crd)
                result['crdComponentMatchesVelero'] = crd['metadata'].get('labels', {}).get('component') == 'velero'
                result['crdReleaseMatchesVeleroServer'] = crd['metadata'].get('annotations', {}).get('meta.helm.sh/release-name') == 'velero'
                result['crdSpecSHA256'] = canonical(crd_spec(crd['spec']))
                versions = crd['spec']['versions']
                B.require(isinstance(versions, list) and len(versions) <= 8, 'diagnostic CRD version bound')
                served = [v for v in versions if v.get('name') == 'v1']
                result['resourcePolicyPresent'] = len(served) == 1 and 'resourcePolicy' in served[0].get('schema', {}).get('openAPIV3Schema', {}).get('properties', {}).get('spec', {}).get('properties', {})
            _, raw = self.k('get', 'deployment', 'velero', '-n', 'velero', '--ignore-not-found',
                            '-o', 'json', label='diagnostic-deployment')
            deployment = json.loads(raw) if raw.strip() else None
            result['deploymentPresent'] = deployment is not None
            if deployment is not None:
                meta = deployment.get('metadata', {})
                B.require(deployment.get('kind') == 'Deployment' and meta.get('name') == 'velero' and
                          meta.get('namespace') == 'velero', 'diagnostic Deployment differs')
                result['replicas'] = {key: value for key, value in deployment.get('status', {}).items()
                                      if key in ('replicas', 'readyReplicas', 'availableReplicas', 'updatedReplicas')
                                      and type(value) is int and 0 <= value <= 1024}
            _, raw = self.k('get', 'pods', '-n', 'velero', '-o', 'json', label='diagnostic-pods')
            pods = json.loads(raw)['items']
            B.require(isinstance(pods, list) and len(pods) <= 128, 'diagnostic Pod inventory bound')
            phases = ('Pending', 'Running', 'Succeeded', 'Failed', 'Unknown')
            result['podPhaseCounts'] = {phase: sum(p.get('status', {}).get('phase') == phase for p in pods)
                                        for phase in phases}
            result['unclassifiedPodPhaseCount'] = sum(p.get('status', {}).get('phase') not in phases for p in pods)
            _, raw = self.k('get', 'events', '-n', 'velero', '-o', 'json', label='diagnostic-events')
            events = json.loads(raw)['items']
            B.require(isinstance(events, list) and len(events) <= 512, 'diagnostic Event inventory bound')
            patterns = {'resource-pressure': r'Insufficient cpu|Insufficient memory|FailedScheduling',
                        'image-pull': r'ImagePullBackOff|ErrImagePull|failed to pull image',
                        'admission-rejected': r'admission webhook.*(?:denied|rejected)|policy.*violation|violates PodSecurity',
                        'mount-failed': r'FailedMount|MountVolume.*failed',
                        'container-unhealthy': r'Unhealthy|CrashLoopBackOff|BackOff',
                        'api-denied': r'\bunauthorized\b|\bforbidden\b|access denied'}
            result['eventPatternCounts'] = {name: sum(bool(re.search(pattern, str(e.get('reason', '')) + ' ' +
                                                str(e.get('message', '')), re.I)) for e in events)
                                           for name, pattern in patterns.items()}
            return result
        except Exception:
            return {'status': 'unavailable'}

    def observe(self, variant, expected, binding, provider_sha, baseline=None):
        B.require(variant in ('baseline', 'candidate'), 'observation variant differs')
        if variant == 'candidate':
            B.require(baseline and baseline['schema'] == 1 and baseline['completed'] is True and
                      baseline['variant'] == 'baseline' and baseline['binding'] == binding and
                      baseline['providerSHA256'] == provider_sha, 'baseline receipt differs')
        _, raw = self.k('get', 'crd', 'restores.velero.io', '-o', 'json', label='read-crd-' + variant)
        uid = validate_crd(json.loads(raw), expected)
        if baseline:
            B.require(uid == baseline['crdUidSHA256'], 'CRD was replaced during Core upgrade')
        _, raw = self.k('get', 'restores.velero.io', '-n', 'velero', '-o', 'json', label='before-restores-' + variant)
        B.require(json.loads(raw)['items'] == [], 'API smoke requires no existing Restore objects')
        cases = [('valid', probe(), variant == 'candidate', None, False),
                 ('unknown', probe(policy=False, unknown=True), False, 'schemaProbeUnexpectedField', False)]
        if variant == 'candidate':
            cases += [(field, probe(missing=field), False, field, True) for field in ('kind', 'name')]
        for name, value, success, field, required in cases:
            path = self.output / (variant + '-' + name + '.json')
            path.write_text(json.dumps(value))
            ok, raw = self.k('create', '--dry-run=server', '--validate=strict', '-f', path, '-o', 'json',
                             label=variant + '-' + name, failure=True)
            B.require(ok is success, 'server dry-run result differs')
            if ok:
                observed = json.loads(raw)
                B.require(observed.get('kind') == 'Restore' and observed.get('apiVersion') == 'velero.io/v1' and
                          observed['spec']['resourcePolicy'] == value['spec']['resourcePolicy'], 'dry-run result differs')
            else:
                B.require(rejected(raw, field or 'resourcePolicy', required), 'API rejection reason differs')
        _, raw = self.k('get', 'restores.velero.io', '-n', 'velero', '-o', 'json', label='after-restores-' + variant)
        B.require(json.loads(raw)['items'] == [], 'a Restore persisted during schema smoke')
        _, raw = self.k('get', 'crd', 'restores.velero.io', '-o', 'json', label='reread-crd-' + variant)
        B.require(validate_crd(json.loads(raw), expected) == uid, 'CRD changed during observation')
        return {'schema': 1, 'scope': 'api-schema-smoke-only', 'variant': variant, 'completed': True,
                'binding': binding, 'providerSHA256': provider_sha, 'crdUidSHA256': uid,
                'crdSpecSHA256': canonical(expected), 'coreHelmOwnerVerified': True,
                'restoreObjectsPersisted': False, 'dryRunCases': len(cases),
                'restoreAcceptance': False, 'ownerAcceptance': False}


def execute(directory, cli, output):
    import shutil
    import signal
    binding, lock = B.context(), B.contract()
    B.require(os.environ.get('CORE_RESTORE_API_SMOKE') == 'true', 'explicit API smoke job required')
    B.verify_recipe(binding)
    recipe = [Path(__file__), B.ROOT / 'tests/ephemeral-cluster.py',
              B.ROOT / '.github/scripts/deploy-slim-core.sh', B.ROOT / 'mise.toml',
              B.ROOT / '.github/workflows/core-restore-source.yaml', HERE / 'build.py',
              HERE / 'inputs.lock.json', HERE / 'change.patch', HERE / 'LICENSE.md',
              B.ROOT / 'takx/native-commoncommo/runner.py']
    for path in recipe:
        result = B.subprocess.run(['/usr/bin/git', '--no-replace-objects', '-C', str(B.ROOT), 'show',
                    binding['sourceSHA'] + ':' + str(path.relative_to(B.ROOT))], timeout=20, check=True, capture_output=True)
        B.require(result.stdout == path.read_bytes(), 'API recipe differs from workflow source')
    pins = {str(path): B.digest(path) for path in recipe}
    cli = B.regular(cli, 256 * 1024**2)
    B.require(B.digest(cli) == lock['cli_sha256'], 'API CLI differs')
    directory, output = Path(directory).absolute(), Path(output).absolute()
    B.require(output.parent.is_dir() and not output.exists() and
              not any(p.is_symlink() for p in (output, *output.parents)) and
              not output.is_relative_to(B.ROOT) and not B.ROOT.is_relative_to(output) and
              not output.is_relative_to(directory) and not directory.is_relative_to(output), 'fresh API output required')
    B.require(shutil.disk_usage(output.parent).free >= 20 * 1024**3, 'API smoke requires 20 GiB free')
    output.mkdir(mode=0o700)
    state = {'schema': 1, 'scope': 'api-schema-smoke-only', 'completed': False,
             'binding': binding, 'phase': 'verify-provider', 'inputs': pins,
             'restoreAcceptance': False, 'ownerAcceptance': False}
    def save():
        (output / 'private-state.json').write_text(json.dumps(state, indent=2) + '\n')
        public = {k: state[k] for k in ('schema', 'scope', 'completed', 'binding', 'phase',
                                       'restoreAcceptance', 'ownerAcceptance')}
        if 'failureType' in state:
            public['failureType'] = state['failureType']
            public['failure'] = state['failure']
        (output / 'public-result.json').write_text(json.dumps(public, indent=2) + '\n')
    def unchanged():
        B.require(all(B.digest(p) == pin for p, pin in pins.items()), 'API recipe changed during execution')
    def expired(_signum, _frame):
        raise TimeoutError('API aggregate deadline exceeded')
    previous_handler = signal.signal(signal.SIGALRM, expired)
    signal.alarm(7200)
    observer = None
    try:
        save()
        provider = B.verify_provider(directory, cli, output / 'provider-verification')
        payload_pins = {p.name: B.digest(p) for p in directory.iterdir()}
        def payload_unchanged():
            B.require({p.name: B.digest(p) for p in directory.iterdir()} == payload_pins,
                      'provider changed during API smoke')
        provider_sha = B.digest(directory / 'provider.json')
        expected = expected_specs(directory, lock)
        state['phase'] = 'bootstrap'; save()
        E.verify_bootstrap(E.CONTEXT, E.command)
        env = {k: os.environ[k] for k in ('HOME', 'PATH', 'KUBECONFIG', 'HTTP_PROXY', 'HTTPS_PROXY',
               'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
               'SSL_CERT_FILE', 'SSL_CERT_DIR') if k in os.environ}
        env.update(SLIM_CORE_VERSION='1.16.0', SLIM_PACKAGE_DIR=str(output / 'checkpoint'),
                   GITHUB_ACTIONS='true', LC_ALL='C')
        observer = Observer(cli, output, lambda: None, env)
        unchanged()
        observer.command(['bash', B.ROOT / '.github/scripts/deploy-slim-core.sh'], 'checkpoint', timeout=2400)
        E.mark(E.CONTEXT, E.command)
        cluster = canonical(E.verify(E.CONTEXT, E.command))
        def guard():
            unchanged()
            payload_unchanged()
            B.require(canonical(E.verify(E.CONTEXT, E.command)) == cluster, 'API trial cluster changed')
        observer.guard = guard
        state['phase'] = 'official-pull'; save()
        baseline_dir = output / 'official'
        baseline_dir.mkdir()
        observer.command([cli, 'zarf', 'package', 'pull',
            'oci://ghcr.io/defenseunicorns/packages/uds/core-backup-restore@' + BASELINE_DIGEST,
            '--architecture', 'amd64', '--output-directory', baseline_dir, '--verify=always',
            '--certificate-identity', UPSTREAM_IDENTITY, '--certificate-oidc-issuer', B.ISSUER,
            '--insecure-ignore-tlog=false', '--tmpdir', output / 'tmp', '--cache', output / 'cache',
            '--no-color'], 'verified-official-pull', timeout=900)
        archives = list(baseline_dir.glob('*.tar.zst'))
        B.require(len(archives) == 1 and archives[0].name ==
                  'zarf-package-core-backup-restore-amd64-1.16.0-upstream.tar.zst', 'official archive inventory differs')
        baseline_pin = B.digest(archives[0])
        baseline = None
        for variant, package_dir in [('baseline', baseline_dir), ('candidate', directory)]:
            state['phase'] = variant; save()
            guard()
            B.require(B.digest(archives[0]) == baseline_pin, 'official package changed')
            folder = output / variant
            folder.mkdir()
            definition = folder / 'uds-bundle.yaml'
            definition.write_text(yaml.safe_dump(bundle(variant, package_dir), sort_keys=False))
            definition_pin = B.digest(definition)
            observer.command([cli, 'create', folder, '--confirm', '--output', folder,
                '--architecture', 'amd64', '--tmpdir', output / 'tmp', '--uds-cache', output / 'uds-cache',
                '--no-progress', '--no-log-file'], variant + '-create', timeout=900)
            built = list(folder.glob('uds-bundle-*.tar.zst'))
            B.require(len(built) == 1 and B.digest(definition) == definition_pin, 'API bundle inventory differs')
            bundle_pin = B.digest(built[0])
            guard()
            B.require(B.digest(built[0]) == bundle_pin and B.digest(archives[0]) == baseline_pin,
                      'API deployment input changed')
            observer.deploy_bundle(built[0], bundle_pin, variant + '-deploy')
            reconciliation = (observer.reconcile_baseline(built[0], bundle_pin, expected['baseline'])
                              if variant == 'baseline' else None)
            result = observer.observe(variant, expected[variant], binding, provider_sha, baseline)
            if reconciliation is not None:
                result['bootstrapReconciliation'] = reconciliation
            result.update(clusterIdentitySHA256=cluster, bundleSHA256=bundle_pin,
                          candidatePackageSHA256=provider['packageSHA256'],
                          officialIndexDigest=BASELINE_DIGEST, officialArchiveSHA256=baseline_pin)
            (output / (variant + '-review.json')).write_text(json.dumps(result, indent=2) + '\n')
            if variant == 'baseline':
                baseline = result
        guard()
        state.update(completed=True, phase='complete', providerSHA256=provider_sha)
        save()
    except BaseException as error:
        diagnostic = failure_diagnostic(error, observer)
        if observer is not None and state['phase'] in ('baseline', 'candidate'):
            diagnostic['workloads'] = observer.failure_status()
        state.update(failed=True, failureType=diagnostic['exceptionType'], failure=diagnostic)
        save()
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--provider-directory', type=Path)
    parser.add_argument('--cli', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if not args.execute:
        B.require(args.provider_directory is args.cli is args.output is None, 'explicit execution required')
        B.contract()
        return
    B.require(all(v is not None for v in (args.provider_directory, args.cli, args.output)), 'complete API arguments required')
    execute(args.provider_directory, args.cli, args.output)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Core API schema smoke failed; private bounded evidence retained when available')
