#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Buffer one complete bounded diagnostic command before exposing either stream."""
import os
import selectors
import signal
import subprocess
import sys
import time

MAX_BYTES = 1024 * 1024
TIMEOUT = 3


class Incomplete(Exception):
    pass


def command(argv):
    process = None
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    total = 0
    deadline = time.monotonic() + TIMEOUT
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            for pipe, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Incomplete('deadline')
                for key, _ in selector.select(min(remaining, 0.1)):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(block)
                    if total > MAX_BYTES:
                        raise Incomplete('output-limit')
                    buffers[key.data].extend(block)
            try:
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise Incomplete('deadline') from None
        if time.monotonic() >= deadline:
            raise Incomplete('deadline')
        return code, bytes(buffers['stdout']), bytes(buffers['stderr'])
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # A kernel-blocked child must not hold failure capture indefinitely.
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
            process.stdout.close()
            process.stderr.close()


def interrupted(_signum, _frame):
    raise Incomplete('deadline')


def main():
    if len(sys.argv) < 2:
        return 2
    signal.signal(signal.SIGTERM, interrupted)
    try:
        code, stdout, stderr = command(sys.argv[1:])
    except Incomplete as error:
        if str(error) == 'output-limit':
            print('Omitted oversized agent diagnostic stream (limit=1048576 bytes)', file=sys.stderr)
            return 125
        print('Omitted incomplete agent diagnostic stream (deadline=3s)', file=sys.stderr)
        return 124
    except OSError:
        print('Omitted unavailable agent diagnostic command', file=sys.stderr)
        return 127
    sys.stdout.buffer.write(stdout)
    sys.stderr.buffer.write(stderr)
    return code if code >= 0 else 128 - code


if __name__ == '__main__':
    raise SystemExit(main())
