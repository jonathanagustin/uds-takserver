# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Fork transport must preserve native gates and reach every package consumer."""

import copy
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"'(?:''|[^'])*'|[A-Za-z_][\w.*-]*|\d+|==|!=|&&|\|\||[!(),]")
TRANSFER = re.compile(r'pin-image-tags\.sh|wait-for-tak-images\.sh|build\.sh --wait|candidate-consumer\.py (?:prepare|load|ready)|uds run create-dev-package')
PIN_OR_CREATE = re.compile(r'pin-image-tags\.sh|candidate-consumer\.py prepare|uds run create-dev-package')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def workflows():
    return {path.name: yaml.safe_load(path.read_text())
            for path in (ROOT / '.github/workflows').glob('*.yaml')}


def context(fork=True, event='pull_request', run='100', arch='amd64'):
    return {'github.event_name': event, 'github.repository': 'owner/repository',
            'github.event.pull_request.head.repo.full_name': 'fork/repository' if fork else 'owner/repository',
            'github.run_id': run, 'matrix.arch': arch, 'github.token': 'available-token',
            'secrets.GITHUB_TOKEN': 'available-token', 'inputs.push': True,
            'needs.plan.outputs.build': 'true', 'needs.changes.outputs.run': 'true',
            'needs.admission.result': 'success', 'needs.admission.outputs.reuse': 'false',
            'needs.check-flavor.result': 'success', 'needs.check-flavor.outputs.diagnostic-case': 'none',
            'github.event.pull_request.labels.*.name': ['benchmark', 'upgrade-pg15'],
            'github.event.action': 'synchronize', 'github.event.label.name': '',
            'inputs.trust_bundle': False,
            'needs.image-keys.outputs.cloudtak': 'cloud-key', 'needs.image-keys.outputs.postgis': 'postgis-key'}


def expression(source, values, success=True, cancelled=False):
    """Evaluate the boolean/format subset used by these workflow boundaries."""
    if isinstance(source, bool):
        return source
    source = source.strip()
    if source.startswith('${{') and source.endswith('}}'):
        source = source[3:-2].strip()
    functions = {'always': lambda: True, 'success': lambda: success,
                 'cancelled': lambda: cancelled, 'failure': lambda: not success,
                 'format': lambda pattern, *args: pattern.format(*args),
                 'contains': lambda sequence, item: item in sequence}
    operators = {'&&': ' and ', '||': ' or ', '!': ' not ', 'true': 'True', 'false': 'False', 'null': 'None'}
    result, end = [], 0
    for match in TOKEN.finditer(source):
        require(not source[end:match.start()].strip(), 'unsupported workflow expression syntax')
        token = match.group()
        if token.startswith("'"):
            result.append(repr(token[1:-1].replace("''", "'")))
        elif token in operators:
            result.append(operators[token])
        elif token in functions or token in ('==', '!=', '(', ')', ',') or token.isdecimal():
            result.append(token)
        else:
            result.append(repr(values.get(token, '')))
        end = match.end()
    require(not source[end:].strip(), 'unsupported workflow expression suffix')
    return eval(''.join(result).strip(), {'__builtins__': {}}, functions)


def render(value, values):
    def replace(match):
        result = expression(match.group(1), values)
        return str(result).lower() if isinstance(result, bool) else str(result)
    return re.sub(r'\$\{\{(.*?)\}\}', replace, str(value))


def environment(document, job, values, step=None):
    values = dict(values)
    for item in (document, job, step or {}):
        for key, value in item.get('env', {}).items():
            values['env.' + key] = render(value, values)
    return values


def active(item, values, success=True, cancelled=False):
    condition = item.get('if', 'success()')
    if not re.search(r'\b(?:always|success|failure|cancelled)\(', str(condition)) and (not success or cancelled):
        return False
    return bool(expression(condition, values, success, cancelled))


def one_step(job, fragment):
    found = [(index, step) for index, step in enumerate(job['steps']) if fragment in step.get('run', '')]
    require(len(found) == 1, f'expected one workflow gate for {fragment}')
    return found[0]


