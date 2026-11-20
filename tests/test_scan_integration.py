# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Execute Scan's handoff without substituting scanner/source acceptance."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from tests.test_workflow_permissions import require_fork_scan_reporting


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / '.github/scripts'
SPEC = importlib.util.spec_from_file_location('candidate_scan_mappings', SCRIPTS / 'candidate-scan-mappings.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def workflow():
    return yaml.safe_load((ROOT / '.github/workflows/scan.yaml').read_text())


def state():
    registry = '127.0.0.1:39123'
    groups = {}
    for group, names in M.IMAGES.GROUPS.items():
        images = []
        for name in names:
            ref = M.IMAGES.REPOSITORY + '/' + name + ':fixture-committed-inputs'
            images.append({'ref': ref, 'mirror': ref.replace('ghcr.io/', registry + '/', 1),
                           'mirror_manifest_digest': 'sha256:' + hashlib.sha256(name.encode()).hexdigest()})
        groups[group] = {'images': images}
    return {'schema': 'uds-takserver/candidate-consumer/v1', 'architecture': 'amd64',
            'registry': registry, 'groups': groups}


def write_state(path, value):
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MappingTests(unittest.TestCase):
    def test_cli_generation_error_emits_no_partial_mappings_or_source_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.json'
            value = state()
            value['groups']['takserver']['images'][-1]['mirror_manifest_digest'] = 'private-fixture-payload'
            result = subprocess.run([sys.executable, str(SCRIPTS / 'candidate-scan-mappings.py'),
                '--state', str(path), '--state-sha256', write_state(path, value)],
                capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertNotIn('private-fixture-payload', result.stderr)
            self.assertNotIn('Traceback', result.stderr)

    def test_all_eight_full_canonical_refs_map_to_exact_loopback_manifest_digests(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.json'
            value = state()
            mappings = M.mappings(path, write_state(path, value))
            self.assertEqual(len(mappings), 8)
            self.assertEqual(mappings, sorted(mappings))
            for group in value['groups'].values():
                for image in group['images']:
                    self.assertIn(image['ref'] + '=' + image['mirror'].rsplit(':', 1)[0]
                                  + '@' + image['mirror_manifest_digest'], mappings)

    def test_changed_bytes_duplicate_json_and_symlink_cannot_supply_mappings(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.json'
            digest = write_state(path, state())
            path.write_text(path.read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'changed'):
                M.mappings(path, digest)
            path.write_text('{"schema":1,"schema":2}')
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                M.mappings(path, hashlib.sha256(path.read_bytes()).hexdigest())
            link = path.parent / 'linked.json'
            link.symlink_to(path)
            with self.assertRaisesRegex(ValueError, 'regular'):
                M.mappings(link, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_partial_wrong_arch_or_registry_and_bare_name_mappings_fail_closed(self):
        original = state()
        bad = []
        value = copy.deepcopy(original); del value['groups']['cloudtak']; bad.append(value)
        value = copy.deepcopy(original); value['groups']['takserver']['images'].pop(); bad.append(value)
        value = copy.deepcopy(original); value['architecture'] = 'arm64'; bad.append(value)
        value = copy.deepcopy(original); value['registry'] = 'registry.example:39123'; bad.append(value)
        value = copy.deepcopy(original); value['groups']['postgis']['images'][0]['ref'] = 'postgis:fixture'; bad.append(value)
        value = copy.deepcopy(original); value['groups']['postgis']['images'][0]['mirror_manifest_digest'] = 'sha256:short'; bad.append(value)
        value = copy.deepcopy(original); value['groups']['postgis']['images'][0]['mirror'] += '-different'; bad.append(value)
        value = copy.deepcopy(original); value['groups']['takserver']['images'][1] = value['groups']['takserver']['images'][0]; bad.append(value)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.json'
            for value in bad:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    M.mappings(path, write_state(path, value))


class WorkflowInvocationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scripts = self.root / '.github/scripts'
        self.scripts.mkdir(parents=True)
        self.temp = self.root / 'runner temporary files'
        self.temp.mkdir()
        self.state_path = self.temp / 'validated state.json'
        write_state(self.state_path, state())
        (self.root / 'zarf.yaml').write_text('canonical pinned YAML\n')
        for name in ('candidate-scan-mappings.py', 'candidate-images.py'):
            shutil.copyfile(SCRIPTS / name, self.scripts / name)
        # These boundary fixtures exercise shell ordering and exact arguments.
        # They do not authenticate producers or perform a vulnerability scan.
        (self.scripts / 'candidate-consumer.py').write_text('''import json, os, pathlib, sys
args = sys.argv[1:]
assert args[0] == 'scan-manifest'
if os.environ.get('VALIDATOR_FAIL'): sys.exit(9)
path = pathlib.Path(args[args.index('--state') + 1])
value = json.loads(path.read_text())
value['validation_witness'] = 'after-validator'
path.write_text(json.dumps(value))
if os.environ.get('BREAK_MAPPINGS'): value['groups'].pop('cloudtak'); path.write_text(json.dumps(value))
pathlib.Path(args[args.index('--output') + 1]).write_text('validated temporary candidate YAML\\n')
''')
        (self.scripts / 'strict-scan-compare.py').write_text('''import hashlib, json, os, pathlib, sys
args = sys.argv[1:]
pathlib.Path('adapter-arguments.json').write_text(json.dumps(args))
if os.environ.get('ADAPTER_FAIL'): sys.exit(7)
if '--candidate-state' in args:
    raw = pathlib.Path(args[args.index('--candidate-state') + 1]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == args[args.index('--candidate-state-sha256') + 1]
    assert json.loads(raw)['validation_witness'] == 'after-validator'
output = pathlib.Path(args[args.index('--output-directory') + 1]); output.mkdir()
(output / 'receipt.json').write_text('{"boundary_fixture": true}')
pathlib.Path(args[args.index('--markdown') + 1]).write_text('### Scan comparison\\n' + 'x' * int(os.environ.get('REPORT_SIZE', '0')))
''')
        self.env = {**os.environ, 'RUNNER_TEMP': str(self.temp), 'GITHUB_OUTPUT': str(self.temp / 'github-output'),
                    'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']}
        self.env.pop('CANDIDATE_IMAGE_STATE', None)
        run = next(step['run'] for step in workflow()['jobs']['scan-and-compare']['steps']
                   if step.get('id') == 'compare-scans')
        # The unchanged pinned Grype installer precedes this integration seam.
        self.run_source = 'set -euo pipefail\n' + run[run.index('scan_options='):]

    def execute(self, fork=False, **env):
        values = {**self.env, **env}
        if fork:
            values['CANDIDATE_IMAGE_STATE'] = str(self.state_path)
        return subprocess.run(['bash', '-c', self.run_source], cwd=self.root, env=values,
                              capture_output=True, text=True, timeout=15)

    def arguments(self):
        return json.loads((self.root / 'adapter-arguments.json').read_text())

    def test_nonfork_uses_canonical_pinned_manifest_without_candidate_options(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.arguments(), ['--zarf-yaml-path', 'zarf.yaml', '--source-root', '.', '--flavor',
            'upstream', '--architecture', 'amd64', '--output-directory', str(self.temp / 'strict-scan-comparison'),
            '--markdown', 'comment.md'])
        self.assertEqual((self.root / 'zarf.yaml').read_text(), 'canonical pinned YAML\n')
        self.assertFalse((self.temp / 'candidate-scan.yaml').exists())
        self.assertIn('artifact=false', Path(self.env['GITHUB_OUTPUT']).read_text())

    def test_fork_passes_validated_yaml_all_eight_full_refs_and_immediate_state_hash(self):
        result = self.execute(fork=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.arguments()
        self.assertEqual(args[args.index('--candidate-state') + 1], str(self.state_path))
        self.assertEqual(args[args.index('--candidate-state-sha256') + 1],
                         hashlib.sha256(self.state_path.read_bytes()).hexdigest())
        self.assertEqual(args[args.index('--zarf-yaml-path') + 1], str(self.temp / 'candidate-scan.yaml'))
        actual = [args[i + 1] for i, token in enumerate(args) if token == '--image-name-override']
        self.assertEqual(actual, M.mappings(self.state_path, hashlib.sha256(self.state_path.read_bytes()).hexdigest()))
        self.assertEqual(len(actual), 8)
        self.assertEqual((self.root / 'zarf.yaml').read_text(), 'canonical pinned YAML\n')

    def test_validator_or_mapping_generation_failure_never_reaches_adapter(self):
        for values in ({'VALIDATOR_FAIL': 'true'}, {'BREAK_MAPPINGS': 'true'}):
            with self.subTest(values=values):
                result = self.execute(fork=True, **values)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / 'adapter-arguments.json').exists())
                self.assertFalse((self.root / 'comment.md').exists())
                (self.temp / 'candidate-scan.yaml').unlink(missing_ok=True)
                write_state(self.state_path, state())

    def test_adapter_failure_has_no_old_comparison_fallback_or_success_output(self):
        result = self.execute(ADAPTER_FAIL='true')
        self.assertEqual(result.returncode, 7)
        self.assertFalse((self.root / 'comment.md').exists())
        self.assertFalse(Path(self.env['GITHUB_OUTPUT']).exists())
        self.assertNotIn('uds-pk scan compare', self.run_source)

    def test_large_success_retains_complete_markdown_and_receipt(self):
        result = self.execute(REPORT_SIZE='66000')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater((self.root / 'comment.md').stat().st_size, 65536)
        self.assertTrue((self.temp / 'strict-scan-comparison/receipt.json').is_file())
        self.assertIn('artifact=true', Path(self.env['GITHUB_OUTPUT']).read_text())

    def test_existing_output_is_rejected_instead_of_reusing_an_old_comparison(self):
        (self.temp / 'strict-scan-comparison').mkdir()
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'comment.md').exists())


class WorkflowPolicyTests(unittest.TestCase):
    def test_report_retention_security_and_unique_unit_gates(self):
        document = workflow()
        require_fork_scan_reporting(document)
        job = document['jobs']['scan-and-compare']
        self.assertEqual(job['permissions'], {'contents': 'read', 'packages': 'read',
                                             'pull-requests': 'write', 'actions': 'read'})
        self.assertEqual(job['env']['GRYPE_VERSION'], 'v0.120.1')
        self.assertEqual(job['env']['GRYPE_SHA256'], '0a9ee97ef5ae2ee953b0a80098105052e846cdbe319a57d808b519c33cd1343d')
        python = next(step for step in job['steps'] if step['name'] == 'Set up Python')
        parser = next(step for step in job['steps'] if step['name'] == 'Install strict comparison parser')
        self.assertEqual(python['uses'], 'actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0')
        self.assertEqual(python['with']['python-version'], '3.12')
        self.assertEqual(parser['run'], 'python3 -m pip install --disable-pip-version-check PyYAML==6.0.3')
        self.assertLess(job['steps'].index(python), job['steps'].index(parser))
        self.assertLess(job['steps'].index(parser), next(i for i, step in enumerate(job['steps']) if step.get('id') == 'compare-scans'))
        cleanup = job['steps'][-1]
        self.assertIn('always()', cleanup['if'])
        self.assertEqual(cleanup['run'], 'python3 .github/scripts/candidate-consumer.py cleanup --state "${RUNNER_TEMP}/uds-candidate/state.json"')
        compare = next(step['run'] for step in job['steps'] if step.get('id') == 'compare-scans')
        self.assertNotIn('uds-pk scan compare', compare)
        self.assertNotIn('<(', compare)
        self.assertIn('set -euo pipefail', compare)
        units = (ROOT / '.github/workflows/unit-tests.yaml').read_text()
        self.assertIn('tests.test_strict_scan_compare tests.test_scan_integration', units)
        self.assertEqual(units.count('tests.test_copa_evidence'), 1)
        for path in ('.github/scripts/strict-scan-compare.py', '.github/scripts/candidate-scan-mappings.py'):
            self.assertIn(path, document[True]['pull_request']['paths'])

    def test_missing_receipt_retention_and_comparison_bypass_are_rejected(self):
        for target, key, value in (('Upload exact comparison receipt', 'path', 'comment.md'),
                                   ('compare-scans', 'continue-on-error', True)):
            changed = workflow()
            step = next(step for step in changed['jobs']['scan-and-compare']['steps'] if step.get('id') == target or step.get('name') == target)
            (step['with'] if key == 'path' else step)[key] = value
            with self.subTest(target=target), self.assertRaises(ValueError):
                require_fork_scan_reporting(changed)


if __name__ == '__main__':
    unittest.main()
