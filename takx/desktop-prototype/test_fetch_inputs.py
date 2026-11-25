# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline tests of fetch_inputs.py.

Nothing here reaches the network or the real work root: urllib is replaced by an
in-process table (or a loopback HTTP server), Git only ever talks to local
file:// repositories (GIT_ALLOW_PROTOCOL=file), and every path lives in a
temporary directory.
"""
import base64
import contextlib
import gzip
import hashlib
import http.client
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('fetch_inputs', HERE / 'fetch_inputs.py')
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)

REAL_LOG = F.log
REAL_RUN = F.run
REAL_SUBPROCESS_RUN = subprocess.run
REAL_GIT_ARCHIVE = F.git_archive
GIT_ENV = {
    'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
    'GIT_CONFIG_SYSTEM': '/dev/null', 'GIT_AUTHOR_NAME': 'fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
    'GIT_COMMITTER_NAME': 'fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
    'GIT_AUTHOR_DATE': '2026-01-01T00:00:00Z', 'GIT_COMMITTER_DATE': '2026-01-01T00:00:00Z',
    'GIT_ALLOW_PROTOCOL': 'file', 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C', 'TZ': 'UTC',
}
POINTER_HEAD = b'version https://git-lfs.github.com/spec/v1\n'


def sha(data, algorithm='sha256'):
    return hashlib.new(algorithm, data).hexdigest()


def pointer_text(data, pad=0):
    text = POINTER_HEAD + b'oid sha256:%s\nsize %d\n' % (sha(data).encode(), len(data))
    return text + b'#' * (pad - len(text)) if pad else text


def git(*argv, cwd=None):
    return REAL_SUBPROCESS_RUN(['git', *map(str, argv)], cwd=cwd, env=GIT_ENV, check=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE).stdout.decode().strip()


def make_repo(path, files, tag=None, gitlink=None):
    """A one-commit repository on branch main. files: path -> bytes, or a str for a symlink target."""
    path.mkdir(parents=True)
    git('init', '-q', '-b', 'main', path)
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.symlink_to(content)
        else:
            target.write_bytes(content)
    git('-C', path, 'add', '-A')
    if gitlink:
        git('-C', path, 'update-index', '--add', '--cacheinfo', '160000,' + 'a' * 40 + ',' + gitlink)
    git('-C', path, 'commit', '-q', '-m', 'fixture')
    if tag:
        git('-C', path, 'tag', tag)
    return git('-C', path, 'rev-parse', 'HEAD')


def http_error(code, url='https://example.invalid/'):
    return urllib.error.HTTPError(url, code, 'status %d' % code, {}, None)


class Body(io.BytesIO):
    """A response that hands out at most `step` bytes per sized read, like a socket does."""
    step = 5
    sizes = []

    def read(self, size=-1):
        self.sizes.append(size)
        return super().read() if size is None or size < 0 else super().read(min(size, self.step))


class Net:
    """Replacement for urllib.request.urlopen. Unknown URLs answer 404."""

    def __init__(self):
        self.routes, self.calls, self.requests, self.timeouts = {}, [], [], []

    def __call__(self, request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        self.calls.append(url)
        self.requests.append(request)
        self.timeouts.append(timeout)
        if url not in self.routes:
            raise http_error(404, url)
        answer = self.routes[url]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if callable(answer):
            answer = answer()
        if isinstance(answer, BaseException):
            raise answer
        return Body(answer)


class Case(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='fetch-inputs-test-')).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.records, self.sleeps, self.logs, self.net, self.run_envs = [], [], [], Net(), []
        Body.sizes.clear()
        self.patch(F, 'RECORDS', self.records)
        self.clock = types.SimpleNamespace(sleep=self.sleeps.append, time=time.time, strftime=time.strftime)
        self.patch(F, 'time', self.clock)
        self.patch(F, 'log', lambda *parts: self.logs.append(parts))
        self.patch(F.urllib.request, 'urlopen', self.net)
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith('GIT_') and not k.lower().endswith('_proxy')}
        # The architecture is pinned to amd64 so that the fixtures give the same
        # answers on an arm64 host; the arm64 cases select it themselves.
        environment.update(GIT_ALLOW_PROTOCOL='file', HOME=str(self.tmp / 'home'), TAKX_PROTO_ROOT=str(self.tmp / 'env-root'),
                           TAKX_PROTO_ARCH='amd64')
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch(self, owner, name, value):
        patcher = mock.patch.object(owner, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def github(self, base):
        """Make https://github.com/<owner>/<repository>.git resolve to base/<owner>/<repository>.git."""
        os.environ.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0=f'url.file://{base}/.insteadOf',
                          GIT_CONFIG_VALUE_0='https://github.com/')

    def spy_run(self):
        """Record every F.run call as (argv, keyword arguments other than env and stdout)."""
        calls = []

        def spy(argv, **keywords):
            calls.append(([str(a) for a in argv], {k: v for k, v in keywords.items() if k not in ('env', 'stdout')}))
            self.run_envs.append(keywords.get('env'))
            return REAL_RUN(argv, **keywords)
        self.patch(F, 'run', spy)
        return calls

    def assert_hermetic_git(self, count=None):
        """Every recorded F.run call got the environment of git_env(), not the inherited one."""
        if count is not None:
            self.assertEqual(len(self.run_envs), count)
        self.assertTrue(self.run_envs)
        for env in self.run_envs:
            self.assertIsNotNone(env)
            self.assertEqual((env['GIT_CONFIG_GLOBAL'], env['GIT_CONFIG_SYSTEM'], env['GIT_LFS_SKIP_SMUDGE'],
                              env['GIT_TERMINAL_PROMPT']), ('/dev/null', '/dev/null', '1', '0'))

    def spy_download(self):
        """Record where download() is asked to write."""
        targets = []
        real = F.download

        def spy(url, target, *arguments, **keywords):
            targets.append(target)
            return real(url, target, *arguments, **keywords)
        self.patch(F, 'download', spy)
        return targets

    def names(self, directory):
        return sorted(p.relative_to(directory).as_posix() for p in directory.rglob('*') if not p.is_dir())


# --------------------------------------------------------------------------- small helpers

class Primitives(Case):
    def test_digest_reads_whole_file_in_blocks(self):
        data = b'ab' * (1 << 20) + b'c'
        path = self.tmp / 'blob'
        path.write_bytes(data)
        self.assertEqual(F.digest(path), sha(data))
        self.assertEqual(F.digest(path, 'sha512'), sha(data, 'sha512'))
        self.assertEqual(F.digest(path, 'sha1'), sha(data, 'sha1'))
        (self.tmp / 'empty').write_bytes(b'')
        self.assertEqual(F.digest(self.tmp / 'empty'), sha(b''))

    def test_log_prints_timestamp_and_parts_and_flushes(self):
        class Stream(io.StringIO):
            flushed = 0

            def flush(self):
                self.flushed += 1
        output = Stream()
        with contextlib.redirect_stdout(output):
            REAL_LOG('alpha', 7)
        self.assertRegex(output.getvalue(), r'\A\d\d:\d\d:\d\d alpha 7\n\Z')
        self.assertEqual(output.flushed, 1)

    def test_digest_never_reads_more_than_a_mebibyte_at_once(self):
        path = self.tmp / 'blob'
        path.write_bytes(b'x' * ((1 << 20) + 5))
        sizes = []
        real = open

        def spying_open(*arguments, **keywords):
            stream = real(*arguments, **keywords)
            read = stream.read
            wrapper = mock.Mock(wraps=stream)
            wrapper.read = lambda size=-1: sizes.append(size) or read(size)
            wrapper.__enter__ = lambda inner: wrapper
            wrapper.__exit__ = lambda inner, *a: stream.close()
            return wrapper
        with mock.patch('builtins.open', spying_open):
            value = F.digest(path)
        self.assertEqual(value, sha(b'x' * ((1 << 20) + 5)))
        self.assertEqual(sizes, [1 << 20] * 3)

    def test_record_defaults_to_unverified_and_reports_it(self):
        first = F.record(name='n', note='why')
        self.assertEqual(first, {'name': 'n', 'note': 'why', 'verified': False})
        self.assertIs(self.records[0], first)
        self.assertEqual(self.logs, [('NOT VERIFIED', 'n', 'why')])
        F.record(kind='k')
        self.assertEqual(self.logs[1], ('NOT VERIFIED', None, ''))
        second = F.record(name='ok', verified=True)
        self.assertEqual(len(self.logs), 2)
        self.assertEqual(self.records, [first, {'kind': 'k', 'verified': False}, second])
        self.assertIs(second['verified'], True)

    def test_run_captures_output_and_raises_on_failure(self):
        done = F.run(['sh', '-c', 'echo "$0"; pwd; echo err >&2', Path('a b')], cwd=self.tmp)
        self.assertEqual(done.stdout, f'a b\n{self.tmp}\n'.encode())
        self.assertEqual(done.stderr, b'err\n')
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            F.run(['sh', '-c', 'echo bad >&2; exit 3'])
        self.assertEqual((caught.exception.returncode, caught.exception.stderr), (3, b'bad\n'))
        with (self.tmp / 'out').open('wb') as stream:
            self.assertIsNone(F.run(['sh', '-c', 'echo "$MARK"'], env={'MARK': 'm', 'PATH': os.environ['PATH']}, stdout=stream).stdout)
        self.assertEqual((self.tmp / 'out').read_bytes(), b'm\n')

    def test_run_passes_limits_to_subprocess(self):
        fake = self.patch(F.subprocess, 'run', mock.Mock(return_value='done'))
        self.assertEqual(F.run(['a', 1]), 'done')
        fake.assert_called_once_with(['a', '1'], cwd=None, timeout=600, env=None, check=True, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        fake.reset_mock()
        F.run(['b'], cwd='c', timeout=5, env={'E': '1'}, stdout=7)
        fake.assert_called_once_with(['b'], cwd='c', timeout=5, env={'E': '1'}, check=True, stdout=7, stderr=subprocess.PIPE)

    def test_git_env_is_hermetic_copy(self):
        os.environ.update(MARK='kept', LC_ALL='de_DE', GIT_CONFIG_GLOBAL='/somewhere')
        before = dict(os.environ)
        env = F.git_env()
        self.assertEqual(dict(os.environ), before)
        self.assertEqual({k: v for k, v in env.items() if before.get(k) != v},
                         {'GIT_LFS_SKIP_SMUDGE': '1', 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_GLOBAL': '/dev/null',
                          'GIT_CONFIG_SYSTEM': '/dev/null', 'LC_ALL': 'C', 'TZ': 'UTC'})
        self.assertEqual(env['MARK'], 'kept')
        self.assertEqual(set(env) - set(before), {'GIT_LFS_SKIP_SMUDGE', 'GIT_TERMINAL_PROMPT', 'GIT_CONFIG_SYSTEM', 'TZ'}
                         - set(before))

    def test_github_archive_parts(self):
        cases = {
            'https://github.com/o/r/archive/v1.2.tar.gz': ('o', 'r', 'v1.2'),
            'https://github.com/o/r/archive/refs/tags/v1.2.tar.gz': ('o', 'r', 'v1.2'),
            'https://github.com/o/r/archive/' + 'a' * 40 + '.tar.gz': ('o', 'r', 'a' * 40),
            'https://github.com/o/r/archive/feature/x.tar.gz': ('o', 'r', 'feature/x'),
            'https://codeload.github.com/o/r/tar.gz/refs/tags/v1.7.0': ('o', 'r', 'v1.7.0'),
            'https://codeload.github.com/o/r/tar.gz/' + 'b' * 40: ('o', 'r', 'b' * 40),
            'https://github.com/o/r/archive/v1.2.tar.gz?x=1': None,
            'https://github.com/o/r/archive/v1.2.zip': None,
            'https://github.com/o/r/releases/download/v1/r-1.tar.gz': None,
            'https://githubXcom/o/r/archive/v1.2.tar.gz': None,
            'https://github.com/o/r/archive/v1Xtar.gz': None,
            'https://github.com/o/r/archive/v1.tarXgz': None,
            'https://codeloadXgithub.com/o/r/tar.gz/v1': None,
            'https://codeload.githubXcom/o/r/tar.gz/v1': None,
            'https://codeload.github.com/o/r/tarXgz/v1': None,
            'https://codeload.github.com/o/r/zip/v1': None,
            'http://github.com/o/r/archive/v1.2.tar.gz': None,
            'x https://github.com/o/r/archive/v1.2.tar.gz': None,
            'https://github.com/o/archive/v1.2.tar.gz': None,
            'https://example.invalid/o/r/archive/v1.2.tar.gz': None,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(F.github_archive_parts(url), expected)

    def test_default_prefix_and_method(self):
        self.assertEqual(F.default_prefix('r', 'v1.7.0'), 'r-1.7.0/')
        self.assertEqual(F.default_prefix('r', 'version-2'), 'r-version-2/')
        self.assertEqual(F.default_prefix('r', 'vx'), 'r-vx/')
        self.assertEqual(F.default_prefix('r', 'xv1'), 'r-xv1/')
        self.assertEqual(F.default_prefix('r', '1.0'), 'r-1.0/')
        self.assertEqual(F.default_prefix('r', 'c' * 40), 'r-' + 'c' * 40 + '/')
        self.assertEqual(F.method_of('https://media.githubusercontent.com/media/o/r/x'), 'lfs')
        self.assertEqual(F.method_of('https://media.githubusercontent.com/other'), 'direct')
        self.assertEqual(F.method_of('x https://media.githubusercontent.com/media/o/r/x'), 'direct')
        self.assertEqual(F.method_of('https://codeload.github.com/o/r/tar.gz/v1'), 'git-archive')
        self.assertEqual(F.method_of('https://github.com/o/r/archive/v1.tar.gz'), 'git-archive')
        self.assertEqual(F.method_of('https://repo1.maven.org/maven2/a/b/1/b-1.jar'), 'direct')

    def test_maven_candidates(self):
        base = 'https://repo1.maven.org/maven2/'
        self.assertEqual(F.maven_candidates('org.a.b--art-1.2.jar'), [base + 'org/a/b/art/1.2/art-1.2.jar'])
        self.assertEqual(F.maven_candidates('g--a-b-2.0-linux-x86_64.jar'), [
            base + 'g/a-b/2.0-linux-x86_64/a-b-2.0-linux-x86_64.jar',
            base + 'g/a-b/2.0/a-b-2.0-linux-x86_64.jar',
            base + 'g/a-b/2.0-linux/a-b-2.0-linux-x86_64.jar',
        ])
        self.assertEqual(F.maven_candidates('g.h--a-1-2-3.jar'), [
            base + 'g/h/a/1-2-3/a-1-2-3.jar', base + 'g/h/a/1/a-1-2-3.jar', base + 'g/h/a/1-2/a-1-2-3.jar',
            base + 'g/h/a-1/2-3/a-1-2-3.jar', base + 'g/h/a-1/2/a-1-2-3.jar',
            base + 'g/h/a-1-2/3/a-1-2-3.jar',
        ])
        self.assertEqual(F.maven_candidates('g--x--y-1.jar'), [base + 'g/x--y/1/x--y-1.jar'])
        self.assertEqual(F.maven_candidates('g--noversion.jar'), [])
        with self.assertRaises(ValueError):
            F.maven_candidates('nogroup-1.jar')

    def test_gradle_versions(self):
        (self.tmp / 'gradle').mkdir()
        (self.tmp / 'gradle/versions.gradle').write_text(
            "ext {\n    alphaVersion = '1.2.3'\n\tbeta_2='4'\n    quoted = \"5\"\n  // gammaVersion = '9'\n"
            "    delta = '6' // note\nepsilon   =   '7-x'\n    empty = ''\n}\n")
        self.assertEqual(F.gradle_versions(self.tmp), {'alphaVersion': '1.2.3', 'beta_2': '4', 'delta': '6', 'epsilon': '7-x'})


# --------------------------------------------------------------------------- download

class Download(Case):
    URL = 'https://example.invalid/file'

    def setUp(self):
        super().setUp()
        self.target = self.tmp / 'deep/dir/out.bin'

    def test_streams_to_partial_then_renames(self):
        seen = {}

        def answer():
            seen['during'] = sorted(p.name for p in self.target.parent.iterdir())
            return b'0123456789abc'
        self.net.routes[self.URL] = answer
        self.assertIsNone(F.download(self.URL, self.target))
        self.assertEqual(self.target.read_bytes(), b'0123456789abc')
        self.assertEqual(len(seen['during']), 1)
        self.assertRegex(seen['during'][0], r'\A\.partial-')
        self.assertEqual(self.names(self.tmp), ['deep/dir/out.bin'])
        self.assertEqual(self.net.calls, [self.URL])
        self.assertEqual(self.net.timeouts, [60])
        self.assertEqual(self.net.requests[0].get_header('User-agent'), 'takx-desktop-prototype-fetch/1')
        self.assertEqual(self.sleeps, [])
        self.assertEqual(Body.sizes, [1 << 20] * 4, 'the response is read in bounded blocks until it is empty')

    def test_limit_is_inclusive_and_counts_all_blocks(self):
        self.net.routes[self.URL] = [b'0123456789abc']
        self.assertIsNone(F.download(self.URL, self.target, limit=13))
        self.assertEqual(self.target.read_bytes(), b'0123456789abc')
        self.net.routes[self.URL] = [b'']
        self.assertIsNone(F.download(self.URL, self.target, limit=0))
        self.assertEqual(self.target.read_bytes(), b'')

    def test_over_limit_never_reaches_target(self):
        for size, limit in ((14, 13), (6, 5), (1, 0), (11, 9)):
            with self.subTest(size=size, limit=limit):
                self.net.calls.clear()
                self.sleeps.clear()
                self.net.routes[self.URL] = [b'x' * size]
                self.assertEqual(F.download(self.URL, self.target, limit=limit), 'ValueError: download exceeds locked size')
                self.assertEqual(self.names(self.tmp), [])
                self.assertEqual(len(self.net.calls), 4)
                self.assertEqual(self.sleeps, [3, 6, 9, 12])

    def test_failed_download_keeps_previous_target(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'previous')
        self.net.routes[self.URL] = [b'longer than the limit']
        self.assertIsNotNone(F.download(self.URL, self.target, limit=3, attempts=1))
        self.assertEqual(self.target.read_bytes(), b'previous')
        self.assertEqual(self.names(self.tmp), ['deep/dir/out.bin'])
        self.net.routes[self.URL] = [b'new']
        self.assertIsNone(F.download(self.URL, self.target, limit=3, attempts=1))
        self.assertEqual(self.target.read_bytes(), b'new')

    def test_permanent_http_status_is_not_retried(self):
        for code in (401, 403, 404, 410):
            with self.subTest(code=code):
                self.net.calls.clear()
                self.net.routes[self.URL] = [http_error(code)]
                self.assertEqual(F.download(self.URL, self.target), f'HTTPError: HTTP Error {code}: status {code}')
                self.assertEqual(len(self.net.calls), 1)
                self.assertEqual(self.sleeps, [])
                self.assertEqual(self.names(self.tmp), [])

    def test_transient_failures_are_retried(self):
        failures = (http_error(400), http_error(402), http_error(405), http_error(409), http_error(411), http_error(429),
                    http_error(500), http_error(503), urllib.error.URLError('boom'), ConnectionResetError('reset'),
                    http.client.IncompleteRead(b'xy', 5))
        for failure in failures:
            with self.subTest(failure=repr(failure)):
                self.net.calls.clear()
                self.sleeps.clear()
                self.net.routes[self.URL] = [failure]
                error = F.download(self.URL, self.target)
                self.assertEqual(error, f'{type(failure).__name__}: {failure}')
                self.assertEqual(len(self.net.calls), 4)
                self.assertEqual(self.sleeps, [3, 6, 9, 12])
                self.assertEqual(self.names(self.tmp), [])
        self.net.calls.clear()
        self.sleeps.clear()
        self.assertIsNotNone(F.download(self.URL, self.target, attempts=2))
        self.assertEqual((len(self.net.calls), self.sleeps), (2, [3, 6]))

    def test_success_after_failures_returns_none(self):
        self.net.routes[self.URL] = [urllib.error.URLError('one'), http_error(502), b'payload']
        self.assertIsNone(F.download(self.URL, self.target))
        self.assertEqual(self.target.read_bytes(), b'payload')
        self.assertEqual(self.sleeps, [3, 6])
        self.assertEqual(self.names(self.tmp), ['deep/dir/out.bin'])

    def test_unexpected_exception_still_removes_partial(self):
        self.net.routes[self.URL] = [KeyError('not a transport error')]
        with self.assertRaises(KeyError):
            F.download(self.URL, self.target)
        self.assertEqual(self.names(self.tmp), [])


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    hits = []

    def log_message(self, *arguments):
        pass

    def do_GET(self):
        self.hits.append(self.path)
        self.close_connection = True
        if self.path == '/ok':
            self.send_response(200)
            self.send_header('Content-Length', '7')
            self.end_headers()
            self.wfile.write(b'content')
        elif self.path == '/moved':
            self.send_response(302)
            self.send_header('Location', '/ok')
            self.send_header('Content-Length', '0')
            self.end_headers()
        elif self.path == '/loop':
            self.send_response(302)
            self.send_header('Location', '/loop')
            self.send_header('Content-Length', '0')
            self.end_headers()
        elif self.path == '/forbidden':
            self.send_response(403)
            self.send_header('Content-Length', '6')
            self.end_headers()
            self.wfile.write(b'denied')
        elif self.path == '/short':
            self.send_response(200)
            self.send_header('Content-Length', '100')
            self.end_headers()
            self.wfile.write(b'only ten b')
        elif self.path == '/chunked-cut':
            self.send_response(200)
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            self.wfile.write(b'a\r\nonly ten b\r\n64\r\ncut')
        else:
            self.send_response(500)
            self.send_header('Content-Length', '0')
            self.end_headers()


class DownloadOverLoopbackHttp(Case):
    """download() against a real HTTP exchange on 127.0.0.1 (no proxy, nothing leaves the machine)."""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        super().setUp()
        Handler.hits.clear()
        self.patch(F.urllib.request, 'urlopen', urllib.request.build_opener(urllib.request.ProxyHandler({})).open)
        self.target = self.tmp / 'out'

    def test_redirect_is_followed_to_the_content(self):
        self.assertIsNone(F.download(self.base + '/moved', self.target))
        self.assertEqual(self.target.read_bytes(), b'content')
        self.assertEqual(Handler.hits, ['/moved', '/ok'])

    def test_redirect_loop_fails_without_output(self):
        error = F.download(self.base + '/loop', self.target, attempts=1)
        self.assertRegex(error, r'\AHTTPError: HTTP Error 302')
        self.assertEqual(self.names(self.tmp), [])

    def test_forbidden_fails_once_without_output(self):
        self.assertEqual(F.download(self.base + '/forbidden', self.target), 'HTTPError: HTTP Error 403: Forbidden')
        self.assertEqual(Handler.hits, ['/forbidden'])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self.names(self.tmp), [])

    def test_server_error_is_retried(self):
        self.assertRegex(F.download(self.base + '/broken', self.target), r'\AHTTPError: HTTP Error 500')
        self.assertEqual(Handler.hits, ['/broken'] * 4)
        self.assertEqual(self.names(self.tmp), [])

    def test_truncated_body_is_an_error_or_short_never_padded(self):
        error = F.download(self.base + '/short', self.target, attempts=1)
        if error is None:
            self.assertEqual(self.target.read_bytes(), b'only ten b')
        else:
            self.assertEqual(self.names(self.tmp), [])

    def test_truncated_chunked_body_is_reported_not_raised(self):
        error = F.download(self.base + '/chunked-cut', self.target)
        self.assertRegex(error, r'\AIncompleteRead: ')
        self.assertEqual(Handler.hits, ['/chunked-cut'] * 4)
        self.assertEqual(self.names(self.tmp), [])


# --------------------------------------------------------------------------- link

class Link(Case):
    def setUp(self):
        super().setUp()
        self.source = self.tmp / 'source'
        self.source.write_bytes(b'payload')
        self.target = self.tmp / 'a/b/target'

    def assert_hard_link(self):
        self.assertFalse(self.target.is_symlink())
        self.assertTrue(os.path.samefile(self.source, self.target))
        self.assertEqual(self.target.read_bytes(), b'payload')

    def test_creates_hard_link_and_parents(self):
        F.link(self.source, self.target)
        self.assert_hard_link()
        self.assertEqual(self.source.stat().st_nlink, 2)

    def test_same_file_is_left_alone(self):
        F.link(self.source, self.target)
        unlink = self.patch(F.os, 'link', mock.Mock(side_effect=AssertionError('must not relink')))
        F.link(self.source, self.target)
        unlink.assert_not_called()
        self.assert_hard_link()

    def test_replaces_other_file_symlink_and_dangling_symlink(self):
        self.target.parent.mkdir(parents=True)
        other = self.tmp / 'other'
        for kind in ('file', 'symlink-to-other', 'symlink-to-source', 'dangling'):
            with self.subTest(kind=kind):
                other.write_bytes(b'other')
                if self.target.is_symlink() or self.target.exists():
                    self.target.unlink()
                if kind == 'file':
                    self.target.write_bytes(b'stale')
                else:
                    self.target.symlink_to({'symlink-to-other': other, 'symlink-to-source': self.source,
                                            'dangling': self.tmp / 'missing'}[kind])
                F.link(self.source, self.target)
                self.assert_hard_link()
                self.assertEqual(other.read_bytes(), b'other')

    def test_copies_when_hard_link_is_refused(self):
        self.patch(F.os, 'link', mock.Mock(side_effect=OSError('cross-device')))
        F.link(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), b'payload')
        self.assertFalse(os.path.samefile(self.source, self.target))
        self.assertFalse(self.target.is_symlink())