def must_gate(step, values):
    require(active(step, values), 'native gate is skipped for a fork')
    require(step.get('continue-on-error', False) is False, 'native gate failures are ignored')


def must_read_candidates(document, job, step):
    require(job.get('permissions', document.get('permissions', {})).get('actions') == 'read',
            'candidate validation cannot read canonical producer runs')
    require(environment(document, job, context(), step).get('env.GH_TOKEN') == 'available-token',
            'candidate validation has no API token')


def require_container_plan(documents):
    document = documents['container.yaml']
    plan = document['jobs']['plan']
    _, decision = one_step(plan, 'wait-for-tak-images.sh')
    require(decision.get('env', {}).get('CANDIDATE_PRODUCER') == 'true',
            'canonical planner can recursively wait on its own candidate')
    must_read_candidates(document, plan, decision)
    cache_index, cache = one_step(plan, 'candidate-consumer.py cache-decision')
    fork = environment(document, plan, context())
    fork['steps.image.outputs.build'] = 'false'
    building = {**fork, 'steps.image.outputs.build': 'true'}
    trusted = environment(document, plan, context(fork=False))
    trusted['steps.image.outputs.build'] = 'false'
    require(active(cache, fork) and not active(cache, building)
            and not active(cache, trusted),
            'fork cache reuse has no producer decision or bypasses the native build')
    must_gate(cache, fork)
    uploads = [(index, step) for index, step in enumerate(plan['steps'])
               if step.get('with', {}).get('name', '').startswith('candidate-cache-takserver-')]
    require(len(uploads) == 1 and uploads[0][0] > cache_index, 'cache decision has no subsequent artifact upload')
    require(active(uploads[0][1], fork) and not active(uploads[0][1], fork, success=False)
            and uploads[0][1]['with'].get('if-no-files-found') == 'error',
            'missing cache decision artifact can succeed')


def require_exports(documents):
    for group, filename, helper in (('takserver', 'container.yaml', 'docker'),
                                    ('cloudtak', 'cloudtak-image.yaml', 'cloudtak'),
                                    ('postgis', 'postgis-image.yaml', 'postgis')):
        document = documents[filename]
        job = document['jobs']['build']
        require({item['arch'] for item in job['strategy']['matrix']['include']} == {'amd64', 'arm64'},
                'both native candidate builds are required')
        for item in job['strategy']['matrix']['include']:
            require(('arm' in item['runner']) == (item['arch'] == 'arm64'), 'candidate build uses the wrong native runner')
        require(job.get('continue-on-error', False) is False, 'producer failure is ignored')
        fork = environment(document, job, context())
        trusted = environment(document, job, context(fork=False))
        require(active(job, fork), 'fork candidate producer job is skipped')
        require(fork.get('env.PUSH_IMAGES') == 'false' and trusted.get('env.PUSH_IMAGES') == 'true',
                'fork and trusted image publication modes are confused')
        for event in ('push', 'workflow_dispatch'):
            require(environment(document, job, context(event=event)).get('env.PUSH_IMAGES') == 'true',
                    'authorized main/manual image publication was disabled')
        export_index, export = one_step(job, f'candidate-artifacts.py export --group {group}')
        require(active(export, fork) and not active(export, trusted), 'fork export is absent or runs for trusted publication')
        require(not active(export, fork, success=False), 'failed native gates can export a candidate')
        require(export.get('continue-on-error', False) is False, 'candidate export failure is ignored')
        require('${{ matrix.arch }}' in export['run'], 'export loses its native architecture binding')
        # An identity-only --tag call in a smoke step is not the build gate.
        pattern = re.compile(rf'container-and-chart/{helper}/build\.sh(?: --if-changed)?[ \t]*(?:\n|$)')
        builds = [(index, step) for index, step in enumerate(job['steps']) if pattern.search(step.get('run', ''))]
        require(len(builds) == 1 and builds[0][0] < export_index, 'export has no preceding native build')
        must_gate(builds[0][1], fork)
        gates = []
        if group == 'takserver':
            gates.extend(('scan-local-images.sh', '${SYFT_IMAGE}'))
        if group == 'postgis':
            gates.append('container-and-chart/postgis/smoke.sh')
        for fragment in gates:
            index, step = one_step(job, fragment)
            require(index < export_index, 'candidate export precedes its native/security gate')
            must_gate(step, fork)
        uploads = [(index, step) for index, step in enumerate(job['steps'])
                   if step.get('uses', '').startswith('actions/upload-artifact@')
                   and step.get('with', {}).get('name', '').startswith('candidate-' + group + '-')]
        require(len(uploads) == 1 and uploads[0][0] > export_index, 'verified candidate has no subsequent upload')
        upload = uploads[0][1]
        require(active(upload, fork) and not active(upload, fork, success=False), 'candidate upload bypasses export success')
        require(upload.get('with', {}).get('if-no-files-found') == 'error', 'missing candidate upload can succeed')


