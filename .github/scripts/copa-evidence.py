#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Retain only the four RPM fix records needed to investigate Copa failures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

PACKAGES = {'krb5-libs', 'libpcap', 'rpm', 'rpm-libs'}
MAX_REPORT = 16 * 1024 * 1024


def require(value):
    if not value:
        raise ValueError('invalid Copa evidence')


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def regular(path):
    require(not any(p.is_symlink() for p in (path, *path.parents)))
    require(stat.S_ISREG(path.lstat().st_mode))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_REPORT)
        raw = stream.read(MAX_REPORT + 1)
        after = os.fstat(stream.fileno())
    require(len(raw) <= MAX_REPORT and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
    return raw


def text(value, pattern):
    require(isinstance(value, str) and re.fullmatch(pattern, value))
    return value


def evidence(raw, architecture, image_id):
    require(architecture in ('amd64', 'arm64'))
    text(image_id, r'sha256:[a-f0-9]{64}')
    report = json.loads(raw, object_pairs_hook=unique)
    metadata = report['Metadata']
    require(isinstance(metadata, dict))
    if 'ImageID' in metadata:
        require(metadata['ImageID'] == image_id)
    operating_system = metadata['OS']
    system = {'family': text(operating_system['Family'], r'[a-z0-9_-]{1,32}'),
              'version': text(operating_system['Name'], r'[A-Za-z0-9._-]{1,32}')}
    configuration = metadata.get('ImageConfig', {})
    require(isinstance(configuration, dict))
    if 'architecture' in configuration:
        require(configuration['architecture'] == architecture)
    results = report.get('Results') or []
    require(isinstance(results, list) and len(results) <= 256)
    records = set()
    for result in results:
        require(isinstance(result, dict))
        if result.get('Class') != 'os-pkgs':
            continue
        vulnerabilities = result.get('Vulnerabilities') or []
        require(isinstance(vulnerabilities, list) and len(vulnerabilities) <= 4096)
        for value in vulnerabilities:
            require(isinstance(value, dict))
            if value.get('PkgName') not in PACKAGES:
                continue
            records.add((value['PkgName'], text(value['VulnerabilityID'], r'(?:CVE-[0-9]{4}-[0-9]{4,12}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4})'),
                         text(value['InstalledVersion'], r'[A-Za-z0-9:._+~, -]{1,256}'),
                         text(value.get('FixedVersion', ''), r'[A-Za-z0-9:._+~, -]{0,256}')))
    require(len(records) <= 512)
    binding = {}
    for key, variable, pattern in [('sourceSHA', 'GITHUB_SHA', r'[a-f0-9]{40}'),
                                   ('runID', 'GITHUB_RUN_ID', r'[1-9][0-9]{0,19}'),
                                   ('runAttempt', 'GITHUB_RUN_ATTEMPT', r'[1-9][0-9]{0,19}')]:
        if variable in os.environ:
            binding[key] = text(os.environ[variable], pattern)
    require(len(binding) in (0, 3))
    return {'schema': 1, 'scope': 'Copa-RPM-scan-diagnostic-only', 'binding': binding,
            'architecture': architecture, 'imageID': image_id, 'os': system,
            'reportSHA256': hashlib.sha256(raw).hexdigest(),
            'packages': [dict(zip(('package', 'vulnerabilityID', 'installedVersion', 'fixedVersion'), row))
                         for row in sorted(records)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--architecture', required=True)
    parser.add_argument('--image-id', required=True)
    args = parser.parse_args()
    value = evidence(regular(args.report), args.architecture, args.image_id)
    directory = args.directory.absolute()
    require(not any(p.is_symlink() for p in (directory, *directory.parents)))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = directory / (args.image_id.removeprefix('sha256:') + '-' + value['reportSHA256'] + '.json')
    payload = (json.dumps(value, indent=2) + '\n').encode()
    require(len(payload) <= 256 * 1024)
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        require(regular(output) == payload)
    else:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        print('copa evidence unavailable: invalid or unreadable input', file=sys.stderr)
        raise SystemExit(1)