# --------------------------------------------------------------------------- git fetch / archive

class Failing:
    """Stand-in for F.run driven by a script of results; records the calls."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, argv, **keywords):
        self.calls.append(([str(a) for a in argv], keywords))
        if argv[1] == 'init' or 'fetch' not in argv:
            return mock.Mock(stdout=b'  resolved-commit\n')
        result = self.script.pop(0)
        if result is not None:
            raise subprocess.CalledProcessError(128, argv, b'', result)
        return mock.Mock(stdout=b'')


class GitFetch(Case):
    REPOSITORY = 'https://github.com/o/r.git'

    def fetches(self, runner):
        return [c[0][-1] for c in runner.calls if 'fetch' in c[0]]

    def test_commit_reference_is_fetched_directly(self):
        runner = self.patch(F, 'run', Failing([None]))
        self.assertEqual(F.git_fetch('work', self.REPOSITORY, 'a' * 40), 'resolved-commit')
        env = runner.calls[0][1]['env']
        self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')
        self.assertEqual(env['GIT_CONFIG_GLOBAL'], '/dev/null')
        self.assertEqual(runner.calls, [
            (['git', 'init', '-q', '--bare', 'work'], {'env': env}),
            (['git', '-C', 'work', 'fetch', '-q', '--depth', '1', '--no-tags', self.REPOSITORY, 'a' * 40],
             {'env': env, 'timeout': 900}),
            (['git', '-C', 'work', 'rev-parse', 'FETCH_HEAD^{commit}'], {'env': env}),
        ])
        self.assertEqual(self.sleeps, [])

    def test_names_try_tag_then_branch(self):
        for reference in ('v1.2', 'A' * 40, 'a' * 39, 'a' * 41, 'g' * 40):
            with self.subTest(reference=reference):
                runner = self.patch(F, 'run', Failing([b"fatal: couldn't find remote ref refs/tags/x\n", None]))
                self.assertEqual(F.git_fetch('work', self.REPOSITORY, reference), 'resolved-commit')
                self.assertEqual(self.fetches(runner), ['refs/tags/' + reference, 'refs/heads/' + reference])
                self.assertEqual(self.sleeps, [])

    def test_transient_failures_are_retried_with_backoff(self):
        runner = self.patch(F, 'run', Failing([b'timeout', b'timeout', None]))
        self.assertEqual(F.git_fetch('work', self.REPOSITORY, 'v1'), 'resolved-commit')
        self.assertEqual(self.fetches(runner), ['refs/tags/v1'] * 3)
        self.assertEqual(self.sleeps, [5, 10])

    def test_exhausted_attempts_raise_with_stderr_tail(self):
        noise = b'  ' + b'x' * 400 + b'END\xff \n'
        runner = self.patch(F, 'run', Failing([b"couldn't find remote ref", noise, noise, noise]))
        with self.assertRaises(RuntimeError) as caught:
            F.git_fetch('work', self.REPOSITORY, 'v1')
        self.assertEqual(str(caught.exception), 'git fetch failed: ' + 'x' * 296 + 'END\ufffd')
        self.assertEqual(self.fetches(runner), ['refs/tags/v1'] + ['refs/heads/v1'] * 3)
        self.assertEqual(self.sleeps, [5, 10, 15])
        runner = self.patch(F, 'run', Failing([b'denied'] * 3))
        with self.assertRaisesRegex(RuntimeError, r'\Agit fetch failed: denied\Z'):
            F.git_fetch('work', self.REPOSITORY, 'b' * 40)
        self.assertEqual(self.fetches(runner), ['b' * 40] * 3)


class GitArchive(Case):
    BIG = b'real bytes of the large object\n' * 3
    SPACED = b'second object'
    LAST = b'third object, listed after everything that is skipped'

    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix='fetch-inputs-git-')).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.base, True)
        cls.files = {'README.md': b'hello\n', 'src/a.c': b'int a;\n', 'link': 'README.md'}
        cls.commit = make_repo(cls.base / 'owner/plain.git', cls.files, tag='v1.2.0')
        git('-C', cls.base / 'owner/plain.git', 'branch', 'release')
        cls.lfs_files = {
            '.gitattributes': b'*.bin filter=lfs\n',
            'big.bin': pointer_text(cls.BIG, pad=300),
            'dir/a b.bin': pointer_text(cls.SPACED),
            'plain.txt': b'plain\n',
            'looks-like-pointer.txt': pointer_text(b'never fetched', pad=301),
            'small-other.txt': b'oid sha256:' + b'0' * 64 + b'\nsize 5\n',
            'headed-only.txt': POINTER_HEAD + b'oid sha256:short\n',
            'tab\tin name.txt': b'tab\n',
            'z-last.bin': pointer_text(cls.LAST),
        }
        cls.lfs_commit = make_repo(cls.base / 'owner/withlfs.git', cls.lfs_files, gitlink='vendored')

    def setUp(self):
        super().setUp()
        self.github(self.base)
        self.out = self.tmp / 'out'
        self.out.mkdir()
        self.target = self.out / 'archive.tar.gz'

    def members(self, path):
        with tarfile.open(path, 'r:gz') as archive:
            return {m.name: (archive.extractfile(m).read() if m.isfile() else m.linkname if m.issym() else None)
                    for m in archive}

    def media(self, name):
        return f'https://media.githubusercontent.com/media/owner/withlfs/{self.lfs_commit}/{name}'

    def test_reconstructs_prefixed_gzip_without_timestamp(self):
        calls = self.spy_run()
        gzip_calls = []

        def spy(argv, **keywords):
            gzip_calls.append((argv, {k: v for k, v in keywords.items() if k not in ('stdin', 'stdout', 'env')}))
            return REAL_SUBPROCESS_RUN(argv, **keywords)
        self.patch(F.subprocess, 'run', spy)
        self.assertEqual(F.git_archive('owner', 'plain', self.commit, 'plain-1.2.0/', self.target), (self.commit, 0))
        raw = self.target.read_bytes()
        self.assertEqual(raw[:2], b'\x1f\x8b')
        self.assertEqual(raw[3] & 0x08, 0, 'gzip header must not carry a file name')
        self.assertEqual(raw[4:8], b'\0\0\0\0', 'gzip header must not carry a timestamp')
        self.assertEqual(self.members(self.target), {
            'plain-1.2.0': None, 'plain-1.2.0/README.md': b'hello\n', 'plain-1.2.0/src': None,
            'plain-1.2.0/src/a.c': b'int a;\n', 'plain-1.2.0/link': 'README.md'})
        self.assertEqual(self.names(self.out), ['archive.tar.gz'])
        self.assertEqual(list(self.out.iterdir()), [self.target])
        work = calls[0][0][-1]
        self.assertRegex(work, '\\A' + str(self.out) + r'/git-archive-[^/]+\Z')
        self.assertEqual(calls[-1], (['git', '-C', work, 'archive', '--format=tar', '--prefix=plain-1.2.0/', self.commit],
                                     {'timeout': 1800}))
        self.assertIn((['gzip', '-n'], {'check': True, 'timeout': 1800}), gzip_calls)
        first = raw
        self.assertEqual(F.git_archive('owner', 'plain', 'v1.2.0', 'plain-1.2.0/', self.target), (self.commit, 0))
        self.assertEqual(self.target.read_bytes(), first)
        self.assertEqual(F.git_archive('owner', 'plain', 'release', 'other/', self.target), (self.commit, 0))
        self.assertEqual(set(self.members(self.target)), {'other', 'other/README.md', 'other/src', 'other/src/a.c', 'other/link'})

    def test_failure_leaves_no_archive_and_keeps_previous_one(self):
        self.target.write_bytes(b'previous')
        with self.assertRaisesRegex(RuntimeError, 'git fetch failed'):
            F.git_archive('owner', 'missing', 'a' * 40, 'p/', self.target)
        with self.assertRaisesRegex(RuntimeError, "couldn't find remote ref"):
            F.git_archive('owner', 'plain', 'no-such-tag', 'p/', self.target)
        self.assertEqual(self.target.read_bytes(), b'previous')
        self.assertEqual(self.names(self.out), ['archive.tar.gz'])
        self.assertEqual(list(self.out.iterdir()), [self.target])

    def test_gzip_failure_is_not_promoted(self):
        real = REAL_SUBPROCESS_RUN

        def failing(argv, **keywords):
            if argv == ['gzip', '-n']:
                keywords['stdout'].write(b'partial output')
                raise subprocess.CalledProcessError(1, argv)
            return real(argv, **keywords)
        self.patch(F.subprocess, 'run', failing)
        with self.assertRaises(subprocess.CalledProcessError):
            F.git_archive('owner', 'plain', self.commit, 'p/', self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.names(self.out), ['archive.tar.gz.partial'])

    def test_without_lfs_the_pointers_stay(self):
        self.assertEqual(F.git_archive('owner', 'withlfs', self.lfs_commit, 'w/', self.target), (self.lfs_commit, 0))
        members = self.members(self.target)
        self.assertEqual(members['w/big.bin'], self.lfs_files['big.bin'])
        self.assertEqual(self.net.calls, [])

    def test_include_lfs_replaces_verified_pointers_only(self):
        self.net.routes[self.media('big.bin')] = self.BIG
        self.net.routes[self.media('dir/a%20b.bin')] = self.SPACED
        self.net.routes[self.media('z-last.bin')] = self.LAST
        calls = self.spy_run()
        self.assertEqual(F.git_archive('owner', 'withlfs', self.lfs_commit, 'w/', self.target, include_lfs=True),
                         (self.lfs_commit, 3))
        self.assert_hermetic_git()
        members = self.members(self.target)
        self.assertEqual(members['w/big.bin'], self.BIG)
        self.assertEqual(members['w/dir/a b.bin'], self.SPACED)
        self.assertEqual(members['w/z-last.bin'], self.LAST)
        for name in ('plain.txt', 'looks-like-pointer.txt', 'small-other.txt', 'headed-only.txt', '.gitattributes',
                     'tab\tin name.txt'):
            self.assertEqual(members['w/' + name], self.lfs_files[name])
        self.assertEqual(self.net.calls, [self.media('big.bin'), self.media('dir/a%20b.bin'), self.media('z-last.bin')])
        self.assertEqual(self.names(self.out), ['archive.tar.gz'])
        work = calls[0][0][-1]
        self.assertEqual(calls[3], (['git', '-C', work, 'ls-tree', '-r', '-l', '-z', self.lfs_commit], {}))
        self.assertEqual(calls[-1], ([
            'git', '-C', work, '-c', f'filter.lfs.smudge={sys.executable} -I {work}/lfs-smudge.py {work}/lfs-store',
            '-c', 'filter.lfs.required=true', 'archive', '--format=tar', '--prefix=w/', self.lfs_commit], {'timeout': 1800}))

    def test_include_lfs_refuses_object_that_differs_from_pointer(self):
        self.net.routes[self.media('dir/a%20b.bin')] = self.SPACED
        self.net.routes[self.media('z-last.bin')] = self.LAST
        for label, body in (('wrong bytes', b'x' * len(self.BIG)), ('too long', self.BIG + b'x'),
                            ('truncated', self.BIG[:-1]), ('missing', None)):
            with self.subTest(label):
                if body is None:
                    del self.net.routes[self.media('big.bin')]
                else:
                    self.net.routes[self.media('big.bin')] = body
                with self.assertRaisesRegex(
                        RuntimeError, r'\AGit LFS object for big\.bin unavailable or differs from its pointer: '
                        + {'wrong bytes': 'None', 'truncated': 'None', 'too long': 'ValueError: download exceeds locked size',
                           'missing': 'HTTPError: HTTP Error 404: status 404'}[label] + r'\Z'):
                    F.git_archive('owner', 'withlfs', self.lfs_commit, 'w/', self.target, include_lfs=True)
                self.assertEqual(self.names(self.out), [])

    def test_smudge_helper_passes_other_content_through(self):
        store = self.tmp / 'store'
        store.mkdir()
        (store / sha(b'object')).write_bytes(b'object')
        helper = self.tmp / 'smudge.py'
        helper.write_text(F.SMUDGE)

        def smudge(data):
            return REAL_SUBPROCESS_RUN([sys.executable, '-I', str(helper), str(store)], input=data, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
        self.assertEqual(smudge(pointer_text(b'object')).stdout, b'object')
        self.assertEqual(smudge(b'not a pointer\n').stdout, b'not a pointer\n')
        self.assertEqual(smudge(b'oid sha256:' + sha(b'object').encode() + b'\nsize 6').stdout[:4], b'oid ')
        missing = smudge(pointer_text(b'absent'))
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(missing.stdout, b'')



# --------------------------------------------------------------------------- lock walk

class LockWalk(Case):
    A, B, C, D, E, G, H = ('a' * 64, 'b' * 64, 'c' * 64, 'd' * 64, 'e' * 64, '9' * 64, '0' * 64)

    def setUp(self):
        super().setUp()
        self.takx = self.tmp / 'repo/takx'
        self.takx.mkdir(parents=True)
        self.patch(F, 'TAKX', self.takx)

    def write(self, relative, data):
        path = self.takx / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def test_lock_files_selection_and_order(self):
        for relative in ('sources.lock.json', 'zeta/inputs.lock.json', 'alpha/inputs.lock.json', 'alpha/data/deep.lock.json',
                         'alpha/a/b/too-deep.lock.json', 'desktop-prototype/own.lock.json', 'alpha/desktop-prototype/x.lock.json',
                         'native-cesium/metadata.lock.json', 'native-cesium/inputs.lock.json', 'alpha/plain.json',
                         'alpha/lock.json', 'other/metadata.lock.json'):
            self.write(relative, {})
        self.assertEqual([p.relative_to(self.takx).as_posix() for p in F.lock_files()], [
            'alpha/data/deep.lock.json', 'alpha/inputs.lock.json', 'native-cesium/inputs.lock.json',
            'other/metadata.lock.json', 'sources.lock.json', 'zeta/inputs.lock.json'])

    def test_entry_name_precedence(self):
        url = 'https://example.invalid/dir/from%20url.bin?query=1'
        archive = 'https://codeload.github.com/o/repo/tar.gz/refs/tags/feature/v1'
        cases = [
            (('key.jar', {'file': 'sub/dir/picked.txt', 'name': 'n.jar'}, url), 'picked.txt'),
            (('key.jar', {'file': 7, 'name': 'n.zip'}, url), 'key.jar'),
            (('dir/key.tar.gz', {'name': 'n.zip'}, url), 'key.tar.gz'),
            (('key', {'name': 'sub/n.zip'}, url), 'n.zip'),
            ((None, {'name': 'n.zip'}, url), 'n.zip'),
            ((None, {'name': 5}, url), 'from url.bin'),
            (('key.jarx', {'name': 'zip'}, url), 'from url.bin'),
            (('keyjar', {}, url), 'from url.bin'),
            (('key', {}, archive), 'repo-feature_v1.tar.gz'),
            (('key', {}, 'https://github.com/o/repo/archive/' + 'a' * 40 + '.tar.gz'), 'repo-' + 'a' * 40 + '.tar.gz'),
            (('key.h', {}, archive), 'key.h'),
            (('key', {}, 'https://example.invalid'), sha(b'https://example.invalid')),
            (('key', {}, 'https://example.invalid/'), sha(b'https://example.invalid/')),
        ]
        for extension in ('jar', 'tar.gz', 'tgz', 'tar.xz', 'tar.bz2', 'zip', 'whl', 'deb', 'h', 'hpp', 'java', 'c', 'cc',
                          'cpp', 'txt', 'md', 'xsd', 'json'):
            cases.append((('k.' + extension, {}, url), 'k.' + extension))
            cases.append((('k.' + extension + '.sig', {}, url), 'from url.bin'))
            cases.append((('k' + extension, {}, url), 'from url.bin'))
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(F.entry_name(*arguments), expected)

    def test_collects_every_pinned_url_once_per_digest(self):
        self.write('sources.lock.json', {
            'atak_source': {'revision': 'r'},
            'archives': [{'path': 'p/zlib.tar.gz', 'sha256': self.A, 'bytes': 11, 'url': 'https://media.example/zlib.tar.gz'},
                         {'path': 'p/other', 'sha256': self.B, 'url': 'https://media.example/other.bin'}]})
        self.write('ui-java/inputs.lock.json', {
            'sources': {'docking.tar.gz': {'url': 'https://codeload.github.com/o/Docking/tar.gz/' + 'f' * 40, 'sha256': self.C,
                                           'bytes': 5, 'prefix': 'Docking-x/'}},
            'jars': {'nested': [[{'file': 'lib/ant.jar', 'url': 'https://repo.example/ant-1.jar', 'sha256': self.D, 'bytes': 3,
                                  'inner': {'url': 'https://repo.example/inner.pom', 'sha256': self.E}}]]},
            'again': {'zlib-copy.tar.gz': {'url': 'https://mirror.example/zlib', 'sha256': self.A}}})
        self.write('alpha/data/deep.lock.json', {'x': {'url': 'https://deep.example/d.txt', 'sha256': self.B, 'bytes': 9},
                                                 'raw': {'url': 'https://raw.githubusercontent.com/o/r/c/file.h', 'sha256': self.G}})
        self.write('native-cesium/inputs.lock.json', {
            'ports': [{'url': 'https://raw.githubusercontent.com/o/r/c/file.cmake', 'sha256': self.G},
                      {'url': 'https://mirror.example/raw.githubusercontent.com/o/r/c/other.cmake', 'sha256': '7' * 64},
                      {'url': 'https://cesium.example/kept.tar.gz', 'sha256': self.H}]})
        self.write('native-cesium/metadata.lock.json', {'x': {'url': 'https://skipped.example/x', 'sha256': '1' * 64}})
        self.write('desktop-prototype/own.lock.json', {'x': {'url': 'https://own.example/x', 'sha256': '2' * 64}})
        self.write('alpha/a/b/too-deep.lock.json', {'x': {'url': 'https://deep.example/x', 'sha256': '3' * 64}})
        self.write('ignored/inputs.lock.json', {
            'http': {'url': 'http://plain.example/x', 'sha256': '4' * 64},
            'prefixed': {'url': 'x https://plain.example/x', 'sha256': '4' * 64},
            'upper': {'url': 'https://plain.example/x', 'sha256': 'A' * 64},
            'short': {'url': 'https://plain.example/x', 'sha256': '5' * 63},
            'long': {'url': 'https://plain.example/x', 'sha256': '5' * 65},
            'number': {'url': 'https://plain.example/x', 'sha256': 5},
            'no-url': {'sha256': '6' * 64}, 'url-list': {'url': ['https://plain.example/x'], 'sha256': '6' * 64},
            'no-sha': {'url': 'https://plain.example/x'}, 'scalar': 'https://plain.example/x', 'none': None})
        found = F.collect_lock_entries()
        self.assertEqual(found, {
            self.B: {'sha256': self.B, 'url': 'https://deep.example/d.txt', 'bytes': 9, 'consumers': [
                {'recipe': 'alpha', 'lock': 'takx/alpha/data/deep.lock.json', 'name': 'd.txt', 'prefix': None},
                {'recipe': 'commoncommo', 'lock': 'takx/sources.lock.json', 'name': 'other.bin', 'prefix': None}]},
            self.G: {'sha256': self.G, 'url': 'https://raw.githubusercontent.com/o/r/c/file.h', 'bytes': None,
                     'consumers': [{'recipe': 'alpha', 'lock': 'takx/alpha/data/deep.lock.json', 'name': 'file.h',
                                    'prefix': None}]},
            self.H: {'sha256': self.H, 'url': 'https://cesium.example/kept.tar.gz', 'bytes': None, 'consumers': [
                {'recipe': 'native-cesium', 'lock': 'takx/native-cesium/inputs.lock.json', 'name': 'kept.tar.gz', 'prefix': None}]},
            self.A: {'sha256': self.A, 'url': 'https://media.example/zlib.tar.gz', 'bytes': 11, 'consumers': [
                {'recipe': 'commoncommo', 'lock': 'takx/sources.lock.json', 'name': 'zlib.tar.gz', 'prefix': None},
                {'recipe': 'ui-java', 'lock': 'takx/ui-java/inputs.lock.json', 'name': 'zlib-copy.tar.gz', 'prefix': None}]},
            self.C: {'sha256': self.C, 'url': 'https://codeload.github.com/o/Docking/tar.gz/' + 'f' * 40, 'bytes': 5,
                     'consumers': [{'recipe': 'ui-java', 'lock': 'takx/ui-java/inputs.lock.json', 'name': 'docking.tar.gz',
                                    'prefix': 'Docking-x/'}]},
            self.D: {'sha256': self.D, 'url': 'https://repo.example/ant-1.jar', 'bytes': 3, 'consumers': [
                {'recipe': 'ui-java', 'lock': 'takx/ui-java/inputs.lock.json', 'name': 'ant.jar', 'prefix': None}]},
            self.E: {'sha256': self.E, 'url': 'https://repo.example/inner.pom', 'bytes': None, 'consumers': [
                {'recipe': 'ui-java', 'lock': 'takx/ui-java/inputs.lock.json', 'name': 'inner.pom', 'prefix': None}]},
        })
        self.assertEqual(list(found), [self.B, self.G, self.H, self.A, self.C, self.D, self.E])

    def test_list_children_inherit_the_key_of_the_list(self):
        self.write('r/inputs.lock.json', {'bundle.zip': [{'url': 'https://e.example/one', 'sha256': self.A},
                                                         [{'url': 'https://e.example/two', 'sha256': self.B}]]})
        found = F.collect_lock_entries()
        self.assertEqual([c['name'] for item in found.values() for c in item['consumers']], ['bundle.zip', 'bundle.zip'])

    def test_non_integer_bytes_never_replace_a_known_size(self):
        self.write('r/a.lock.json', {'x': {'url': 'https://e.example/x', 'sha256': self.A, 'bytes': 4}})
        self.write('r/b.lock.json', {'x': {'url': 'https://e.example/y', 'sha256': self.A, 'bytes': '5'},
                                     'y': {'url': 'https://e.example/z', 'sha256': self.A}})
        self.assertEqual(F.collect_lock_entries()[self.A]['bytes'], 4)
        self.write('r/a.lock.json', {'x': {'url': 'https://e.example/x', 'sha256': self.A, 'bytes': '4'}})
        self.write('r/b.lock.json', {'y': {'url': 'https://e.example/z', 'sha256': self.A}})
        self.assertEqual(F.collect_lock_entries()[self.A]['bytes'], '4')

    @unittest.skipUnless((F.TAKX / 'sources.lock.json').is_file(), 'recipe locks are not next to the script')
    def test_real_recipe_locks_parse_into_safe_entries(self):
        self.patch(F, 'TAKX', HERE.parent)
        found = F.collect_lock_entries()
        self.assertGreater(len(found), 50)
        recipes, methods = set(), set()
        for digest, item in found.items():
            self.assertRegex(digest, r'\A[0-9a-f]{64}\Z')
            self.assertEqual(item['sha256'], digest)
            self.assertRegex(item['url'], r'\Ahttps://[^/]+/')
            self.assertTrue(item['bytes'] is None or (isinstance(item['bytes'], int) and item['bytes'] > 0), item)
            methods.add(F.method_of(item['url']))
            if F.method_of(item['url']) == 'git-archive':
                self.assertEqual(len(F.github_archive_parts(item['url'])), 3)
            for consumer in item['consumers']:
                recipes.add(consumer['recipe'])
                self.assertTrue((HERE.parent.parent / consumer['lock']).is_file(), consumer)
                self.assertNotIn('/', consumer['name'])
                self.assertNotIn(consumer['name'], ('', '.', '..'))
                self.assertFalse(consumer['name'].startswith('.'), consumer)
        self.assertEqual(methods, {'direct', 'lfs', 'git-archive'})
        self.assertLessEqual({'commoncommo', 'ui-java', 'kernel-java', 'native-deps', 'native-gles', 'native-curl', 'gis-java',
                              'native-commoncommo', 'kernel-spatial-db'}, recipes)
        self.assertNotIn('desktop-prototype', recipes)
        locked = json.loads((HERE.parent / 'sources.lock.json').read_text())
        for archive in locked['archives']:
            self.assertEqual(found[archive['sha256']]['bytes'], archive['bytes'])
        for name in ('native-cesium/inputs.lock.json', 'native-cesium/cesium-tree.json', 'client-compiler/inputs.lock.json',
                     'native-commoncommo/inputs.lock.json', 'worldwind-java/inputs.lock.json', 'gis-java/sources.lock.json'):
            self.assertTrue((HERE.parent / name).is_file(), name)
        cesium = json.loads((HERE.parent / 'native-cesium/inputs.lock.json').read_text())
        for entry in [cesium['cesium']] + [e for e in cesium['ports'] if e['selected_action'] == 'acquire_source']:
            self.assertEqual(len(F.github_archive_parts(entry['source_url'])), 3, entry['source_url'])
            self.assertTrue(entry['source_sha512'] is None or len(entry['source_sha512']) == 128)
        tools = json.loads((HERE.parent / 'native-commoncommo/inputs.lock.json').read_text())['tools']
        self.assertIn('/dists/', tools['metadata_url'])
        for package in tools['packages']:
            self.assertRegex(Path(package['Filename']).name, r'\A[A-Za-z0-9][A-Za-z0-9_.+~-]*\.deb\Z')
        jars = [k for k in json.loads((HERE.parent / 'client-compiler/inputs.lock.json').read_text())['files'] if k.endswith('.jar')]
        for reference in jars:
            family, path = reference.split(':', 1)
            self.assertNotIn('..', Path(family, path).parts, reference)
            self.assertFalse(path.startswith('/'), reference)
            if path.startswith('maven-public/'):
                self.assertTrue(F.maven_candidates(Path(path).name), reference)


# --------------------------------------------------------------------------- lock entries

class LockEntry(Case):
    URL = 'https://files.example/pool/thing-1.0.tar.xz'
    DATA = b'the pinned bytes'

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        self.cache = self.root / 'cache'
        self.cache.mkdir(parents=True)
        self.archive_calls = []

    def item(self, data=None, url=None, size='len', consumers=None):
        data = self.DATA if data is None else data
        return {'sha256': sha(data), 'url': url or self.URL, 'bytes': len(data) if size == 'len' else size,
                'consumers': consumers or [{'recipe': 'native-x', 'lock': 'takx/native-x/inputs.lock.json', 'name': 'thing.tar.xz',
                                            'prefix': None}]}

    def expected(self, item, **changes):
        record = {'kind': 'lock-entry', 'name': item['consumers'][0]['name'], 'source': item['url'], 'method': 'direct',
                  'expected_sha256': item['sha256'], 'actual_sha256': item['sha256'], 'bytes': item['bytes'], 'verified': True,
                  'required': True, 'cached': False, 'path': str(self.cache / item['sha256']),
                  'consumers': [{k: v for k, v in c.items() if k != 'prefix'} for c in item['consumers']], 'note': ''}
        record.update(changes)
        return record

    def failed(self, item, **changes):
        return self.expected(item, **{'verified': False, 'path': None, 'actual_sha256': None, **changes})

    def fake_archive(self, result):
        def fake(owner, repository, reference, prefix, target, include_lfs=False):
            self.archive_calls.append((owner, repository, reference, prefix, target, include_lfs))
            if isinstance(result, BaseException):
                raise result
            target.write_bytes(result)
            return 'c' * 40, 0
        self.patch(F, 'git_archive', fake)

    def test_download_is_verified_cached_and_linked(self):
        targets = self.spy_download()
        item = self.item(consumers=[
            {'recipe': 'native-x', 'lock': 'takx/native-x/inputs.lock.json', 'name': 'thing.tar.xz', 'prefix': None},
            {'recipe': 'compat', 'lock': 'takx/compat/a.lock.json', 'name': 'other-name.tar.xz', 'prefix': None}])
        self.net.routes[self.URL] = self.DATA
        F.fetch_lock_entry(item, self.cache, {})
        self.assertEqual(self.records, [self.expected(item)])
        self.assertEqual(self.names(self.root), ['cache/' + item['sha256'], 'inputs/compat/other-name.tar.xz',
                                                 'inputs/native-x/thing.tar.xz'])
        for name in ('inputs/compat/other-name.tar.xz', 'inputs/native-x/thing.tar.xz'):
            self.assertTrue(os.path.samefile(self.root / name, self.cache / item['sha256']))
            self.assertFalse((self.root / name).is_symlink())
        self.assertEqual((self.cache / item['sha256']).read_bytes(), self.DATA)
        self.assertEqual(self.logs, [])
        self.assertEqual(targets, [self.cache / ('.candidate-' + item['sha256'])])

    def test_wrong_bytes_are_rejected_not_cached(self):
        item = self.item()
        wrong = b'the pinneD bytes'
        self.net.routes[self.URL] = wrong
        F.fetch_lock_entry(item, self.cache, {})
        self.assertEqual(self.records, [self.failed(
            item, actual_sha256=sha(wrong), note='digest or size differs from lock (bytes 16, expected 16)')])
        self.assertEqual(self.names(self.root), ['rejected/' + item['sha256'] + '.got-' + sha(wrong)[:16]])
        self.assertEqual((self.root / 'rejected' / (item['sha256'] + '.got-' + sha(wrong)[:16])).read_bytes(), wrong)
        self.assertEqual(self.logs, [('NOT VERIFIED', 'thing.tar.xz', 'digest or size differs from lock (bytes 16, expected 16)')])
        self.assertEqual(len(self.net.calls), 1)
        # A second rejection goes next to the first one.
        F.fetch_lock_entry(item, self.cache, {})
        self.assertEqual(len(self.names(self.root)), 1)
        self.assertFalse(self.records[1]['verified'])

    def test_truncated_download_is_rejected(self):
        item = self.item()
        self.net.routes[self.URL] = self.DATA[:-1]
        F.fetch_lock_entry(item, self.cache, {})
        self.assertEqual(self.records, [self.failed(
            item, actual_sha256=sha(self.DATA[:-1]), note='digest or size differs from lock (bytes 15, expected 16)')])
        self.assertEqual(self.names(self.cache), [])
        self.assertFalse((self.root / 'inputs').exists())

    def test_oversized_download_is_cut_off_at_the_locked_size(self):
        item = self.item()
        self.net.routes[self.URL] = self.DATA + b'!'
        F.fetch_lock_entry(item, self.cache, {})
        self.assertEqual(self.records, [self.failed(item, note='ValueError: download exceeds locked size')])
        self.assertEqual(self.names(self.root), [])
        self.assertEqual(len(self.net.calls), 4)

    def test_right_digest_with_wrong_locked_size_is_rejected(self):
        item = self.item(size=len(self.DATA) + 1)
        self.net.routes[self.URL] = self.DATA
        F.fetch_lock_entry(item, self.cache, {})
        self.assertEqual(self.records, [self.failed(
            item, actual_sha256=item['sha256'], note='digest or size differs from lock (bytes 16, expected 17)')])
        self.assertEqual(self.names(self.root), ['rejected/' + item['sha256'] + '.got-' + item['sha256'][:16]])

    def test_entry_without_size_is_judged_by_digest_alone(self):
        for size in (None, '16', 16.0):
            with self.subTest(size=size):
                self.records.clear()
                shutil.rmtree(self.root)
                self.cache.mkdir(parents=True)
                item = self.item(size=size)
                self.net.routes[self.URL] = self.DATA
                F.fetch_lock_entry(item, self.cache, {})
                self.assertEqual(self.records, [self.expected(item)])
                self.net.routes[self.URL] = self.DATA + b'much longer than any limit'
                (self.cache / item['sha256']).unlink()
                F.fetch_lock_entry(item, self.cache, {})
                self.assertFalse(self.records[1]['verified'])
                self.assertEqual(self.records[1]['actual_sha256'], sha(self.DATA + b'much longer than any limit'))

    def test_download_error_is_recorded_and_leaves_nothing(self):
        item = self.item()
        for failure, note in ((http_error(403), 'HTTPError: HTTP Error 403: status 403'),
                              (urllib.error.URLError('down'), 'URLError: <urlopen error down>')):
            with self.subTest(note=note):
                self.records.clear()
                self.net.routes[self.URL] = [failure]
                F.fetch_lock_entry(item, self.cache, {})
                self.assertEqual(self.records, [self.failed(item, note=note)])
                self.assertEqual(self.names(self.root), [])

    def test_cached_entry_is_not_downloaded_again(self):
        item = self.item()
        (self.cache / item['sha256']).write_bytes(self.DATA)
        F.fetch_lock_entry(item, self.cache, {})
        self.assertEqual(self.net.calls, [])
        self.assertEqual(self.records, [self.expected(item, cached=True)])
        self.assertTrue(os.path.samefile(self.root / 'inputs/native-x/thing.tar.xz', self.cache / item['sha256']))

    def test_corrupted_cache_entry_is_replaced_or_reported(self):
        item = self.item()
        target = self.cache / item['sha256']
        for label, corrupt in (('content', lambda: target.write_bytes(b'the pinned bytez')),
                               ('truncated', lambda: target.write_bytes(self.DATA[:5])),
                               ('symlink', lambda: (self.tmp / 'elsewhere').write_bytes(self.DATA)
                                and target.symlink_to(self.tmp / 'elsewhere')),
                               ('directory', target.mkdir)):
            with self.subTest(label):
                self.records.clear()
                self.net.calls.clear()
                shutil.rmtree(self.root)
                self.cache.mkdir(parents=True)
                corrupt()
                self.net.routes[self.URL] = [http_error(404)]
                F.fetch_lock_entry(item, self.cache, {})
                self.assertEqual(self.records, [self.failed(item, note='HTTPError: HTTP Error 404: status 404')])
                self.assertFalse((self.root / 'inputs').exists())
                if label == 'directory':
                    continue
                self.records.clear()
                self.net.routes[self.URL] = [self.DATA]
                F.fetch_lock_entry(item, self.cache, {})
                self.assertEqual(self.records, [self.expected(item)])
                self.assertFalse(target.is_symlink())
                self.assertEqual(target.read_bytes(), self.DATA)

    def test_cache_entry_with_right_digest_but_wrong_locked_size_is_not_trusted(self):
        item = self.item(size=len(self.DATA) - 1)
        (self.cache / item['sha256']).write_bytes(self.DATA)
        self.net.routes[self.URL] = [http_error(404)]
        F.fetch_lock_entry(item, self.cache, {})
        self.assertEqual(self.records, [self.failed(item, note='HTTPError: HTTP Error 404: status 404')])

    def test_required_unless_every_consumer_is_optional(self):
        def consumer(recipe):
            return {'recipe': recipe, 'lock': f'takx/{recipe}/inputs.lock.json', 'name': 'n.jar', 'prefix': None}
        self.net.routes[self.URL] = [http_error(404)]
        for recipes, required in ((['compat'], False), (['compat', 'sqlite-jdbc'], False), (['compat', 'ui-java'], True),
                                  (['ui-java', 'compat'], True), (['commoncommo'], True)):
            with self.subTest(recipes=recipes):
                self.records.clear()
                F.fetch_lock_entry(self.item(consumers=[consumer(r) for r in recipes]), self.cache, {})
                self.assertIs(self.records[0]['required'], required)
        for recipe in ('compat', 'sqlite-jdbc', 'registry-cache', 'geos-cache', 'contour-elevation', 'contour-grid',
                       'contour-style', 'client58-public-api', 'mission-package', 'cot-conformance', 'elevation-download-compat'):
            self.records.clear()
            F.fetch_lock_entry(self.item(consumers=[consumer(recipe)]), self.cache, {})
            self.assertIs(self.records[0]['required'], False, recipe)
        self.assertEqual(len(F.OPTIONAL), 11)

    def test_gitiles_text_is_decoded_before_verification(self):
        url = 'https://android.googlesource.com/platform/+/rev/Base64.java?format=TEXT'
        item = self.item(url=url)
        self.net.routes[url] = base64.b64encode(self.DATA)
        F.fetch_lock_entry(item, self.cache, {})
        self.assertEqual(self.records, [self.expected(item, transport='gitiles base64, decoded before verification')])
        self.assertEqual((self.cache / item['sha256']).read_bytes(), self.DATA)

    def test_gitiles_garbage_is_refused(self):
        url = 'https://android.googlesource.com/platform/+/rev/Base64.java?format=TEXT'
        item = self.item(url=url)
        encoded = base64.b64encode(self.DATA)
        for body in (b'<html>not base64</html>', encoded + b'\n', encoded[:8] + b' ' + encoded[8:], encoded[:-2]):
            with self.subTest(body=body):
                self.records.clear()
                self.net.routes[url] = body
                F.fetch_lock_entry(item, self.cache, {})
                self.assertEqual(len(self.records), 1)
                self.assertRegex(self.records[0].pop('note'), r'\Agitiles response is not base64: ')
                self.assertEqual(self.records, [{k: v for k, v in self.failed(item).items() if k != 'note'}])
                self.assertEqual(self.names(self.root), [])

    def test_only_gitiles_text_urls_are_decoded(self):
        encoded = base64.b64encode(self.DATA)
        for url in ('https://android.googlesource.com/platform/+/rev/Base64.java',
                    'https://android.googlesource.com/platform/+/rev/Base64.java?format=TEXT&x=1',
                    'https://mirror.example/android.googlesource.com/Base64.java?format=TEXT',
                    'https://files.example/https://android.googlesource.com/x?format=TEXT'):
            with self.subTest(url=url):
                self.records.clear()
                shutil.rmtree(self.root)
                self.cache.mkdir(parents=True)
                item = self.item(data=encoded, url=url)
                self.net.routes[url] = encoded
                F.fetch_lock_entry(item, self.cache, {})
                self.assertEqual(self.records, [self.expected(item)])
                self.assertEqual((self.cache / item['sha256']).read_bytes(), encoded)

    def test_git_archive_reconstruction_must_reproduce_the_pin(self):
        url = 'https://codeload.github.com/NVIDIA/libglvnd/tar.gz/refs/tags/v1.7.0'
        item = self.item(url=url)
        self.fake_archive(self.DATA)
        F.fetch_lock_entry(item, self.cache, {})
        candidate = self.cache / ('.candidate-' + item['sha256'])
        self.assertEqual(self.archive_calls, [('NVIDIA', 'libglvnd', 'v1.7.0', 'libglvnd-1.7.0/', candidate, False)])
        self.assertEqual(self.records, [self.expected(item, method='git-archive', commit='c' * 40, prefix='libglvnd-1.7.0/')])
        self.assertEqual(self.net.calls, [])
        self.assertEqual(self.names(self.root), ['cache/' + item['sha256'], 'inputs/native-x/thing.tar.xz'])

    def test_git_archive_uses_the_locked_prefix(self):
        url = 'https://github.com/o/Docking/archive/' + 'f' * 40 + '.tar.gz'
        consumers = [{'recipe': 'a', 'lock': 'takx/a/x.lock.json', 'name': 'one.tar.gz', 'prefix': None},
                     {'recipe': 'b', 'lock': 'takx/b/x.lock.json', 'name': 'two.tar.gz', 'prefix': ''},
                     {'recipe': 'c', 'lock': 'takx/c/x.lock.json', 'name': 'three.tar.gz', 'prefix': 'Locked-Root/'},
                     {'recipe': 'd', 'lock': 'takx/d/x.lock.json', 'name': 'four.tar.gz', 'prefix': 'Later/'},
                     {'recipe': 'e', 'lock': 'takx/e/x.lock.json', 'name': 'five.tar.gz'}]
        item = self.item(url=url, consumers=consumers)
        self.fake_archive(self.DATA)
        F.fetch_lock_entry(item, self.cache, {})
        self.assertEqual(self.archive_calls[0][:4], ('o', 'Docking', 'f' * 40, 'Locked-Root/'))
        self.assertEqual(self.records, [self.expected(item, method='git-archive', commit='c' * 40, prefix='Locked-Root/')])
        self.assertEqual(len(self.names(self.root / 'inputs')), 5)

    def test_git_archive_that_differs_is_rejected(self):
        url = 'https://codeload.github.com/o/r/tar.gz/' + 'f' * 40
        item = self.item(url=url)
        self.fake_archive(b'a different reconstruction')
        F.fetch_lock_entry(item, self.cache, {})
        actual = sha(b'a different reconstruction')
        self.assertEqual(self.records, [self.failed(
            item, method='git-archive', actual_sha256=actual, commit='c' * 40, prefix='r-' + 'f' * 40 + '/',
            note='digest or size differs from lock (bytes 26, expected 16)')])
        self.assertEqual(self.names(self.root), ['rejected/' + item['sha256'] + '.got-' + actual[:16]])

    def test_git_archive_failure_is_recorded(self):
        url = 'https://codeload.github.com/o/r/tar.gz/' + 'f' * 40
        item = self.item(url=url)
        for failure in (RuntimeError('git fetch failed: x'), subprocess.CalledProcessError(1, ['gzip', '-n']),
                        subprocess.TimeoutExpired(['git'], 5), OSError(28, 'No space left on device')):
            with self.subTest(failure=repr(failure)):
                self.records.clear()
                (self.cache / ('.candidate-' + item['sha256'])).write_bytes(self.DATA)
                self.fake_archive(failure)
                F.fetch_lock_entry(item, self.cache, {})
                self.assertEqual(self.records, [self.failed(item, method='git-archive', note=str(failure))])
                self.assertEqual(self.names(self.root), [])

    def test_git_archive_end_to_end_against_a_local_repository(self):
        base = self.tmp / 'github'
        commit = make_repo(base / 'owner/project.git', {'a.txt': b'a\n', 'd/b.txt': b'b\n'}, tag='v2.0')
        self.github(base)
        probe = self.tmp / 'probe.tar.gz'
        REAL_GIT_ARCHIVE('owner', 'project', 'v2.0', 'project-2.0/', probe)
        pinned = probe.read_bytes()
        probe.unlink()
        url = 'https://github.com/owner/project/archive/refs/tags/v2.0.tar.gz'
        item = self.item(data=pinned, url=url)
        F.fetch_lock_entry(item, self.cache, {})
        self.assertEqual(self.records, [self.expected(item, method='git-archive', commit=commit, prefix='project-2.0/')])
        self.assertEqual(self.names(self.cache), [item['sha256']])
        # The same repository under a pin made with another root directory does not verify.
        other = self.item(data=pinned, url=url, consumers=[{'recipe': 'x', 'lock': 'l', 'name': 'n.tar.gz', 'prefix': 'other/'}])
        (self.cache / item['sha256']).unlink()
        self.records.clear()
        F.fetch_lock_entry(other, self.cache, {})
        self.assertFalse(self.records[0]['verified'])
        self.assertEqual(self.names(self.cache), [])
        self.assertEqual(len(self.names(self.root / 'rejected')), 1)

    def lfs_item(self, name='depends/x y.tar.gz', revision='9' * 40):
        url = F.LFS_MEDIA + revision + '/' + name.replace(' ', '%20')
        self.net.routes[url] = self.DATA
        return self.item(url=url)

    def test_lfs_entry_needs_matching_pointer(self):
        item = self.lfs_item()
        F.fetch_lock_entry(item, self.cache, {'depends/x y.tar.gz': {'oid': item['sha256'], 'size': len(self.DATA)}})
        self.assertEqual(self.records, [self.expected(item, method='lfs', lfs_pointer_oid=item['sha256'], lfs_pointer_matches=True)])
        self.assertTrue((self.root / 'inputs/native-x/thing.tar.xz').is_file())

    def test_lfs_pointer_oid_or_size_mismatch_fails_closed(self):
        item = self.lfs_item()
        for pointer in ({'oid': 'e' * 64, 'size': len(self.DATA)}, {'oid': item['sha256'], 'size': len(self.DATA) + 1}):
            for precached in (False, True):
                with self.subTest(pointer=pointer, precached=precached):
                    self.records.clear()
                    shutil.rmtree(self.root)
                    self.cache.mkdir(parents=True)
                    if precached:
                        (self.cache / item['sha256']).write_bytes(self.DATA)
                    F.fetch_lock_entry(item, self.cache, {'depends/x y.tar.gz': pointer})
                    self.assertEqual(self.records, [self.failed(
                        item, method='lfs', cached=precached, lfs_pointer_oid=pointer['oid'], lfs_pointer_matches=False,
                        note='lock digest differs from the Git LFS pointer oid')])
                    self.assertFalse((self.root / 'inputs').exists())

    def test_lfs_entry_without_pointer_is_marked(self):
        note = 'no pointer at this path in the checked-out submodule'
        item = self.lfs_item()
        F.fetch_lock_entry(item, self.cache, {'depends/x%20y.tar.gz': {'oid': 'e' * 64, 'size': 1},
                                              'other': {'oid': 'e' * 64, 'size': 1}})
        self.assertEqual(self.records, [self.expected(item, method='lfs', lfs_pointer=note)])
        for url in ('https://media.githubusercontent.com/media/Other/repo/' + '9' * 40 + '/depends/x.tar.gz',
                    F.LFS_MEDIA + 'main/depends/x.tar.gz', F.LFS_MEDIA + '9' * 39 + '/depends/x.tar.gz',
                    F.LFS_MEDIA + '9' * 40 + '/'):
            with self.subTest(url=url):
                self.records.clear()
                self.net.routes[url] = self.DATA
                item = self.item(url=url)
                F.fetch_lock_entry(item, self.cache, {'depends/x.tar.gz': {'oid': 'e' * 64, 'size': 1},
                                                      '': {'oid': 'e' * 64, 'size': 1}})
                self.assertEqual(self.records, [self.expected(item, method='lfs', cached=True, lfs_pointer=note)])

    def test_lfs_download_failure_skips_the_pointer_check(self):
        item = self.lfs_item()
        self.net.routes[item['url']] = b'wrong'
        F.fetch_lock_entry(item, self.cache, {'depends/x y.tar.gz': {'oid': item['sha256'], 'size': len(self.DATA)}})
        self.assertEqual(self.records, [self.failed(item, method='lfs', actual_sha256=sha(b'wrong'),
                                                    note='digest or size differs from lock (bytes 5, expected 16)')])

    def test_direct_entry_ignores_pointers(self):
        item = self.item()
        self.net.routes[self.URL] = self.DATA
        F.fetch_lock_entry(item, self.cache, {'pool/thing-1.0.tar.xz': {'oid': 'e' * 64, 'size': 1}})
        self.assertEqual(self.records, [self.expected(item)])


# --------------------------------------------------------------------------- Git LFS

class LfsPointers(Case):
    def test_reads_small_tracked_pointer_files_only(self):
        source = self.tmp / 'checkout'
        files = {
            'a/plain pointer.bin': pointer_text(b'one'),
            'exactly-300.bin': pointer_text(b'two', pad=300),
            'over-300.bin': pointer_text(b'three', pad=301),
            'no-size.bin': POINTER_HEAD + b'oid sha256:' + b'1' * 64 + b'\n',
            'no-final-newline.bin': pointer_text(b'four')[:-1],
            'short-oid.bin': POINTER_HEAD + b'oid sha256:' + b'1' * 63 + b'\nsize 3\n',
            'wrong-head.bin': b'x' + pointer_text(b'five'),
            'head-without-newline.bin': POINTER_HEAD[:-1] + b'oid sha256:' + b'1' * 64 + b'\nsize 3\n',
            'ordinary.txt': b'hello\n',
            'removed.bin': pointer_text(b'six'),
            'link.bin': 'a/plain pointer.bin',
            'extra-lines.bin': POINTER_HEAD + b'ext-0 x\noid sha256:' + b'2' * 64 + b'\nsize 12345678901\nmore\n',
            'zz-last.bin': pointer_text(b'last'),
        }
        make_repo(source, files)
        (source / 'removed.bin').unlink()
        (source / 'untracked.bin').write_bytes(pointer_text(b'seven'))
        calls = self.spy_run()
        self.assertEqual(F.lfs_pointers(source), {
            'a/plain pointer.bin': {'oid': sha(b'one'), 'size': 3},
            'exactly-300.bin': {'oid': sha(b'two'), 'size': 3},
            'extra-lines.bin': {'oid': '2' * 64, 'size': 12345678901},
            'zz-last.bin': {'oid': sha(b'last'), 'size': 4},
        })
        self.assertEqual(calls, [(['git', '-C', str(source), 'ls-files', '-z'], {})])
        self.assert_hermetic_git(1)

    def test_unreadable_entries_are_skipped(self):
        source = self.tmp / 'checkout'
        make_repo(source, {'good.bin': pointer_text(b'one'), 'bad.bin': pointer_text(b'two')})
        real = Path.read_bytes

        def read_bytes(path):
            if path.name == 'bad.bin':
                raise PermissionError(13, 'denied')
            return real(path)
        self.patch(F.Path, 'read_bytes', read_bytes)
        self.assertEqual(F.lfs_pointers(source), {'good.bin': {'oid': sha(b'one'), 'size': 3}})


class FetchLfs(Case):
    DATA = b'large file content'
    REVISION = '9' * 40
    NAME = 'depends/some dir/x+y.tar.gz'
    URL = 'https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/' + '9' * 40 + '/depends/some%20dir/x%2By.tar.gz'

    def setUp(self):
        super().setUp()
        self.cache = self.tmp / 'root/cache'
        self.cache.mkdir(parents=True)
        self.tree = self.tmp / 'root/src/atak-civ-lfs'
        self.pointer = {'oid': sha(self.DATA), 'size': len(self.DATA)}
        self.target = self.cache / self.pointer['oid']

    def fetch(self, pinned=()):
        F.fetch_lfs(self.NAME, self.pointer, self.REVISION, self.cache, self.tree, set(pinned))

    def expected(self, **changes):
        record = {'kind': 'atak-civ-lfs', 'name': self.NAME, 'source': self.URL, 'method': 'lfs',
                  'expected_sha256': self.pointer['oid'], 'actual_sha256': self.pointer['oid'], 'bytes': len(self.DATA),
                  'verified': True, 'required': False, 'cached': False, 'path': str(self.tree / self.NAME),
                  'pinned_by_recipe_lock': False, 'note': ''}
        record.update(changes)
        return record

    def test_download_is_checked_against_pointer_and_linked(self):
        self.net.routes[self.URL] = self.DATA
        targets = self.spy_download()
        self.fetch()
        self.assertEqual(targets, [self.cache / ('.candidate-lfs-' + self.pointer['oid'])])
        self.assertEqual(self.records, [self.expected()])
        self.assertEqual(self.names(self.tmp / 'root'), ['cache/' + self.pointer['oid'], 'src/atak-civ-lfs/' + self.NAME])
        self.assertTrue(os.path.samefile(self.target, self.tree / self.NAME))
        self.assertEqual(self.net.calls, [self.URL])

    def test_pinned_objects_are_required(self):
        self.net.routes[self.URL] = self.DATA
        self.fetch(pinned={'0' * 64, self.pointer['oid']})
        self.assertEqual(self.records, [self.expected(required=True, pinned_by_recipe_lock=True)])
        self.records.clear()
        self.fetch(pinned={'0' * 64})
        self.assertEqual(self.records, [self.expected(cached=True)])
        self.assertEqual(len(self.net.calls), 1)

    def test_bytes_that_differ_from_the_pointer_are_dropped(self):
        wrong = b'large file contenT'
        self.net.routes[self.URL] = wrong
        self.fetch(pinned={self.pointer['oid']})
        self.assertEqual(self.records, [self.expected(verified=False, actual_sha256=sha(wrong), path=None, required=True,
                                                      pinned_by_recipe_lock=True, note='bytes differ from Git LFS pointer')])
        self.assertEqual(self.names(self.tmp / 'root'), [])

    def test_truncated_and_oversized_downloads_fail(self):
        self.net.routes[self.URL] = self.DATA[:-1]
        self.fetch()
        self.assertEqual(self.records, [self.expected(verified=False, actual_sha256=sha(self.DATA[:-1]), path=None,
                                                      note='bytes differ from Git LFS pointer')])
        self.records.clear()
        self.net.routes[self.URL] = self.DATA + b'!'
        self.fetch()
        self.assertEqual(self.records, [self.expected(verified=False, actual_sha256=None, path=None,
                                                      note='ValueError: download exceeds locked size')])
        self.assertEqual(self.names(self.tmp / 'root'), [])

    def test_download_error_is_recorded(self):
        self.net.routes[self.URL] = [http_error(410)]
        self.fetch()
        self.assertEqual(self.records, [self.expected(verified=False, actual_sha256=None, path=None,
                                                      note='HTTPError: HTTP Error 410: status 410')])
        self.assertEqual(self.names(self.tmp / 'root'), [])

    def test_pointer_size_is_part_of_the_pin(self):
        self.pointer['size'] = len(self.DATA) + 1
        self.net.routes[self.URL] = self.DATA
        self.fetch()
        self.assertEqual(self.records, [self.expected(verified=False, bytes=len(self.DATA) + 1, path=None)])
        self.assertFalse(self.tree.exists())

    def test_corrupted_cache_entry_is_downloaded_again(self):
        for corrupt in (b'large file contenT', self.DATA[:-1], self.DATA + b'x'):
            with self.subTest(corrupt=corrupt):
                self.records.clear()
                self.net.calls.clear()
                self.target.write_bytes(corrupt)
                self.net.routes[self.URL] = [http_error(404)]
                self.fetch()
                self.assertEqual(self.records, [self.expected(verified=False, actual_sha256=None, path=None,
                                                              note='HTTPError: HTTP Error 404: status 404')])
                self.records.clear()
                self.net.routes[self.URL] = [self.DATA]
                self.fetch()
                self.assertEqual(self.records, [self.expected()])
                self.assertEqual(self.target.read_bytes(), self.DATA)
        self.assertFalse((self.tmp / 'root/rejected').exists())


# --------------------------------------------------------------------------- Cesium

def make_tar(members):
    """members: (name, kind, payload) with kind file/dir/symlink/hardlink/fifo."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, kind, payload in members:
            info = tarfile.TarInfo(name)
            if kind == 'file':
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
                continue
            info.type = {'dir': tarfile.DIRTYPE, 'symlink': tarfile.SYMTYPE, 'hardlink': tarfile.LNKTYPE,
                         'fifo': tarfile.FIFOTYPE}[kind]
            info.linkname = payload or ''
            archive.addfile(info)
    return buffer.getvalue()