def require_test_consumption(documents):
    document = documents['test.yaml']
    for group in ('cloudtak', 'postgis'):
        native = document['jobs'][group + '-image']
        producer = documents[group + '-image.yaml']['jobs']['build']
        fork = environment(document, native, context())
        trusted = environment(document, native, context(fork=False))
        require(active(native, fork), 'fork native validation job is skipped')
        require(native.get('continue-on-error', False) is False, 'fork native validation failure is ignored')
        require(group + '-index' not in native.get('needs', []), 'native consumer depends on its own index gate')
        load_index, load = one_step(native, f'candidate-consumer.py load --group {group}')
        must_gate(load, fork)
        must_read_candidates(document, native, load)
        require(not active(load, trusted) and '${{ matrix.arch }}' in load['run'], 'fork load loses its event/platform binding')
        _, build = one_step(native, f'container-and-chart/{group}/build.sh --if-changed')
        require(not active(build, fork) and active(build, trusted), 'fork consumer duplicates a publishing producer')
        if group == 'postgis':
            smoke_index, smoke = one_step(native, 'container-and-chart/postgis/smoke.sh')
            require(smoke_index > load_index, 'PostGIS smoke runs before the candidate is loaded')
            must_gate(smoke, fork)
        for item in (native, producer):
            require(item['concurrency'].get('queue') == 'max' and item['concurrency'].get('cancel-in-progress') is False,
                    'image admission must keep its FIFO non-cancelling queue')
        consumer_group, producer_group = native['concurrency']['group'], producer['concurrency']['group']
        require(render(consumer_group, trusted) == render(producer_group, trusted), 'trusted producers no longer share admission')
        require(render(consumer_group, fork) != render(producer_group, fork), 'fork consumer holds its own producer mutex')
        require(render(consumer_group, fork) != render(consumer_group, context(run='200')), 'fork consumers from different runs block each other')
        for filename, job_name in (('test.yaml', group + '-index'), (group + '-image.yaml', 'index')):
            index_document = documents[filename]
            index = index_document['jobs'][job_name]
            values = environment(index_document, index, context())
            require(active(index, values), 'fork index validation job is skipped')
            require(index.get('continue-on-error', False) is False, 'fork index validation failure is ignored')
            _, ready = one_step(index, f'candidate-consumer.py ready --group {group}')
            must_gate(ready, values)
            must_read_candidates(index_document, index, ready)
            require('--architecture' not in ready['run'], 'index validates only one native architecture')
            _, merge = one_step(index, f'container-and-chart/{group}/build.sh --merge')
            require(not active(merge, values) and active(merge, environment(index_document, index, context(fork=False))),
                    'fork index publishes or trusted index no longer publishes')
            dependencies = index.get('needs', [])
            require((group + '-image' if filename == 'test.yaml' else 'build') in dependencies,
                    'index validation does not depend on its native jobs')
    for name in ('validate-install', 'validate-upgrade', 'validate-upgrade-pg15'):
        require({'images', 'cloudtak-index', 'postgis-index'} <= set(document['jobs'][name]['needs']),
                'application validation bypasses a required image readiness gate')
        require(active(document['jobs'][name], context()), 'selected fork application validation is skipped')


