#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Fetch, verify, and cache the pinned upstream Git LFS payloads a job needs.

The pinned ATAK checkout is made with GIT_LFS_SKIP_SMUDGE=1, so it holds LFS
pointers and makes no LFS request. A job names a set from upstream-lfs.lock.json
and gets exactly those payloads in a content-addressed store, <store>/<sha256>.

Every payload is checked against the sha256 oid and size of its LFS pointer,
whether it came from the network or from a restored cache. With --source, the
path must also be that exact pointer at the pinned revision of the checkout.
Recipe locks keep their own, stricter checks on what they consume.
"""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

MEDIA_ROOT = "https://media.githubusercontent.com/media/"
KEY_PREFIX = "takx-upstream-lfs-v1"
MAX_BYTES = 256 * 1024 * 1024
ATTEMPTS = 5
BACKOFF_SECONDS = (2, 4, 8, 16)
TIMEOUT_SECONDS = 30
BLOCK = 1024 * 1024


class Transient(Exception):
    """A network failure that a later attempt may not repeat."""


def check_path(path):
    if (not isinstance(path, str) or any(part in ("", ".", "..") for part in path.split("/"))
            or "\\" in path or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        raise ValueError(f"Unexpected upstream path in lock: {path!r}")


def load_lock(path):
    lock = json.loads(Path(path).read_text())
    if not isinstance(lock, dict) or lock.get("schema") != 1:
        raise ValueError("Unsupported upstream LFS lock schema")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(lock.get("repository"))):
        raise ValueError("Unexpected upstream repository in lock")
    if not re.fullmatch(r"[0-9a-f]{40}", str(lock.get("revision"))):
        raise ValueError("Upstream revision must be a full commit id")
    files, sets = lock.get("files"), lock.get("sets")
    if not isinstance(files, dict) or not isinstance(sets, dict):
        raise ValueError("Upstream LFS lock needs files and sets")
    for name, entry in files.items():
        check_path(name)
        if (not isinstance(entry, dict) or set(entry) != {"sha256", "bytes"}
                or not re.fullmatch(r"[0-9a-f]{64}", str(entry["sha256"]))
                or type(entry["bytes"]) is not int or not 0 < entry["bytes"] <= MAX_BYTES):
            raise ValueError(f"Unexpected lock entry for {name}")
    for name, paths in sets.items():
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) or not isinstance(paths, list) or not paths
                or any(not isinstance(path, str) or path not in files for path in paths)
                or len(set(paths)) != len(paths)):
            raise ValueError(f"Unexpected set in lock: {name!r}")
    return lock


def entries(lock, name):
    if name not in lock["sets"]:
        raise ValueError(f"Unknown upstream LFS set: {name}")
    return [(path, lock["files"][path]["sha256"], lock["files"][path]["bytes"])
            for path in sorted(lock["sets"][name])]


def cache_key(lock, name):
    lines = sorted(f"{sha256} {size} {path}\n" for path, sha256, size in entries(lock, name))
    listing = hashlib.sha256("".join(lines).encode()).hexdigest()
    return f"{KEY_PREFIX}-{lock['revision']}-{listing}"


def pointer(sha256, size):
    return f"version https://git-lfs.github.com/spec/v1\noid sha256:{sha256}\nsize {size}\n".encode()


def pointer_blob(sha256, size):
    data = pointer(sha256, size)
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def verify_pointers(source, lock, items):
    """Require each path to be the locked LFS pointer at the pinned revision.

    Only tree objects are read, so a blobless partial clone answers offline.
    """
    environment = dict(os.environ, GIT_NO_LAZY_FETCH="1")
    for path, sha256, size in items:
        try:
            listing = subprocess.check_output(
                ["git", "-C", str(source), "ls-tree", "-z", lock["revision"], "--", path],
                env=environment, timeout=30, stderr=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise ValueError(f"Pinned revision {lock['revision']} is not readable in {source}") from error
        expected = f"blob {pointer_blob(sha256, size)}\t{path}\0".encode()
        if listing not in (b"100644 " + expected, b"100755 " + expected):
            raise ValueError(f"Not the locked LFS pointer at {lock['revision']}: {path}")


def check_file(path, sha256, size):
    """Fail unless path is a regular file with exactly the locked bytes."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise ValueError(f"Payload is missing or not a regular file: {path}") from error
    with os.fdopen(descriptor, "rb") as stream:
        state = os.fstat(stream.fileno())
        if not stat.S_ISREG(state.st_mode):
            raise ValueError(f"Payload is not a regular file: {path}")
        if state.st_size != size:
            raise ValueError(f"Payload size {state.st_size} differs from pointer size {size}: {path}")
        value = hashlib.sha256()
        for block in iter(lambda: stream.read(BLOCK), b""):
            value.update(block)
    if value.hexdigest() != sha256:
        raise ValueError(f"Payload sha256 differs from pointer oid: {path}")