def blob(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


class Cesium(Case):
    COMMIT = 'f' * 40
    CESIUM_URL = 'https://github.com/CesiumGS/cesium-native/archive/' + 'f' * 40 + '.tar.gz'
    PORT_URL = 'https://github.com/martinmoene/expected-lite/archive/v0.8.0.tar.gz'
    FILES = {'CMakeLists.txt': b'project(x)\n', 'src/a.cpp': b'int a;\n', 'src/deep/b.h': b''}
    PINNED = 'sha512 pinned in takx/native-cesium/inputs.lock.json'
    BY_TREE = 'every archive file equals its pinned Git blob in takx/native-cesium/cesium-tree.json'

    def setUp(self):
        super().setUp()
        self.takx = self.tmp / 'repo/takx'
        (self.takx / 'native-cesium').mkdir(parents=True)
        self.patch(F, 'TAKX', self.takx)
        self.root = self.tmp / 'root'
        self.root.mkdir()
        self.good = make_tar([('expected-lite-0.8.0/a.txt', 'file', b'a')])
        self.calls = []
        tree = [{'path': path, 'mode': '100644', 'type': 'blob', 'sha': blob(data), 'size': len(data)}
                for path, data in self.FILES.items()]
        tree += [{'path': 'src', 'mode': '040000', 'type': 'tree', 'sha': '1' * 40},
                 {'path': 'extern/sub', 'mode': '160000', 'type': 'commit', 'sha': '2' * 40}]
        (self.takx / 'native-cesium/cesium-tree.json').write_text(json.dumps(tree))
        self.write_lock(sha(self.good, 'sha512'))

    def write_lock(self, port_sha512):
        self.lock = {
            'cesium': {'version': '0.40.1', 'commit': self.COMMIT, 'source_url': self.CESIUM_URL, 'source_sha512': None},
            'ports': [
                {'port': 'skipped', 'selected_action': 'use_system', 'source_url': 'https://github.com/o/skipped/archive/v1.tar.gz'},
                {'port': 'expected-lite', 'selected_action': 'acquire_source', 'source_url': self.PORT_URL,
                 'source_sha512': port_sha512},
                {'port': 'second', 'selected_action': 'acquire_source', 'commit': 'e' * 40,
                 'source_url': 'https://github.com/o/second/archive/' + 'e' * 40 + '.tar.gz', 'source_sha512': '0' * 128},
            ]}
        (self.takx / 'native-cesium/inputs.lock.json').write_text(json.dumps(self.lock))

    def archive(self, *results):
        results = list(results)

        def fake(owner, repository, reference, prefix, target, include_lfs=False):
            self.calls.append((owner, repository, reference, prefix, target, include_lfs))
            result = results.pop(0)
            if isinstance(result, BaseException):
                raise result
            target.write_bytes(result)
            return ('1' if include_lfs else '2') * 40, 3 if include_lfs else 0
        self.patch(F, 'git_archive', fake)

    def run_port(self, index=1):
        entries, one = F.fetch_cesium(self.root)
        one(entries[index])
        return self.records[-1]

    def port_record(self, **changes):
        record = {'kind': 'cesium-source', 'name': 'expected-lite.tar.gz', 'source': self.PORT_URL, 'method': 'git-archive',
                  'expected_sha512': sha(self.good, 'sha512'), 'actual_sha512': sha(self.good, 'sha512'),
                  'actual_sha256': sha(self.good), 'verified': True, 'required': True, 'cached': False,
                  'path': str(self.root / 'cache-cesium/expected-lite.tar.gz'), 'commit': '2' * 40,
                  'prefix': 'expected-lite-0.8.0/', 'lfs_objects_included': 0, 'verification': self.PINNED, 'note': ''}
        record.update(changes)
        return record

    def cesium_record(self, data, **changes):
        record = {'kind': 'cesium-source', 'name': 'cesium.tar.gz', 'source': self.CESIUM_URL, 'method': 'git-archive',
                  'expected_sha512': None, 'actual_sha512': sha(data, 'sha512'), 'actual_sha256': sha(data), 'verified': True,
                  'required': True, 'cached': False, 'path': str(self.root / 'cache-cesium/cesium.tar.gz'), 'commit': '2' * 40,
                  'prefix': 'cesium-native-' + self.COMMIT + '/', 'lfs_objects_included': 0, 'verification': self.BY_TREE,
                  'note': ''}
        record.update(changes)
        return record

    def tree_archive(self, extra=(), files=None, root='cesium-native-' + 'f' * 40):
        files = self.FILES if files is None else files
        members = [(root, 'dir', None), (root + '/src', 'dir', None), (root + '/extern/sub', 'dir', None)]
        members += [(root + '/' + path, 'file', data) for path, data in files.items()]
        return make_tar(members + list(extra))

    def test_entries_are_cesium_then_acquired_ports(self):
        entries, _ = F.fetch_cesium(self.root)
        self.assertEqual(entries, [{'port': 'cesium', **self.lock['cesium']}, self.lock['ports'][1], self.lock['ports'][2]])
        self.assertTrue((self.root / 'cache-cesium').is_dir())
        F.fetch_cesium(self.root)

    def test_matching_reconstruction_is_cached(self):
        self.archive(self.good)
        self.assertEqual(self.run_port(), self.port_record())
        staging = self.root / 'tmp/cesium-expected-lite.tar.gz'
        self.assertEqual(self.calls, [('martinmoene', 'expected-lite', 'v0.8.0', 'expected-lite-0.8.0/', staging, False)])
        self.assertEqual(self.names(self.root), ['cache-cesium/expected-lite.tar.gz'])
        self.assertEqual((self.root / 'cache-cesium/expected-lite.tar.gz').read_bytes(), self.good)
        self.assertEqual(self.logs, [])

    def test_second_attempt_includes_lfs_objects(self):
        self.archive(b'plain form', self.good)
        self.assertEqual(self.run_port(), self.port_record(commit='1' * 40, lfs_objects_included=3))
        self.assertEqual([c[5] for c in self.calls], [False, True])
        self.assertEqual(self.calls[1][:5], self.calls[0][:5])
        self.assertEqual(self.names(self.root), ['cache-cesium/expected-lite.tar.gz'])

    def test_neither_form_matching_is_rejected(self):
        self.archive(b'plain form', b'lfs form')
        self.assertEqual(self.run_port(), self.port_record(
            verified=False, actual_sha512=sha(b'lfs form', 'sha512'), actual_sha256=None, path=None, commit='1' * 40,
            lfs_objects_included=3,
            note=f'neither form matches the pin (without LFS objects: sha512 {sha(b"plain form", "sha512")[:32]}...)'))
        self.assertEqual(self.names(self.root), ['rejected/cesium-expected-lite.tar.gz'])
        self.assertEqual((self.root / 'rejected/cesium-expected-lite.tar.gz').read_bytes(), b'lfs form')

    def test_failed_second_attempt_rejects_the_first_form(self):
        self.archive(b'plain form', RuntimeError('Git LFS object for x unavailable'))
        self.assertEqual(self.run_port(), self.port_record(
            verified=False, actual_sha512=sha(b'plain form', 'sha512'), actual_sha256=None, path=None,
            note='Git LFS object for x unavailable'))
        self.assertEqual(self.names(self.root), ['rejected/cesium-expected-lite.tar.gz'])

    def test_reconstruction_failure_is_recorded(self):
        for failure in (RuntimeError('git fetch failed: x'), subprocess.TimeoutExpired(['git'], 1), OSError(5, 'io')):
            with self.subTest(failure=repr(failure)):
                self.archive(failure)
                self.assertEqual(self.run_port(), self.port_record(
                    verified=False, actual_sha512=None, actual_sha256=None, path=None, commit=None, verification=None,
                    note=str(failure)))
                self.assertEqual(self.names(self.root), [])

    def test_cached_archive_is_verified_without_git(self):
        self.archive()
        F.fetch_cesium(self.root)
        (self.root / 'cache-cesium/expected-lite.tar.gz').write_bytes(self.good)
        self.assertEqual(self.run_port(), self.port_record(cached=True, commit=None))
        (self.root / 'cache-cesium/second.tar.gz').write_bytes(b'anything')
        self.assertEqual(self.run_port(2)['commit'], 'e' * 40)
        self.assertEqual(self.calls, [])

    def test_corrupted_cached_archive_is_unverified_and_stays_out_of_rejected(self):
        self.archive()
        F.fetch_cesium(self.root)
        (self.root / 'cache-cesium/expected-lite.tar.gz').write_bytes(self.good[:-1])
        self.assertEqual(self.run_port(), self.port_record(
            verified=False, cached=True, commit=None, actual_sha512=sha(self.good[:-1], 'sha512'), actual_sha256=None, path=None))
        self.assertEqual(self.names(self.root), ['cache-cesium/expected-lite.tar.gz'])
        self.assertEqual(self.logs, [('NOT VERIFIED', 'expected-lite.tar.gz', '')])

    def test_mismatch_of_unpinned_reconstruction_has_a_note(self):
        self.write_lock('0' * 128)
        self.archive(self.good, self.good)
        record = self.run_port()
        self.assertFalse(record['verified'])
        self.assertRegex(record['note'], r'\Aneither form matches the pin')
        self.write_lock(sha(self.good, 'sha512'))
        (self.root / 'tmp/cesium-expected-lite.tar.gz').write_bytes(b'left over')
        self.archive(RuntimeError('boom'))
        self.assertEqual(self.run_port(), self.port_record(
            verified=False, actual_sha512=sha(b'left over', 'sha512'), actual_sha256=None, path=None, commit=None, note='boom'))
        self.assertEqual((self.root / 'rejected/cesium-expected-lite.tar.gz').read_bytes(), b'left over')

    def test_cesium_native_is_bound_to_the_pinned_tree(self):
        data = self.tree_archive()
        self.archive(data)
        self.assertEqual(self.run_port(0), self.cesium_record(data))
        self.assertEqual(self.calls, [('CesiumGS', 'cesium-native', self.COMMIT, 'cesium-native-' + self.COMMIT + '/',
                                       self.root / 'tmp/cesium-cesium.tar.gz', False)])
        self.assertEqual(self.names(self.root), ['cache-cesium/cesium.tar.gz'])
        self.records.clear()
        self.archive()
        self.assertEqual(self.run_port(0), self.cesium_record(data, cached=True, commit=self.COMMIT))

    def test_cesium_native_archive_that_departs_from_the_tree_is_rejected(self):
        root = 'cesium-native-' + self.COMMIT
        changed = dict(self.FILES, **{'src/a.cpp': b'int b;\n'})
        missing = {k: v for k, v in self.FILES.items() if k != 'src/a.cpp'}
        cases = {
            'changed file': (self.tree_archive(files=changed), 3, 1, 0),
            'missing file': (self.tree_archive(files=missing), 2, 1, 0),
            'extra file': (self.tree_archive(extra=[(root + '/extra.sh', 'file', b'#!/bin/sh\n')]), 4, 0, 0),
            'symlink': (self.tree_archive(extra=[(root + '/evil', 'symlink', '/etc/passwd')]), 3, 0, 1),
            'hard link': (self.tree_archive(extra=[(root + '/evil', 'hardlink', root + '/CMakeLists.txt')]), 3, 0, 1),
            'fifo': (self.tree_archive(extra=[(root + '/pipe', 'fifo', None)]), 3, 0, 1),
            'second copy of a pinned file': (self.tree_archive(extra=[(root + '/src/a.cpp', 'file', b'int a;\n')]), 3, 0, 1),
            'file outside the root directory': (self.tree_archive(extra=[('stray', 'file', b'x')]), 3, 0, 1),
            'symlink replacing a pinned file': (
                self.tree_archive(files=missing, extra=[(root + '/src/a.cpp', 'symlink', 'deep/b.h')]), 2, 1, 1),
            'two links': (self.tree_archive(extra=[(root + '/one', 'symlink', 'x'), (root + '/two', 'symlink', 'y')]), 3, 0, 2),
            'link listed before the files': (make_tar(
                [(root + '/evil', 'symlink', '/etc/passwd')] + [(root + '/' + k, 'file', v) for k, v in self.FILES.items()]), 3, 0, 1),
            'empty archive': (make_tar([]), 0, 3, 0),
        }
        for label, (data, files, differing, unpinned) in cases.items():
            for cached in (False, True):
                with self.subTest(label, cached=cached):
                    self.records.clear()
                    shutil.rmtree(self.root)
                    (self.root / 'cache-cesium').mkdir(parents=True)
                    if cached:
                        (self.root / 'cache-cesium/cesium.tar.gz').write_bytes(data)
                        self.archive()
                    else:
                        self.archive(data)
                    self.assertEqual(self.run_port(0), self.cesium_record(
                        data, verified=False, actual_sha256=None, path=None, cached=cached,
                        commit=self.COMMIT if cached else '2' * 40,
                        note=f'archive has {files} files, tree has 3 blobs, {differing} differ or are missing, '
                             f'{unpinned} members are not pinned regular files'))
                    self.assertEqual(self.names(self.root), ['cache-cesium/cesium.tar.gz'] if cached
                                     else ['rejected/cesium-cesium.tar.gz'])

    def test_unreadable_cesium_native_archive_is_reported_not_raised(self):
        good = self.tree_archive()
        for label, data in (('not gzip', b'plain text'), ('truncated', good[:len(good) // 2]), ('empty', b''),
                            ('gzip of garbage', gzip.compress(b'x' * 2000))):
            with self.subTest(label):
                self.records.clear()
                shutil.rmtree(self.root)
                (self.root / 'cache-cesium').mkdir(parents=True)
                (self.root / 'cache-cesium/cesium.tar.gz').write_bytes(data)
                self.archive()
                record = self.run_port(0)
                self.assertRegex(record.pop('note'), r'\Aarchive is not readable: (ReadError|EOFError|BadGzipFile|error): ')
                expected = self.cesium_record(data, verified=False, actual_sha256=None, path=None, cached=True, commit=self.COMMIT)
                del expected['note']
                self.assertEqual(record, expected)

    def test_read_error_after_an_unpinned_member_cannot_cancel_it(self):
        root = 'cesium-native-' + self.COMMIT
        noise = b''.join(hashlib.sha256(b'%d' % index).digest() for index in range(4096))
        whole = self.tree_archive(extra=[(root + '/evil', 'symlink', '/etc/passwd'), (root + '/zz-noise.bin', 'file', noise)])
        data = whole[:-20000]
        (self.root / 'cache-cesium').mkdir()
        (self.root / 'cache-cesium/cesium.tar.gz').write_bytes(data)
        self.archive()
        record = self.run_port(0)
        self.assertRegex(record.pop('note'), r'\Aarchive is not readable: EOFError: ')
        expected = self.cesium_record(data, verified=False, actual_sha256=None, path=None, cached=True, commit=self.COMMIT)
        del expected['note']
        self.assertEqual(record, expected)

    def test_failure_without_a_message_still_gets_a_note(self):
        self.archive(b'plain form', RuntimeError(''))
        self.assertEqual(self.run_port(), self.port_record(
            verified=False, actual_sha512=sha(b'plain form', 'sha512'), actual_sha256=None, path=None,
            note='reconstructed archive differs from the SHA-512 pin'))

    def test_other_root_directory_name_is_accepted_like_the_recipe_does(self):
        data = self.tree_archive(root='renamed')
        self.archive(data)
        self.assertTrue(self.run_port(0)['verified'])


# --------------------------------------------------------------------------- git checkouts

class Checkout(Case):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix='fetch-inputs-checkout-')).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.base, True)
        cls.files = {'a.txt': b'a\n', 'lfs/z.bin': pointer_text(b'zz'), 'lfs/b.bin': pointer_text(b'bbbbb')}
        cls.revision = make_repo(cls.base / 'origin', cls.files)
        cls.repository = 'file://' + str(cls.base / 'origin')

    def setUp(self):
        super().setUp()
        self.target = self.tmp / 'root/src/takx57'

    def expected(self, **changes):
        record = {'kind': 'git-source', 'name': 'the name', 'source': self.repository, 'method': 'git',
                  'expected_revision': self.revision, 'actual_revision': self.revision, 'verified': True, 'required': True,
                  'path': str(self.target), 'note': ''}
        record.update(changes)
        return record

    def test_fresh_checkout_at_the_pinned_revision(self):
        calls = self.spy_run()
        direct = []

        def spy(argv, **keywords):
            direct.append((argv, keywords.get('stdout'), keywords.get('stderr')))
            return REAL_SUBPROCESS_RUN(argv, **keywords)
        self.patch(F.subprocess, 'run', spy)
        self.target.mkdir(parents=True)
        F.checkout(self.target, self.repository, self.revision, 'the name')
        self.assertEqual(self.records, [self.expected()])
        target = str(self.target)
        self.assertEqual(calls, [
            (['git', 'init', '-q', target], {}),
            (['git', '-C', target, 'remote', 'add', 'origin', self.repository], {}),
            (['git', '-C', target, 'fetch', '-q', '--depth', '1', '--no-tags', 'origin', self.revision], {'timeout': 1500}),
            (['git', '-C', target, '-c', 'advice.detachedHead=false', 'checkout', '-q', '--detach', self.revision], {'timeout': 900}),
            (['git', '-C', target, 'rev-parse', 'HEAD'], {}),
            (['git', '-C', target, 'status', '--porcelain', '--untracked-files=all'], {}),
        ])
        self.assertIn((['git', '-C', target, 'rev-parse', '-q', '--verify', 'HEAD'], subprocess.PIPE, subprocess.DEVNULL), direct)
        self.assertEqual((self.target / 'a.txt').read_bytes(), b'a\n')
        self.assertEqual(git('-C', self.target, 'rev-parse', 'HEAD'), self.revision)
        self.assertFalse((self.target.parent / 'takx57.lfs-pointers.tsv').exists())
        self.assertEqual(self.sleeps, [])
        # Second run: already there, nothing is fetched or initialised again.
        calls.clear()
        self.records.clear()
        F.checkout(self.target, self.repository, self.revision, 'the name', required=False)
        self.assertEqual(self.records, [self.expected(required=False)])
        self.assertEqual([c[0][3] for c in calls], ['rev-parse', 'status'])

    def test_git_runs_with_the_hermetic_environment(self):
        seen = []
        real = REAL_RUN

        def spy(argv, **keywords):
            seen.append(keywords.get('env'))
            return real(argv, **keywords)
        self.patch(F, 'run', spy)
        direct = []
        self.patch(F.subprocess, 'run', lambda argv, **k: direct.append(k.get('env')) or REAL_SUBPROCESS_RUN(argv, **k))
        F.checkout(self.target, self.repository, self.revision, 'the name')
        for env in seen + direct:
            self.assertEqual((env['GIT_LFS_SKIP_SMUDGE'], env['GIT_CONFIG_GLOBAL']), ('1', '/dev/null'))
        self.assertEqual(len(seen), 6)

    def test_work_tree_that_differs_is_not_verified(self):
        F.checkout(self.target, self.repository, self.revision, 'the name')
        self.records.clear()
        (self.target / 'junk.txt').write_bytes(b'x')
        F.checkout(self.target, self.repository, self.revision, 'the name')
        self.assertEqual(self.records, [self.expected(verified=False, note='work tree differs from the revision: ?? junk.txt')])
        (self.target / 'junk.txt').unlink()
        (self.target / 'a.txt').write_bytes(b'changed\n')
        self.records.clear()
        F.checkout(self.target, self.repository, self.revision, 'the name')
        self.assertEqual(self.records, [self.expected(verified=False, note='work tree differs from the revision: M a.txt')])
        for index in range(40):
            (self.target / f'untracked-{index:02d}.txt').write_bytes(b'x')
        self.records.clear()
        F.checkout(self.target, self.repository, self.revision, 'the name')
        note = self.records[0]['note']
        self.assertEqual(len(note), len('work tree differs from the revision: ') + 200)
        self.assertRegex(note, r'\Awork tree differs from the revision: M a\.txt\n\?\? untracked-00\.txt\n')

    def test_unavailable_revision_is_recorded_after_three_attempts(self):
        calls = self.spy_run()
        F.checkout(self.target, self.repository, 'd' * 40, 'the name')
        self.assertEqual(len(self.records), 1)
        note = self.records[0].pop('note')
        self.assertTrue(0 < len(note) <= 300, note)
        self.assertEqual(self.records, [{k: v for k, v in self.expected(
            verified=False, expected_revision='d' * 40, actual_revision=None).items() if k != 'note'}])
        self.assertEqual([c[0][1] if c[0][1] != '-C' else c[0][3] for c in calls], ['init', 'remote', 'fetch', 'fetch', 'fetch'])
        self.assertEqual(self.sleeps, [10, 10])

    def test_fetch_error_text_is_the_stderr_tail(self):
        def runner(argv, **keywords):
            if 'fetch' in argv:
                raise subprocess.CalledProcessError(128, argv, b'', b'E' * 400 + b'tail\xff')
            return REAL_RUN(argv, **keywords)
        self.patch(F, 'run', runner)
        F.checkout(self.target, self.repository, self.revision, 'the name')
        self.assertEqual(self.records, [self.expected(verified=False, actual_revision=None, note='E' * 295 + 'tail�')])

    def test_fetch_succeeding_on_a_later_attempt_is_fine(self):
        for failures, sleeps in ((1, [10]), (2, [10, 10])):
            with self.subTest(failures=failures):
                shutil.rmtree(self.tmp / 'root', ignore_errors=True)
                self.records.clear()
                self.sleeps.clear()
                state = {'left': failures}

                def runner(argv, **keywords):
                    if 'fetch' in argv and state['left']:
                        state['left'] -= 1
                        raise subprocess.CalledProcessError(128, argv, b'', b'temporary')
                    return REAL_RUN(argv, **keywords)
                self.patch(F, 'run', runner)
                F.checkout(self.target, self.repository, self.revision, 'the name')
                self.assertEqual(self.records, [self.expected()])
                self.assertEqual(self.sleeps, sleeps)

    def test_existing_checkout_is_moved_to_the_pinned_revision(self):
        origin = self.tmp / 'moving'
        first = make_repo(origin, {'a.txt': b'one\n'})
        self.repository = 'file://' + str(origin)
        F.checkout(self.target, self.repository, first, 'the name')
        (origin / 'a.txt').write_bytes(b'two\n')
        git('-C', origin, 'commit', '-q', '-a', '-m', 'second')
        second = git('-C', origin, 'rev-parse', 'HEAD')
        self.records.clear()
        F.checkout(self.target, self.repository, second, 'the name')
        self.assertEqual(self.records, [self.expected(expected_revision=second, actual_revision=second)])
        self.assertEqual((self.target / 'a.txt').read_bytes(), b'two\n')

    def test_checkout_resolving_to_another_revision_is_not_verified(self):
        def runner(argv, **keywords):
            done = REAL_RUN(argv, **keywords)
            return mock.Mock(stdout=b'e' * 40 + b'\n') if argv[-2:] == ['rev-parse', 'HEAD'] else done
        self.patch(F, 'run', runner)
        F.checkout(self.target, self.repository, self.revision, 'the name', True)
        self.assertEqual(self.records, [self.expected(verified=False, actual_revision='e' * 40)])
        self.assertFalse((self.target.parent / 'takx57.lfs-pointers.tsv').exists())

    def test_lfs_pointer_listing(self):
        F.checkout(self.target, self.repository, self.revision, 'the name', True)
        listing = self.target.parent / 'takx57.lfs-pointers.tsv'
        self.assertEqual(self.records, [self.expected(lfs_pointer_files=2, lfs_pointer_bytes=7, lfs_pointer_list=str(listing))])
        self.assertEqual(listing.read_text(), f'{sha(b"bbbbb")}\t5\tlfs/b.bin\n{sha(b"zz")}\t2\tlfs/z.bin\n')
        self.assertEqual((self.target / 'lfs/z.bin').read_bytes(), self.files['lfs/z.bin'])

    def test_unusable_target_is_recorded(self):
        (self.tmp / 'root').mkdir()
        (self.tmp / 'root/src').write_bytes(b'a file where a directory is needed')
        F.checkout(self.target, self.repository, self.revision, 'the name', True)
        self.assertEqual(len(self.records), 1)
        self.assertRegex(self.records[0].pop('note'), 'Not a directory|File exists')
        self.assertEqual(self.records, [{k: v for k, v in self.expected(verified=False, actual_revision=None).items()
                                         if k != 'note'}])


