# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Execute migration and kill/restart boundaries without a PostgreSQL server."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/container-and-chart/postgis/docker-entrypoint-upgrade.sh"
FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, shutil, signal, sys
name = pathlib.Path(sys.argv[0]).name
data = pathlib.Path(os.environ['PGDATA'])
old = pathlib.Path(str(data) + '.pg15')
state = pathlib.Path(str(data) + '.pg15-to-pg18.state')
args = sys.argv[1:]
def event(point):
    with open(os.environ['UPGRADE_EVENTS'], 'a') as out:
        out.write(json.dumps({'point': point, 'stage': state.read_text().strip() if state.exists() else None}) + '\n')
    if os.environ.get('CRASH_POINT') == point:
        os.kill(int(os.environ['UPGRADE_PID']), signal.SIGKILL)
        sys.exit(0)
if name == 'id':
    print('1001')
elif name == 'getent':
    pass
elif name == 'sync':
    event('sync-' + (state.read_text().strip() if state.exists() else 'clean'))
elif name == 'pg_ctl':
    version = pathlib.Path(sys.argv[0]).parent.name
    event(version + '-' + args[-1])
elif name == 'pg_controldata':
    print('Data page checksum version: 0')
elif name == 'psql':
    sql = args[args.index('-c') + 1]
    if 'template0' in sql:
        print('C.UTF-8|C.UTF-8|UTF8|c')
    elif 'datallowconn' in sql:
        if os.environ.get('FAIL_POINT') == 'databases':
            sys.exit(1)
        print('cot')
    elif "extname = 'postgis'" in sql:
        print('1')
    else:
        event('postgis-upgrade')
elif name == 'initdb':
    data.mkdir(exist_ok=True)
    (data / 'PG_VERSION').write_text('18\n')
    event('initdb-done')
    if os.environ.get('FAIL_POINT') == 'initdb':
        sys.exit(1)
elif name == 'pg_upgrade':
    shutil.copyfile(old / 'retained-row', data / 'retained-row')
    (old / 'control-disabled').write_text('linked')
    event('pg-upgrade-linking')
    if os.environ.get('FAIL_POINT') == 'pg_upgrade':
        sys.exit(1)
elif name == 'docker-entrypoint.sh':
    event('ordinary-startup')
    print((data / 'retained-row').read_text() if (data / 'retained-row').exists() else 'empty')
else:
    raise RuntimeError((name, args))
