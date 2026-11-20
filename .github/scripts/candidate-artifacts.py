#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Resolve read-only PR candidate archives from their canonical native jobs."""

import argparse
from contextlib import contextmanager
import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('candidate_images', Path(__file__).with_name('candidate-images.py'))
IMAGES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMAGES)
require = IMAGES.require
PRODUCERS = {'postgis': 'postgis-image.yaml', 'cloudtak': 'cloudtak-image.yaml', 'takserver': 'container.yaml'}
HELPERS = {'postgis': 'postgis', 'cloudtak': 'cloudtak', 'takserver': 'docker'}
PRODUCER_PATHS = {
    'postgis': ['.github/container-and-chart/postgis/**', '.github/workflows/postgis-image.yaml',
                '.github/actions/attest-images/**', '.github/scripts/candidate-*.py'],
    'cloudtak': ['.gitmodules', 'upstream/cloudtak', '.github/container-and-chart/cloudtak/**',
                 '.github/workflows/cloudtak-image.yaml', '.github/actions/attest-images/**', '.github/scripts/candidate-*.py'],
    'takserver': ['.github/container-and-chart/**', '.github/workflows/container.yaml',
                  '.github/scripts/merge-image-manifests.sh', '.github/scripts/copa-patch.sh',
                  '.github/scripts/copa-evidence.py', 'tests/test_copa_evidence.py',
                  '.github/actions/attest-images/**', '.gitmodules', 'upstream/takserver', '.github/scripts/candidate-*.py'],
}
API = 'https://api.github.com'


class Pending(Exception):
    pass


class NoCredentialRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        require(urlparse(new_url).scheme == 'https', 'artifact download redirected outside HTTPS')
        redirected = super().redirect_request(request, fp, code, message, headers, new_url)
        if urlparse(new_url).netloc != urlparse(request.full_url).netloc:
            redirected.remove_header('Authorization')
        return redirected


