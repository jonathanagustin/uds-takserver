# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline tests for run-in-toolchain.sh and fetch.sh.

Nothing here starts a container, reaches the network or writes outside a
temporary directory. The scripts run with a private PATH whose `docker`,
`python3` (and, where named, `mkdir`, `tee`, `keytool`) are recorders that store
their argv and environment; a per-test hook file gives a recorder behaviour.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import pty
import shutil
import stat
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RUN = HERE / 'run-in-toolchain.sh'
FETCH = HERE / 'fetch.sh'
ARCH = HERE / 'arch.sh'
ARCH_TREE = HERE / 'arch_tree.py'
TIMEOUT = float(os.environ.get('TAKX_TEST_TIMEOUT', '60'))
TOOLCHAIN = 'takx-desktop-proto:toolchain'
RUNTIME = 'takx-desktop-proto:runtime'
DEFAULT_ROOT = '/var/lib/takx-proto'
DEFAULT_CA = '/root/.ccr/ca-bundle.crt'
CA_ENV = ['-e', 'SSL_CERT_FILE=/ca.crt', '-e', 'CURL_CA_BUNDLE=/ca.crt',
          '-e', 'GIT_SSL_CAINFO=/ca.crt', '-e', 'PIP_CERT=/ca.crt',
          '-e', 'REQUESTS_CA_BUNDLE=/ca.crt']
PROXY_ENV = {
    'HTTP_PROXY': 'http://proxy.invalid:1', 'HTTPS_PROXY': 'http://proxy.invalid:2',
    'http_proxy': 'http://proxy.invalid:3', 'https_proxy': 'http://proxy.invalid:4',
    'ALL_PROXY': 'socks5://proxy.invalid:5', 'all_proxy': 'socks5://proxy.invalid:6',
    'NO_PROXY': 'localhost', 'no_proxy': 'localhost', 'FTP_PROXY': 'http://proxy.invalid:7',
    'JAVA_TOOL_OPTIONS': '-Dhttps.proxyHost=proxy.invalid', 'GLOBAL_AGENT_HTTP_PROXY': 'x',
}
CERT_1 = '-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n'
CERT_2 = '-----BEGIN CERTIFICATE-----\nBBBB\nCCCC\n-----END CERTIFICATE-----\n'
BUNDLE = '# leading text that is not a certificate\n' + CERT_1 + CERT_2

RECORDER = r'''#!/bin/bash
name="${0##*/}"
shopt -s nullglob
have=("$FAKE_REC/$name".*.argv)
n=${#have[@]}
{ (($#)) && printf '%s\0' "$@"; } > "$FAKE_REC/$name.$n.argv"
env -0 > "$FAKE_REC/$name.$n.env"
echo x >> "$FAKE_REC/$name.count"
hook="$FAKE_REC/$name.hook"
if [[ -f "$hook" ]]; then . "$hook"; fi
exit 0
'''
# Hook: behave like `docker run IMAGE cmd...` by running cmd on the host.
EXEC_COMMAND = r'''
while (($#)) && [[ "$1" != takx-desktop-proto:* ]]; do shift; done
shift
exec "$@"
'''
# Hook: `docker run ... truststore OUT` publishes a store, as the image would.
MAKE_STORE = r'''
if [[ "${@: -2:1}" == truststore ]]; then echo 153 >&2; echo STORE > "${@: -1}"; fi
'''
KEYTOOL = r'''
mode="$1"; dest=""; alias=""; file=""; ks=""
while (($#)); do
  case "$1" in
    -destkeystore) dest="$2";; -alias) alias="$2";; -file) file="$2";; -keystore) ks="$2";;
  esac
  shift
done
echo "keytool noise on stdout"; echo "keytool noise on stderr" >&2
case "$mode" in
  -importkeystore) [[ -z "${FAKE_KEYSTORE_FAIL-}" ]] || exit 1; echo base > "$dest";;
  -importcert)
    [[ -s "$ks" ]] || exit 1
    cat "$file" > "$FAKE_REC/pem.$alias"
    [[ "$alias" != "${FAKE_BAD_ALIAS-}" ]] || exit 1
    echo "$alias" >> "$ks";;
  -list)
    [[ -z "${FAKE_LIST_EMPTY-}" ]] || exit 0
    echo "Keystore type: PKCS12"
    while read -r line; do echo "$line, Nov 20, 2026, trustedCertEntry,"; done < "$ks";;
esac
'''


def default_ca():
    """The bundle the script falls back to on this host, or None."""
    try:
        return DEFAULT_CA if os.path.getsize(DEFAULT_CA) > 0 else None
    except OSError:
        return None


class ScriptCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name).resolve()
        self.bin = self.tmp / 'bin'
        self.rec = self.tmp / 'rec'
        self.bin.mkdir()
        self.rec.mkdir()
        # Spaces and a glob character in both paths: any unquoted use breaks.
        self.root = self.tmp / 'work root *'
        self.ca = self.tmp / 'ca dir' / 'ca bundle.crt'
        self.ca.parent.mkdir()
        self.ca.write_text(BUNDLE)
        self.cwd = self.tmp / 'cwd'
        self.cwd.mkdir()
        self.fake('docker')
        self.fake('python3')
        # The machine is x86_64 in every test unless one says otherwise, so the
        # suite gives the same answers on an arm64 host.
        self.fake('uname', 'echo "${FAKE_UNAME:-x86_64}"\n')
        self.store = self.root / 'ca' / 'java-cacerts.p12'

    # -- plumbing ---------------------------------------------------------
    def fake(self, name, hook=None):
        path = self.bin / name
        path.write_text(RECORDER)
        path.chmod(0o755)
        if hook is not None:
            self.hook(name, hook)

    def hook(self, name, text):
        (self.rec / (name + '.hook')).write_text(text)

    def env(self, **extra):
        env = {'PATH': '%s:/usr/bin:/bin' % self.bin, 'HOME': str(self.tmp), 'LC_ALL': 'C',
               'TMPDIR': str(self.tmp), 'FAKE_REC': str(self.rec),
               'TAKX_PROTO_ROOT': str(self.root), 'TAKX_PROTO_CA': str(self.ca)}
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = str(value)
        return env

    def script(self, script, *args, stdin=subprocess.DEVNULL, **extra):
        return subprocess.run([str(script)] + [str(a) for a in args], env=self.env(**extra),
                              cwd=self.cwd, stdin=stdin, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=TIMEOUT)

    def run_tc(self, *args, **extra):
        return self.script(RUN, *args, **extra)

    def fetch(self, *args, **extra):
        return self.script(FETCH, *args, **extra)

    def calls(self, name='docker'):
        found = sorted(self.rec.glob(name + '.*.argv'), key=lambda p: int(p.name.split('.')[-2]))
        result = []
        for path in found:
            data = path.read_bytes()
            result.append([part.decode() for part in data.split(b'\0')[:-1]] if data else [])
        return result

    def call_env(self, name, index):
        data = (self.rec / ('%s.%d.env' % (name, index))).read_bytes()
        return dict(item.decode().split('=', 1) for item in data.split(b'\0') if b'=' in item)

    def count(self, name='docker'):
        path = self.rec / (name + '.count')
        return len(path.read_text().splitlines()) if path.exists() else 0

    def base(self, root=None, ca='test'):
        root = str(root or self.root)
        ca = str(self.ca) if ca == 'test' else ca
        argv = ['--init', '-v', '%s:%s:ro' % (REPO, REPO), '-v', '%s:%s' % (root, root),
                '-e', 'TAKX_PROTO_ROOT=' + root, '-e', 'TAKX_PROTO_REPO=%s' % REPO,
                '-e', 'HOME=/tmp']
        if ca:
            argv += ['-v', ca + ':/ca.crt:ro'] + CA_ENV
        return argv

    def expect(self, command, middle=('--rm',), image=TOOLCHAIN, workdir=None, **base):
        return (['run'] + self.base(**base) + ['-w', str(workdir or self.root)] + list(middle)
                + [image] + list(command))

    def assert_refused(self, result, code, message):
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertIn(message, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.count('docker'), 0, 'docker must not be invoked')
        self.assertEqual(self.count('python3'), 0)

    def make_store(self, content='STORE\n', fingerprint=True):
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(content)
        if fingerprint:
            digest = hashlib.sha256(self.ca.read_bytes()).hexdigest()
            Path(str(self.store) + '.source-sha256').write_text(digest + '\n')


