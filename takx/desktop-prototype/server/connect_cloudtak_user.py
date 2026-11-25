#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Desktop prototype, connected run: the Keycloak user of the CloudTAK peer.

CloudTAK is the browser peer of the TAKX client. Its user must be allowed to
sign in to CloudTAK (/CloudTAK/Users) and must be able to send as well as
receive in the TAK group of the TakClient 'takx-desktop' (Operators), so it
is put in /TAK/Operators_READ and /TAK/Operators_WRITE (docs/sso.md: the
suffix sets the direction). The repository's test user 'mapuser' has the read
direction only, which is why this prototype has its own user.

Usage: connect_cloudtak_user.py [SECRETS_DIR]
  SECRETS_DIR  default $TAKX_PROTO_ROOT/secrets/cloudtak-user. password.txt
               there (mode 600) is created with a random value when missing
               and is never printed; user.json (no secret) names the user.

The Keycloak admin calls are those of tests/ensure-tak-group.py and
tests/ensure-map-user.py (admin token from the cluster's Secret).
"""
import importlib.util
import json
import os
import secrets
import string
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
USER = 'takx-cloudtak'
GROUPS = ['/CloudTAK/Users', '/TAK/Operators_READ', '/TAK/Operators_WRITE']


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def new_password():
    alphabet = string.ascii_letters + string.digits
    body = ''.join(secrets.choice(alphabet) for _ in range(24))
    # One of each class the realm policy asks for, at fixed places.
    return 'Tx7!' + body + '#q'


def main():
    root = Path(os.environ.get('TAKX_PROTO_ROOT', '/var/lib/takx-proto'))
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'secrets/cloudtak-user'
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    secret = directory / 'password.txt'
    if not secret.exists():
        descriptor = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(new_password() + '\n')
    kc = load('ensure_tak_group', REPO / 'tests/ensure-tak-group.py')
    users = load('ensure_map_user', REPO / 'tests/ensure-map-user.py')
    access = kc.token()
    wanted = {users.ensure_path(access, path): path for path in GROUPS}
    query = urllib.parse.urlencode({'username': USER, 'exact': 'true'})
    _status, found = kc.request('GET', f'/admin/realms/{kc.REALM}/users?{query}', access)
    if not found:
        kc.request('POST', f'/admin/realms/{kc.REALM}/users', access, {
            'username': USER, 'enabled': True, 'email': f'{USER}@uds.dev', 'emailVerified': True,
            'firstName': 'TAKX', 'lastName': 'CloudTAK peer', 'requiredActions': []})
        _status, found = kc.request('GET', f'/admin/realms/{kc.REALM}/users?{query}', access)
    uid = found[0]['id']
    request = urllib.request.Request(
        kc.ADMIN + f'/admin/realms/{kc.REALM}/users/{uid}/reset-password',
        data=json.dumps({'type': 'password', 'value': secret.read_text().strip(), 'temporary': False}).encode(),
        method='PUT', headers={'Authorization': f'Bearer {access}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=30):
            pass
    except urllib.error.HTTPError as error:
        # The realm refuses a password used in the last changes: it is already set.
        if not (error.code == 400 and 'invalidPasswordHistory' in error.read().decode()):
            raise SystemExit(f'reset-password failed: HTTP {error.code}') from None
    _status, current = kc.request('GET', f'/admin/realms/{kc.REALM}/users/{uid}/groups', access)
    for group in current or []:
        if group['id'] not in wanted:
            kc.request('DELETE', f'/admin/realms/{kc.REALM}/users/{uid}/groups/{group["id"]}', access)
    for gid in wanted:
        kc.request('PUT', f'/admin/realms/{kc.REALM}/users/{uid}/groups/{gid}', access, {})
    _status, current = kc.request('GET', f'/admin/realms/{kc.REALM}/users/{uid}/groups', access)
    record = {'username': USER, 'groups': sorted(group['path'] for group in current or []),
              'password_file': str(secret)}
    (directory / 'user.json').write_text(json.dumps(record, indent=1) + '\n')
    print(json.dumps(record))


if __name__ == '__main__':
    main()