class GitHub:
    def __init__(self, repository, token):
        require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) is not None, 'invalid GitHub repository')
        require(bool(token), 'GH_TOKEN is required for candidate artifact reads')
        self.repository, self.token = repository, token
        self.opener = build_opener(NoCredentialRedirect())
        self.metadata_cache = {}
        self.snapshot_cache = None

    @contextmanager
    def snapshot(self):
        """Share one freshly revalidated response per URL within one poll."""
        require(self.snapshot_cache is None, 'candidate metadata snapshots cannot nest')
        self.snapshot_cache = {}
        try:
            yield self
        finally:
            self.snapshot_cache = None

    def request(self, path, etag=None):
        require(path.startswith('/repos/' + self.repository + '/actions/') or
                re.fullmatch('/repos/' + re.escape(self.repository) + r'/pulls/[1-9][0-9]*(?:/files)?(?:\?.*)?', path) is not None,
                'invalid GitHub candidate API path')
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28'}
        if etag is not None:
            require(valid_etag(etag), 'invalid candidate metadata ETag')
            headers['If-None-Match'] = etag
        try:
            return self.opener.open(Request(API + path, headers=headers), timeout=60)
        except HTTPError as error:
            if error.code == 304:
                raise
            # Keep API failures closed, but retain the server's bounded reason
            # and rate evidence so permission and throttling failures differ.
            # Never log credentials, redirect URLs or arbitrary response headers.
            reason = 'unclassified-api-error'
            try:
                raw = error.read(4097)
                if len(raw) <= 4096:
                    body = json.loads(raw)
                    if isinstance(body, dict) and isinstance(body.get('message'), str):
                        message = body['message'].lower()
                        if 'secondary rate limit' in message:
                            reason = 'secondary-rate-limit'
                        elif message.startswith('api rate limit exceeded'):
                            reason = 'primary-rate-limit'
                        elif message == 'resource not accessible by integration':
                            reason = 'integration-access-denied'
                        elif message == 'resource not accessible by personal access token':
                            reason = 'token-access-denied'
                        elif message == 'bad credentials':
                            reason = 'bad-credentials'
                        elif message == 'not found':
                            reason = 'not-found'
            except (OSError, ValueError, TypeError):
                pass
            error.close()
            evidence = []
            for header, pattern in (('X-RateLimit-Remaining', r'[0-9]{1,12}'),
                                    ('X-RateLimit-Reset', r'[0-9]{1,12}'),
                                    ('Retry-After', r'[0-9]{1,6}'),
                                    ('X-GitHub-Request-Id', r'[A-Fa-f0-9]{4,16}(?::[A-Fa-f0-9]{4,16}){3,5}')):
                value = (error.headers or {}).get(header, '')
                if isinstance(value, str) and self.token not in value and re.fullmatch(pattern, value):
                    evidence.append(f'{header}={value}')
            # Free-form server messages can contain other credentials or
            # signed URLs. Emit only fixed reason labels and safe headers.
            detail = '; '.join([f'reason={reason}', *evidence])
            raise ValueError(f'GitHub candidate API GET {urlparse(path).path} returned HTTP {error.code}: {detail}') from error

    def get(self, path):
        if self.snapshot_cache is not None and path in self.snapshot_cache:
            return self.snapshot_cache[path]
        cached = self.metadata_cache.get(path)
        try:
            with self.request(path, etag=cached['etag'] if cached else None) as response:
                raw = response.read(16 * 1024**2 + 1)
                etag = (getattr(response, 'headers', None) or {}).get('ETag')
            require(len(raw) <= 16 * 1024**2, 'GitHub metadata response exceeds its size limit')
            value = IMAGES.read_json(raw)
            if valid_etag(etag):
                self.metadata_cache[path] = {'etag': etag, 'value': value}
            else:
                self.metadata_cache.pop(path, None)
        except HTTPError as error:
            try:
                require(error.code == 304 and cached is not None and valid_etag(cached['etag']),
                        'GitHub candidate metadata returned 304 without a validated ETag')
                value = cached['value']
            finally:
                error.close()
        if self.snapshot_cache is not None:
            self.snapshot_cache[path] = value
        return value

    def pages(self, path, field):
        separator = '&' if '?' in path else '?'
        for page in range(1, 101):
            response = self.get(path + separator + urlencode({'per_page': 100, 'page': page}))
            items = response if field == 'files' else response[field]
            require(isinstance(items, list), 'invalid GitHub candidate metadata list')
            yield from items
            if len(items) < 100:
                return
        raise ValueError('GitHub candidate metadata pagination limit exceeded')

    def download(self, artifact, destination, cache=False):
        require(type(artifact.get('id')) is int and artifact['id'] > 0, 'invalid candidate artifact id')
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', artifact.get('digest', '')) is not None,
                'GitHub candidate archive digest is missing')
        require(type(artifact.get('size_in_bytes')) is int and 0 < artifact['size_in_bytes'] <= IMAGES.MAX_ARCHIVE,
                'invalid GitHub candidate archive size')
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix('.zip')
        size = 0
        checksum = hashlib.sha256()
        try:
            with self.request(f'/repos/{self.repository}/actions/artifacts/{artifact["id"]}/zip') as response, temporary.open('xb') as target:
                while block := response.read(1024 * 1024):
                    size += len(block)
                    require(size <= IMAGES.MAX_ARCHIVE, 'downloaded candidate artifact is too large')
                    checksum.update(block)
                    target.write(block)
            require('sha256:' + checksum.hexdigest() == artifact['digest'], 'GitHub candidate archive digest mismatch')
            unpack_artifact(temporary, destination, cache=cache)
        finally:
            temporary.unlink(missing_ok=True)


def valid_etag(value):
    return isinstance(value, str) and re.fullmatch(r'(?:W/)?"[\x21\x23-\x7e]{0,256}"', value) is not None


def pending_delay(started, deadline, reasons, interval):
    """Bound pending polls without extending the missing-producer lease."""
    now = time.monotonic()
    missing = any('not been scheduled' in reason for reason in reasons)
    limit = min(deadline, started + 90) if missing else deadline
    require(now < limit, '; '.join(reasons))
    return min(interval, limit - now)


def unpack_artifact(archive, destination, cache=False):
    require(not destination.exists(), 'candidate download directory already exists')
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        wanted = {'cache.json'} if cache else {'receipt.json', 'images.tar'}
        require(len(members) == len(wanted) and {member.filename for member in members} == wanted,
                'unexpected files in candidate artifact')
        for member in members:
            require(not member.is_dir() and stat.S_IFMT(member.external_attr >> 16) in (0, stat.S_IFREG),
                    'candidate ZIP member is not a regular file')
            limit = IMAGES.MAX_ARCHIVE if member.filename == 'images.tar' else IMAGES.MAX_METADATA
            require(0 < member.file_size <= limit, 'candidate ZIP member exceeds its size limit')
        destination.mkdir(parents=True, mode=0o700)
        for member in members:
            with source.open(member) as input_stream, (destination / member.filename).open('xb') as output:
                while block := input_stream.read(1024 * 1024):
                    output.write(block)