# --------------------------------------------------------------------------- client-compiler JARs

class ClientCompilerJars(Case):
    MAVEN = 'https://repo1.maven.org/maven2/'
    JAR = b'jar one'
    NOT_FETCHED = 'no public URL in any recipe lock: produced by a prototype build stage, not fetched'
    DIFFERS = 'a Maven Central candidate exists but its SHA-256 differs from the lock'

    def setUp(self):
        super().setUp()
        self.takx = self.tmp / 'repo/takx'
        (self.takx / 'client-compiler').mkdir(parents=True)
        self.patch(F, 'TAKX', self.takx)
        self.root = self.tmp / 'root'
        self.cache = self.root / 'cache'
        self.cache.mkdir(parents=True)

    def prepare(self, files):
        (self.takx / 'client-compiler/inputs.lock.json').write_text(json.dumps({'files': files}))
        return F.fetch_client_compiler_jars(self.root, self.cache)

    def one(self, reference, data=None):
        _, fetch = self.prepare({reference: sha(self.JAR if data is None else data)})
        fetch(reference)
        return self.records[-1]

    def expected(self, reference, **changes):
        family, path = reference.split(':', 1)
        record = {'kind': 'client-compiler-jar', 'name': reference, 'source': None, 'method': 'direct',
                  'expected_sha256': sha(self.JAR), 'actual_sha256': sha(self.JAR), 'verified': True, 'required': True,
                  'built_by_prototype': False, 'path': str(self.root / 'jars/client-compiler' / family / path), 'note': ''}
        record.update(changes)
        return record

    def failed(self, reference, **changes):
        return self.expected(reference, **{'verified': False, 'actual_sha256': None, 'path': None, **changes})

    def test_references_are_the_sorted_jar_keys(self):
        references, _ = self.prepare({'z:b.jar': '1' * 64, 'a:probe/complete.json': '2' * 64, 'a:x/y.jar': '3' * 64,
                                      'm:c.jar.sha1': '4' * 64, 'a:b.jar': '5' * 64})
        self.assertEqual(references, ['a:b.jar', 'a:x/y.jar', 'z:b.jar'])

    def test_public_jar_is_fetched_from_maven_central_and_linked(self):
        reference = 'closure:maven-public/org.a.b--lib-1.0.jar'
        url = self.MAVEN + 'org/a/b/lib/1.0/lib-1.0.jar'
        self.net.routes[url] = self.JAR
        self.assertEqual(self.one(reference), self.expected(reference, source=url))
        self.assertEqual(self.names(self.root), ['cache/' + sha(self.JAR), 'jars/client-compiler/closure/maven-public/org.a.b--lib-1.0.jar'])
        self.assertTrue(os.path.samefile(self.cache / sha(self.JAR), self.root / 'jars/client-compiler/closure/maven-public/org.a.b--lib-1.0.jar'))
        self.assertEqual(self.net.calls, [url])

    def test_only_the_first_colon_separates_family_from_path(self):
        reference = 'family:dir/with:colon/x:y.jar'
        (self.cache / sha(self.JAR)).write_bytes(self.JAR)
        self.assertEqual(self.one(reference), self.expected(reference))
        self.assertEqual(self.names(self.root / 'jars'), ['client-compiler/family/dir/with:colon/x:y.jar'])

    def test_later_candidate_is_used_when_the_first_is_absent(self):
        reference = 'closure:maven-public/g--art-2.0-natives.jar'
        first, second = self.MAVEN + 'g/art/2.0-natives/art-2.0-natives.jar', self.MAVEN + 'g/art/2.0/art-2.0-natives.jar'
        self.net.routes[second] = self.JAR
        self.assertEqual(self.one(reference), self.expected(reference, source=second))
        self.assertEqual(self.net.calls, [first, second])
        self.assertEqual(self.sleeps, [])

    def test_candidate_with_other_bytes_is_never_cached(self):
        reference = 'closure:maven-public/g--art-2.0-natives.jar'
        first, second = self.MAVEN + 'g/art/2.0-natives/art-2.0-natives.jar', self.MAVEN + 'g/art/2.0/art-2.0-natives.jar'
        self.net.routes[first] = b'another jar'
        self.assertEqual(self.one(reference), self.failed(reference, note=self.DIFFERS))
        self.assertEqual(self.names(self.root), [])
        self.assertEqual(self.net.calls, [first, second])
        self.records.clear()
        self.net.routes[second] = self.JAR
        self.assertEqual(self.one(reference), self.expected(reference, source=second, note=self.DIFFERS))
        self.assertEqual(self.names(self.cache), [sha(self.JAR)])

    def test_first_matching_candidate_wins(self):
        reference = 'closure:maven-public/g--art-2.0-natives.jar'
        first, second = self.MAVEN + 'g/art/2.0-natives/art-2.0-natives.jar', self.MAVEN + 'g/art/2.0/art-2.0-natives.jar'
        self.net.routes[first] = self.JAR
        self.net.routes[second] = self.JAR
        self.assertEqual(self.one(reference), self.expected(reference, source=first))
        self.assertEqual(self.net.calls, [first])

    def test_unreachable_candidates_get_two_attempts_each(self):
        reference = 'closure:maven-public/g--art-2.0.jar'
        url = self.MAVEN + 'g/art/2.0/art-2.0.jar'
        self.net.routes[url] = [urllib.error.URLError('down')]
        self.assertEqual(self.one(reference), self.failed(reference))
        self.assertEqual(self.net.calls, [url, url])
        self.assertEqual(self.names(self.root), [])

    def test_stale_candidate_file_is_removed(self):
        reference = 'closure:maven-public/g--art-2.0.jar'
        (self.cache / ('.candidate-jar-' + sha(self.JAR))).write_bytes(self.JAR)
        self.assertEqual(self.one(reference), self.failed(reference))
        self.assertEqual(self.names(self.root), [])

    def test_jar_without_public_source_is_a_build_product(self):
        for reference in ('closure:ui-original/x.jar', 'closure:other/maven-public/g--a-1.jar', 'tools:xmaven-public/g--a-1.jar'):
            with self.subTest(reference=reference):
                self.records.clear()
                self.assertEqual(self.one(reference), self.failed(reference, method='not-fetched', required=False,
                                                                  built_by_prototype=True, note=self.NOT_FETCHED))
                self.assertEqual(self.net.calls, [])
                self.assertEqual(self.names(self.root), [])

    def test_cached_jar_reports_where_it_came_from(self):
        (self.cache / sha(self.JAR)).write_bytes(self.JAR)
        public, private = 'closure:maven-public/g--a-1.jar', 'closure:ui-original/a.jar'
        self.assertEqual(self.one(public), self.expected(public, source='Maven Central (already cached)'))
        self.records.clear()
        self.assertEqual(self.one(private), self.expected(private))
        self.assertTrue(os.path.samefile(self.cache / sha(self.JAR), self.root / 'jars/client-compiler/closure/ui-original/a.jar'))
        self.records[:] = [{'expected_sha256': '0' * 64, 'source': 'https://other.example/x'},
                           {'expected_sha256': sha(self.JAR), 'source': None},
                           {'expected_sha256': sha(self.JAR), 'source': ''},
                           {'kind': 'no digest', 'source': 'https://nodigest.example/x'},
                           {'expected_sha256': sha(self.JAR), 'source': 'https://lock.example/a.jar'},
                           {'expected_sha256': sha(self.JAR), 'source': 'https://later.example/a.jar'}]
        self.assertEqual(self.one(public), self.expected(public, source='https://lock.example/a.jar'))
        self.assertEqual(self.one(private), self.expected(private, source='https://lock.example/a.jar'))
        self.assertEqual(self.net.calls, [])

    def test_corrupted_cached_jar_is_fetched_again_or_fails(self):
        reference = 'closure:maven-public/g--a-1.jar'
        (self.cache / sha(self.JAR)).write_bytes(b'jar onE')
        self.assertEqual(self.one(reference), self.failed(reference))
        self.assertFalse((self.root / 'jars').exists())
        private = 'closure:ui-original/a.jar'
        self.assertEqual(self.one(private), self.failed(private, method='not-fetched', required=False, built_by_prototype=True,
                                                        note=self.NOT_FETCHED))
        self.net.routes[self.MAVEN + 'g/a/1/a-1.jar'] = self.JAR
        self.assertEqual(self.one(reference), self.expected(reference, source=self.MAVEN + 'g/a/1/a-1.jar'))
        self.assertEqual((self.cache / sha(self.JAR)).read_bytes(), self.JAR)