def fork_jobs(documents):
    for filename, document in documents.items():
        events = document.get('on', document.get(True, {}))
        if 'pull_request' not in events:
            continue
        for name, job in document.get('jobs', {}).items():
            values = environment(document, job, context())
            if active(job, values):
                yield filename, name, document, job, values


def require_consumers(documents):
    jobs = list(fork_jobs(documents))
    consumers = [entry for entry in jobs if any(PIN_OR_CREATE.search(step.get('run', ''))
                                              for step in entry[3].get('steps', []))]
    require({'package.yaml', 'test.yaml', 'full-core.yaml', 'benchmark.yaml', 'scan.yaml', 'atak-e2e.yaml'}
            <= {filename for filename, *_ in consumers}, 'a fork packaging consumer disappeared from validation')
    selected = {(filename, name) for filename, name, *_ in jobs}
    for filename, names in {'package.yaml': ('package',),
                            'test.yaml': ('validate-install', 'validate-upgrade', 'validate-upgrade-pg15'),
                            'full-core.yaml': ('validate-full-core',), 'benchmark.yaml': ('benchmark',),
                            'scan.yaml': ('images', 'scan-and-compare'), 'atak-e2e.yaml': ('e2e',)}.items():
        require(all((filename, name) in selected for name in names), 'a selected fork consumer job is skipped')
    for filename, name, document, job, _values in jobs:
        for step in job.get('steps', []):
            if TRANSFER.search(step.get('run', '')):
                permissions = job.get('permissions', document.get('permissions', {}))
                require(permissions.get('actions') == 'read', f'{filename}/{name} cannot resolve canonical candidate runs')
                effective = environment(document, job, context(), step)
                require(effective.get('env.GH_TOKEN') == 'available-token', f'{filename}/{name} loses the candidate API token')
    for filename, name, document, job, values in consumers:
        cleanup_index, cleanup = one_step(job, 'candidate-consumer.py cleanup')
        require(active(cleanup, values, success=False, cancelled=True), f'{filename}/{name} leaks its registry after cancellation')
        require('--state' in cleanup['run'] and 'uds-candidate/state.json' in cleanup['run']
                and 'CANDIDATE_IMAGE_STATE' not in cleanup['run'], 'cleanup cannot find state after interrupted preparation')
        starts = [index for index, step in enumerate(job['steps']) if PIN_OR_CREATE.search(step.get('run', ''))]
        require(cleanup_index > max(starts), 'candidate registry is cleaned before its consumer finishes')


