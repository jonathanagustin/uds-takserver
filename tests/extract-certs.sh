#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Read the current installation's admin certificate pair on every invocation.
# A clean reinstall can replace the CA while /tmp/tak-certs still exists.
set -euo pipefail

if [[ -x ./zarf ]]; then
  kctl=(./zarf tools kubectl)
elif command -v uds >/dev/null 2>&1; then
  kctl=(uds zarf tools kubectl)
else
  kctl=(kubectl)
fi

python3 - "${1:-/tmp/tak-certs}" "${kctl[@]}" <<'PY'
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def require(value):
    if not value:
        raise ValueError('certificate source or destination differs')


def read(kind, name):
    result = subprocess.run([*sys.argv[2:], 'get', kind.lower(), name, '-n', 'takserver',
                             '--ignore-not-found', '-o', 'json'],
                            capture_output=True, timeout=45, check=True)
    require(len(result.stdout) <= 2 * 1024**2)
    if not result.stdout.strip():
        return None
    value = json.loads(result.stdout)
    meta = value.get('metadata', {})
    require(value.get('kind') == kind and meta.get('name') == name and
            meta.get('namespace') == 'takserver' and meta.get('uid') and
            meta.get('resourceVersion') and not meta.get('deletionTimestamp'))
    return value


try:
    os.umask(0o077)
    dest = Path(sys.argv[1]).absolute()
    require(not dest.is_symlink())
    # macOS exposes /tmp through a system symlink. Resolve parent aliases,
    # while still refusing a symlink at the requested destination itself.
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(dest.is_dir() and dest.stat().st_uid == os.getuid())
    dest.chmod(0o700)
    for name in ('admin.pem', 'admin.key'):
        path = dest / name
        require(not path.is_symlink() and (not path.exists() or path.is_file()))
    secret = read('Secret', 'takserver-certs')
    data = secret.get('data', {}) if secret else {}
    present = [bool(data.get(name)) for name in ('admin.pem', 'admin.key')]
    # Older releases stored both files in this ConfigMap. Never mix one key
    # from the Secret with the other from a different resource snapshot.
    if not any(present):
        legacy = read('ConfigMap', 'cert-migration')
        data = legacy.get('binaryData', {}) if legacy else {}
    else:
        require(all(present))
    pair = {}
    for name in ('admin.pem', 'admin.key'):
        require(isinstance(data.get(name), str))
        pair[name] = base64.b64decode(data[name], validate=True)
        require(0 < len(pair[name]) <= 256 * 1024)
    with tempfile.TemporaryDirectory(prefix='.extract-', dir=dest) as temp:
        for name, content in pair.items():
            path = Path(temp) / name
            with path.open('xb') as output:
                output.write(content)
            path.chmod(0o600)
        for name in pair:
            os.replace(Path(temp) / name, dest / name)
    print(dest)
except Exception:
    # Kubectl stderr and decoded key material must never reach a public log.
    raise SystemExit('Unable to extract the current installation certificate pair') from None
PY