# --------------------------------------------------------------------------- POM walk

def pom(group='g', artifact='a', version='1', parent=None, properties=None, managed=(), dependencies=(), namespace=True):
    def dependency(item):
        return '<dependency>' + ''.join(f'<{k}>{v}</{k}>' for k, v in item.items()) + '</dependency>'
    text = '<?xml version="1.0"?>\n<project' + (' xmlns="http://maven.apache.org/POM/4.0.0"' if namespace else '') + '>'
    if parent:
        text += '<parent><groupId>%s</groupId><artifactId>%s</artifactId><version>%s</version></parent>' % parent
    text += f'<groupId>{group}</groupId><artifactId>{artifact}</artifactId><version>{version}</version>'
    if properties:
        text += '<properties>' + ''.join(f'<{k}>{v}</{k}>' for k, v in properties.items()) + '</properties>'
    if managed:
        text += '<dependencyManagement><dependencies>' + ''.join(map(dependency, managed)) + '</dependencies></dependencyManagement>'
    return (text + '<dependencies>' + ''.join(map(dependency, dependencies)) + '</dependencies></project>').encode()


class PomDependencies(Case):
    REPOSITORY = 'https://repo.example/maven/'

    def publish(self, group, artifact, version, body):
        url = f'{self.REPOSITORY}{group.replace(".", "/")}/{artifact}/{version}/{artifact}-{version}.pom'
        self.net.routes[url] = body
        return url

    def dependencies(self, group='org.x', artifact='child', version='2.0', memo=None):
        return F.pom_dependencies(self.REPOSITORY, group, artifact, version, {} if memo is None else memo)

    def test_scopes_types_and_optional_are_filtered(self):
        url = self.publish('org.x', 'child', '2.0', pom(dependencies=[
            {'groupId': 'g', 'artifactId': 'plain', 'version': '1'},
            {'groupId': 'g', 'artifactId': 'compile', 'version': '1', 'scope': 'compile'},
            {'groupId': 'g', 'artifactId': 'runtime', 'version': '1', 'scope': 'runtime'},
            {'groupId': 'g', 'artifactId': 'test', 'version': '1', 'scope': 'test'},
            {'groupId': 'g', 'artifactId': 'provided', 'version': '1', 'scope': 'provided'},
            {'groupId': 'g', 'artifactId': 'system', 'version': '1', 'scope': 'system'},
            {'groupId': 'g', 'artifactId': 'optional', 'version': '1', 'optional': 'true'},
            {'groupId': 'g', 'artifactId': 'not-optional', 'version': '1', 'optional': 'false'},
            {'groupId': 'g', 'artifactId': 'typed-jar', 'version': '1', 'type': 'jar'},
            {'groupId': 'g', 'artifactId': 'typed-pom', 'version': '1', 'type': 'pom'},
            {'groupId': 'g', 'artifactId': 'classified', 'version': '1', 'classifier': 'natives'},
            {'groupId': 'g', 'artifactId': 'unversioned'},
        ]))
        self.assertEqual(self.dependencies(), [('g', 'plain', '1'), ('g', 'compile', '1'), ('g', 'runtime', '1'),
                                               ('g', 'not-optional', '1'), ('g', 'typed-jar', '1'), ('g', 'unversioned', '')])
        self.assertEqual(self.net.calls, [url])
        self.assertEqual(self.net.timeouts, [60])

    def test_parents_properties_and_managed_versions_are_followed(self):
        self.publish('org.x', 'child', '2.0', pom(
            parent=('org.x', 'parent', '7'), properties={'shared': 'child-value', 'own': '3.3', 'indirect': '${deep}', 'blank': ''},
            managed=[{'groupId': 'g', 'artifactId': 'managed-both', 'version': 'child-managed'}],
            dependencies=[
                {'groupId': 'g', 'artifactId': 'own', 'version': '${own}'},
                {'groupId': 'g', 'artifactId': 'shared', 'version': '${shared}'},
                {'groupId': 'g', 'artifactId': 'from-parent', 'version': '${parentOnly}'},
                {'groupId': 'g', 'artifactId': 'from-grandparent', 'version': '${grand}'},
                {'groupId': '${project.groupId}', 'artifactId': 'sibling-${own}', 'version': '${project.version}'},
                {'groupId': 'g', 'artifactId': 'pom-version', 'version': '${pom.version}'},
                {'groupId': 'g', 'artifactId': 'parent-version', 'version': '${project.parent.version}'},
                {'groupId': 'g', 'artifactId': 'indirect', 'version': '${indirect}'},
                {'groupId': 'g', 'artifactId': 'unknown', 'version': '${nowhere}-x'},
                {'groupId': 'g', 'artifactId': 'blank', 'version': '${blank}'},
                {'groupId': 'g', 'artifactId': 'managed-parent'},
                {'groupId': 'g', 'artifactId': 'managed-both'},
                {'groupId': 'g', 'artifactId': 'managed-by-property'},
                {'groupId': 'other', 'artifactId': 'managed-parent'},
            ]))
        self.publish('org.x', 'parent', '7', pom(
            parent=('org.y', 'grand', '1'), properties={'shared': 'parent-value', 'parentOnly': '5', 'deep': '${deeper}',
                                                        'deeper': '${deepest}', 'deepest': '9'},
            managed=[{'groupId': 'g', 'artifactId': 'managed-parent', 'version': '4.4'},
                     {'groupId': 'g', 'artifactId': 'managed-both', 'version': 'parent-managed'},
                     {'groupId': 'g', 'artifactId': 'managed-by-property', 'version': '${grand}'}],
            dependencies=[{'groupId': 'g', 'artifactId': 'parents-own-dependency', 'version': '1'}]))
        self.publish('org.y', 'grand', '1', pom(properties={'grand': '8.8', 'shared': 'grand-value'}, namespace=False))
        self.assertEqual(self.dependencies(), [
            ('g', 'own', '3.3'), ('g', 'shared', 'child-value'), ('g', 'from-parent', '5'), ('g', 'from-grandparent', '8.8'),
            ('org.x', 'sibling-3.3', '2.0'), ('g', 'pom-version', '2.0'), ('g', 'parent-version', '7'), ('g', 'indirect', '9'),
            ('g', 'unknown', '${nowhere}-x'), ('g', 'blank', ''), ('g', 'managed-parent', '4.4'),
            ('g', 'managed-both', 'child-managed'), ('g', 'managed-by-property', '8.8'), ('other', 'managed-parent', '')])
        self.assertEqual(len(self.net.calls), 3)

    def test_property_expansion_is_bounded(self):
        chain = {f'p{i}': '${p%d}' % (i + 1) for i in range(6)}
        chain['p6'] = 'end'
        self.publish('org.x', 'child', '2.0', pom(properties=chain, dependencies=[
            {'groupId': 'g', 'artifactId': 'five', 'version': '${p2}'}, {'groupId': 'g', 'artifactId': 'six', 'version': '${p1}'},
            {'groupId': 'g', 'artifactId': 'seven', 'version': '${p0}'}]))
        self.assertEqual(self.dependencies(), [('g', 'five', 'end'), ('g', 'six', '${p6}'), ('g', 'seven', '${p5}')])

    def test_without_parent_there_is_no_parent_version(self):
        self.publish('org.x', 'child', '2.0', pom(dependencies=[
            {'groupId': 'g', 'artifactId': 'a', 'version': '${project.parent.version}'}]))
        self.assertEqual(self.dependencies(), [('g', 'a', '${project.parent.version}')])

    def test_missing_or_broken_pom(self):
        self.assertIsNone(self.dependencies())
        memo = {}
        self.publish('org.x', 'child', '2.0', b'<project><unclosed></project>')
        self.assertIsNone(self.dependencies(memo=memo))
        self.assertEqual(memo, {('org.x', 'child', '2.0'): None})
        for failure in (urllib.error.URLError('down'), ConnectionResetError('reset'), http.client.IncompleteRead(b'<proj')):
            self.publish('org.x', 'child', '2.0', [failure])
            self.assertIsNone(self.dependencies())

    def test_unavailable_parent_leaves_the_child_alone(self):
        self.publish('org.x', 'child', '2.0', pom(parent=('org.x', 'gone', '1'), dependencies=[
            {'groupId': 'g', 'artifactId': 'a', 'version': '${project.parent.version}'}]))
        self.assertEqual(self.dependencies(), [('g', 'a', '${project.parent.version}')])
        self.assertEqual(len(self.net.calls), 2)

    def test_memo_avoids_second_download(self):
        url = self.publish('org.x', 'child', '2.0', pom(dependencies=[{'groupId': 'g', 'artifactId': 'a', 'version': '1'}]))
        memo = {}
        self.assertEqual(self.dependencies(memo=memo), [('g', 'a', '1')])
        self.assertEqual(list(memo), [('org.x', 'child', '2.0')])
        del self.net.routes[url]
        self.assertEqual(self.dependencies(memo=memo), [('g', 'a', '1')])
        self.assertEqual(self.net.calls, [url])

    def test_parent_chain_is_followed_eight_levels(self):
        for level in range(12):
            self.publish('org.x', f'level{level}', '1', pom(
                parent=('org.x', f'level{level + 1}', '1'), properties={f'at{level}': f'value{level}'},
                dependencies=[{'groupId': 'g', 'artifactId': f'seven', 'version': '${at7}'},
                              {'groupId': 'g', 'artifactId': f'eight', 'version': '${at8}'}]))
        self.assertEqual(self.dependencies(artifact='level0', version='1'), [('g', 'seven', 'value7'), ('g', 'eight', '${at8}')])
        self.assertEqual(len(self.net.calls), 8)

    def test_only_the_first_namespace_declaration_is_dropped(self):
        body = pom(dependencies=[{'groupId': 'g', 'artifactId': 'a', 'version': '1'}]).replace(
            b'<dependencies>', b'<extension xmlns="urn:other"><dependencies><dependency><groupId>h</groupId>'
            b'<artifactId>hidden</artifactId><version>1</version></dependency></dependencies></extension><dependencies>')
        self.publish('org.x', 'child', '2.0', body)
        self.assertEqual(self.dependencies(), [('g', 'a', '1')])
        body = pom(dependencies=[{'groupId': 'g', 'artifactId': 'a', 'version': '1'}]).replace(
            b'</dependency>', b'</dependency><dependency xmlns="urn:other"><groupId>h</groupId><artifactId>foreign</artifactId>'
            b'<version>1</version></dependency>')
        self.publish('org.x', 'second', '2.0', body)
        self.assertEqual(self.dependencies(artifact='second'), [('g', 'a', '1')])

    def test_direct_parent_version_is_known_without_grandparent(self):
        self.publish('org.x', 'child', '2.0', pom(parent=('org.x', 'parent', '7.1'), dependencies=[
            {'groupId': 'g', 'artifactId': 'a', 'version': '${project.parent.version}'}]))
        self.publish('org.x', 'parent', '7.1', pom())
        self.assertEqual(self.dependencies(), [('g', 'a', '7.1')])