'''


class UpgradeRestartTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / 'data'
        self.old = self.root / 'data.pg15'
        self.state = self.root / 'data.pg15-to-pg18.state'
        self.events = self.root / 'events'
        self.data.mkdir()
        (self.data / 'PG_VERSION').write_text('15\n')
        (self.data / 'retained-row').write_text('keep-me|original-timestamp')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake = self.bin / 'fake'
        fake.write_text(FAKE)
        fake.chmod(0o755)
        for command in ('id', 'getent', 'sync', 'docker-entrypoint.sh'):
            (self.bin / command).symlink_to(fake)
        text = SCRIPT.read_text()
        for version in ('15', '18'):
            directory = self.root / version
            directory.mkdir()
            for command in ('pg_ctl', 'pg_controldata', 'psql', 'initdb', 'pg_upgrade'):
                (directory / command).symlink_to(fake)
            variable = 'old_bindir' if version == '15' else 'new_bindir'
            original = f'{variable}="/usr/lib/postgresql/${{{"old_major" if version == "15" else "new_major"}}}/bin"'
            self.assertEqual(text.count(original), 1)
            # Substitute only binary locations; execute the real shell logic.
            text = text.replace(original, f'{variable}="{directory}"')
        self.script = self.root / 'entrypoint.sh'
        self.script.write_text(text)

    def run_script(self, crash='', fail='', command='postgres'):
        return subprocess.run(
            ['bash', '-c', 'export UPGRADE_PID=$$; exec bash "$@"', 'bash', str(self.script), command],
            env={**os.environ, 'PGDATA': str(self.data), 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                 'UPGRADE_EVENTS': str(self.events), 'CRASH_POINT': crash, 'FAIL_POINT': fail},
            text=True, capture_output=True, timeout=15,
        )

    def points(self):
        return [json.loads(line) for line in self.events.read_text().splitlines()] if self.events.exists() else []

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for directory in (self.data, self.old) if directory.exists()
                for path in directory.rglob('*') if path.is_file()}

    def assert_refuses_unchanged(self):
        before = self.snapshot()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('refusing normal startup', result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn('ordinary-startup', [event['point'] for event in self.points()])

    def test_normal_upgrade_commits_only_after_postgis_and_shutdown(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'keep-me|original-timestamp')
        stages = {event['point']: event['stage'] for event in self.points()}
        self.assertEqual(stages['initdb-done'], 'preparing')
        self.assertEqual(stages['pg-upgrade-linking'], 'linking')
        self.assertEqual(stages['postgis-upgrade'], 'postgis')
        self.assertEqual(stages['18-stop'], 'postgis')
        self.assertEqual(stages['sync-complete'], 'complete')
        self.assertFalse(self.old.exists())
        self.assertFalse(self.state.exists())

    def test_kill_after_initdb_can_retry_without_losing_old_rows(self):
        result = self.run_script(crash='initdb-done')
        self.assertEqual(result.returncode, -9)
        self.assertEqual((self.data / 'PG_VERSION').read_text().strip(), '18')
        self.assertEqual(self.state.read_text().strip(), 'preparing')
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'keep-me|original-timestamp')

    def test_linking_and_postgis_interruptions_preserve_both_directories(self):
        for point in ('pg-upgrade-linking', 'postgis-upgrade', '18-stop'):
            with self.subTest(point=point):
                # Each interruption has an independent original cluster.
                case = UpgradeRestartTests()
                case.setUp()
                try:
                    result = case.run_script(crash=point)
                    self.assertEqual(result.returncode, -9, result.stderr)
                    case.assert_refuses_unchanged()
                finally:
                    case.doCleanups()

    def test_completed_migration_resumes_cleanup(self):
        result = self.run_script(crash='sync-complete')
        self.assertEqual(result.returncode, -9)
        self.assertTrue(self.old.exists())
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'keep-me|original-timestamp')
        self.assertFalse(self.old.exists())
        self.assertFalse(self.state.exists())

    def test_legacy_unjournaled_pg18_plus_pg15_is_never_started(self):
        self.data.rename(self.old)
        self.data.mkdir()
        (self.data / 'PG_VERSION').write_text('18\n')
        self.assert_refuses_unchanged()

    def test_failed_initdb_can_retry_but_failed_linking_needs_recovery(self):
        self.assertNotEqual(self.run_script(fail='initdb').returncode, 0)
        self.assertEqual(self.state.read_text().strip(), 'preparing')
        self.assertNotEqual(self.run_script(fail='pg_upgrade').returncode, 0)
        self.assertEqual(self.state.read_text().strip(), 'linking')
        self.assert_refuses_unchanged()

    def test_failed_database_enumeration_cannot_commit_the_upgrade(self):
        self.assertNotEqual(self.run_script(fail='databases').returncode, 0)
        self.assertEqual(self.state.read_text().strip(), 'postgis')
        self.assert_refuses_unchanged()

    def test_unrecognized_or_inconsistent_journals_are_preserved(self):
        for stage in ('unknown', 'complete'):
            with self.subTest(stage=stage):
                self.state.write_text(stage + '\n')
                self.assert_refuses_unchanged()

    def test_existing_pg18_without_upgrade_state_starts_normally(self):
        (self.data / 'PG_VERSION').write_text('18\n')
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([event['point'] for event in self.points()], ['ordinary-startup'])


class SmokePreparationTests(unittest.TestCase):
    def test_smoke_retry_initialization_resolves_numeric_uid(self):
        """Execute smoke's actual retry shell payload; Docker/server responses are fixtures."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / 'bin'
            binary.mkdir()
            entrypoint = root / 'entrypoint.sh'
            entrypoint.write_text('docker_setup_env() { : "${POSTGRES_PASSWORD:?}"; }\n'
                'docker_init_database_dir() { UID_LOOKUP_READY=true "$SMOKE_INITDB" -D "$PGDATA" --username="$POSTGRES_USER" "$@"; }\n')
            initdb = root / 'initdb'
            initdb.write_text('#!/bin/sh\n'
                'if [ "${UID_LOOKUP_READY:-}" != true ]; then echo "initdb: missing numeric UID lookup" >&2; exit 1; fi\n'
                'mkdir -p "$PGDATA"\nprintf "18\\n" > "$PGDATA/PG_VERSION"\n')
            initdb.chmod(0o755)
            docker = binary / 'docker'
            docker.write_text(r"""#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
if args[0] == 'run' and '--entrypoint' in args:
    env = dict(os.environ)
    for i, arg in enumerate(args[:-1]):
        if arg == '-e':
            key, value = args[i + 1].split('=', 1)
            env[key] = value
    data = pathlib.Path(env['SMOKE_ROOT']) / env['POSTGRES_USER'] / 'data'
    data.mkdir(parents=True)
    (data / 'PG_VERSION').write_text('15\n')
    env['PGDATA'] = str(data)
    command = args[-1].replace('/usr/local/bin/docker-entrypoint.sh', env['SMOKE_ENTRYPOINT'])
    command = command.replace('/usr/lib/postgresql/18/bin/initdb', env['SMOKE_INITDB'])
    result = subprocess.run(['bash', '-ec', command], env=env)
    if result.returncode:
        sys.exit(result.returncode)
    assert (data / 'PG_VERSION').read_text().strip() == '18'
    assert pathlib.Path(str(data) + '.pg15/PG_VERSION').read_text().strip() == '15'
    assert pathlib.Path(str(data) + '.pg15-to-pg18.state').read_text().strip() == 'preparing'
    with open(env['SMOKE_CALLS'], 'a') as out:
        out.write(json.dumps({'user': env['POSTGRES_USER'], 'prepared': True}) + '\n')
elif args[0] == 'exec' and 'psql' in args:
    query = args[args.index('-c') + 1]
    if 'SELECT marker' in query:
        print('keep-me|original-timestamp')
    elif 'server_version_num' in query:
        print('180006')
    elif 'extversion' in query:
        print('3.6.4')
elif args[:2] == ['run', '-d']:
    print('fixture-container')
""")
            docker.chmod(0o755)
            calls = root / 'calls.jsonl'
            result = subprocess.run(['bash', str(ROOT / '.github/container-and-chart/postgis/smoke.sh'),
                                     'fixture-image'], capture_output=True, text=True, timeout=15,
                                    env={**os.environ, 'PATH': str(binary) + os.pathsep + os.defpath,
                                         'SMOKE_ROOT': str(root), 'SMOKE_ENTRYPOINT': str(entrypoint),
                                         'SMOKE_INITDB': str(initdb), 'SMOKE_CALLS': str(calls)})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PASS bitnami-retry:', result.stdout)
            self.assertIn('PASS cloudtak-retry:', result.stdout)
            self.assertEqual([json.loads(line) for line in calls.read_text().splitlines()],
                             [{'user': 'postgres', 'prepared': True},
                              {'user': 'cloudtak', 'prepared': True}])


if __name__ == '__main__':
    unittest.main()