class RunInToolchain(ScriptCase):
    def test_standard_mounts_environment_and_command(self):
        result = self.run_tc('--', 'echo', 'hi')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        self.assertEqual(self.calls(), [self.expect(['echo', 'hi'])])
        argv = self.calls()[0]
        # Repository read-only at the same path; work root writable at the same path.
        self.assertIn('%s:%s:ro' % (REPO, REPO), argv)
        self.assertIn('%s:%s' % (self.root, self.root), argv)
        self.assertNotIn('%s:%s:ro' % (self.root, self.root), argv)
        self.assertIn('%s:/ca.crt:ro' % self.ca, argv)
        self.assertTrue((REPO / 'takx' / 'desktop-prototype' / 'run-in-toolchain.sh').is_file())
        for name in ('cache', 'src', 'P', 'logs', 'status', 'failed', 'ca'):
            self.assertTrue((self.root / name).is_dir(), name)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         sorted(['cache', 'src', 'P', 'logs', 'status', 'failed', 'ca']))

    def test_proxy_variables_and_java_tool_options_never_reach_the_container(self):
        result = self.run_tc('--cpus', '2', '-e', 'KEEP=1', '--', 'env', **PROXY_ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = self.calls()[0]
        self.assertEqual(argv, self.expect(['env'], middle=['--rm', '--cpus', '2', '-e', 'KEEP=1']))
        for item in argv:
            self.assertNotIn('proxy', item.lower())
            self.assertNotIn('JAVA_TOOL_OPTIONS', item)
            self.assertFalse(item.startswith('--env-file'), item)
        # Every -e is KEY=VALUE: a bare `-e NAME` would copy the caller's value.
        for index, item in enumerate(argv[:argv.index(TOOLCHAIN)]):
            if item in ('-e', '--env'):
                self.assertIn('=', argv[index + 1])
        self.assertNotIn('--network', argv)
        self.assertNotIn('--privileged', argv)

    def test_tls_verification_is_never_disabled(self):
        self.run_tc('--', 'true')
        joined = ' '.join(self.calls()[0])
        for word in ('GIT_SSL_NO_VERIFY', 'NODE_TLS_REJECT_UNAUTHORIZED', 'PYTHONHTTPSVERIFY',
                     '--insecure', 'sslVerify', 'trusted-host'):
            self.assertNotIn(word, joined)

    def test_without_a_bundle_setting_the_host_default_applies(self):
        for extra in ({'TAKX_PROTO_CA': None}, {'TAKX_PROTO_CA': ''}):
            for path in self.rec.glob('docker.*'):
                path.unlink()
            result = self.run_tc('--', 'true', **extra)
            self.assertEqual(result.returncode, 0, result.stderr)
            argv = self.calls()[0]
            expected = self.expect(['true'], ca=default_ca())
            if default_ca():
                # A trust store may exist only under the temporary root: none.
                self.assertIn(DEFAULT_CA + ':/ca.crt:ro', argv)
            else:
                self.assertFalse([a for a in argv if 'ca.crt' in a or 'CA' in a.split('=')[0]
                                  and a != '-e' and 'TAKX' not in a], argv)
            self.assertEqual(argv, expected)

    def test_explicit_bundle_wins_over_the_host_default(self):
        self.run_tc('--', 'true')
        argv = self.calls()[0]
        self.assertEqual(sum(1 for a in argv if a.endswith(':/ca.crt:ro')), 1)
        self.assertNotIn(DEFAULT_CA + ':/ca.crt:ro', argv)

    def test_missing_or_empty_bundle_refuses_before_docker(self):
        missing = self.tmp / 'no such bundle.crt'
        result = self.run_tc('--', 'true', TAKX_PROTO_CA=missing)
        self.assert_refused(result, 2, "run-in-toolchain.sh: CA bundle '%s' is missing or empty"
                            % missing)
        empty = self.tmp / 'empty.crt'
        empty.write_text('')
        result = self.run_tc('--', 'true', TAKX_PROTO_CA=empty)
        self.assert_refused(result, 2, "CA bundle '%s' is missing or empty" % empty)
        result = self.run_tc('--java-truststore', TAKX_PROTO_CA=missing)
        self.assert_refused(result, 2, 'is missing or empty')
        self.assertFalse(self.store.exists())

    def test_every_option_maps_to_its_docker_flag_in_order(self):
        result = self.run_tc(
            '--image', 'example/image:1', '--name', 'stage-1', '-d', '--cpus', '1.5',
            '--memory', '6g', '-e', 'A=1', '--env', 'B=2', '-w', '/somewhere/else',
            '--mount', '/host:/guest:ro', '--docker-arg', '--pids-limit', '--docker-arg', '99',
            '--mount', 'vol:/data', '--', 'make', '-j2')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [self.expect(
            ['make', '-j2'], image='example/image:1', workdir='/somewhere/else',
            middle=['--name', 'stage-1', '-d', '--cpus', '1.5', '--memory', '6g',
                    '-v', '/host:/guest:ro', '--pids-limit', '99', '-v', 'vol:/data',
                    '-e', 'A=1', '-e', 'B=2'])])

    def test_long_and_short_spellings_are_the_same(self):
        self.run_tc('--name', 'n', '--detach', '--workdir', '/w', '--env', 'K=V', '--', 'x')
        self.run_tc('--name', 'n', '-d', '-w', '/w', '-e', 'K=V', '--', 'x')
        first, second = self.calls()
        self.assertEqual(first, second)
        self.assertEqual(first, self.expect(['x'], workdir='/w',
                                            middle=['--name', 'n', '-d', '-e', 'K=V']))

    def test_runtime_image_and_last_image_option_wins(self):
        self.run_tc('--runtime', '--', 'x')
        self.run_tc('--image', 'a:b', '--runtime', '--', 'x')
        self.run_tc('--runtime', '--image', 'a:b', '--', 'x')
        self.assertEqual(self.calls(), [self.expect(['x'], image=RUNTIME),
                                        self.expect(['x'], image=RUNTIME),
                                        self.expect(['x'], image='a:b')])

    def test_foreground_is_removed_and_detached_is_kept(self):
        self.run_tc('--name', 'kept', '--', 'x')
        self.run_tc('--name', 'kept', '-d', '--', 'x')
        foreground, detached = self.calls()
        self.assertEqual(foreground, self.expect(['x'], middle=['--name', 'kept', '--rm']))
        self.assertEqual(detached, self.expect(['x'], middle=['--name', 'kept', '-d']))
        self.assertNotIn('--rm', detached)
        self.assertNotIn('-d', foreground)

    def test_detach_needs_a_name(self):
        self.assert_refused(self.run_tc('-d', '--', 'x'), 2, 'run-in-toolchain.sh: -d needs --name')
        self.assert_refused(self.run_tc('-d', '--name', '', '--', 'x'), 2, '-d needs --name')

    def test_container_id_of_a_detached_run_is_printed(self):
        self.hook('docker', 'echo 0123456789abcdef\n')
        result = self.run_tc('-d', '--name', 'n', '--', 'x')
        self.assertEqual((result.returncode, result.stdout), (0, '0123456789abcdef\n'))

    def test_command_arguments_pass_through_unmodified(self):
        (self.cwd / 'glob-bait.txt').write_text('')
        command = ['printf', '%s\\n', 'a b', '  lead and trail  ', '*', '?', '[a-z]*', '$(id)',
                   '`id`', '$HOME', '${TAKX_PROTO_ROOT}', '"double"', "'single'", 'a\\b', '',
                   '--', '-d', '--name', '--rm', '-e', 'X=Y', '--image', 'evil', '--help', '-h',
                   'line one\nline two', '; touch %s' % (self.tmp / 'injected'), '&& false',
                   '| cat', '> %s' % (self.tmp / 'redirected'), '~', '#comment', '!', 'ünïcode',
                   '\t', '-']
        result = self.run_tc('--', *command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [self.expect(command)])
        self.assertFalse((self.tmp / 'injected').exists())
        self.assertFalse((self.tmp / 'redirected').exists())

    def test_option_values_pass_through_unmodified(self):
        values = ['a b', '*', '$(id)', '-d', '--', 'x;y', "q'q\"q", 'n\nl']
        for value in values:
            for path in self.rec.glob('docker.*'):
                path.unlink()
            result = self.run_tc('--image', value, '--name', value, '--cpus', value,
                                 '--memory', value, '-e', value, '-w', value, '--mount', value,
                                 '--docker-arg', value, '--', 'cmd', value)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.calls(), [self.expect(
                ['cmd', value], image=value, workdir=value,
                middle=['--name', value, '--rm', '--cpus', value, '--memory', value,
                        '-v', value, value, '-e', value])], value)

    def test_empty_option_values_are_kept_as_empty_arguments(self):
        self.run_tc('--docker-arg', '', '-e', '', '--', 'cmd', '')
        self.assertEqual(self.calls(), [self.expect(['cmd', ''], middle=['--rm', '', '-e', ''])])

    def test_unknown_option_and_command_without_separator_are_refused(self):
        for args, shown in ((['--bogus', '--', 'x'], '--bogus'), (['echo', 'hi'], 'echo'),
                            (['-x'], '-x'), (['--cpus=2', '--', 'x'], '--cpus=2'),
                            (['', '--', 'x'], ''), (['-dd', '--', 'x'], '-dd'),
                            (['--runtime', 'bash', '-c', 'true'], 'bash')):
            result = self.run_tc(*args)
            self.assert_refused(result, 2, "run-in-toolchain.sh: unknown option '%s' "
                                "(use -- before the command)" % shown)
        self.assertFalse(self.root.exists(), 'refused before anything is created')

    def test_option_without_its_value_aborts(self):
        for option in ('--image', '--name', '--log', '--cpus', '--memory', '-e', '--env', '-w',
                       '--workdir', '--mount', '--docker-arg'):
            result = self.run_tc(option)
            self.assertNotEqual(result.returncode, 0, option)
            self.assertIn('unbound variable', result.stderr, option)
            self.assertEqual(self.count(), 0, option)
            self.assertFalse(self.root.exists(), option)

    def test_no_command_is_refused(self):
        for args in ([], ['--'], ['--runtime'], ['--name', 'n', '-d', '--']):
            self.assert_refused(self.run_tc(*args), 2, 'run-in-toolchain.sh: no command given')

    def test_exit_status_and_streams_of_docker_are_those_of_the_script(self):
        self.hook('docker', 'echo to-stdout; echo to-stderr >&2; exit "${FAKE_EXIT:-0}"\n')
        for code in (0, 1, 2, 37, 125, 127, 255):
            result = self.run_tc('--', 'x', FAKE_EXIT=code)
            self.assertEqual((result.returncode, result.stdout, result.stderr),
                             (code, 'to-stdout\n', 'to-stderr\n'))

    def test_command_really_runs_with_its_arguments_and_status(self):
        self.hook('docker', EXEC_COMMAND)
        result = self.run_tc('--', 'bash', '-c', 'printf "<%s>" "$@"; exit 7', 'zero', 'a b', '*', '')
        self.assertEqual((result.returncode, result.stdout), (7, '<a b><*><>'))

    def test_docker_replaces_the_script_process(self):
        # Signals sent to the script (timeout, kill) must reach docker itself.
        self.hook('docker', 'echo $$ > "$FAKE_REC/pid"\n')
        log = self.root / 'logs' / 'x.log'
        for args in (['--', 'x'], ['--log', str(log), '--', 'x'], ['-d', '--name', 'n', '--', 'x']):
            process = subprocess.Popen([str(RUN)] + args, env=self.env(), cwd=self.cwd,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
            process.communicate(timeout=TIMEOUT)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(int((self.rec / 'pid').read_text()), process.pid, args)

    def test_standard_input_reaches_docker(self):
        self.hook('docker', 'cat\n')
        process = subprocess.run([str(RUN), '--', 'cat'], env=self.env(), cwd=self.cwd,
                                 input='piped text\n', stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=TIMEOUT)
        self.assertEqual((process.returncode, process.stdout), (0, 'piped text\n'))

    def test_help_prints_the_header_and_does_nothing_else(self):
        for option in ('-h', '--help'):
            result = self.run_tc(option, '--bogus')
            self.assertEqual((result.returncode, result.stderr), (0, ''))
            lines = result.stdout.splitlines()
            self.assertEqual(lines[0], 'Run one command in the desktop-prototype toolchain '
                             '(or runtime) image with the')
            self.assertEqual(lines[-1], 'that compare tool output.')
            self.assertEqual(len(lines), 46)
            self.assertIn('  run-in-toolchain.sh [options] -- command [args...]', lines)
            self.assertIn('  TAKX_PROTO_CA    CA bundle to mount, default '
                          '/root/.ccr/ca-bundle.crt when', lines)
            self.assertIn('', lines)
            self.assertFalse([line for line in lines if line.startswith('#')])
            for word in ('--runtime', '--image IMAGE', '--name NAME', '-d, --detach', '--log FILE',
                         '--cpus N', '--memory SIZE', '-e, --env KEY=VALUE', '-w, --workdir DIR',
                         '--mount SPEC', '--docker-arg ARG', '--java-truststore',
                         'TAKX_PROTO_ROOT', 'TAKX_PROTO_ARCH'):
                self.assertIn(word, result.stdout)
            self.assertEqual(self.count(), 0)
            self.assertFalse(self.root.exists())

    def test_help_text_is_the_header_of_the_script(self):
        source = RUN.read_text().splitlines()
        self.assertEqual(source[0], '#!/usr/bin/env bash')
        self.assertEqual(source[1:3], ['# Copyright 2026 uds-takserver authors',
                                       '# SPDX-License-Identifier: GPL-3.0-only'])
        header = []
        for line in source[4:]:
            if not line.startswith('#'):
                break
            header.append(line[2:] if line.startswith('# ') else line[1:])
        self.assertEqual(self.run_tc('--help').stdout.splitlines(), header)
        self.assertEqual(len(header), 46)

    # -- architecture -------------------------------------------------------
    def test_without_an_architecture_setting_docker_gets_no_platform(self):
        for extra in ({}, {'TAKX_PROTO_ARCH': ''}, {'FAKE_UNAME': 'aarch64'}):
            with self.subTest(extra=extra):
                result = self.run_tc('--', 'x', **extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.calls()[-1], self.expect(['x']))
                self.assertNotIn('--platform', self.calls()[-1])
        self.assertEqual(self.count('uname'), 0, 'the wrapper itself never asks for the machine')

    def test_architecture_setting_selects_the_platform_and_reaches_the_container(self):
        for value, arch in (('arm64', 'arm64'), ('aarch64', 'arm64'), ('amd64', 'amd64'), ('x86_64', 'amd64')):
            with self.subTest(value=value):
                result = self.run_tc('--', 'x', TAKX_PROTO_ARCH=value)
                self.assertEqual(result.returncode, 0, result.stderr)
                base = self.base()
                split = base.index('HOME=/tmp') + 1
                self.assertEqual(self.calls()[-1], ['run'] + base[:split] + [
                    '--platform', 'linux/' + arch, '-e', 'TAKX_PROTO_ARCH=' + arch] + base[split:] + [
                    '-w', str(self.root), '--rm', TOOLCHAIN, 'x'])

    def test_unknown_architecture_is_refused_before_docker(self):
        result = self.run_tc('--', 'x', TAKX_PROTO_ARCH='riscv64')
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported architecture 'riscv64'", result.stderr)
        self.assertEqual(self.count(), 0)

    def tree(self, base_parts=('upstream', '.git')):
        """A copy of the wrapper inside a directory marked as an architecture tree."""
        tree, base = self.tmp / 'arch tree', self.tmp / 'base repo'
        proto = tree / 'takx' / 'desktop-prototype'
        proto.mkdir(parents=True)
        for script in (RUN, ARCH):
            shutil.copy2(script, proto / script.name)
        for part in base_parts:
            (base / part).mkdir(parents=True)
        (tree / '.takx-arch-tree.base').write_text('%s\n' % base)
        return tree, base, proto / RUN.name

    def test_architecture_tree_gets_upstream_and_git_of_the_base_repository(self):
        tree, base, script = self.tree()
        result = self.script(script, '--', 'x')
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        argv = self.calls()[0]
        expected = ['run', '--init', '-v', '%s:%s:ro' % (tree, tree), '-v', '%s:%s' % (self.root, self.root),
                    '-e', 'TAKX_PROTO_ROOT=%s' % self.root, '-e', 'TAKX_PROTO_REPO=%s' % tree, '-e', 'HOME=/tmp',
                    '-v', '%s/upstream:%s/upstream:ro' % (base, tree), '-v', '%s/.git:%s/.git:ro' % (base, tree),
                    '-e', 'TAKX_PROTO_BASE_REPO=%s' % base]
        self.assertEqual(argv[:len(expected)], expected)
        self.assertEqual(argv[-5:], ['-w', str(self.root), '--rm', TOOLCHAIN, 'x'])
        # The tracked wrapper, outside a tree, mounts neither.
        self.run_tc('--', 'x')
        self.assertFalse([item for item in self.calls()[-1] if '/upstream:' in item or '/.git:' in item])

    def test_architecture_tree_without_its_base_repository_is_refused(self):
        for parts in ((), ('upstream',), ('.git',)):
            with self.subTest(parts=parts):
                shutil.rmtree(self.tmp / 'arch tree', ignore_errors=True)
                shutil.rmtree(self.tmp / 'base repo', ignore_errors=True)
                script = self.tree(parts)[2]
                result = self.script(script, '--', 'x')
                self.assertEqual(result.returncode, 2)
                self.assertIn('has no upstream/ and .git/', result.stderr)
                self.assertEqual(self.count(), 0)

    def test_work_root_that_cannot_be_created_aborts_before_docker(self):
        blocker = self.tmp / 'a file'
        blocker.write_text('x')
        result = self.run_tc('--', 'true', TAKX_PROTO_ROOT=blocker / 'root')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('mkdir', result.stderr)
        self.assertEqual(self.count(), 0)
        result = self.run_tc('--', 'true', TAKX_PROTO_ROOT=blocker)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.count(), 0)

    def test_default_work_root(self):
        # `mkdir` is a recorder here, so the real default root is only named.
        self.fake('mkdir')
        result = self.run_tc('--', 'true', TAKX_PROTO_ROOT=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls('mkdir'), [['-p'] + [
            DEFAULT_ROOT + '/' + name
            for name in ('cache', 'src', 'P', 'logs', 'status', 'failed', 'ca')]])
        argv = self.calls()[0]
        self.assertEqual(argv[:9], ['run', '--init', '-v', '%s:%s:ro' % (REPO, REPO),
                                    '-v', DEFAULT_ROOT + ':' + DEFAULT_ROOT,
                                    '-e', 'TAKX_PROTO_ROOT=' + DEFAULT_ROOT, '-e'])
        self.assertEqual(argv[argv.index('-w') + 1], DEFAULT_ROOT)
        self.assertEqual(argv[-2:], [TOOLCHAIN, 'true'])

    def test_empty_work_root_setting_means_the_default(self):
        self.fake('mkdir')
        self.run_tc('--', 'true', TAKX_PROTO_ROOT='')
        self.assertIn(DEFAULT_ROOT + ':' + DEFAULT_ROOT, self.calls()[0])

    def test_runs_from_any_directory_and_through_a_relative_path(self):
        relative = os.path.relpath(RUN, self.cwd)
        process = subprocess.run([relative, '--', 'x'], env=self.env(), cwd=self.cwd,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=TIMEOUT)
        self.assertEqual(process.returncode, 0, process.stderr)
        process = subprocess.run(['bash', 'run-in-toolchain.sh', '--', 'x'], env=self.env(),
                                 cwd=HERE, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=TIMEOUT)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.calls(), [self.expect(['x'])] * 2)

    def test_scripts_are_executable_and_strict(self):
        for script in (RUN, FETCH):
            self.assertTrue(script.stat().st_mode & stat.S_IXUSR, script)
            text = script.read_text()
            self.assertTrue(text.startswith('#!/usr/bin/env bash\n'))
            self.assertIn('\nset -euo pipefail\n', text)
            syntax = subprocess.run(['/bin/bash', '-n', str(script)], stderr=subprocess.PIPE,
                                    timeout=TIMEOUT)
            self.assertEqual(syntax.returncode, 0, syntax.stderr)

    # -- terminal ----------------------------------------------------------
    def with_terminal(self, args, stdin_tty, stdout_tty):
        master, slave = pty.openpty()
        try:
            process = subprocess.run(
                [str(RUN)] + args, env=self.env(), cwd=self.cwd,
                stdin=slave if stdin_tty else subprocess.DEVNULL,
                stdout=slave if stdout_tty else subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=TIMEOUT)
        finally:
            os.close(slave)
            os.close(master)
        self.assertEqual(process.returncode, 0, process.stderr)
        return self.calls()[-1]

    def test_interactive_only_when_both_ends_are_a_terminal(self):
        self.assertEqual(self.with_terminal(['--', 'sh'], True, True),
                         self.expect(['sh'], middle=['--rm', '-it']))
        self.assertEqual(self.with_terminal(['--', 'sh'], True, False), self.expect(['sh']))
        self.assertEqual(self.with_terminal(['--', 'sh'], False, True), self.expect(['sh']))
        self.assertEqual(self.with_terminal(['-d', '--name', 'n', '--', 'sh'], True, True),
                         self.expect(['sh'], middle=['--name', 'n', '-d']))

    # -- --log -------------------------------------------------------------
    WRAPPER = ['bash', '-c', 'log="$1"; shift; exec "$@" >>"$log" 2>&1', 'takx-log']

    def test_log_wraps_the_command_and_creates_the_directory(self):
        log = self.root / 'logs' / 'sub dir' / 'stage one.log'
        result = self.run_tc('-d', '--name', 'n', '--log', log, '--', 'make', 'a b', '*')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [self.expect(
            self.WRAPPER + [str(log), 'make', 'a b', '*'], middle=['--name', 'n', '-d'])])
        self.assertTrue(log.parent.is_dir())
        self.assertFalse(log.exists())

    def test_logged_command_appends_both_streams_and_keeps_its_status(self):
        self.hook('docker', EXEC_COMMAND)
        log = self.root / 'logs' / 'stage *.log'
        (self.root / 'logs').mkdir(parents=True)
        log.write_text('earlier\n')
        command = ['bash', '-c', 'echo "out $1"; echo "err $2" >&2; exit 5', 'zero', 'a b', '*']
        result = self.run_tc('--log', log, '--', *command)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (5, '', ''))
        self.assertEqual(log.read_text(), 'earlier\nout a b\nerr *\n')
        result = self.run_tc('--log', log, '--', 'echo', 'again')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(log.read_text(), 'earlier\nout a b\nerr *\nagain\n')
        self.assertEqual(sorted(p.name for p in self.cwd.iterdir()), [])

    def test_log_outside_the_work_root_is_refused(self):
        outside = self.tmp / 'outside' / 'x.log'
        sibling = Path(str(self.root) + '-sibling') / 'x.log'
        for log in (outside, sibling, 'relative/x.log', 'x.log', self.root, '/x.log',
                    str(self.root).replace('*', 'X') + '/logs/x.log'):
            result = self.run_tc('--log', log, '--', 'x')
            self.assert_refused(result, 2, 'run-in-toolchain.sh: --log must be under %s' % self.root)
        self.assertFalse(outside.parent.exists())
        self.assertFalse(sibling.parent.exists())
        self.assertEqual(sorted(p.name for p in self.cwd.iterdir()), [])

    def test_log_cannot_leave_the_work_root_through_dot_dot(self):
        escaped = self.tmp / 'escaped'
        for log in ('%s/../escaped/x.log' % self.root, '%s/logs/../../escaped/x.log' % self.root,
                    '%s/logs/..' % self.root, '%s/..' % self.root,
                    '%s/a/../../escaped/deeper/x.log' % self.root):
            result = self.run_tc('--log', log, '--', 'x')
            self.assert_refused(result, 2, 'run-in-toolchain.sh: --log must be under %s' % self.root)
        self.assertFalse(escaped.exists())
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()),
                         ['bin', 'ca dir', 'cwd', 'rec', 'work root *'])

    def test_log_names_that_only_look_like_dot_dot_are_accepted(self):
        for name in ('..hidden/x.log', 'a..b/x.log', 'dir../x.log', 'x..log', '.../x.log'):
            log = self.root / 'logs' / name
            result = self.run_tc('--log', log, '--', 'x')
            self.assertEqual(result.returncode, 0, (name, result.stderr))
            self.assertEqual(self.calls()[-1][-2:], [str(log), 'x'])
            self.assertTrue(log.parent.is_dir())

    # -- Java trust store ----------------------------------------------------
    def test_truststore_is_built_once_and_tied_to_the_bundle(self):
        self.hook('docker', MAKE_STORE)
        result = self.run_tc('--java-truststore')
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, '%s\n' % self.store, ''))
        (argv,) = self.calls()
        head = ['run', '--rm'] + self.base() + [TOOLCHAIN, 'bash', '-euo', 'pipefail', '-c']
        self.assertEqual(argv[:len(head)], head)
        self.assertEqual(argv[len(head) + 1:], ['truststore', str(self.store)])
        self.assertEqual(self.store.read_text(), 'STORE\n')
        digest = hashlib.sha256(self.ca.read_bytes()).hexdigest()
        self.assertEqual(Path(str(self.store) + '.source-sha256').read_text(), digest + '\n')
        self.assertEqual((self.root / 'logs' / 'java-truststore.log').read_text(), '153\n')
        self.assertTrue((self.root / 'ca' / '.lock').is_file())
        # Unchanged bundle: nothing is rebuilt.
        for _ in range(2):
            result = self.run_tc('--java-truststore')
            self.assertEqual((result.returncode, result.stdout), (0, '%s\n' % self.store))
        self.assertEqual(self.count(), 1)

    def test_truststore_is_rebuilt_when_bundle_or_store_changed(self):
        self.hook('docker', MAKE_STORE)
        fingerprint = Path(str(self.store) + '.source-sha256')
        self.make_store('OLD\n')
        self.assertEqual(self.run_tc('--java-truststore').returncode, 0)
        self.assertEqual((self.count(), self.store.read_text()), (0, 'OLD\n'))
        # Bundle changed.
        self.ca.write_text(BUNDLE + CERT_1)
        self.assertEqual(self.run_tc('--java-truststore').returncode, 0)
        self.assertEqual((self.count(), self.store.read_text()), (1, 'STORE\n'))
        self.assertEqual(fingerprint.read_text(),
                         hashlib.sha256(self.ca.read_bytes()).hexdigest() + '\n')
        # Store emptied.
        self.store.write_text('')
        self.assertEqual(self.run_tc('--java-truststore').returncode, 0)
        self.assertEqual((self.count(), self.store.read_text()), (2, 'STORE\n'))
        # Store removed.
        self.store.unlink()
        self.assertEqual(self.run_tc('--java-truststore').returncode, 0)
        self.assertEqual((self.count(), self.store.read_text()), (3, 'STORE\n'))
        # Fingerprint removed or emptied.
        fingerprint.unlink()
        self.assertEqual(self.run_tc('--java-truststore').returncode, 0)
        self.assertEqual(self.count(), 4)
        fingerprint.write_text('')
        self.assertEqual(self.run_tc('--java-truststore').returncode, 0)
        self.assertEqual(self.count(), 5)
        self.assertEqual(self.run_tc('--java-truststore').returncode, 0)
        self.assertEqual(self.count(), 5)

    def test_failed_truststore_build_fails_and_leaves_no_fingerprint(self):
        self.hook('docker', 'echo keytool exploded >&2; exit 3\n')
        result = self.run_tc('--java-truststore')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.count(), 1)
        self.assertFalse(Path(str(self.store) + '.source-sha256').exists())
        self.assertEqual((self.root / 'logs' / 'java-truststore.log').read_text(),
                         'keytool exploded\n')
        # A stale store keeps its old fingerprint, so the next call builds again.
        self.make_store('OLD\n')
        self.ca.write_text(BUNDLE + CERT_2)
        old = Path(str(self.store) + '.source-sha256').read_text()
        self.assertNotEqual(self.run_tc('--java-truststore').returncode, 0)
        self.assertEqual(Path(str(self.store) + '.source-sha256').read_text(), old)
        self.assertNotEqual(self.run_tc('--java-truststore').returncode, 0)
        self.assertEqual(self.count(), 3)

    def test_truststore_mode_uses_the_chosen_image_and_ignores_a_command(self):
        self.hook('docker', MAKE_STORE)
        result = self.run_tc('--image', 'jdk:x', '--java-truststore', '--name', 'n', '--cpus', '1',
                             '--', 'echo', 'never')
        self.assertEqual((result.returncode, result.stdout), (0, '%s\n' % self.store))
        (argv,) = self.calls()
        head = ['run', '--rm'] + self.base() + ['jdk:x', 'bash', '-euo', 'pipefail', '-c']
        self.assertEqual(argv[:len(head)], head)
        self.assertNotIn('never', argv)

    def test_concurrent_truststore_requests_build_once(self):
        self.hook('docker', 'sleep 1\n' + MAKE_STORE)
        first = subprocess.Popen([str(RUN), '--java-truststore'], env=self.env(), cwd=self.cwd,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen([str(RUN), '--java-truststore'], env=self.env(), cwd=self.cwd,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        outputs = [first.communicate(timeout=TIMEOUT), second.communicate(timeout=TIMEOUT)]
        self.assertEqual([first.returncode, second.returncode], [0, 0], outputs)
        self.assertEqual([out for out, _ in outputs], ['%s\n' % self.store] * 2)
        self.assertEqual(self.count(), 1)

    def test_current_truststore_is_served_without_waiting_for_the_lock(self):
        # Another stage may hold the lock for a long build of a different bundle.
        self.make_store()
        with open(self.root / 'ca' / '.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.run_tc('--java-truststore')
        self.assertEqual((result.returncode, result.stdout), (0, '%s\n' % self.store))
        self.assertEqual(self.count(), 0)

    def test_truststore_without_a_bundle_says_so_and_succeeds(self):
        if default_ca():
            self.skipTest('this host has a default CA bundle')
        result = self.run_tc('--java-truststore', TAKX_PROTO_CA='')
        self.assertEqual((result.returncode, result.stdout), (0, ''))
        self.assertIn('run-in-toolchain.sh: no CA bundle; the JDK trust store applies', result.stderr)
        self.assertEqual(self.count(), 0)
        self.assertFalse(self.store.exists())

    def test_existing_truststore_is_named_to_the_container(self):
        named = ['-e', 'TAKX_JAVA_TRUSTSTORE=%s' % self.store]
        self.run_tc('--', 'x')
        self.assertEqual(self.calls()[-1], self.expect(['x']))
        self.make_store('')
        self.run_tc('--', 'x')
        self.assertEqual(self.calls()[-1], self.expect(['x']))
        self.make_store()
        self.run_tc('--', 'x')
        self.assertEqual(self.calls()[-1],
                         ['run'] + self.base() + named + ['-w', str(self.root), '--rm', TOOLCHAIN, 'x'])
        if not default_ca():
            # No bundle mounted: the store of some earlier bundle is not offered.
            self.run_tc('--', 'x', TAKX_PROTO_CA='')
            self.assertEqual(self.calls()[-1], self.expect(['x'], ca=None))

    # The script handed to the image, run here against a recording keytool.
    def inner(self, **extra):
        self.hook('docker', MAKE_STORE)
        self.assertEqual(self.run_tc('--java-truststore').returncode, 0)
        argv = self.calls()[-1]
        text = argv[argv.index('-c') + 1]
        self.assertEqual(text.count('/ca.crt'), 1)
        self.fake('keytool', KEYTOOL)
        out = self.tmp / 'published' / 'store.p12'
        out.parent.mkdir(exist_ok=True)
        work = self.tmp / 'inner tmp'
        work.mkdir(exist_ok=True)
        command = argv[argv.index(TOOLCHAIN) + 1:argv.index('-c') + 1]
        self.assertEqual(command, ['bash', '-euo', 'pipefail', '-c'])
        result = subprocess.run(
            command + [text.replace('/ca.crt', "'%s'" % self.ca), 'truststore', str(out)],
            env=self.env(JAVA_HOME=self.tmp / 'jdk home', TMPDIR=work, **extra), cwd=self.cwd,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=TIMEOUT)
        return result, out, work

    def test_inner_truststore_script_imports_jdk_roots_and_every_bundle_certificate(self):
        result, out, work = self.inner()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', 'keytool noise on stderr\n3\n'))
        (scratch,) = list(work.iterdir())
        store = str(scratch / 'store.p12')
        calls = self.calls('keytool')
        self.assertEqual(calls, [
            ['-importkeystore', '-noprompt', '-srckeystore',
             '%s/lib/security/cacerts' % (self.tmp / 'jdk home'), '-srcstorepass', 'changeit',
             '-destkeystore', store, '-deststoretype', 'PKCS12', '-deststorepass', 'changeit'],
            ['-importcert', '-noprompt', '-trustcacerts', '-alias', 'bundle-ca-1',
             '-file', str(scratch / 'ca-1.pem'), '-keystore', store, '-storepass', 'changeit'],
            ['-importcert', '-noprompt', '-trustcacerts', '-alias', 'bundle-ca-2',
             '-file', str(scratch / 'ca-2.pem'), '-keystore', store, '-storepass', 'changeit'],
            ['-list', '-keystore', store, '-storepass', 'changeit']])
        self.assertEqual((self.rec / 'pem.bundle-ca-1').read_text(), CERT_1)
        self.assertEqual((self.rec / 'pem.bundle-ca-2').read_text(), CERT_2)
        self.assertEqual(out.read_text(), 'base\nbundle-ca-1\nbundle-ca-2\n')
        self.assertEqual([p.name for p in out.parent.iterdir()], ['store.p12'])
        self.assertEqual(sorted(p.name for p in self.cwd.iterdir()), [])

    def test_inner_truststore_script_tolerates_one_bad_certificate_only(self):
        result, out, _ = self.inner(FAKE_BAD_ALIAS='bundle-ca-1')
        self.assertEqual((result.returncode, result.stderr), (0, 'keytool noise on stderr\n2\n'))
        self.assertEqual(out.read_text(), 'base\nbundle-ca-2\n')

    def test_inner_truststore_script_fails_without_publishing(self):
        result, out, _ = self.inner(FAKE_KEYSTORE_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.count('keytool'), 1)
        self.assertEqual(list(out.parent.iterdir()), [])
        for path in self.rec.glob('keytool.*.argv'):
            path.unlink()
        (self.rec / 'keytool.count').unlink()
        result, out, _ = self.inner(FAKE_LIST_EMPTY='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, 'keytool noise on stderr\n0\n')
        self.assertEqual(list(out.parent.iterdir()), [])


class Fetch(ScriptCase):
    INSPECT = ['image', 'inspect', TOOLCHAIN]
    HOOK = MAKE_STORE + r'''
if [[ "$1" == image ]]; then echo inspect-stdout; echo inspect-stderr >&2; exit "${FAKE_INSPECT:-0}"; fi
if [[ "${@: -2:1}" == truststore ]]; then exit "${FAKE_STORE_EXIT:-0}"; fi
echo fetch-stdout; echo fetch-stderr >&2; exit "${FAKE_EXIT:-0}"
'''

    def setUp(self):
        super().setUp()
        self.hook('docker', self.HOOK)
        self.hook('python3', 'echo host-stdout; echo host-stderr >&2; exit "${FAKE_EXIT:-0}"\n')
        self.log = self.root / 'logs' / 'fetch.log'

    def python(self, *args, jobs='4', root=None):
        return ['python3', '-I', '-B', str(HERE / 'fetch_inputs.py'), '--root',
                str(root or self.root), '--jobs', jobs] + list(args)

    def test_missing_image_is_refused_before_anything_runs(self):
        result = self.fetch(FAKE_INSPECT=1)
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertEqual(result.stderr,
                         'fetch.sh: build takx-desktop-proto:toolchain first (see Dockerfile)\n')
        self.assertEqual(self.calls(), [self.INSPECT])
        self.assertEqual(self.count('python3'), 0)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.store.exists())

    def test_full_run_checks_image_builds_truststore_then_fetches_in_the_toolchain(self):
        result = self.fetch(**PROXY_ENV)
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertEqual(sorted(result.stdout.splitlines()), ['fetch-stderr', 'fetch-stdout'])
        inspect, store, run = self.calls()
        self.assertEqual(inspect, self.INSPECT)
        self.assertEqual(store[:2] + store[-2:], ['run', '--rm', 'truststore', str(self.store)])
        self.assertEqual(run, ['run'] + self.base() + ['-e', 'TAKX_JAVA_TRUSTSTORE=%s' % self.store,
                                                       '-w', str(self.root), '--rm', TOOLCHAIN]
                         + self.python())
        for item in store + run:
            self.assertNotIn('proxy', item.lower())
            self.assertNotIn('JAVA_TOOL_OPTIONS', item)
        self.assertEqual(sorted(self.log.read_text().splitlines()), ['fetch-stderr', 'fetch-stdout'])
        self.assertTrue((self.root / 'status').is_dir())
        self.assertEqual(self.count('python3'), 0, 'host python is not used by default')

    def test_arguments_and_job_count_pass_through(self):
        arguments = ['--only', 'a b', '*', '$(id)', '', '--', '-x', "q'q"]
        result = self.fetch(*arguments, TAKX_FETCH_JOBS='7 *')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[2][-len(self.python(*arguments)):],
                         self.python(*arguments, jobs='7 *'))
        for path in self.rec.glob('docker.*.argv'):
            path.unlink()
        self.fetch(TAKX_FETCH_JOBS='')
        self.assertEqual(self.calls()[-1][-8:], self.python())

    def test_fetch_status_propagates_and_output_is_logged(self):
        self.log.parent.mkdir(parents=True)
        self.log.write_text('earlier run\n')
        for code in (1, 3, 125):
            result = self.fetch(FAKE_EXIT=code)
            self.assertEqual(result.returncode, code)
            self.assertEqual(sorted(result.stdout.splitlines()), ['fetch-stderr', 'fetch-stdout'])
            self.assertEqual(result.stderr, '')
        lines = self.log.read_text().splitlines()
        self.assertEqual(lines[0], 'earlier run')
        self.assertEqual(sorted(lines[1:]), ['fetch-stderr'] * 3 + ['fetch-stdout'] * 3)

    def test_fetch_status_survives_a_failing_log_writer(self):
        self.fake('tee', 'cat; exit 1\n')
        self.assertEqual(self.fetch(FAKE_EXIT=6).returncode, 6)
        self.assertEqual(self.fetch(FAKE_EXIT=0).returncode, 0)
        self.assertEqual(self.calls('tee'), [['-a', str(self.log)]] * 2)

    def test_truststore_failure_stops_before_the_fetch(self):
        result = self.fetch(FAKE_STORE_EXIT=4)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.count(), 2)
        self.assertFalse(self.log.exists())

    def test_truststore_path_is_not_part_of_the_fetch_output(self):
        result = self.fetch()
        self.assertNotIn('java-cacerts', result.stdout)
        self.assertNotIn('inspect-', result.stdout + result.stderr)
        self.assertNotIn('java-cacerts', self.log.read_text())

    def test_on_host_uses_host_python_and_never_docker(self):
        result = self.fetch('--only', 'a b', TAKX_FETCH_ON_HOST=1, TAKX_FETCH_JOBS=2)
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertEqual(sorted(result.stdout.splitlines()), ['host-stderr', 'host-stdout'])
        self.assertEqual(self.calls('python3'), [self.python('--only', 'a b', jobs='2')[1:]])
        self.assertEqual(self.count('docker'), 0)
        self.assertEqual(sorted(self.log.read_text().splitlines()), ['host-stderr', 'host-stdout'])
        self.assertTrue((self.root / 'status').is_dir())
        self.assertFalse((self.root / 'ca').exists())

    def test_on_host_status_propagates_and_log_is_appended(self):
        self.log.parent.mkdir(parents=True)
        self.log.write_text('earlier run\n')
        for code in (9, 1):
            result = self.fetch(TAKX_FETCH_ON_HOST=1, FAKE_EXIT=code)
            self.assertEqual(result.returncode, code)
            self.assertEqual(self.count('docker'), 0)
        self.assertEqual(self.log.read_text().splitlines()[0], 'earlier run')
        self.assertEqual(len(self.log.read_text().splitlines()), 5)

    def test_on_host_success_with_failing_log_writer_fails(self):
        self.fake('tee', 'cat; exit 1\n')
        result = self.fetch(TAKX_FETCH_ON_HOST=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.count('docker'), 0)

    def test_only_the_value_one_selects_the_host(self):
        for value in ('0', '', 'true', 'yes', '11', '01', ' 1'):
            for path in self.rec.glob('*.count'):
                path.unlink()
            result = self.fetch(TAKX_FETCH_ON_HOST=value)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.count('python3'), 0, repr(value))
            self.assertEqual(self.calls()[-1][-8:], self.python(), repr(value))

    def test_default_work_root_and_job_count(self):
        # `mkdir` and `tee` are recorders here, so the real default root is only named.
        self.fake('mkdir')
        self.fake('tee', 'cat\n')
        result = self.fetch(TAKX_FETCH_ON_HOST=1, TAKX_PROTO_ROOT=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls('mkdir'), [['-p', DEFAULT_ROOT + '/logs', DEFAULT_ROOT + '/status']])
        self.assertEqual(self.calls('tee'), [['-a', DEFAULT_ROOT + '/logs/fetch.log']])
        self.assertEqual(self.calls('python3'), [self.python(root=DEFAULT_ROOT)[1:]])
        self.assertEqual(self.count('docker'), 0)

    def test_work_root_that_cannot_be_created_aborts(self):
        blocker = self.tmp / 'a file'
        blocker.write_text('x')
        for extra in ({}, {'TAKX_FETCH_ON_HOST': 1}):
            result = self.fetch(TAKX_PROTO_ROOT=blocker / 'root', **extra)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((self.count('docker'), self.count('python3')), (0, 0))

    def test_bad_bundle_stops_the_fetch(self):
        result = self.fetch(TAKX_PROTO_CA=self.tmp / 'missing.crt')
        self.assertEqual(result.returncode, 2)
        self.assertIn('is missing or empty', result.stderr)
        self.assertEqual(self.calls(), [self.INSPECT])

    def test_without_any_bundle_the_fetch_still_runs(self):
        if default_ca():
            self.skipTest('this host has a default CA bundle')
        result = self.fetch(TAKX_PROTO_CA='')
        self.assertEqual(result.returncode, 0)
        self.assertIn('no CA bundle', result.stderr)
        inspect, run = self.calls()
        self.assertEqual(run, self.expect(self.python(), ca=None))

    # -- architecture -------------------------------------------------------
    def tree_stub(self):
        """A stand-in for the patched tree: its fetch.sh records how it was called."""
        tree = self.tmp / 'arch tree'
        proto = tree / 'takx' / 'desktop-prototype'
        proto.mkdir(parents=True)
        stub = proto / 'fetch.sh'
        stub.write_text('#!/bin/bash\nprintf "%s\\n" "$0" "$@" > "$FAKE_REC/tree-fetch"\n'
                        'flock -n -x "$TAKX_PROTO_ROOT/overrides/arm64/.lock" true || echo locked >> "$FAKE_REC/tree-fetch"\n'
                        'exit 7\n')
        stub.chmod(0o755)
        lock = self.root / 'overrides' / 'arm64' / '.lock'
        lock.parent.mkdir(parents=True)
        lock.write_text('')
        self.hook('python3', 'echo "$FAKE_TREE"; exit "${FAKE_TREE_EXIT:-0}"\n')
        return tree, stub

    def test_amd64_never_asks_for_a_tree(self):
        self.assertFalse(list((HERE / 'overrides').glob('amd64/*.patch')), 'amd64 has no override patches')
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.count('python3'), 0)
        self.assertEqual(self.count('uname'), 1)
        self.assertEqual(self.calls()[0], self.INSPECT)

    def test_arm64_continues_from_the_patched_tree(self):
        self.assertTrue(list((HERE / 'overrides').glob('arm64/*.patch')), 'arm64 has override patches')
        for extra in ({'FAKE_UNAME': 'aarch64'}, {'TAKX_PROTO_ARCH': 'arm64'}):
            with self.subTest(extra=extra):
                shutil.rmtree(self.tmp / 'arch tree', ignore_errors=True)
                shutil.rmtree(self.root, ignore_errors=True)
                for stale in self.rec.glob('*.argv'):
                    stale.unlink()
                tree, stub = self.tree_stub()
                arguments = ['--only', 'a b', '*']
                result = self.fetch(*arguments, FAKE_TREE=tree, **extra)
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertEqual(self.calls('python3'), [['-I', '-B', str(ARCH_TREE), 'ensure', '--arch', 'arm64',
                                                          '--root', str(self.root)]])
                # The same script inside the tree got the same arguments, and
                # holds the shared lock that keeps the tree from being replaced.
                self.assertEqual((self.rec / 'tree-fetch').read_text().splitlines(),
                                 [str(stub)] + arguments + ['locked'])
                self.assertEqual(self.calls(), [], 'the tracked fetch.sh itself runs nothing on arm64')

    def test_a_tree_that_cannot_be_prepared_stops_the_fetch(self):
        tree, _ = self.tree_stub()
        result = self.fetch(FAKE_UNAME='aarch64', FAKE_TREE=tree, FAKE_TREE_EXIT=2)
        self.assertEqual(result.returncode, 2)
        self.assertIn('the arm64 tree could not be prepared', result.stderr)
        self.assertFalse((self.rec / 'tree-fetch').exists())
        self.assertEqual(self.count(), 0)
        # A path that does not hold this script is refused as well.
        result = self.fetch(FAKE_UNAME='aarch64', FAKE_TREE=self.tmp / 'elsewhere')
        self.assertEqual(result.returncode, 2)
        self.assertIn('has no counterpart', result.stderr)
        self.assertEqual(self.count(), 0)

    def test_unknown_machine_is_refused(self):
        result = self.fetch(FAKE_UNAME='riscv64')
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported architecture 'riscv64'", result.stderr)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.count('python3'), 0)

    def test_runs_from_any_directory(self):
        process = subprocess.run(['bash', 'fetch.sh', '--only', 'jars'], env=self.env(), cwd=HERE,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=TIMEOUT)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(self.calls()[2][-10:], self.python('--only', 'jars'))


if __name__ == '__main__':
    unittest.main()
