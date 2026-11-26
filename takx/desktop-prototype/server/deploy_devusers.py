#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Write the sign-in data of the repository's Keycloak test users to private files.

  deploy_devusers.py REPO OUTDIR

80-keycloak-users.sh creates two users with the public development defaults
of this repository's tests: `doug` (in /UDS Core/Admin) and `mapuser` (in
/CloudTAK/Users only). The browser checks of the desktop package sign in as
both. This script reads the defaults from the files that define them
(tests/auth.setup.ts and tests/ensure-map-user.py; the environment variables
those files honour win) and writes OUTDIR/<user>.json with mode 600. It
prints file names only, never a value.
"""
import json
import os
import re
import sys
from pathlib import Path


def default_of(path, pattern):
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"deploy_devusers.py: no default found in {path}")
    return match.group(1)


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    repo, out = Path(sys.argv[1]), Path(sys.argv[2])
    setup = repo / "tests" / "auth.setup.ts"
    mapuser = repo / "tests" / "ensure-map-user.py"
    users = {
        "doug": {
            "username": os.environ.get("KEYCLOAK_USER")
            or default_of(setup, r'KEYCLOAK_USER \|\| "([^"]+)"'),
            "password": os.environ.get("KEYCLOAK_PASSWORD")
            or default_of(setup, r'KEYCLOAK_PASSWORD \|\| "([^"]+)"'),
            "groups": ["/UDS Core/Admin", "/TAK/Operators_READ"],
        },
        "mapuser": {
            "username": os.environ.get("KEYCLOAK_MAP_USER")
            or default_of(mapuser, r'"KEYCLOAK_MAP_USER", "([^"]+)"'),
            "password": os.environ.get("KEYCLOAK_MAP_USER_PASSWORD")
            or default_of(mapuser, r'"KEYCLOAK_MAP_USER_PASSWORD", "([^"]+)"'),
            "groups": ["/CloudTAK/Users"],
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    for name, record in users.items():
        path = out / f"{name}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream)
        os.chmod(path, 0o600)
        print(f"{path}: user {name}, groups {', '.join(record['groups'])}")


if __name__ == "__main__":
    main()
