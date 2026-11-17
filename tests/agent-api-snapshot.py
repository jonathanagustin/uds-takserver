#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Postfailure namespace lookup from an existing agent; never historical proof."""
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

UUID = r'[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}'
NAME = r'[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?'
STARTED = r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z'
MAX_POD_BYTES = 64 * 1024
SNAPSHOT_SECONDS = 3


class SnapshotInterrupted(Exception):
    pass


def interrupted(_signum, _frame):
    raise SnapshotInterrupted()


def identity(pod):
    """Only a canonical live agent/server incarnation can select exec."""
    meta, spec = pod['metadata'], pod['spec']
    name, uid, account = meta['name'], meta['uid'], spec['serviceAccountName']
    node = spec['nodeName']
    assert meta['namespace'] == 'zarf'
    assert re.fullmatch(r'agent-hook-[a-z0-9-]{1,242}', name)
    assert re.fullmatch(UUID, uid)
    assert re.fullmatch(NAME, account)
    assert re.fullmatch(NAME, node)
    assert meta.get('deletionTimestamp') is None
    assert len([c for c in spec['containers'] if c['name'] == 'server']) == 1
    statuses = [s for s in pod['status']['containerStatuses'] if s['name'] == 'server']
    assert len(statuses) == 1
    status = statuses[0]
    container_id, restart = status['containerID'], status['restartCount']
    started = status['state']['running']['startedAt']
    assert re.fullmatch(r'containerd://[a-f0-9]{64}', container_id)
    assert type(restart) is int and 0 <= restart <= 2147483647
    assert re.fullmatch(STARTED, started)
    return {'namespace': 'zarf', 'name': name, 'uid': uid, 'serviceAccount': account,
            'node': node, 'container': 'server', 'containerID': container_id,
            'restartCount': restart, 'containerStartedAt': started}


def command(args, maximum, deadline):
    """Keep bounded private bytes and one aggregate deadline, even if timeout stalls."""
    if time.monotonic() >= deadline:
        return b'', None, 'timeout'
    process = None
    value = bytearray()
    try:
        process = subprocess.Popen(['timeout', '--kill-after=1s', '3s', *args],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b'', None, 'timeout'
                for key, _ in selector.select(min(.05, remaining)):
                    block = os.read(key.fileobj.fileno(), min(65536, maximum + 1 - len(value)))
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    value.extend(block)
                    if len(value) > maximum:
                        return b'', None, 'output-limit'
            try:
                code = process.wait(timeout=max(.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                return b'', None, 'timeout'
        if time.monotonic() >= deadline:
            return b'', None, 'timeout'
        return bytes(value), code, 'complete'
    except OSError:
        return b'', None, 'unavailable'
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # Do not let a kernel-blocked child hold the diagnostic indefinitely.
            try:
                process.wait(timeout=.1)
            except subprocess.TimeoutExpired:
                pass
            process.stdout.close()


def live_identity(selected, deadline):
    value, code, state = command(['uds', 'zarf', 'tools', 'kubectl', '--request-timeout=2s',
        'get', 'pod', '-n', 'zarf', selected['name'], '-o', 'json'], MAX_POD_BYTES, deadline)
    if code != 0 or state != 'complete':
        return None
    try:
        return identity(json.loads(value))
    except (AssertionError, AttributeError, KeyError, TypeError, ValueError, RecursionError):
        return None


def lookup_result(value, code, state):
    if state == 'output-limit':
        return {'result': 'invalid-lookup-output'}
    if state == 'timeout' or code in (124, 137):
        return {'result': 'exec-or-lookup-timeout'}
    if state == 'unavailable' or code in (None, 126, 127):
        return {'result': 'exec-unavailable'}
    if code != 0:
        return {'result': 'exec-or-lookup-failed'}
    if re.fullmatch(UUID.encode(), value):
        return {'result': 'lookup-succeeded', 'targetNamespace': 'velero',
                'targetUID': value.decode('ascii')}
    return {'result': 'invalid-lookup-output'}


def snapshot(path):
    start = time.monotonic()
    deadline = start + SNAPSHOT_SECONDS
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            assert stat.S_ISREG(os.fstat(source.fileno()).st_mode)
            raw = source.read(MAX_POD_BYTES + 1)
        assert len(raw) <= MAX_POD_BYTES
        selected = identity(json.loads(raw))
    except (AssertionError, AttributeError, KeyError, TypeError, ValueError, OSError, RecursionError):
        return {'snapshot': 'postfailure', 'result': 'invalid-agent-identity',
                'identityQualified': False, 'execAttempted': False}
    result = {'snapshot': 'postfailure', 'selectedAgent': selected,
              'identityQualified': False, 'execAttempted': False, 'exit': None}
    def finish(reason):
        result.update(result=reason, elapsed_ms=round((time.monotonic() - start) * 1000))
        return result
    before = live_identity(selected, deadline)
    if before is None:
        return finish('agent-identity-unavailable')
    if before != selected:
        return finish('agent-identity-changed')
    args = ['uds', 'zarf', 'tools',
               'kubectl', '--request-timeout=2s', 'exec', '-n', 'zarf',
               selected['name'], '-c', 'server', '--', '/zarf', 'tools',
               'kubectl', '--request-timeout=2s', 'get', 'namespace', 'velero',
               '-o', 'jsonpath={.metadata.uid}']
    # Never expose exec errors: they can contain credentials or arbitrary output.
    result['execAttempted'] = True
    value, code, state = command(args, 128, deadline)
    result['exit'] = code
    outcome = lookup_result(value, code, state)
    after = live_identity(selected, deadline)
    if after is None or after != before:
        result['execResult'] = outcome['result']
        return finish('agent-identity-unavailable' if after is None else 'agent-identity-changed')
    # Stable API observations bracket the name-based exec; this is not an
    # atomic incarnation guarantee and never establishes a historical cause.
    result.pop('selectedAgent')
    result.update(agent=before, identityQualified=True, **outcome)
    return finish(outcome['result'])


if __name__ == '__main__':
    # Caller runs this only after primary evidence, within its existing deadline.
    signal.signal(signal.SIGTERM, interrupted)
    try:
        for source in sys.argv[1:3]:
            print(json.dumps(snapshot(source)), flush=True)
    except SnapshotInterrupted:
        print(json.dumps({'snapshot': 'postfailure', 'result': 'snapshot-interrupted',
                          'identityQualified': False}), flush=True)
        raise SystemExit(143) from None