# --------------------------------------------------------------------------- runtime extras

WANTED = [
    ('M', 'com.adobe.xmp', 'xmpcore', 'xmpcoreVersion', None),
    ('M', 'com.github.jai-imageio', 'jai-imageio-core', 'jaiImageIOVersion', None),
    ('M', 'com.koloboke', 'koloboke-impl-jdk8', 'kolobokeVersion', None),
    ('M', 'com.zaxxer', 'HikariCP', 'hikariCPVersion', None),
    ('M', 'commons-logging', 'commons-logging', 'commonsLoggingVersion', None),
    ('M', 'net.sf.joost', 'joost', 'joostVersion', None),
    ('M', 'org.hibernate.common', 'hibernate-commons-annotations', 'hibernateAnnotationsVersion', None),
    ('M', 'org.jboss.logging', 'jboss-logging', 'jbossLoggingVersion', None),
    ('M', 'org.jboss.weld', 'weld-spi', 'weldApiVersion', None),
    ('M', 'org.ow2.asm', 'asm', 'ow2Version', None),
    ('M', 'org.ow2.asm', 'asm-commons', 'ow2Version', None),
    ('M', 'org.hibernate.orm', 'hibernate-hikaricp', 'hibernateCoreVersion', None),
    ('M', 'org.aspectj', 'aspectjweaver', 'aspectjVersion', None),
    ('J', 'org.jogamp.jocl', 'jocl-main', 'joclVersion', None),
    ('J', 'org.jogamp.jocl', 'jocl', 'joclVersion', None),
    ('J', 'org.jogamp.jocl', 'jocl', 'joclVersion', 'natives-linux-amd64'),
    ('J', 'org.jogamp.gluegen', 'gluegen-rt', 'joglVersion', None),
    ('J', 'org.jogamp.gluegen', 'gluegen-rt', 'joglVersion', 'natives-linux-amd64'),
    ('J', 'org.jogamp.jogl', 'jogl-all', 'joglVersion', 'natives-linux-amd64'),
    ('M', 'org.bytedeco', 'ffmpeg', 'ffmpegVersion', None),
    ('M', 'org.bytedeco', 'ffmpeg', 'ffmpegVersion', 'linux-x86_64'),
]
REPOSITORIES = {'M': 'https://repo1.maven.org/maven2/', 'J': 'https://jogamp.org/deployment/maven/'}


class RuntimeExtras(Case):
    DECLARED = 'declared in TAKX Gradle runtime configuration'
    CHECK = 'SHA-1 published by the Maven repository; no project pin exists'
    UNRESOLVED = 'transitive version not resolvable from the POM chain'

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        self.root.mkdir()
        self.takx = self.tmp / 'takx57'
        (self.takx / 'gradle').mkdir(parents=True)
        self.directory = self.root / 'jars/runtime-extras'

    def versions(self, **values):
        (self.takx / 'gradle/versions.gradle').write_text(''.join(f"    {k} = '{v}'\n" for k, v in values.items()))

    def url(self, repository, group, artifact, version, classifier=None):
        return (f'{REPOSITORIES[repository]}{group.replace(".", "/")}/{artifact}/{version}/'
                f'{artifact}-{version}{"-" + classifier if classifier else ""}.jar')

    def publish(self, repository, group, artifact, version, data, classifier=None, pom_body=None, sha1=None):
        url = self.url(repository, group, artifact, version, classifier)
        self.net.routes[url] = data
        self.net.routes[url + '.sha1'] = (sha(data, 'sha1') if sha1 is None else sha1).encode()
        if pom_body is not None:
            self.net.routes[url[:-4] + '.pom'] = pom_body
        return url

    def fetch(self, closure=()):
        F.fetch_runtime_extras(self.root, self.takx, set(closure))

    def results(self):
        return [r for r in self.records if not r['note'].startswith('version property ')]

    def expected(self, url, name, data, **changes):
        record = {'kind': 'runtime-extra-jar', 'name': name, 'source': url, 'method': 'direct',
                  'expected_sha1': sha(data, 'sha1'), 'actual_sha256': sha(data), 'verified': True, 'required': False,
                  'pinned_by_recipe_lock': False, 'also_in_compile_closure': False, 'reason': self.DECLARED,
                  'path': str(self.directory / url.rsplit('/', 1)[1]), 'verification': self.CHECK, 'note': ''}
        record.update(changes)
        return record

    def failed(self, url, name, data, **changes):
        return self.expected(url, name, data, **{'verified': False, 'actual_sha256': None, 'path': None, **changes})

    def test_missing_version_properties_are_reported_without_any_request(self):
        self.versions(unrelated='1')
        self.fetch()
        self.assertEqual(self.records, [
            {'kind': 'runtime-extra-jar', 'name': f'{group}:{artifact}', 'source': 'gradle/versions.gradle', 'method': 'direct',
             'verified': False, 'required': False, 'note': f'version property {key} not found in the official TAKX checkout'}
            for _, group, artifact, key, _ in WANTED])
        self.assertEqual(self.net.calls, [])
        self.assertTrue(self.directory.is_dir())

    def test_every_declared_artifact_is_requested_from_its_repository(self):
        keys = sorted({row[3] for row in WANTED})
        self.versions(**{key: f'{index}.0' for index, key in enumerate(keys)})
        self.fetch()
        expected = []
        for repository, group, artifact, key, classifier in WANTED:
            version = f'{keys.index(key)}.0'
            url = self.url(repository, group, artifact, version, classifier)
            expected.append({
                'kind': 'runtime-extra-jar', 'name': f'{group}:{artifact}:{version}' + (':' + classifier if classifier else ''),
                'source': url, 'method': 'direct', 'expected_sha1': None, 'actual_sha256': None, 'verified': False,
                'required': False, 'pinned_by_recipe_lock': False, 'also_in_compile_closure': False, 'reason': self.DECLARED,
                'path': None, 'verification': self.CHECK, 'note': 'no published SHA-1: HTTP Error 404: status 404'})
        self.assertEqual(self.records, expected)
        self.assertEqual(self.net.calls, [r['source'] + '.sha1' for r in expected])
        self.assertEqual(self.net.timeouts, [60] * 21)
        self.assertEqual(self.names(self.root), [])

    def test_artifact_is_checked_against_published_sha1(self):
        self.versions(xmpcoreVersion='6.1.11')
        url = self.publish('M', 'com.adobe.xmp', 'xmpcore', '6.1.11', b'jar', sha1=sha(b'jar', 'sha1').upper() + '  xmpcore-6.1.11.jar\n')
        self.fetch(closure={'other.jar'})
        self.assertEqual(self.results(), [self.expected(url, 'com.adobe.xmp:xmpcore:6.1.11', b'jar')])
        self.assertEqual(self.names(self.root), ['jars/runtime-extras/xmpcore-6.1.11.jar'])
        self.assertEqual((self.directory / 'xmpcore-6.1.11.jar').read_bytes(), b'jar')
        self.assertEqual(self.net.calls[:3], [url + '.sha1', url, url[:-4] + '.pom'])
        self.assertEqual(len(self.records), 20 + 1)
        # Second run: the verified file is kept and not downloaded again.
        self.net.calls.clear()
        self.records.clear()
        self.fetch(closure={'other.jar', 'xmpcore-6.1.11.jar'})
        self.assertEqual(self.results(), [self.expected(url, 'com.adobe.xmp:xmpcore:6.1.11', b'jar', also_in_compile_closure=True)])
        self.assertEqual(self.net.calls, [url + '.sha1', url[:-4] + '.pom'])

    def test_bytes_that_differ_from_the_published_sha1_are_deleted(self):
        self.versions(xmpcoreVersion='6.1.11')
        url = self.publish('M', 'com.adobe.xmp', 'xmpcore', '6.1.11', b'jar', sha1=sha(b'other', 'sha1'),
                           pom_body=pom(dependencies=[{'groupId': 'g', 'artifactId': 'a', 'version': '1'}]))
        self.fetch()
        self.assertEqual(self.results(), [self.failed(url, 'com.adobe.xmp:xmpcore:6.1.11', b'other')])
        self.assertEqual(self.names(self.root), [])
        self.assertEqual(self.net.calls, [url + '.sha1', url])

    def test_stale_file_is_replaced_or_removed(self):
        self.versions(xmpcoreVersion='6.1.11')
        url = self.publish('M', 'com.adobe.xmp', 'xmpcore', '6.1.11', b'jar')
        self.directory.mkdir(parents=True)
        stale = self.directory / 'xmpcore-6.1.11.jar'
        stale.write_bytes(b'stale')
        self.fetch()
        self.assertEqual(self.results(), [self.expected(url, 'com.adobe.xmp:xmpcore:6.1.11', b'jar')])
        self.assertEqual(stale.read_bytes(), b'jar')
        for label, change, note in (
                ('download fails', lambda: self.net.routes.update({url: [http_error(403)]}), 'HTTPError: HTTP Error 403: status 403'),
                ('no sha1', lambda: self.net.routes.pop(url + '.sha1'), 'no published SHA-1: HTTP Error 404: status 404'),
                ('empty sha1', lambda: self.net.routes.update({url + '.sha1': b' \n'}), 'no published SHA-1: list index out of range'),
                ('cut sha1', lambda: self.net.routes.update({url + '.sha1': [http.client.IncompleteRead(b'ab')]}),
                 'no published SHA-1: IncompleteRead(2 bytes read)')):
            with self.subTest(label):
                self.publish('M', 'com.adobe.xmp', 'xmpcore', '6.1.11', b'jar')
                stale.write_bytes(b'stale')
                self.records.clear()
                change()
                self.fetch()
                published = None if 'sha1' in label else sha(b'jar', 'sha1')
                self.assertEqual(self.results(), [self.failed(url, 'com.adobe.xmp:xmpcore:6.1.11', b'jar', expected_sha1=published,
                                                            note=note)])
                self.assertEqual(self.names(self.root), [])

    def test_pom_dependencies_are_followed_once_from_the_right_repository(self):
        self.versions(xmpcoreVersion='6', joclVersion='2.5', ffmpegVersion='7')
        top = self.publish('M', 'com.adobe.xmp', 'xmpcore', '6', b'top', pom_body=pom(dependencies=[
            {'groupId': 'org.dep', 'artifactId': 'first', 'version': '1.0'},
            {'groupId': 'org.dep', 'artifactId': 'unresolved', 'version': '${missing}'},
            {'groupId': 'org.dep', 'artifactId': 'range', 'version': '[1.0,2.0)'},
            {'groupId': 'org.dep', 'artifactId': 'unversioned'},
            {'groupId': 'org.jogamp.other', 'artifactId': 'from-central', 'version': '3'},
        ]))
        first = self.publish('M', 'org.dep', 'first', '1.0', b'first', pom_body=pom(dependencies=[
            {'groupId': 'com.adobe.xmp', 'artifactId': 'xmpcore', 'version': '6'},
            {'groupId': 'org.dep', 'artifactId': 'second', 'version': '2-beta+1'},
        ]))
        second = self.publish('M', 'org.dep', 'second', '2-beta+1', b'second')
        central = self.url('M', 'org.jogamp.other', 'from-central', '3')
        jocl = self.publish('J', 'org.jogamp.jocl', 'jocl', '2.5', b'jocl', pom_body=pom(dependencies=[
            {'groupId': 'org.jogamp.gluegen', 'artifactId': 'gluegen-rt', 'version': '2.5'},
            {'groupId': 'com.plain', 'artifactId': 'plain', 'version': '1'},
        ]))
        natives = self.publish('J', 'org.jogamp.jocl', 'jocl', '2.5', b'natives', classifier='natives-linux-amd64',
                               pom_body=pom(dependencies=[{'groupId': 'never', 'artifactId': 'read', 'version': '1'}]))
        gluegen = self.publish('J', 'org.jogamp.gluegen', 'gluegen-rt', '2.5', b'gluegen')
        plain = self.url('M', 'com.plain', 'plain', '1')
        ffmpeg = self.publish('M', 'org.bytedeco', 'ffmpeg', '7', b'ffmpeg', pom_body=pom(dependencies=[
            {'groupId': 'org.bytedeco', 'artifactId': 'javacpp', 'version': '1.5'},
            {'groupId': 'org.bytedeco.other', 'artifactId': 'not-preset', 'version': '1'},
        ]))
        ffmpeg_natives = self.publish('M', 'org.bytedeco', 'ffmpeg', '7', b'ffmpeg natives', classifier='linux-x86_64')
        javacpp = self.publish('M', 'org.bytedeco', 'javacpp', '1.5', b'javacpp')
        javacpp_natives = self.publish('M', 'org.bytedeco', 'javacpp', '1.5', b'javacpp natives', classifier='linux-x86_64')
        not_preset = self.url('M', 'org.bytedeco.other', 'not-preset', '1')
        jocl_main = self.url('J', 'org.jogamp.jocl', 'jocl-main', '2.5')
        self.fetch()

        def unresolved(name, source):
            return {'kind': 'runtime-extra-jar', 'name': name, 'source': source, 'method': 'direct', 'verified': False,
                    'required': False, 'note': self.UNRESOLVED}

        def absent(url, name, reason):
            return self.failed(url, name, b'', expected_sha1=None, reason=reason, note='no published SHA-1: HTTP Error 404: status 404')
        from_top, from_first = 'POM dependency of com.adobe.xmp:xmpcore:6', 'POM dependency of org.dep:first:1.0'
        from_jocl, from_ffmpeg = 'POM dependency of org.jogamp.jocl:jocl:2.5', 'POM dependency of org.bytedeco:ffmpeg:7'
        self.assertEqual(self.records, [
            {'kind': 'runtime-extra-jar', 'name': f'{row[1]}:{row[2]}', 'source': 'gradle/versions.gradle', 'method': 'direct',
             'verified': False, 'required': False, 'note': f'version property {row[3]} not found in the official TAKX checkout'}
            for row in WANTED if row[3] not in ('xmpcoreVersion', 'joclVersion', 'ffmpegVersion')] + [
            self.expected(top, 'com.adobe.xmp:xmpcore:6', b'top'),
            unresolved('org.dep:unresolved:${missing}', 'POM of com.adobe.xmp:xmpcore:6'),
            unresolved('org.dep:range:[1.0,2.0)', 'POM of com.adobe.xmp:xmpcore:6'),
            unresolved('org.dep:unversioned:?', 'POM of com.adobe.xmp:xmpcore:6'),
            absent(jocl_main, 'org.jogamp.jocl:jocl-main:2.5', self.DECLARED),
            self.expected(jocl, 'org.jogamp.jocl:jocl:2.5', b'jocl'),
            self.expected(natives, 'org.jogamp.jocl:jocl:2.5:natives-linux-amd64', b'natives'),
            self.expected(ffmpeg, 'org.bytedeco:ffmpeg:7', b'ffmpeg'),
            self.expected(ffmpeg_natives, 'org.bytedeco:ffmpeg:7:linux-x86_64', b'ffmpeg natives'),
            self.expected(first, 'org.dep:first:1.0', b'first', reason=from_top),
            absent(central, 'org.jogamp.other:from-central:3', from_top),
            self.expected(gluegen, 'org.jogamp.gluegen:gluegen-rt:2.5', b'gluegen', reason=from_jocl),
            absent(plain, 'com.plain:plain:1', from_jocl),
            self.expected(javacpp, 'org.bytedeco:javacpp:1.5', b'javacpp', reason=from_ffmpeg),
            self.expected(javacpp_natives, 'org.bytedeco:javacpp:1.5:linux-x86_64', b'javacpp natives',
                          reason='linux natives of org.bytedeco:javacpp:1.5'),
            absent(not_preset, 'org.bytedeco.other:not-preset:1', from_ffmpeg),
            self.expected(second, 'org.dep:second:2-beta+1', b'second', reason=from_first),
        ])
        self.assertEqual(self.net.calls.count(top), 1)
        self.assertNotIn(natives[:-4] + '.pom', self.net.calls)
        self.assertNotIn(self.url('M', 'never', 'read', '1') + '.sha1', self.net.calls)
        self.assertNotIn(self.url('M', 'org.bytedeco.other', 'not-preset', '1', 'linux-x86_64') + '.sha1', self.net.calls)

    def test_architecture_names_come_from_the_one_table(self):
        self.assertEqual((F.arch_names()['arch'], F.arch_names()['jogamp'], F.arch_names()['bytedeco'],
                          F.arch_names()['debian']), ('amd64', 'natives-linux-amd64', 'linux-x86_64', 'amd64'))
        self.assertEqual(F.runtime_native_pins('amd64'), {})
        os.environ['TAKX_PROTO_ARCH'] = 'arm64'
        self.assertEqual((F.arch_names()['arch'], F.arch_names()['jogamp'], F.arch_names()['bytedeco'],
                          F.arch_names()['debian']), ('arm64', 'natives-linux-aarch64', 'linux-arm64', 'arm64'))
        pins = F.runtime_native_pins('arm64')
        self.assertEqual(sorted(pins), ['org.bytedeco:ffmpeg:4.1.3-1.5.1:linux-arm64',
                                        'org.jogamp.gluegen:gluegen-rt:2.5.0:natives-linux-aarch64',
                                        'org.jogamp.jocl:jocl:2.5.0:natives-linux-aarch64',
                                        'org.jogamp.jogl:jogl-all:2.5.0:natives-linux-aarch64'])
        for value in pins.values():
            self.assertRegex(value, r'^[0-9a-f]{64}$')
        os.environ['TAKX_PROTO_ARCH'] = 'riscv64'
        with self.assertRaises(subprocess.CalledProcessError):
            F.arch_names()

    def test_arm64_requests_its_own_classifiers_and_never_the_amd64_ones(self):
        os.environ['TAKX_PROTO_ARCH'] = 'arm64'
        self.patch(F, 'runtime_native_pins', lambda arch: {})
        keys = sorted({row[3] for row in WANTED})
        self.versions(**{key: f'{index}.0' for index, key in enumerate(keys)})
        self.fetch()
        swap = {'natives-linux-amd64': 'natives-linux-aarch64', 'linux-x86_64': 'linux-arm64'}
        self.assertEqual([r['name'] for r in self.records], [
            f'{group}:{artifact}:{keys.index(key)}.0' + (':' + swap[classifier] if classifier else '')
            for _, group, artifact, key, classifier in WANTED])
        self.assertFalse([url for url in self.net.calls if 'amd64' in url or 'x86_64' in url])
        self.assertEqual(len([url for url in self.net.calls if 'natives-linux-aarch64' in url]), 3)
        self.assertEqual(len([url for url in self.net.calls if 'linux-arm64' in url]), 1)

    def test_arm64_classifier_jars_are_checked_against_their_own_pins(self):
        os.environ['TAKX_PROTO_ARCH'] = 'arm64'
        lock = 'takx/desktop-prototype/inputs.arm64.lock.json'
        good, bad = 'org.jogamp.jocl:jocl:2.5:natives-linux-aarch64', 'org.bytedeco:ffmpeg:7:linux-arm64'
        self.patch(F, 'runtime_native_pins', lambda arch: {good: sha(b'natives'), bad: sha(b'what was pinned')}
                   if arch == 'arm64' else self.fail('pins of another architecture were read'))
        self.versions(joclVersion='2.5', ffmpegVersion='7')
        natives = self.publish('J', 'org.jogamp.jocl', 'jocl', '2.5', b'natives', classifier='natives-linux-aarch64')
        ffmpeg = self.publish('M', 'org.bytedeco', 'ffmpeg', '7', b'ffmpeg', pom_body=pom(dependencies=[
            {'groupId': 'org.bytedeco', 'artifactId': 'javacpp', 'version': '1.5'}]))
        ffmpeg_natives = self.publish('M', 'org.bytedeco', 'ffmpeg', '7', b'published, but not what was pinned',
                                      classifier='linux-arm64')
        javacpp_natives = self.publish('M', 'org.bytedeco', 'javacpp', '1.5', b'javacpp natives', classifier='linux-arm64')
        self.fetch()
        by_name = {r['name']: r for r in self.results()}
        self.assertEqual(by_name[good], self.expected(natives, good, b'natives', expected_sha256=sha(b'natives'),
                                                      pinned_by=lock))
        # The repository's own SHA-1 matched, the architecture pin did not.
        self.assertEqual(by_name[bad], self.failed(ffmpeg_natives, bad, b'published, but not what was pinned',
                                                   expected_sha256=sha(b'what was pinned'), pinned_by=lock,
                                                   note='differs from the architecture pin'))
        self.assertFalse((self.directory / 'ffmpeg-7-linux-arm64.jar').exists())
        self.assertTrue(by_name['org.bytedeco:ffmpeg:7']['verified'])
        self.assertEqual(by_name['org.bytedeco:ffmpeg:7']['source'], ffmpeg)
        self.assertNotIn('expected_sha256', by_name['org.bytedeco:ffmpeg:7'])
        # Transitive JavaCPP presets get the classifier of this architecture too; no pin exists for them.
        unpinned = by_name['org.bytedeco:javacpp:1.5:linux-arm64']
        self.assertEqual((unpinned['source'], unpinned['verified']), (javacpp_natives, True))
        self.assertNotIn('expected_sha256', unpinned)

    def test_coordinates_that_are_not_plain_names_never_become_paths(self):
        self.versions(xmpcoreVersion='6')
        evil = [('org.dep', '../../../escape', '1'), ('org.dep', 'a', '../1'), ('org/dep', 'a', '1'), ('org.dep', 'a/b', '1'),
                ('org.dep', '.hidden', '1'), ('org.dep', 'a', '1 2'), ('', 'a', '1'), ('org.dep', '', '1'),
                ('org.dep', 'a\\b', '1'), ('-g', 'a', '1'), ('org.dep', 'a', '1\n'), ('org.dep', 'a?x', '1'),
                ('org.dep', 'a', '1%2e'), ('org.dep', 'a:b', '1')]
        top = self.publish('M', 'com.adobe.xmp', 'xmpcore', '6', b'top', pom_body=pom(dependencies=[
            {'groupId': g, 'artifactId': a, 'version': v} for g, a, v in evil] + [
            {'groupId': 'Org_dep-x.y', 'artifactId': 'Fine_name-1.x+y', 'version': '1.0-b_2+c'}]))
        fine = self.url('M', 'Org_dep-x.y', 'Fine_name-1.x+y', '1.0-b_2+c')
        self.patch(F, 'download', mock.Mock(side_effect=F.download))
        self.fetch()
        self.assertEqual(self.results()[1:-1], [
            {'kind': 'runtime-extra-jar', 'name': f'{g}:{a}:{v}', 'source': 'POM of com.adobe.xmp:xmpcore:6', 'method': 'direct',
             'verified': False, 'required': False, 'note': 'transitive coordinate is not a plain Maven name; refused'}
            for g, a, v in evil])
        self.assertEqual(self.records[-1]['source'], fine)
        self.assertEqual(self.net.calls, [top + '.sha1', top, top[:-4] + '.pom', fine + '.sha1'])
        self.assertEqual(F.download.call_count, 1)
        self.assertEqual(self.names(self.tmp), ['root/jars/runtime-extras/xmpcore-6.jar', 'takx57/gradle/versions.gradle'])


