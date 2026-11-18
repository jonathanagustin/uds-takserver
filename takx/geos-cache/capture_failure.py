#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Adapt complete GEOS command logs to the existing native log sanitizer."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
MAX_LOG = 8 * 1024**2
MAX_TOTAL = 24 * 1024**2
NAMES = re.compile(r'[0-9]+-(?:isolated-(?:build|consume)|configure|fresh-source-build|source-install|'
                   r'configured-int64-(?:compile|runtime)|geometry-compile|real-geometry|'
                   r'whole-archive-pic-link|pic-dynamic)')


def capture(source, output):
    if any(path.is_symlink() for path in (source, *source.parents)):
        raise ValueError('ordinary input required')
    with tempfile.TemporaryDirectory(prefix='geos-complete-logs-') as directory:
        staged = Path(directory)
        total = 0
        statuses = sorted(source.glob('*.status.json')) if source.exists() else []
        if len(statuses) > 256:
            raise ValueError('too many command logs')
        for path in statuses:
            name = path.name.removesuffix('.status.json')
            if not NAMES.fullmatch(name):
                continue
            log = source / (name + '.log')
            if path.is_symlink() or log.is_symlink() or not path.is_file() or not log.is_file():
                raise ValueError('ordinary command log required')
            if path.stat().st_size > 4096:
                raise ValueError('status too large')
            status = json.loads(path.read_bytes())
            if (status.get('complete') is not True or type(status.get('returncode')) is not int or
                    type(status.get('bytes')) is not int or not 0 <= status['bytes'] <= MAX_LOG or
                    status['bytes'] != log.stat().st_size or total + status['bytes'] > MAX_TOTAL):
                continue
            raw = log.read_bytes()
            if len(raw) != status['bytes'] or hashlib.sha256(raw).hexdigest() != status.get('sha256'):
                continue
            # This is only a log-format adapter, never a build-success receipt.
            status['schema'] = 'uds-native-command-log-v1'
            (staged / (name + '.log')).write_bytes(raw)
            (staged / path.name).write_text(json.dumps(status))
            total += len(raw)
        subprocess.run([sys.executable, '-B', str(ROOT / 'takx/registry-cache/capture_failure.py'),
                        '--input', str(staged), '--output', str(output)], check=True, timeout=90)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        capture(args.input, args.output)
    except (OSError, ValueError, subprocess.SubprocessError):
        print('GEOS diagnostics omitted: complete-stream capture failed', file=sys.stderr)
        raise SystemExit(1) from None
