# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Acquire only the reviewed maintenance metadata; never export response bodies."""
import json
import os
import re
import urllib.error
import urllib.request

REPO = 'jonathanagustin/uds-takserver'
ALERTS = (87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError('redirect refused')


def snapshot(data):
    if not isinstance(data, dict):
        raise ValueError('invalid response')
    state = data.get('state')
    if state not in ('open', 'fixed', 'dismissed'):
        raise ValueError('unknown state')
    instance = data.get('most_recent_instance') or {}
    ref = instance.get('ref')
    if ref is not None and (not isinstance(ref, str) or len(ref) > 200):
        raise ValueError('invalid ref')
    return {'state': state, 'ref': ref}


def fetch(number):
    request = urllib.request.Request('https://api.github.com/repos/' + REPO + '/code-scanning/alerts/' + str(number),
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN']})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError('oversize response')
            return snapshot(json.loads(raw))
    except urllib.error.HTTPError:
        return {'available': False}


def main():
    if os.environ.get('GITHUB_REPOSITORY') != REPO or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('context')
    sha = os.environ.get('GITHUB_SHA')
    if not re.fullmatch(r'[a-f0-9]{40}', sha or ''):
        raise ValueError('invalid source')
    print(json.dumps({'source': sha, 'repository': REPO, 'alerts': {n: fetch(n) for n in ALERTS}}))

if __name__ == '__main__':
    main()