def context_from_environment():
    require(os.environ.get('GITHUB_EVENT_NAME') == 'pull_request', 'candidate transfer requires a pull-request event')
    event = IMAGES.read_metadata(Path(os.environ['GITHUB_EVENT_PATH']))
    pull = event['pull_request']
    context = {'repository': os.environ['GITHUB_REPOSITORY'], 'head_repository': pull['head']['repo']['full_name'],
               'head_sha': pull['head']['sha'], 'base_sha': pull['base']['sha'],
               'pull_number': event['number']}
    require(context['repository'] == pull['base']['repo']['full_name'], 'candidate repository does not match the PR base')
    require(context['head_repository'] != context['repository'], 'candidate transfer is only for read-only fork PRs')
    require(re.fullmatch(r'[0-9a-f]{40}', context['head_sha']) is not None, 'invalid candidate source head')
    require(re.fullmatch(r'[0-9a-f]{40}', context['base_sha']) is not None and
            type(context['pull_number']) is int and context['pull_number'] > 0, 'invalid candidate PR base identity')
    checkout = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    require(checkout == os.environ['GITHUB_SHA'], 'candidate checkout does not match the workflow commit')
    context['checkout_sha'] = checkout
    return context


def canonical_triggered(api, context, group):
    """Prove an unchanged group's producer is outside the PR path filter."""
    pull_path = f'/repos/{api.repository}/pulls/{context["pull_number"]}'
    pull = api.get(pull_path)
    require(pull.get('head', {}).get('sha') == context['head_sha'] and
            pull.get('head', {}).get('repo', {}).get('full_name') == context['head_repository'] and
            pull.get('base', {}).get('sha') == context['base_sha'], 'PR source changed while resolving candidate images')
    files = list(api.pages(pull_path + '/files', 'files'))
    require(len(files) == pull.get('changed_files') and len(files) < 3000, 'PR path admission evidence is incomplete')
    return any(fnmatch.fnmatchcase(filename, pattern)
               for record in files for filename in (record['filename'], record.get('previous_filename', record['filename']))
               for pattern in PRODUCER_PATHS[group])


def expectation(group, arch, producer):
    helper = ROOT / '.github/container-and-chart' / HELPERS[group] / 'build.sh'
    values = []
    for option in ('--inputs', '--tag'):
        value = subprocess.check_output(['bash', str(helper), option], cwd=ROOT, text=True).strip()
        require('\n' not in value, 'unexpected image identity helper output')
        values.append(value)
    result = {'producer': producer, 'group': group, 'architecture': arch, 'inputs': values[0], 'tag': values[1]}
    IMAGES.validate_expected(result)
    return result


def artifact_name(group, arch, attempt):
    require(group in PRODUCERS and arch in ('amd64', 'arm64') and type(attempt) is int and attempt > 0,
            'invalid native artifact name fields')
    return f'candidate-{group}-{arch}-{attempt}'


def select_run(api, context, group):
    workflow = PRODUCERS[group]
    query = urlencode({'event': 'pull_request', 'head_sha': context['head_sha']})
    runs = list(api.pages(f'/repos/{api.repository}/actions/workflows/{workflow}/runs?{query}', 'workflow_runs'))
    matching = [run for run in runs if run.get('head_sha') == context['head_sha']]
    if not matching:
        raise Pending('canonical candidate producer has not been scheduled for this head')
    run = max(matching, key=lambda candidate: (candidate['id'], candidate['run_attempt']))
    require(run.get('repository', {}).get('full_name') == context['repository'] and
            run.get('head_repository', {}).get('full_name') == context['head_repository'] and
            run.get('event') == 'pull_request' and run.get('path') == '.github/workflows/' + workflow,
            'candidate producer source or workflow mismatch')
    require(type(run.get('id')) is int and run['id'] > 0 and type(run.get('run_attempt')) is int and run['run_attempt'] > 0,
            'invalid candidate producer run identity')
    require(run.get('conclusion') != 'action_required', 'canonical candidate producer requires fork workflow approval')
    return run