# --------------------------------------------------------------------------- Debian tools

class CommoncommoTools(Case):
    BASE = 'https://deb.example/dists/debian'
    INRELEASE, PACKAGES, ANT, DOS2UNIX = b'signed release', b'packages index', b'ant package', b'dos2unix package'
    LOCK = 'takx/native-commoncommo/inputs.lock.json'
    DIFFERS = ('live file differs from the pin (a Debian point release replaces InRelease; '
               'the pinned bytes then only exist on snapshot.debian.org)')

    def setUp(self):
        super().setUp()
        self.takx = self.tmp / 'repo/takx'
        (self.takx / 'native-commoncommo').mkdir(parents=True)
        self.patch(F, 'TAKX', self.takx)
        self.root = self.tmp / 'root'
        self.root.mkdir()
        self.metadata = self.root / 'cache-commoncommo-tools/metadata'
        self.extracted = self.root / 'cache-commoncommo-tools/root'
        self.urls = {
            'InRelease': self.BASE + '/dists/trixie/InRelease',
            'Packages.xz': self.BASE + '/dists/trixie/main/binary-amd64/by-hash/SHA256/' + sha(self.PACKAGES),
            'ant_1.10_all.deb': self.BASE + '/pool/main/a/ant/ant_1.10_all.deb',
            'dos2unix_7_amd64.deb': self.BASE + '/pool/main/d/dos2unix/dos2unix_7_amd64.deb'}
        self.content = dict(zip(self.urls, (self.INRELEASE, self.PACKAGES, self.ANT, self.DOS2UNIX)))
        for name, url in self.urls.items():
            self.net.routes[url] = self.content[name]
        self.write_lock()
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()
        self.invocations = self.tmp / 'dpkg-deb.log'
        (self.bin / 'dpkg-deb').write_text(
            '#!/bin/sh\necho "$*" >> "%s"\n[ "$1" = -x ] || exit 9\n[ -z "$FAIL_DPKG" ] || exit 7\n'
            'cp "$2" "$3/$(basename "$2").extracted"\n' % self.invocations)
        (self.bin / 'dpkg-deb').chmod(0o755)
        os.environ['PATH'] = f'{self.bin}:{os.environ["PATH"]}'

    def write_lock(self, **changes):
        tools = {'metadata_url': self.urls['InRelease'], 'inrelease_sha256': sha(self.INRELEASE),
                 'packages_sha256': sha(self.PACKAGES), 'packages_bytes': len(self.PACKAGES),
                 'packages': [{'Package': 'ant', 'Filename': 'pool/main/a/ant/ant_1.10_all.deb', 'Size': str(len(self.ANT)),
                               'SHA256': sha(self.ANT)},
                              {'Package': 'dos2unix', 'Filename': 'pool/main/d/dos2unix/dos2unix_7_amd64.deb',
                               'Size': str(len(self.DOS2UNIX)), 'SHA256': sha(self.DOS2UNIX)}]}
        tools.update(changes)
        (self.takx / 'native-commoncommo/inputs.lock.json').write_text(json.dumps({'tools': tools}))
        return tools

    def expected(self, name, **changes):
        record = {'kind': 'debian-tool-metadata', 'name': name, 'source': self.urls[name], 'method': 'direct',
                  'expected_sha256': sha(self.content[name]), 'actual_sha256': sha(self.content[name]), 'verified': True,
                  'required': True, 'path': str(self.metadata / name),
                  'consumers': [{'recipe': 'native-commoncommo', 'lock': self.LOCK}], 'note': ''}
        record.update(changes)
        return record

    def failed(self, name, **changes):
        return self.expected(name, **{'verified': False, 'actual_sha256': None, 'path': None, **changes})

    def test_everything_verified_is_stored_and_extracted(self):
        stale = self.root / 'cache-commoncommo-tools/.root-staging'
        stale.mkdir(parents=True)
        (stale / 'left-over').write_bytes(b'x')
        calls = self.spy_run()
        targets = self.spy_download()
        F.fetch_commoncommo_tools(self.root)
        self.assertEqual(targets, [self.metadata / ('.candidate-' + name) for name in self.urls])
        self.assertEqual(self.records, [self.expected(name) for name in self.urls])
        self.assertEqual(self.net.calls, list(self.urls.values()))
        self.assertEqual(self.names(self.root / 'cache-commoncommo-tools'), [
            'metadata/InRelease', 'metadata/Packages.xz', 'metadata/ant_1.10_all.deb', 'metadata/dos2unix_7_amd64.deb',
            'root/ant_1.10_all.deb.extracted', 'root/dos2unix_7_amd64.deb.extracted'])
        staging = str(stale)
        self.assertEqual(calls, [(['dpkg-deb', '-x', str(self.metadata / 'ant_1.10_all.deb'), staging], {}),
                                 (['dpkg-deb', '-x', str(self.metadata / 'dos2unix_7_amd64.deb'), staging], {})])
        self.assertEqual((self.extracted / 'ant_1.10_all.deb.extracted').read_bytes(), self.ANT)
        # Second run: all cached, the extracted tree is left alone.
        self.net.calls.clear()
        self.records.clear()
        self.invocations.unlink()
        F.fetch_commoncommo_tools(self.root)
        self.assertEqual(self.records, [self.expected(name) for name in self.urls])
        self.assertEqual(self.net.calls, [])
        self.assertFalse(self.invocations.exists())

    def test_any_file_that_differs_from_its_pin_blocks_extraction(self):
        for name in self.urls:
            with self.subTest(name=name):
                shutil.rmtree(self.root)
                self.root.mkdir()
                self.records.clear()
                for other, url in self.urls.items():
                    self.net.routes[url] = self.content[other]
                self.net.routes[self.urls[name]] = b'replaced upstream'
                F.fetch_commoncommo_tools(self.root)
                self.assertEqual(self.records, [self.failed(n, note=self.DIFFERS) if n == name else self.expected(n)
                                                for n in self.urls])
                self.assertEqual(self.names(self.root), sorted(
                    'cache-commoncommo-tools/metadata/' + n for n in self.urls if n != name))
                self.assertFalse(self.invocations.exists())

    def test_download_error_is_recorded(self):
        self.net.routes[self.urls['Packages.xz']] = [http_error(404)]
        F.fetch_commoncommo_tools(self.root)
        self.assertEqual(self.records[1], self.failed('Packages.xz', note='HTTPError: HTTP Error 404: status 404'))
        self.assertEqual([r['verified'] for r in self.records], [True, False, True, True])
        self.assertFalse(self.extracted.exists())
        self.assertEqual(self.names(self.metadata), ['InRelease', 'ant_1.10_all.deb', 'dos2unix_7_amd64.deb'])

    def test_locked_sizes_are_enforced(self):
        self.write_lock(packages_bytes=len(self.PACKAGES) + 1)
        F.fetch_commoncommo_tools(self.root)
        self.assertEqual(self.records[1], self.failed('Packages.xz'))
        self.assertFalse(self.extracted.exists())
        tools = self.write_lock()
        tools['packages'][1]['Size'] = str(len(self.DOS2UNIX) - 1)
        self.write_lock(packages=tools['packages'])
        self.records.clear()
        F.fetch_commoncommo_tools(self.root)
        self.assertEqual(self.records, [self.expected('InRelease'), self.expected('Packages.xz'),
                                        self.expected('ant_1.10_all.deb'), self.failed('dos2unix_7_amd64.deb')])
        self.assertFalse(self.extracted.exists())
        self.assertFalse(self.invocations.exists())

    def test_corrupted_stored_file_is_downloaded_again_or_fails(self):
        F.fetch_commoncommo_tools(self.root)
        (self.metadata / 'InRelease').write_bytes(b'signed releasE')
        shutil.rmtree(self.extracted)
        self.invocations.unlink()
        self.net.routes[self.urls['InRelease']] = [http_error(404)]
        self.records.clear()
        self.net.calls.clear()
        F.fetch_commoncommo_tools(self.root)
        self.assertEqual(self.records[0], self.failed('InRelease', note='HTTPError: HTTP Error 404: status 404'))
        self.assertEqual(self.net.calls, [self.urls['InRelease']])
        self.assertFalse(self.extracted.exists())
        self.net.routes[self.urls['InRelease']] = [self.INRELEASE]
        self.records.clear()
        F.fetch_commoncommo_tools(self.root)
        self.assertEqual(self.records, [self.expected(name) for name in self.urls])
        self.assertEqual((self.metadata / 'InRelease').read_bytes(), self.INRELEASE)
        self.assertTrue(self.extracted.is_dir())

    def test_arm64_reads_the_arm64_package_index(self):
        os.environ['TAKX_PROTO_ARCH'] = 'arm64'
        amd64 = self.urls['Packages.xz']
        self.urls['Packages.xz'] = amd64.replace('binary-amd64', 'binary-arm64')
        self.assertNotEqual(self.urls['Packages.xz'], amd64)
        self.net.routes[self.urls['Packages.xz']] = self.net.routes.pop(amd64)
        F.fetch_commoncommo_tools(self.root)
        self.assertEqual(self.records, [self.expected(name) for name in self.urls])
        self.assertEqual(self.net.calls, list(self.urls.values()))
        self.assertTrue(self.extracted.is_dir())

    def official(self):
        """Point the lock at the official archive, where a dated snapshot exists."""
        for url in self.urls.values():
            del self.net.routes[url]
        self.urls = {name: url.replace(self.BASE, F.DEBIAN_ARCHIVE) for name, url in self.urls.items()}
        self.write_lock()
        self.snapshot = {
            'InRelease': F.DEBIAN_SNAPSHOT + '/dists/trixie/InRelease',
            'Packages.xz': F.DEBIAN_SNAPSHOT + '/dists/trixie/main/binary-amd64/Packages.xz',
            'ant_1.10_all.deb': F.DEBIAN_SNAPSHOT + '/pool/main/a/ant/ant_1.10_all.deb',
            'dos2unix_7_amd64.deb': F.DEBIAN_SNAPSHOT + '/pool/main/d/dos2unix/dos2unix_7_amd64.deb'}

    def test_pinned_bytes_replaced_upstream_come_from_the_dated_snapshot(self):
        self.official()
        self.assertTrue(F.DEBIAN_SNAPSHOT.startswith('https://snapshot.debian.org/archive/debian/2'))
        # A point release replaced InRelease, the old by-hash index is gone, one package still answers.
        self.net.routes[self.urls['InRelease']] = b'the next point release'
        self.net.routes[self.urls['ant_1.10_all.deb']] = self.ANT
        for name in ('InRelease', 'Packages.xz', 'dos2unix_7_amd64.deb'):
            self.net.routes[self.snapshot[name]] = self.content[name]
        F.fetch_commoncommo_tools(self.root)
        moved = '; the pinned bytes were fetched from the dated snapshot instead'
        self.assertEqual(self.records, [
            self.expected('InRelease', source=self.snapshot['InRelease'], note=self.DIFFERS + moved),
            self.expected('Packages.xz', source=self.snapshot['Packages.xz'],
                          note='HTTPError: HTTP Error 404: status 404' + moved),
            self.expected('ant_1.10_all.deb'),
            self.expected('dos2unix_7_amd64.deb', source=self.snapshot['dos2unix_7_amd64.deb'],
                          note='HTTPError: HTTP Error 404: status 404' + moved)])
        self.assertNotIn(self.snapshot['ant_1.10_all.deb'], self.net.calls)
        self.assertEqual((self.metadata / 'InRelease').read_bytes(), self.INRELEASE)
        self.assertTrue(self.extracted.is_dir())
        # The pins did not move: the digests recorded are the locked ones.
        self.assertEqual([r['expected_sha256'] for r in self.records], [sha(self.content[name]) for name in self.urls])

    def test_snapshot_that_differs_from_the_pin_is_refused_too(self):
        self.official()
        for name in self.urls:
            self.net.routes[self.urls[name]] = self.content[name]
        self.net.routes[self.urls['InRelease']] = b'the next point release'
        self.net.routes[self.snapshot['InRelease']] = b'not the pinned release either'
        self.net.routes[self.urls['Packages.xz']] = [http_error(404)]
        F.fetch_commoncommo_tools(self.root)
        self.assertEqual(self.records[0], self.failed(
            'InRelease', note=self.DIFFERS + '; snapshot %s: differs from the pin' % self.snapshot['InRelease']))
        self.assertEqual(self.records[1], self.failed(
            'Packages.xz', note='HTTPError: HTTP Error 404: status 404; snapshot %s: HTTPError: HTTP Error 404: status 404'
            % self.snapshot['Packages.xz']))
        self.assertEqual([r['verified'] for r in self.records], [False, False, True, True])
        self.assertFalse(self.extracted.exists())
        self.assertEqual(self.names(self.metadata), ['ant_1.10_all.deb', 'dos2unix_7_amd64.deb'])

    def test_only_the_official_archive_has_a_snapshot(self):
        # Another base (the fixtures use one) is never looked up on snapshot.debian.org.
        self.net.routes[self.urls['InRelease']] = b'replaced upstream'
        F.fetch_commoncommo_tools(self.root)
        self.assertEqual(self.records[0], self.failed('InRelease', note=self.DIFFERS))
        self.assertFalse([url for url in self.net.calls if 'snapshot.debian.org' in url])

    def test_without_dpkg_deb_nothing_is_extracted(self):
        self.patch(F.shutil, 'which', lambda name: None if name == 'dpkg-deb' else '/usr/bin/' + name)
        F.fetch_commoncommo_tools(self.root)
        self.assertEqual([r['verified'] for r in self.records], [True] * 4)
        self.assertEqual(self.names(self.root / 'cache-commoncommo-tools'), ['metadata/' + n for n in sorted(self.urls)])

    def test_failed_extraction_is_not_published(self):
        os.environ['FAIL_DPKG'] = '1'
        with self.assertRaises(subprocess.CalledProcessError):
            F.fetch_commoncommo_tools(self.root)
        self.assertFalse(self.extracted.exists())
        del os.environ['FAIL_DPKG']
        F.fetch_commoncommo_tools(self.root)
        self.assertEqual(self.names(self.extracted), ['ant_1.10_all.deb.extracted', 'dos2unix_7_amd64.deb.extracted'])


# --------------------------------------------------------------------------- main

class Abort(Exception):
    pass