class CandidateWorkflowTests(unittest.TestCase):
    def test_canonical_planner_avoids_self_wait_and_records_cache_reuse(self):
        require_container_plan(workflows())

    def test_recursive_planner_and_missing_cache_admission_are_rejected(self):
        for mutation in ('recursive-plan', 'skip-cache', 'missing-cache-upload'):
            documents = workflows()
            plan = documents['container.yaml']['jobs']['plan']
            if mutation == 'recursive-plan':
                one_step(plan, 'wait-for-tak-images.sh')[1]['env'].pop('CANDIDATE_PRODUCER')
            elif mutation == 'skip-cache':
                one_step(plan, 'candidate-consumer.py cache-decision')[1]['if'] = 'false'
            else:
                plan['steps'] = [step for step in plan['steps']
                                 if not step.get('with', {}).get('name', '').startswith('candidate-cache-takserver-')]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                require_container_plan(documents)

    def test_native_exports_follow_required_build_security_and_smoke_gates(self):
        require_exports(workflows())

    def test_broken_native_export_gates_are_rejected(self):
        for mutation in ('skip-export', 'ignore-smoke', 'export-before-scan', 'export-after-failure', 'publish-from-fork', 'ignore-upload'):
            documents = workflows()
            if mutation in ('skip-export', 'export-after-failure'):
                _, step = one_step(documents['postgis-image.yaml']['jobs']['build'], 'candidate-artifacts.py export')
                step['if'] = 'false' if mutation == 'skip-export' else f"always() && ({step['if']})"
            elif mutation == 'ignore-smoke':
                one_step(documents['postgis-image.yaml']['jobs']['build'], 'postgis/smoke.sh')[1]['continue-on-error'] = True
            elif mutation == 'export-before-scan':
                job = documents['container.yaml']['jobs']['build']
                index, step = one_step(job, 'candidate-artifacts.py export')
                scan_index, _ = one_step(job, 'scan-local-images.sh')
                job['steps'].insert(scan_index, job['steps'].pop(index))
            elif mutation == 'publish-from-fork':
                documents['cloudtak-image.yaml']['jobs']['build']['env']['PUSH_IMAGES'] = 'true'
            else:
                upload = next(step for step in documents['cloudtak-image.yaml']['jobs']['build']['steps']
                              if step.get('with', {}).get('name', '').startswith('candidate-cloudtak-'))
                upload['with']['if-no-files-found'] = 'ignore'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                require_exports(documents)

    def test_fork_test_jobs_validate_both_architectures_without_producer_deadlocks(self):
        require_test_consumption(workflows())

    def test_skipped_index_smoke_and_shared_consumer_mutex_are_rejected(self):
        for mutation in ('shared-mutex', 'skip-index', 'missing-ready', 'ignore-smoke', 'ignore-index', 'bypass-install', 'circular-needs'):
            documents = workflows()
            jobs = documents['test.yaml']['jobs']
            if mutation == 'shared-mutex':
                jobs['cloudtak-image']['concurrency'] = copy.deepcopy(documents['cloudtak-image.yaml']['jobs']['build']['concurrency'])
            elif mutation == 'skip-index':
                jobs['cloudtak-index']['if'] = 'false'
            elif mutation == 'missing-ready':
                job = jobs['postgis-index']
                job['steps'].pop(one_step(job, 'candidate-consumer.py ready')[0])
            elif mutation == 'ignore-smoke':
                one_step(jobs['postgis-image'], 'postgis/smoke.sh')[1]['continue-on-error'] = True
            elif mutation == 'ignore-index':
                jobs['postgis-index']['continue-on-error'] = True
            elif mutation == 'bypass-install':
                jobs['validate-install']['needs'].remove('cloudtak-index')
            else:
                jobs['cloudtak-image']['needs'] = ['image-keys', 'cloudtak-index']
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                require_test_consumption(documents)

    def test_every_triggered_package_consumer_keeps_token_permissions_and_cancellation_cleanup(self):
        require_consumers(workflows())

    def test_missing_consumer_token_permission_and_cleanup_are_rejected(self):
        for mutation in ('missing-token', 'missing-permission', 'success-only-cleanup', 'late-state-only-cleanup', 'new-consumer'):
            documents = workflows()
            job = documents['package.yaml']['jobs']['package']
            if mutation == 'missing-token':
                for item in (documents['package.yaml'], job, *job['steps']):
                    item.get('env', {}).pop('GH_TOKEN', None)
            elif mutation == 'missing-permission':
                job['permissions'].pop('actions', None)
            elif mutation in ('success-only-cleanup', 'late-state-only-cleanup'):
                _, cleanup = one_step(job, 'candidate-consumer.py cleanup')
                if mutation == 'success-only-cleanup':
                    cleanup['if'] = 'success()'
                else:
                    cleanup['run'] = 'python3 .github/scripts/candidate-consumer.py cleanup --state "${CANDIDATE_IMAGE_STATE}"'
            else:
                documents['new-consumer.yaml'] = {'on': {'pull_request': {}}, 'jobs': {'package': {
                    'steps': [{'run': './.github/scripts/pin-image-tags.sh'}]}}}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                require_consumers(documents)


if __name__ == '__main__':
    unittest.main()