def resolve_once(api, context, group, arch):
    run = select_run(api, context, group)
    jobs = list(api.pages(f'/repos/{api.repository}/actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs', 'jobs'))
    matching = [job for job in jobs if job.get('name') == f'build ({arch})']
    if not matching and group == 'takserver':
        # GitHub skips a job-level false condition before expanding its
        # strategy, exposing this literal name rather than native job rows.
        matching = [job for job in jobs if job.get('name') == 'build (${{ matrix.arch }})' and
                    job.get('status') == 'completed' and job.get('conclusion') == 'skipped']
    if not matching or any(job.get('status') != 'completed' for job in matching):
        if run.get('status') == 'completed':
            raise ValueError('canonical native candidate job did not finish successfully')
        raise Pending('canonical native candidate build is still running')
    require(len(matching) == 1, 'duplicate canonical native candidate jobs')
    cache = group == 'takserver' and matching[0].get('conclusion') == 'skipped'
    if cache:
        plan = [job for job in jobs if job.get('name') == 'plan']
        require(len(plan) == 1 and plan[0].get('conclusion') == 'success', 'canonical candidate cache plan failed')
        require(all(job.get('conclusion') == 'skipped' for job in jobs if job.get('name', '').startswith('build (')),
                'a failed native build cannot become a candidate cache decision')
    else:
        require(matching[0].get('conclusion') == 'success', 'canonical native candidate build failed')
    wanted = f'candidate-cache-takserver-{run["run_attempt"]}' if cache else artifact_name(group, arch, run['run_attempt'])
    artifacts = [artifact for artifact in api.pages(f'/repos/{api.repository}/actions/runs/{run["id"]}/artifacts', 'artifacts')
                 if artifact.get('name') == wanted]
    require(len(artifacts) == 1 and artifacts[0].get('expired') is False, 'canonical native candidate artifact is missing or expired')
    artifact = artifacts[0]
    require(artifact.get('workflow_run', {}).get('id') == run['id'] and
            artifact.get('workflow_run', {}).get('head_sha') == context['head_sha'], 'candidate artifact source run mismatch')
    producer = {'repository': context['repository'], 'head_sha': context['head_sha'], 'checkout_sha': context['checkout_sha'],
                'workflow': '.github/workflows/' + PRODUCERS[group], 'run_id': run['id'], 'run_attempt': run['run_attempt']}
    return artifact, expectation(group, arch, producer)


def resolve(api, context, group, arch, timeout=10800, interval=15):
    deadline, started = time.monotonic() + timeout, time.monotonic()
    while True:
        try:
            return resolve_once(api, context, group, arch)
        except Pending as pending:
            # A missing workflow is an admission/configuration error, not an
            # excuse to wait hours for a producer that was never dispatched.
            delay = pending_delay(started, deadline, [str(pending)], interval)
            print(f'candidate-artifacts: {pending}', flush=True)
            time.sleep(delay)
            interval = min(120, interval * 2)


def export(group, arch, directory, source_tag):
    context, producer = producer_context(group)
    expected = expectation(group, arch, producer)
    return IMAGES.export(directory, expected, source_tag)


def producer_context(group):
    context = context_from_environment()
    workflow = os.environ['GITHUB_WORKFLOW_REF'].split('@', 1)[0]
    require(workflow == context['repository'] + '/.github/workflows/' + PRODUCERS[group], 'noncanonical candidate producer')
    producer = {'repository': context['repository'], 'head_sha': context['head_sha'], 'checkout_sha': context['checkout_sha'],
                'workflow': '.github/workflows/' + PRODUCERS[group],
                'run_id': int(os.environ['GITHUB_RUN_ID']), 'run_attempt': int(os.environ['GITHUB_RUN_ATTEMPT'])}
    return context, producer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('export', 'download', 'ready'))
    parser.add_argument('--group', required=True, choices=PRODUCERS)
    parser.add_argument('--architecture', required=True, choices=('amd64', 'arm64'))
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--source-tag')
    parser.add_argument('--timeout', type=int, default=10800)
    args = parser.parse_args()
    require(args.timeout > 0, 'candidate wait timeout must be positive')
    if args.command == 'export':
        require(args.directory is not None and args.source_tag is not None, 'candidate export needs a directory and source tag')
        export(args.group, args.architecture, args.directory, args.source_tag)
        return
    context = context_from_environment()
    api = GitHub(context['repository'], os.environ.get('GH_TOKEN', ''))
    artifact, expected = resolve(api, context, args.group, args.architecture, args.timeout)
    if args.command == 'download':
        require(args.directory is not None, 'candidate download needs a directory')
        api.download(artifact, args.directory)
        IMAGES.verify(args.directory, expected)
    print(json.dumps({'group': args.group, 'architecture': args.architecture,
                      'artifact_id': artifact['id'], 'producer': expected['producer']}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, HTTPError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        raise SystemExit(f'candidate-artifacts: {error}') from error