class Main(Case):
    LIB, MENTIONED, UNPINNED = b'pinned lfs archive', b'lfs object named by a lock', b'lfs object no lock names'
    TOOL, OPTIONAL, JAR, PORT, EXTRA = b'tool archive', b'optional source', b'public jar', b'port archive', b'extra jar'
    INRELEASE, PACKAGES, DEB = b'release', b'packages', b'deb'
    GROUPS = ('locks', 'lfs', 'cesium', 'git', 'jars', 'extras', 'debian')
    CLONE = 'atak-civ writable clone (LFS pointers, real bytes in src/atak-civ-lfs)'

    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix='fetch-inputs-main-')).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.base, True)
        cls.repo = cls.base / 'repo'
        cls.submodule = cls.repo / 'upstream/atak-civ'
        cls.revision = make_repo(cls.submodule, {
            'README.md': b'atak\n', 'depends/lib.tar.gz': pointer_text(cls.LIB),
            'depends/mentioned.bin': pointer_text(cls.MENTIONED), 'depends/unpinned.bin': pointer_text(cls.UNPINNED)})
        cls.takx_revision = make_repo(cls.base / 'takx-origin', {
            'gradle/versions.gradle': b"ext {\n    xmpcoreVersion = '6'\n}\n", 'assets/model.bin': pointer_text(b'model')})
        cls.worldwind_revision = make_repo(cls.base / 'worldwind-origin', {'build.xml': b'<project/>\n'})

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'work root'
        self.takx = self.tmp / 'takx'
        self.patch(F, 'REPO', self.repo)
        self.patch(F, 'TAKX', self.takx)
        self.patch(F, 'TAKX_REPOSITORY', 'file://' + str(self.base / 'takx-origin'))
        self.patch(F, 'TAKX_REVISION', self.takx_revision)
        self.patch(F.shutil, 'which', lambda name: None)
        self.media = F.LFS_MEDIA + self.revision + '/depends/'
        self.maven = 'https://repo1.maven.org/maven2/'
        self.extra_url = self.maven + 'com/adobe/xmp/xmpcore/6/xmpcore-6.jar'
        self.cesium_url = 'https://github.com/CesiumGS/cesium-native/archive/' + 'f' * 40 + '.tar.gz'
        self.cesium = make_tar([('cesium-native-' + 'f' * 40 + '/a.txt', 'file', b'a')])
        self.net.routes.update({
            self.media + 'lib.tar.gz': self.LIB, self.media + 'mentioned.bin': self.MENTIONED,
            self.media + 'unpinned.bin': self.UNPINNED, 'https://files.example/tool.tar.xz': self.TOOL,
            'https://files.example/optional.java': self.OPTIONAL, self.maven + 'org/a/lib/1.0/lib-1.0.jar': self.JAR,
            self.extra_url: self.EXTRA, self.extra_url + '.sha1': sha(self.EXTRA, 'sha1').encode(),
            'https://deb.example/debian/dists/trixie/InRelease': self.INRELEASE,
            'https://deb.example/debian/dists/trixie/main/binary-amd64/by-hash/SHA256/' + sha(self.PACKAGES): self.PACKAGES,
            'https://deb.example/debian/pool/a/ant.deb': self.DEB})
        self.locks = {
            'sources.lock.json': {'atak_source': {'revision': self.revision}, 'archives': [
                {'path': 'depends/lib.tar.gz', 'sha256': sha(self.LIB), 'bytes': len(self.LIB), 'url': self.media + 'lib.tar.gz'}]},
            'native-x/inputs.lock.json': {'tool.tar.xz': {'url': 'https://files.example/tool.tar.xz', 'sha256': sha(self.TOOL),
                                                          'bytes': len(self.TOOL)},
                                          'outputs': {'mentioned': sha(self.MENTIONED)}},
            'compat/source.lock.json': {'optional.java': {'url': 'https://files.example/optional.java', 'sha256': sha(self.OPTIONAL)}},
            'native-cesium/inputs.lock.json': {
                'cesium': {'commit': 'f' * 40, 'source_url': self.cesium_url, 'source_sha512': None},
                'ports': [{'port': 'port', 'selected_action': 'acquire_source', 'source_sha512': sha(self.PORT, 'sha512'),
                           'source_url': 'https://github.com/o/port/archive/v1.0.tar.gz'}]},
            'native-cesium/cesium-tree.json': [{'path': 'a.txt', 'type': 'blob', 'sha': blob(b'a'), 'size': 1}],
            'native-commoncommo/inputs.lock.json': {'tools': {
                'metadata_url': 'https://deb.example/debian/dists/trixie/InRelease', 'inrelease_sha256': sha(self.INRELEASE),
                'packages_sha256': sha(self.PACKAGES), 'packages_bytes': len(self.PACKAGES),
                'packages': [{'Filename': 'pool/a/ant.deb', 'Size': str(len(self.DEB)), 'SHA256': sha(self.DEB)}]}},
            'worldwind-java/inputs.lock.json': {'origin': 'file://' + str(self.base / 'worldwind-origin'),
                                                'revision': self.worldwind_revision},
            'client-compiler/inputs.lock.json': {'files': {
                'closure:maven-public/org.a--lib-1.0.jar': sha(self.JAR), 'closure:ui-original/built.jar': sha(b'built later'),
                'closure:ui-original/tool.jar': sha(self.TOOL), 'closure:probe/complete.json': '1' * 64}},
            'gis-java/sources.lock.json': {'archives': {'lib': {'sha256': sha(self.LIB)}, 'absent': {'sha256': '0' * 64}}},
        }
        self.write_locks()

        def archive(owner, repository, reference, prefix, target, include_lfs=False):
            target.write_bytes(self.cesium if repository == 'cesium-native' else self.PORT)
            return 'c' * 40, 0
        self.patch(F, 'git_archive', archive)
        self.jobs = []
        executor = F.concurrent.futures.ThreadPoolExecutor

        class Recording(executor):
            def __init__(inner, *arguments, **keywords):
                self.jobs.append((arguments, keywords))
                super().__init__(*arguments, **keywords)
        self.patch(F.concurrent.futures, 'ThreadPoolExecutor', Recording)

    def write_locks(self):
        for relative, data in self.locks.items():
            path = self.takx / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))

    def main(self, *arguments, root=True):
        """Run main(); refuses to go on if the parsed work root is anywhere but this test's directory."""
        real = F.argparse.ArgumentParser.parse_args

        def guarded(parser, *a, **k):
            parsed = real(parser, *a, **k)
            if self.tmp not in Path(parsed.root).resolve().parents and self.base not in Path(parsed.root).resolve().parents \
                    and Path(parsed.root).resolve() != self.repo:
                raise AssertionError(f'work root escaped the test directory: {parsed.root}')
            return parsed
        ticks = iter([1000.0])
        self.clock.time = lambda: next(ticks, 1007.6)
        argv = ['fetch_inputs.py'] + (['--root', str(self.root)] if root else []) + list(arguments)
        with mock.patch.object(F.argparse.ArgumentParser, 'parse_args', guarded), mock.patch.object(sys, 'argv', argv):
            return F.main()

    def status(self, name='fetch.json'):
        return json.loads((self.root / 'status' / name).read_text())

    def by_kind(self, status):
        kinds = {}
        for item in status['inputs']:
            kinds.setdefault(item['kind'], []).append(item)
        return kinds

    def assert_fails_closed(self, status, code):
        """No required, unverified input may coexist with an ok status or a zero exit code."""
        unverified = [r['name'] for r in status['inputs'] if not r['verified'] and r.get('required') and not r.get('built_by_prototype')]
        self.assertEqual(status['failed_required'], unverified)
        self.assertEqual(status['status'], 'failed' if unverified else 'ok')
        self.assertEqual(code, 1 if unverified else 0)
        for item in status['inputs']:
            self.assertIs(type(item['verified']), bool)
            if not item['verified']:
                self.assertIsNone(item.get('path') if item['kind'] != 'git-source' else None, item)
                self.assertTrue(item['name'] in status['failed_required'] or item['name'] in status['failed_optional']
                                or item.get('built_by_prototype'), item)
        totals = {kind: {'total': len(items), 'verified': sum(r['verified'] for r in items)}
                  for kind, items in self.by_kind(status).items()}
        self.assertEqual(status['counts'], totals)

    def test_full_run_writes_ok_status_only_when_everything_required_verified(self):
        cache = self.root / 'cache'
        cache.mkdir(parents=True)
        (self.root / 'status').mkdir()
        for stale in ('cache/.candidate-old', 'cache/.partial-old', 'cache/kept', 'status/.candidate-elsewhere'):
            (self.root / stale).write_bytes(b'stale')
        replaced = []
        real_replace = Path.replace

        def replace(path, target):
            replaced.append((path, target))
            return real_replace(path, target)
        self.patch(F.Path, 'replace', replace)
        self.spy_run()
        self.assertEqual(self.main(), 0)
        self.assert_hermetic_git()
        self.assertEqual(self.jobs, [((4,), {})])
        self.assertEqual(replaced, [(self.root / 'status/.fetch.json', self.root / 'status/fetch.json')])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [
            'cache', 'cache-cesium', 'cache-commoncommo-tools', 'cache-gis-java', 'inputs', 'jars', 'logs', 'src', 'status'])
        text = (self.root / 'status/fetch.json').read_text()
        status = json.loads(text)
        self.assertEqual(text, json.dumps(status, indent=1) + '\n')
        self.assertEqual(sorted(p.name for p in (self.root / 'status').iterdir()), ['.candidate-elsewhere', 'fetch.json'])
        self.assertEqual(list(status), ['stage', 'status', 'seconds', 'outputs', 'notes', 'counts', 'failed_required',
                                        'failed_optional', 'inputs'])
        self.assertEqual((status['stage'], status['status'], status['seconds']), ('fetch', 'ok', 8))
        self.assertEqual(status['outputs'], [str(self.root / n) for n in (
            'cache', 'cache-cesium', 'cache-gis-java', 'cache-commoncommo-tools', 'inputs', 'jars', 'src/atak-civ',
            'src/atak-civ-lfs', 'src/takx57', 'src/WorldWindJava')])
        self.assertEqual(status['counts'], {
            'atak-civ-lfs': {'total': 3, 'verified': 3}, 'cesium-source': {'total': 2, 'verified': 2},
            'client-compiler-jar': {'total': 3, 'verified': 2}, 'debian-tool-metadata': {'total': 3, 'verified': 3},
            'git-source': {'total': 3, 'verified': 3}, 'lock-entry': {'total': 3, 'verified': 3},
            'runtime-extra-jar': {'total': 21, 'verified': 1}})
        self.assertEqual(list(status['counts']), sorted(status['counts']))
        self.assertEqual(status['notes'], '17 of 38 records verified; 0 required and 20 optional inputs unverified; '
                                          '1 client-compiler JARs are build products, not fetched')
        self.assertEqual(status['failed_required'], [])
        missing = sorted(f'{row[1]}:{row[2]}' for row in WANTED if row[3] != 'xmpcoreVersion')
        self.assertEqual(status['failed_optional'], missing)
        self.assertEqual(status['inputs'], json.loads(json.dumps(self.records)))
        self.assertEqual([(r['kind'], r['name']) for r in status['inputs']], [
            ('atak-civ-lfs', 'depends/lib.tar.gz'), ('atak-civ-lfs', 'depends/mentioned.bin'), ('atak-civ-lfs', 'depends/unpinned.bin'),
            ('cesium-source', 'cesium.tar.gz'), ('cesium-source', 'port.tar.gz'),
            ('client-compiler-jar', 'closure:maven-public/org.a--lib-1.0.jar'), ('client-compiler-jar', 'closure:ui-original/built.jar'),
            ('client-compiler-jar', 'closure:ui-original/tool.jar'),
            ('debian-tool-metadata', 'InRelease'), ('debian-tool-metadata', 'Packages.xz'), ('debian-tool-metadata', 'ant.deb'),
            ('git-source', 'NASA WorldWindJava'), ('git-source', 'TAKX 5.7 official source'), ('git-source', self.CLONE),
            ('lock-entry', 'lib.tar.gz'), ('lock-entry', 'optional.java'), ('lock-entry', 'tool.tar.xz'),
            ('runtime-extra-jar', 'com.adobe.xmp:xmpcore:6')] + [('runtime-extra-jar', name) for name in missing])
        self.assert_fails_closed(status, 0)
        kinds = self.by_kind(status)
        self.assertEqual([(r['required'], r['pinned_by_recipe_lock'], r['path']) for r in kinds['atak-civ-lfs']], [
            (True, True, str(self.root / 'src/atak-civ-lfs/depends/lib.tar.gz')),
            (True, True, str(self.root / 'src/atak-civ-lfs/depends/mentioned.bin')),
            (False, False, str(self.root / 'src/atak-civ-lfs/depends/unpinned.bin'))])
        self.assertEqual([r['source'] for r in kinds['atak-civ-lfs']],
                         [self.media + n for n in ('lib.tar.gz', 'mentioned.bin', 'unpinned.bin')])
        lib, optional, tool = kinds['lock-entry']
        self.assertEqual((lib['method'], lib['lfs_pointer_matches'], lib['required'], lib['path']),
                         ('lfs', True, True, str(cache / sha(self.LIB))))
        self.assertEqual((optional['required'], tool['required']), (False, True))
        worldwind, takx, clone = kinds['git-source']
        self.assertEqual((worldwind['source'], worldwind['expected_revision'], worldwind['path'], worldwind['required']),
                         ('file://' + str(self.base / 'worldwind-origin'), self.worldwind_revision,
                          str(self.root / 'src/WorldWindJava'), True))
        self.assertNotIn('lfs_pointer_files', worldwind)
        self.assertEqual((takx['source'], takx['expected_revision'], takx['path'], takx['lfs_pointer_files'], takx['required']),
                         ('file://' + str(self.base / 'takx-origin'), self.takx_revision, str(self.root / 'src/takx57'), 1, True))
        self.assertEqual((clone['source'], clone['expected_revision'], clone['path'], clone['required']),
                         ('file://' + str(self.submodule), self.revision, str(self.root / 'src/atak-civ'), True))
        self.assertNotIn('lfs_pointer_files', clone)
        public, built, cached = kinds['client-compiler-jar']
        self.assertEqual((public['source'], public['verified']), (self.maven + 'org/a/lib/1.0/lib-1.0.jar', True))
        self.assertEqual((built['verified'], built['built_by_prototype'], built['required']), (False, True, False))
        self.assertEqual((cached['source'], cached['verified']), ('https://files.example/tool.tar.xz', True))
        extra = kinds['runtime-extra-jar'][0]
        self.assertEqual((extra['verified'], extra['path'], extra['also_in_compile_closure']),
                         (True, str(self.root / 'jars/runtime-extras/xmpcore-6.jar'), False))
        self.assertEqual([r['path'] for r in kinds['cesium-source']],
                         [str(self.root / 'cache-cesium/cesium.tar.gz'), str(self.root / 'cache-cesium/port.tar.gz')])
        self.assertEqual(kinds['debian-tool-metadata'][0]['path'], str(self.root / 'cache-commoncommo-tools/metadata/InRelease'))
        # Work root layout.
        self.assertEqual(sorted(p.name for p in cache.iterdir()),
                         sorted(['kept'] + [sha(d) for d in (self.LIB, self.MENTIONED, self.UNPINNED, self.TOOL, self.OPTIONAL, self.JAR)]))
        self.assertFalse((self.root / 'tmp').exists())
        self.assertFalse((self.root / 'rejected').exists())
        for name in ('logs', 'inputs', 'jars', 'src', 'status'):
            self.assertTrue((self.root / name).is_dir(), name)
        self.assertEqual(self.names(self.root / 'cache-gis-java'), [sha(self.LIB) + '.tar.gz'])
        self.assertTrue(os.path.samefile(self.root / 'cache-gis-java' / (sha(self.LIB) + '.tar.gz'), cache / sha(self.LIB)))
        self.assertEqual(self.names(self.root / 'inputs'), ['commoncommo/lib.tar.gz', 'compat/optional.java', 'native-x/tool.tar.xz'])
        self.assertEqual((self.root / 'src/atak-civ/README.md').read_bytes(), b'atak\n')
        self.assertEqual((self.root / 'src/atak-civ-lfs/depends/lib.tar.gz').read_bytes(), self.LIB)
        self.assertEqual(self.logs[0], (f'3 lock entries, 3 LFS pointers at {self.revision}',))
        summary = {k: status[k] for k in ('status', 'seconds', 'notes', 'counts', 'failed_required', 'failed_optional')}
        self.assertEqual(self.logs[-1], (json.dumps(summary, indent=1),))

    def test_required_input_that_fails_makes_the_run_fail(self):
        self.net.routes['https://files.example/tool.tar.xz'] = self.TOOL[:-1]
        del self.net.routes[self.media + 'mentioned.bin']
        self.assertEqual(self.main('--jobs', '2'), 1)
        self.assertEqual(self.jobs, [((2,), {})])
        status = self.status()
        self.assertEqual(status['status'], 'failed')
        self.assertEqual(status['failed_required'], ['depends/mentioned.bin', 'tool.tar.xz'])
        self.assertEqual(status['notes'], '14 of 38 records verified; 2 required and 20 optional inputs unverified; '
                                          '2 client-compiler JARs are build products, not fetched')
        self.assert_fails_closed(status, 1)
        self.assertFalse((self.root / 'cache' / sha(self.TOOL)).exists())
        self.assertFalse((self.root / 'inputs/native-x').exists())
        self.assertEqual(self.names(self.root / 'rejected'), [sha(self.TOOL) + '.got-' + sha(self.TOOL[:-1])[:16]])

    def test_optional_failures_are_listed_but_do_not_fail_the_run(self):
        del self.net.routes['https://files.example/optional.java']
        del self.net.routes[self.media + 'unpinned.bin']
        self.assertEqual(self.main(), 0)
        status = self.status()
        self.assertEqual(status['status'], 'ok')
        self.assertEqual(status['failed_required'], [])
        self.assertEqual(status['failed_optional'][:2], ['depends/unpinned.bin', 'optional.java'])
        self.assertEqual(len(status['failed_optional']), 22)
        self.assertNotIn('closure:ui-original/built.jar', status['failed_optional'])
        self.assert_fails_closed(status, 0)

    def test_status_decision_for_every_kind_of_record(self):
        cases = [
            ([dict(verified=True, required=True)], 'ok', [], []),
            ([dict(verified=False, required=True)], 'failed', ['n0'], []),
            ([dict(verified=False, required=False)], 'ok', [], ['n0']),
            ([dict(verified=False)], 'ok', [], ['n0']),
            ([dict(required=True)], 'failed', ['n0'], []),
            ([dict(verified=False, required=False, built_by_prototype=True)], 'ok', [], []),
            ([dict(verified=False, required=True, built_by_prototype=False)], 'failed', ['n0'], []),
            ([dict(verified=True, required=True), dict(verified=False, required=False), dict(verified=False, required=True),
              dict(verified=True, required=False)], 'failed', ['n2'], ['n1']),
            ([], 'ok', [], []),
        ]
        for records, expected, required, optional in cases:
            with self.subTest(records=records):
                self.records.clear()
                shutil.rmtree(self.root, ignore_errors=True)

                def inject(root, records=records):
                    self.assertEqual(root, self.root)
                    for index, fields in enumerate(records):
                        F.record(kind='injected', name=f'n{index}', **fields)
                self.patch(F, 'fetch_commoncommo_tools', inject)
                code = self.main('--only', 'debian')
                status = self.status('fetch-partial.json')
                self.assertEqual((status['status'], status['failed_required'], status['failed_optional']),
                                 (expected, required, optional))
                self.assertEqual(code, 1 if expected == 'failed' else 0)
                verified = sum(1 for r in records if r.get('verified'))
                self.assertEqual(status['notes'], f'{verified} of {len(records)} records verified; {len(required)} required and '
                                                  f'{len(optional)} optional inputs unverified; '
                                                  f'{sum(1 for r in records if r.get("built_by_prototype"))} client-compiler JARs '
                                                  'are build products, not fetched')
                self.assertEqual(status['counts'], {'injected': {'total': len(records), 'verified': verified}} if records else {})

    def test_worker_exception_aborts_without_a_status_file(self):
        for group, name in (('debian', 'fetch_commoncommo_tools'), ('locks', 'fetch_lock_entry'), ('lfs', 'fetch_lfs'),
                            ('git', 'checkout')):
            with self.subTest(group=group):
                self.patch(F, name, mock.Mock(side_effect=KeyError('unexpected')))
                with self.assertRaises(KeyError):
                    self.main('--only', group)
                self.assertEqual(self.names(self.root / 'status'), [])
        (self.takx / 'client-compiler/inputs.lock.json').write_text(json.dumps({'files': {'broken-reference.jar': '0' * 64}}))
        with self.assertRaises(ValueError):
            self.main('--only', 'jars')
        self.assertEqual(self.names(self.root / 'status'), [])

    def test_worker_exception_in_any_group_of_a_full_run_is_not_swallowed(self):
        originals = {name: getattr(F, name) for name in ('fetch_lock_entry', 'fetch_lfs', 'fetch_commoncommo_tools', 'checkout')}
        for name in originals:
            with self.subTest(name=name):
                shutil.rmtree(self.root, ignore_errors=True)
                with mock.patch.object(F, name, mock.Mock(side_effect=KeyError('unexpected'))), self.assertRaises(KeyError):
                    self.main()
                self.assertEqual(self.names(self.root / 'status'), [])
        entries, one = F.fetch_cesium(self.tmp)
        with mock.patch.object(F, 'fetch_cesium', lambda root: (entries, mock.Mock(side_effect=KeyError('unexpected')))), \
                self.assertRaises(KeyError):
            self.main()
        self.assertEqual(self.names(self.root / 'status'), [])

    def test_work_directory_removed_by_someone_else_does_not_break_the_end_of_the_run(self):
        def inject(root):
            shutil.rmtree(root / 'tmp')
        self.patch(F, 'fetch_commoncommo_tools', inject)
        self.assertEqual(self.main('--only', 'debian'), 0)
        self.assertEqual(self.names(self.root / 'status'), ['fetch-partial.json'])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['cache', 'inputs', 'jars', 'logs', 'src', 'status'])

    def test_only_limits_the_groups_and_writes_the_partial_status(self):
        (self.root / 'status').mkdir(parents=True)
        (self.root / 'status/fetch.json').write_text('{"previous": "full run"}\n')
        expected = {'locks': {'lock-entry': 3}, 'lfs': {'atak-civ-lfs': 3}, 'cesium': {'cesium-source': 2},
                    'git': {'git-source': 3}, 'jars': {'client-compiler-jar': 3}, 'extras': {'runtime-extra-jar': 1},
                    'debian': {'debian-tool-metadata': 3}}
        self.assertEqual(tuple(expected), self.GROUPS)
        for group, kinds in expected.items():
            with self.subTest(group=group):
                self.records.clear()
                shutil.rmtree(self.root / 'cache', ignore_errors=True)
                shutil.rmtree(self.root / 'src', ignore_errors=True)
                code = self.main('--only', group)
                status = self.status('fetch-partial.json')
                self.assertEqual({k: v['total'] for k, v in status['counts'].items()}, kinds)
                self.assert_fails_closed(status, code)
                self.assertEqual((self.root / 'status/fetch.json').read_text(), '{"previous": "full run"}\n')
                self.assertEqual(self.names(self.root / 'status'), ['fetch-partial.json', 'fetch.json'])
                if group == 'extras':
                    self.assertEqual(status['inputs'], [{
                        'kind': 'runtime-extra-jar', 'name': 'runtime extras', 'source': 'file://' + str(self.base / 'takx-origin'),
                        'method': 'direct', 'verified': False, 'required': False,
                        'note': 'official TAKX checkout missing; versions unknown'}])
                    self.assertEqual(code, 0)
                if group == 'jars':
                    self.assertEqual(code, 0)
                    self.assertEqual([(r['verified'], r['built_by_prototype']) for r in status['inputs']],
                                     [(True, False), (False, True), (False, True)])
        self.records.clear()
        self.assertEqual(self.main('--only', 'locks', '--only', 'git', '--only', 'extras'), 0)
        status = self.status('fetch-partial.json')
        self.assertEqual({k: v['total'] for k, v in status['counts'].items()},
                         {'git-source': 3, 'lock-entry': 3, 'runtime-extra-jar': 21})

    def test_jar_names_of_the_compile_closure_are_passed_to_the_extras(self):
        self.locks['client-compiler/inputs.lock.json'] = {'files': {
            'closure:maven-public/com.adobe.xmp--xmpcore-6.jar': sha(self.EXTRA), 'closure:lib/plain-name.jar': '2' * 64}}
        self.write_locks()
        self.net.routes[self.maven + 'com/adobe/xmp/xmpcore/6/xmpcore-6.jar'] = self.EXTRA
        (self.root / 'src/takx57/gradle').mkdir(parents=True)
        (self.root / 'src/takx57/gradle/versions.gradle').write_text("xmpcoreVersion = '6'\n")
        seen = {}
        real = F.fetch_runtime_extras

        def spy(root, takx, closure_names):
            seen.update(root=root, takx=takx, closure=closure_names)
            return real(root, takx, closure_names)
        self.patch(F, 'fetch_runtime_extras', spy)
        self.main('--only', 'jars', '--only', 'extras')
        self.assertEqual(seen, {'root': self.root, 'takx': self.root / 'src/takx57', 'closure': {'xmpcore-6.jar', 'plain-name.jar'}})
        extra = [r for r in self.records if r['name'] == 'com.adobe.xmp:xmpcore:6']
        self.assertIs(extra[0]['also_in_compile_closure'], True)
        self.records.clear()
        self.main('--only', 'extras')
        self.assertEqual(seen['closure'], set())

    def test_work_root_inside_the_repository_is_refused(self):
        link = self.tmp / 'link'
        link.symlink_to(self.repo)
        for root in (self.repo, self.repo / 'work', self.repo / 'a/b/c', link / 'via-link'):
            with self.subTest(root=root):
                self.root = root
                with self.assertRaises(SystemExit) as caught:
                    self.main()
                self.assertEqual(caught.exception.code, 'the work root must be outside the repository')
        self.assertEqual(sorted(p.name for p in self.repo.iterdir()), ['upstream'])
        self.root = self.base / 'repo-sibling'
        self.addCleanup(shutil.rmtree, self.root, True)
        self.assertEqual(self.main('--only', 'debian'), 0)

    def test_submodule_at_another_revision_is_refused(self):
        self.locks['sources.lock.json']['atak_source']['revision'] = 'e' * 40
        self.write_locks()
        with self.assertRaises(SystemExit) as caught:
            self.main()
        self.assertEqual(caught.exception.code,
                         f'upstream/atak-civ is at {self.revision}, takx/sources.lock.json pins {"e" * 40}')
        self.assertEqual(self.names(self.root), [])
        self.assertEqual(self.records, [])
        self.assertEqual(self.net.calls, [])

    def parse_only(self, *arguments):
        """The parsed command line, without letting main() go any further."""
        real = F.argparse.ArgumentParser.parse_args

        def capture(parser, *a, **k):
            raise Abort(real(parser, *a, **k), parser)
        with mock.patch.object(F.argparse.ArgumentParser, 'parse_args', capture), \
                mock.patch.object(sys, 'argv', ['fetch_inputs.py', *arguments]), self.assertRaises(Abort) as caught:
            F.main()
        return caught.exception.args

    def test_command_line_defaults(self):
        parsed, parser = self.parse_only()
        self.assertEqual((parsed.root, parsed.jobs, parsed.only), (self.tmp / 'env-root', 4, None))
        self.assertEqual(set(vars(parsed)), {'root', 'jobs', 'only'})
        del os.environ['TAKX_PROTO_ROOT']
        parsed, _ = self.parse_only()
        self.assertEqual(parsed.root, Path('/var/lib/takx-proto'))
        parsed, _ = self.parse_only('--root', 'relative/dir', '--jobs', '7', '--only', 'jars', '--only', 'git')
        self.assertEqual((parsed.root, parsed.jobs, parsed.only), (Path('relative/dir'), 7, ['jars', 'git']))
        self.assertIsInstance(parsed.root, Path)
        for group in self.GROUPS:
            self.assertEqual(self.parse_only('--only', group)[0].only, [group])
        self.assertEqual(parser.description, F.__doc__)
        self.assertIs(parser.formatter_class, F.argparse.RawDescriptionHelpFormatter)

    def test_command_line_errors_and_help(self):
        for arguments in (['--only', 'everything'], ['--only', 'LOCKS'], ['--jobs', 'many'], ['--unknown'], ['positional'],
                          ['--only'], ['--root']):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit) as caught:
                    self.parse_only(*arguments)
                self.assertEqual(caught.exception.code, 2)
                self.assertIn('error:', errors.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as caught:
            self.parse_only('--help')
        self.assertEqual(caught.exception.code, 0)
        self.assertIn('cache/<sha256>                       flat cache used by native-deps `acquire`\n', output.getvalue())
        self.assertRegex(output.getvalue(), r'\n +limit to some groups \(default: all\); the status file\n +'
                                            r'is only written for a full run\n\Z')
        self.assertIn('--only {locks,lfs,cesium,git,jars,extras,debian}', output.getvalue())


if __name__ == '__main__':
    unittest.main()