def media_url(lock, path):
    return f"{MEDIA_ROOT}{lock['repository']}/{lock['revision']}/{urllib.parse.quote(path)}"


def fetch_once(url, output, size, opener):
    """Copy at most size + 1 bytes of the response body to output."""
    def network(call, *arguments, **options):
        try:
            return call(*arguments, **options)
        except urllib.error.HTTPError as error:
            if error.code == 429 or error.code >= 500:
                raise Transient(f"HTTP {error.code}") from error
            raise ValueError(f"HTTP {error.code} for {url}") from error
        except (OSError, EOFError, http.client.HTTPException) as error:
            # URLError, timeouts, resets, and TLS failures are all OSError.
            raise Transient(f"{type(error).__name__}: {error}") from error

    received = 0
    with network(opener, url, timeout=TIMEOUT_SECONDS) as response:
        while received <= size:
            block = network(response.read, min(BLOCK, size + 1 - received))
            if not block:
                break
            received += len(block)
            output.write(block)
    if received > size:
        raise ValueError(f"Response is larger than pointer size {size}: {url}")
    if received < size:
        raise Transient(f"truncated at {received} of {size} bytes")


def download(url, final, sha256, size, opener, sleep):
    for attempt in range(1, ATTEMPTS + 1):
        descriptor, name = tempfile.mkstemp(dir=final.parent, prefix=".partial-")
        try:
            with os.fdopen(descriptor, "wb") as output:
                os.fchmod(descriptor, 0o644)  # as a plain download; mkstemp alone gives 0600
                fetch_once(url, output, size, opener)
            check_file(name, sha256, size)
            os.replace(name, final)
            return
        except Transient as error:
            if attempt == ATTEMPTS:
                raise ValueError(f"Download failed after {ATTEMPTS} attempts ({error}): {url}") from error
            delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS)) - 1]
            print(f"attempt {attempt}/{ATTEMPTS} failed ({error}); retrying in {delay}s: {url}", file=sys.stderr)
            sleep(delay)
        finally:
            if os.path.lexists(name):
                os.unlink(name)


def materialize(lock, name, store, source=None, offline=False, opener=urllib.request.urlopen, sleep=time.sleep):
    """Leave every payload of the set verified at <store>/<sha256>."""
    items = entries(lock, name)
    if source is not None:
        verify_pointers(source, lock, items)
    store = Path(store)
    if store.is_symlink():
        raise ValueError(f"Store must not be a symlink: {store}")
    store.mkdir(parents=True, exist_ok=True)
    result = []
    for path, sha256, size in items:
        final = store / sha256
        origin = "cached"
        if os.path.lexists(final):
            try:
                check_file(final, sha256, size)
            except ValueError as error:
                # A restored cache is never trusted: drop the entry and refetch.
                print(f"rejected stored payload ({error})", file=sys.stderr)
                final.unlink()
        if not os.path.lexists(final):
            if offline:
                raise ValueError(f"Verified payload is not stored and --offline is set: {path}")
            download(media_url(lock, path), final, sha256, size, opener, sleep)
            origin = "fetched"
        result.append((path, final, origin))
    return result


def locate(lock, store, path):
    if path not in lock["files"]:
        raise ValueError(f"Path is not in the upstream LFS lock: {path!r}")
    final = Path(store) / lock["files"][path]["sha256"]
    check_file(final, lock["files"][path]["sha256"], lock["files"][path]["bytes"])
    return final


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("key", "materialize", "path"))
    parser.add_argument("upstream_path", nargs="?", help="path: the upstream file to locate in the store")
    parser.add_argument("--lock", type=Path, default=Path(__file__).with_name("upstream-lfs.lock.json"))
    parser.add_argument("--set", dest="name", help="key, materialize: the named file set")
    parser.add_argument("--store", type=Path, help="materialize, path: the content-addressed directory")
    parser.add_argument("--source", type=Path, help="materialize: pinned checkout to check the pointers in")
    parser.add_argument("--offline", action="store_true", help="materialize: fail instead of downloading")
    # The workflow writes "path --store DIR FILE". Before Python 3.13 plain
    # parse_args binds the optional positional as soon as it sees the command,
    # then rejects FILE after an option.
    args = parser.parse_intermixed_args()
    lock = load_lock(args.lock)
    if args.command == "path":
        if args.store is None or args.upstream_path is None:
            parser.error("path requires --store and an upstream path")
        print(locate(lock, args.store, args.upstream_path))
        return
    if args.name is None or args.upstream_path is not None:
        parser.error(f"{args.command} requires --set and takes no path")
    if args.command == "key":
        print(cache_key(lock, args.name))
        return
    if args.store is None:
        parser.error("materialize requires --store")
    for path, final, origin in materialize(lock, args.name, args.store, args.source, args.offline):
        print(f"verified {origin} {final.name} {path}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        sys.exit(2)
