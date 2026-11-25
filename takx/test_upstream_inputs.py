# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Upstream LFS payload guards, offline: a tiny Git fixture and scripted HTTP."""

import contextlib
import copy
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import upstream_inputs

HERE = Path(__file__).resolve().parent
WORKFLOW = HERE.parent / ".github/workflows/takx-gl-qualification.yaml"
PAYLOAD = b"pinned upstream payload fixture\n" * 64
OTHER = b"second pinned upstream payload\n" * 8


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Response(io.BytesIO):
    """A scripted response body that records how much of it was read."""

    def __init__(self, data):
        super().__init__(data)
        self.consumed = 0

    def read(self, size=-1):
        block = super().read(size)
        self.consumed += len(block)
        return block


class Scripted:
    """An opener that replays bodies and exceptions in order, then refuses."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.urls = []
        self.responses = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        if not self.steps:
            raise AssertionError(f"unexpected request: {url}")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        self.responses.append(Response(step))
        return self.responses[-1]


def unavailable(code=503):
    return urllib.error.HTTPError("https://media.githubusercontent.com/", code, "scripted", {}, None)


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = self.root / "store"
        self.source = self.root / "source"
        (self.source / "depends").mkdir(parents=True)
        (self.source / "depends/payload.tar.gz").write_bytes(upstream_inputs.pointer(sha256(PAYLOAD), len(PAYLOAD)))
        (self.source / "depends/other.zip").write_bytes(upstream_inputs.pointer(sha256(OTHER), len(OTHER)))
        (self.source / "depends/plain.txt").write_text("an ordinary tracked file\n")
        os.symlink("payload.tar.gz", self.source / "depends/link.tar.gz")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=0+fixture@users.noreply.github.com",
                 "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")
        self.lock = upstream_inputs.load_lock(self.write_lock({
            "schema": 1, "repository": "TAK-Product-Center/atak-civ", "revision": self.git("rev-parse", "HEAD").strip(),
            "files": {
                "depends/payload.tar.gz": {"sha256": sha256(PAYLOAD), "bytes": len(PAYLOAD)},
                "depends/other.zip": {"sha256": sha256(OTHER), "bytes": len(OTHER)},
            },
            "sets": {"one": ["depends/payload.tar.gz"], "both": ["depends/payload.tar.gz", "depends/other.zip"]},
        }))
        self.final = self.store / sha256(PAYLOAD)
        self.sleeps = []
        self.log = io.StringIO()

    def git(self, *arguments):
        return subprocess.check_output(["git", "-C", str(self.source), *arguments], text=True)

    def write_lock(self, value):
        path = self.root / "lock.json"
        path.write_text(json.dumps(value))
        return path

    def run_set(self, opener, name="one", lock=None, **options):
        with contextlib.redirect_stderr(self.log):
            return upstream_inputs.materialize(lock or self.lock, name, self.store, opener=opener,
                                               sleep=self.sleeps.append, **options)

    def stored(self):
        return sorted(path.name for path in self.store.iterdir()) if self.store.exists() else []


class Pointers(Fixture):
    def test_pointer_blobs_match_the_pinned_upstream_tree(self):
        # Blob ids as `git ls-tree 9f6893dd657feacc35ec5de03dad721c2e44170e` reports them.
        files = upstream_inputs.load_lock(HERE / "upstream-lfs.lock.json")["files"]
        recorded = {
            "takthirdparty/distfiles/libkml.tar.gz": "a31ea824a85660cb7e86888670e86e52e7618ee9",
            "depends/tinygltf-2.4.1-mod.tar.gz": "a6cc6eb134d6dec7170ed4ebe5708f68637b8228",
            "depends/tinygltfloader-0.9.5-mod.tar.gz": "a0a732582dcfe6ad52fb747a1cc51e531587d621",
            "takkernel/engine/src/main/assets/loresel.sqlite.zip": "5c60bbcf11ea8e41981eb6c4b0deb74e5f4f98d0",
        }
        self.assertEqual(recorded, {path: upstream_inputs.pointer_blob(entry["sha256"], entry["bytes"])
                                    for path, entry in files.items()})

    def test_pointer_blob_is_the_git_object_id_of_the_pointer_text(self):
        for path in ("depends/payload.tar.gz", "depends/other.zip"):
            entry = self.lock["files"][path]
            self.assertEqual(self.git("rev-parse", f"HEAD:{path}").strip(),
                             upstream_inputs.pointer_blob(entry["sha256"], entry["bytes"]))

    def test_locked_pointers_at_the_pinned_revision_pass(self):
        upstream_inputs.verify_pointers(self.source, self.lock, upstream_inputs.entries(self.lock, "both"))

    def test_pointer_with_another_oid_or_size_is_refused(self):
        for sha, size in ((sha256(OTHER), len(PAYLOAD)), (sha256(PAYLOAD), len(PAYLOAD) + 1)):
            with self.subTest(sha=sha, size=size), self.assertRaisesRegex(ValueError, "Not the locked LFS pointer"):
                upstream_inputs.verify_pointers(self.source, self.lock, [("depends/payload.tar.gz", sha, size)])

    def test_path_that_is_not_an_lfs_pointer_is_refused(self):
        plain = (self.source / "depends/plain.txt").read_bytes()
        for path, data in (("depends/plain.txt", plain), ("depends/link.tar.gz", PAYLOAD),
                           ("depends", PAYLOAD), ("depends/absent.zip", PAYLOAD)):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "Not the locked LFS pointer"):
                upstream_inputs.verify_pointers(self.source, self.lock, [(path, sha256(data), len(data))])

    def test_executable_pointer_passes(self):
        # Upstream commits both tinygltf archive pointers with mode 100755.
        os.chmod(self.source / "depends/other.zip", 0o755)
        self.git("-c", "user.name=Fixture", "-c", "user.email=0+fixture@users.noreply.github.com",
                 "-c", "core.hooksPath=/dev/null", "commit", "-qam", "executable pointer")
        lock = dict(self.lock, revision=self.git("rev-parse", "HEAD").strip())
        self.assertRegex(self.git("ls-tree", "HEAD", "--", "depends/other.zip"), r"^100755 blob ")
        upstream_inputs.verify_pointers(self.source, lock, upstream_inputs.entries(lock, "both"))

    def test_checkout_without_the_pinned_revision_is_refused(self):
        lock = dict(self.lock, revision="0" * 40)
        with self.assertRaisesRegex(ValueError, "is not readable"):
            upstream_inputs.verify_pointers(self.source, lock, upstream_inputs.entries(lock, "one"))

    def test_refused_pointer_stops_before_any_request(self):
        (self.source / "depends/payload.tar.gz").write_bytes(PAYLOAD)
        self.git("-c", "user.name=Fixture", "-c", "user.email=0+fixture@users.noreply.github.com",
                 "-c", "core.hooksPath=/dev/null", "commit", "-qam", "payload committed in place of its pointer")
        lock = dict(self.lock, revision=self.git("rev-parse", "HEAD").strip())
        opener = Scripted()
        with self.assertRaisesRegex(ValueError, "Not the locked LFS pointer"):
            self.run_set(opener, lock=lock, source=self.source)
        self.assertEqual(([], []), (opener.urls, self.stored()))


class LockGuards(Fixture):
    def reject(self, change, message):
        value = copy.deepcopy(self.lock)
        change(value)
        with self.assertRaisesRegex(ValueError, message):
            upstream_inputs.load_lock(self.write_lock(value))

    def test_path_traversal_in_a_locked_path_is_refused(self):
        for path in ("../escape.zip", "depends/../../escape.zip", "/etc/passwd", "depends//x.zip", "./x.zip",
                     "depends/x.zip/", "depends\\x.zip", "depends/x\n.zip", ""):
            def change(value, path=path):
                value["files"][path] = value["files"]["depends/payload.tar.gz"]
            with self.subTest(path=path):
                self.reject(change, "Unexpected upstream path")

    def test_digest_that_is_not_a_store_file_name_is_refused(self):
        for digest in ("../../escape", "A" * 64, "a" * 63, "", None, 7):
            with self.subTest(digest=digest):
                self.reject(lambda value, digest=digest: value["files"]["depends/other.zip"].update(sha256=digest),
                            "Unexpected lock entry")

    def test_size_outside_the_bound_is_refused(self):
        for size in (0, -1, True, "17", 1.5, upstream_inputs.MAX_BYTES + 1):
            with self.subTest(size=size):
                self.reject(lambda value, size=size: value["files"]["depends/other.zip"].update(bytes=size),
                            "Unexpected lock entry")

    def test_malformed_identity_and_sets_are_refused(self):
        cases = (
            (lambda value: value.update(schema=2), "schema"),
            (lambda value: value.update(repository="TAK-Product-Center/atak-civ/../other"), "repository"),
            (lambda value: value.update(revision="main"), "full commit id"),
            (lambda value: value["files"]["depends/other.zip"].update(url="https://example.invalid/x"), "lock entry"),
            (lambda value: value["sets"].update(one=["depends/unlisted.zip"]), "Unexpected set"),
            (lambda value: value["sets"].update(one=["depends/other.zip", "depends/other.zip"]), "Unexpected set"),
            (lambda value: value["sets"].update(one=[]), "Unexpected set"),
            (lambda value: value["sets"].update(one=[["depends/other.zip"]]), "Unexpected set"),
            (lambda value: value["sets"].update({"../one": ["depends/other.zip"]}), "Unexpected set"),
        )
        for index, (change, message) in enumerate(cases):
            with self.subTest(case=index):
                self.reject(change, message)

    def test_unknown_set_and_unlocked_path_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Unknown upstream LFS set"):
            self.run_set(Scripted(), name="absent")
        self.run_set(Scripted(PAYLOAD))
        for path in ("../store/" + self.final.name, str(self.final), "depends/plain.txt"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "not in the upstream LFS lock"):
                upstream_inputs.locate(self.lock, self.store, path)


class CacheKey(Fixture):
    def changed(self, change, name="both"):
        value = copy.deepcopy(self.lock)
        change(value)
        return upstream_inputs.cache_key(value, name)

    def test_key_names_the_revision_and_ignores_listing_order(self):
        key = upstream_inputs.cache_key(self.lock, "both")
        self.assertRegex(key, rf"^takx-upstream-lfs-v1-{self.lock['revision']}-[0-9a-f]{{64}}$")
        self.assertEqual(key, self.changed(lambda value: value["sets"]["both"].reverse()))

    def test_key_changes_with_revision_paths_oid_or_size(self):
        other = self.lock["files"]["depends/other.zip"]
        keys = {
            upstream_inputs.cache_key(self.lock, "both"),
            upstream_inputs.cache_key(self.lock, "one"),
            self.changed(lambda value: value.update(revision="1" * 40)),
            self.changed(lambda value: value["files"]["depends/other.zip"].update(sha256="2" * 64)),
            self.changed(lambda value: value["files"]["depends/other.zip"].update(bytes=other["bytes"] + 1)),
            self.changed(lambda value: (value["files"].update({"depends/renamed.zip": other}),
                                        value["sets"].update(both=["depends/payload.tar.gz", "depends/renamed.zip"]))),
        }
        self.assertEqual(6, len(keys))

    def test_command_line_prints_only_the_key(self):
        output = subprocess.check_output(
            [sys.executable, "-B", str(HERE / "upstream_inputs.py"), "key", "--set", "kernel-native"], text=True)
        self.assertEqual(upstream_inputs.cache_key(upstream_inputs.load_lock(HERE / "upstream-lfs.lock.json"),
                                                   "kernel-native") + "\n", output)


class Materialize(Fixture):
    def test_fetches_only_the_pinned_media_url_into_the_store(self):
        before = sorted(path for path in self.root.rglob("*") if ".git" not in path.parts)
        opener = Scripted(OTHER, PAYLOAD)
        result = self.run_set(opener, name="both", source=self.source)
        root = f"https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/{self.lock['revision']}/"
        self.assertEqual([root + "depends/other.zip", root + "depends/payload.tar.gz"], opener.urls)
        self.assertEqual([("depends/other.zip", self.store / sha256(OTHER), "fetched"),
                          ("depends/payload.tar.gz", self.final, "fetched")], result)
        self.assertEqual((PAYLOAD, OTHER), (self.final.read_bytes(), (self.store / sha256(OTHER)).read_bytes()))
        after = sorted(path for path in self.root.rglob("*") if ".git" not in path.parts)
        self.assertEqual(sorted([*before, self.store, self.final, self.store / sha256(OTHER)]), after)
        self.assertEqual("", self.git("status", "--porcelain"))

    def test_path_is_quoted_into_the_url(self):
        self.assertEqual(
            f"https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/{self.lock['revision']}"
            "/takcot/examples/CoT%20Types%20Data%20Package.zip",
            upstream_inputs.media_url(self.lock, "takcot/examples/CoT Types Data Package.zip"))

    def test_verified_store_makes_no_request(self):
        self.run_set(Scripted(PAYLOAD))
        self.assertEqual([("depends/payload.tar.gz", self.final, "cached")], self.run_set(Scripted()))
        self.assertEqual([("depends/payload.tar.gz", self.final, "cached")], self.run_set(Scripted(), offline=True))
        self.assertEqual(self.final, upstream_inputs.locate(self.lock, self.store, "depends/payload.tar.gz"))

    def test_oid_mismatch_fails_closed_without_retry(self):
        opener = Scripted(PAYLOAD[:-1] + b"X")
        with self.assertRaisesRegex(ValueError, "sha256 differs from pointer oid"):
            self.run_set(opener)
        self.assertEqual((1, [], []), (len(opener.urls), self.sleeps, self.stored()))

    def test_oversized_response_fails_closed_after_one_extra_byte(self):
        opener = Scripted(PAYLOAD + b"trailing bytes beyond the pointer size")
        with self.assertRaisesRegex(ValueError, "larger than pointer size"):
            self.run_set(opener)
        self.assertEqual(len(PAYLOAD) + 1, opener.responses[0].consumed)
        self.assertEqual((1, [], []), (len(opener.urls), self.sleeps, self.stored()))

    def test_truncated_download_is_retried_then_fails_closed(self):
        opener = Scripted(*[PAYLOAD[:100]] * upstream_inputs.ATTEMPTS)
        with self.assertRaisesRegex(ValueError, "failed after 5 attempts .truncated at 100 of"):
            self.run_set(opener)
        self.assertEqual((5, [2, 4, 8, 16], []), (len(opener.urls), self.sleeps, self.stored()))
        self.assertEqual(4, self.log.getvalue().count("retrying in"))

    def test_retry_then_success(self):
        opener = Scripted(unavailable(503), urllib.error.URLError("scripted reset"), TimeoutError("scripted"),
                          unavailable(429), PAYLOAD)
        self.assertEqual([("depends/payload.tar.gz", self.final, "fetched")], self.run_set(opener))
        self.assertEqual((5, [2, 4, 8, 16], [self.final.name]), (len(opener.urls), self.sleeps, self.stored()))
        self.assertEqual((PAYLOAD, 0o644), (self.final.read_bytes(), self.final.stat().st_mode & 0o777))

    def test_retry_exhaustion_fails_closed(self):
        opener = Scripted(*[unavailable(502) for _ in range(upstream_inputs.ATTEMPTS)])
        with self.assertRaisesRegex(ValueError, "failed after 5 attempts .HTTP 502"):
            self.run_set(opener)
        self.assertEqual((5, [2, 4, 8, 16], []), (len(opener.urls), self.sleeps, self.stored()))

    def test_client_error_is_not_retried(self):
        for code in (403, 404):
            opener = Scripted(unavailable(code))
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, f"HTTP {code}"):
                self.run_set(opener)
            self.assertEqual((1, [], []), (len(opener.urls), self.sleeps, self.stored()))

    def test_corrupted_store_entry_is_detected_and_refetched(self):
        for damage, reason in ((PAYLOAD[:-1] + b"X", "sha256 differs from pointer oid"),
                               (PAYLOAD[:7], "size 7 differs from pointer size 2048"),
                               (PAYLOAD + b"X", "size 2049 differs from pointer size 2048")):
            with self.subTest(reason=reason):
                self.run_set(Scripted(PAYLOAD))
                self.final.write_bytes(damage)
                self.log = io.StringIO()
                self.assertEqual([("depends/payload.tar.gz", self.final, "fetched")], self.run_set(Scripted(PAYLOAD)))
                self.assertEqual(PAYLOAD, self.final.read_bytes())
                self.assertIn(f"rejected stored payload (Payload {reason}", self.log.getvalue())

    def test_corrupted_store_entry_fails_closed_offline(self):
        self.run_set(Scripted(PAYLOAD))
        self.final.write_bytes(PAYLOAD[:-1] + b"X")
        with self.assertRaisesRegex(ValueError, "not stored and --offline"):
            self.run_set(Scripted(), offline=True)
        self.assertEqual([], self.stored())
        with self.assertRaisesRegex(ValueError, "missing or not a regular file"):
            upstream_inputs.locate(self.lock, self.store, "depends/payload.tar.gz")

    def test_located_payload_is_verified_again(self):
        self.run_set(Scripted(PAYLOAD))
        self.final.write_bytes(PAYLOAD[:-1] + b"X")
        with self.assertRaisesRegex(ValueError, "sha256 differs from pointer oid"):
            upstream_inputs.locate(self.lock, self.store, "depends/payload.tar.gz")

    def test_symlinked_store_entry_is_replaced_and_its_target_untouched(self):
        outside = self.root / "outside"
        outside.write_bytes(PAYLOAD)
        self.store.mkdir()
        self.final.symlink_to(outside)
        self.assertEqual([("depends/payload.tar.gz", self.final, "fetched")], self.run_set(Scripted(PAYLOAD)))
        self.assertFalse(self.final.is_symlink())
        self.assertEqual((PAYLOAD, PAYLOAD), (self.final.read_bytes(), outside.read_bytes()))

    def test_dangling_symlink_store_entry_is_never_taken_as_stored(self):
        self.store.mkdir()
        self.final.symlink_to(self.root / "absent")
        with self.assertRaisesRegex(ValueError, "not stored and --offline"):
            self.run_set(Scripted(), offline=True)
        self.final.symlink_to(self.root / "absent")
        self.assertEqual([("depends/payload.tar.gz", self.final, "fetched")], self.run_set(Scripted(PAYLOAD)))
        self.assertEqual((False, PAYLOAD, False),
                         (self.final.is_symlink(), self.final.read_bytes(), (self.root / "absent").exists()))

    def test_download_is_staged_inside_the_store_until_verified(self):
        # The rename that publishes a payload must stay within one directory.
        staged = []

        def opener(url, timeout):
            staged.append(self.stored())
            return Response(PAYLOAD)

        self.run_set(opener)
        self.assertEqual((1, 1), (len(staged), len(staged[0])))
        self.assertRegex(staged[0][0], r"^\.partial-")
        self.assertEqual([self.final.name], self.stored())

    def test_symlinked_store_is_refused(self):
        (self.root / "elsewhere").mkdir()
        self.store.symlink_to(self.root / "elsewhere")
        opener = Scripted()
        with self.assertRaisesRegex(ValueError, "Store must not be a symlink"):
            self.run_set(opener)
        self.assertEqual(([], []), (opener.urls, list((self.root / "elsewhere").iterdir())))

    def test_command_line_fails_closed_offline(self):
        result = subprocess.run(
            [sys.executable, "-B", str(HERE / "upstream_inputs.py"), "materialize", "--set", "minizip",
             "--store", str(self.store), "--offline"], capture_output=True, text=True, check=False)
        self.assertEqual((2, ""), (result.returncode, result.stdout))
        self.assertIn("not stored and --offline", result.stderr)


class Loopback(Fixture):
    """The real urllib transport against a local server: 503, a cut body, then the payload."""

    def test_real_transport_errors_are_retried(self):
        replies = [(503, b"", 0), (200, PAYLOAD[:100], len(PAYLOAD)), (200, PAYLOAD, len(PAYLOAD))]
        paths = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                status, body, length = replies.pop(0)
                self.send_response(status)
                self.send_header("Content-Length", str(length))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *arguments):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def opener(url, timeout):
            self.assertTrue(url.startswith(upstream_inputs.MEDIA_ROOT))
            local = f"http://127.0.0.1:{server.server_address[1]}/" + url[len(upstream_inputs.MEDIA_ROOT):]
            return direct.open(local, timeout=timeout)

        self.assertEqual([("depends/payload.tar.gz", self.final, "fetched")], self.run_set(opener))
        self.assertEqual([f"/TAK-Product-Center/atak-civ/{self.lock['revision']}/depends/payload.tar.gz"] * 3, paths)
        self.assertEqual(([2, 4], PAYLOAD, [self.final.name]), (self.sleeps, self.final.read_bytes(), self.stored()))


class RepositoryPins(unittest.TestCase):
    """The shared lock must agree with every recipe lock and with the workflow."""

    @classmethod
    def setUpClass(cls):
        cls.lock = upstream_inputs.load_lock(HERE / "upstream-lfs.lock.json")
        cls.workflow = WORKFLOW.read_text()

    def pinned(self, path):
        return self.lock["files"][path]

    def test_gltf_archives_match_the_kernel_native_lock(self):
        recipe = json.loads((HERE / "kernel-native/gltf-inputs.lock.json").read_text())
        self.assertEqual({"tinygltf-2.4.1-mod.tar.gz", "tinygltfloader-0.9.5-mod.tar.gz"}, set(recipe))
        for name, entry in recipe.items():
            path = f"depends/{name}"
            self.assertEqual(upstream_inputs.media_url(self.lock, path), entry["url"])
            self.assertEqual(self.pinned(path), {"sha256": entry["sha256"], "bytes": entry["bytes"]})
            self.assertIn(path, self.lock["sets"]["kernel-native"])

    def test_libkml_matches_the_scene_and_kml_locks(self):
        for recipe in ("kernel-scene/inputs.lock.json", "native-kml/inputs.lock.json"):
            entry = json.loads((HERE / recipe).read_text())["libkml"]
            self.assertEqual(self.pinned("takthirdparty/distfiles/libkml.tar.gz"),
                             {"sha256": entry["sha256"], "bytes": entry["bytes"]}, recipe)

    def test_loresel_matches_the_sdk_runtime_resources_lock(self):
        recipe = json.loads((HERE / "sdk-package/runtime-resources.lock.json").read_text())
        self.assertEqual(self.lock["revision"], recipe["official_revision"])
        payloads = {entry["source"]: entry for entry in recipe["files"] if "lfs_payload" in entry}
        self.assertEqual(set(self.lock["sets"]["sdk-resources"]), set(payloads))
        for path, entry in payloads.items():
            pinned = self.pinned(path)
            self.assertEqual(pinned, entry["lfs_payload"])
            # The recipe records the pointer blob itself: its Git id, size, and sha256.
            text = upstream_inputs.pointer(pinned["sha256"], pinned["bytes"])
            self.assertEqual((entry["blob"], entry["bytes"], entry["sha256"]),
                             (upstream_inputs.pointer_blob(pinned["sha256"], pinned["bytes"]), len(text), sha256(text)))

    def test_workflow_never_smudges_and_fetches_only_through_the_lock(self):
        self.assertRegex(self.workflow, r'(?m)^env:\n(?:  .*\n)*?  GIT_LFS_SKIP_SMUDGE: "1"\n')
        self.assertNotIn("media.githubusercontent.com", self.workflow)
        revisions = set(re.findall(r"atak-civ\.git\n.* fetch .* origin ([0-9a-f]{40})\n", self.workflow))
        self.assertEqual({self.lock["revision"]}, revisions)
        keyed = re.findall(r"upstream_inputs\.py key --set ([a-z0-9-]+)", self.workflow)
        fetched = re.findall(r"upstream_inputs\.py materialize --set ([a-z0-9-]+) \\\n +--store \S+ --source ",
                             self.workflow)
        self.assertEqual((keyed, set(self.lock["sets"])), (fetched, set(keyed)))

    def test_each_fetch_restores_and_saves_its_own_store_under_the_resolved_key(self):
        jobs = [job for job in re.split(r"(?m)^  (?=[a-z0-9-]+:$)", self.workflow)
                if "upstream_inputs.py materialize" in job]
        self.assertEqual(self.workflow.count("upstream_inputs.py materialize"), len(jobs))
        for job in jobs:
            with self.subTest(job=job.split(":", 1)[0]):
                steps = ("\n" + job).split("\n      - ")[1:]
                fetch = [index for index, step in enumerate(steps) if "upstream_inputs.py materialize" in step]
                store = re.findall(r'materialize --set \S+ \\\n +--store "\$\{RUNNER_TEMP\}/([a-z-]+)" --source ', job)
                caches = [(index, dict(re.findall(r"(?m)^ {8}([a-z-]+): (.+)$", "        " + step)),
                           dict(re.findall(r"(?m)^ {10}([a-z-]+): (.+)$", step)))
                          for index, step in enumerate(steps) if "actions/cache" in step]
                self.assertEqual((1, 1, 2), (len(fetch), len(store), len(caches)))
                (before, restore, restored), (after, save, saved) = caches
                self.assertTrue(before < fetch[0] < after)
                # No restore-keys: only the exact revision and file list may hit.
                wanted = {"path": "${{ runner.temp }}/" + store[0], "key": "${{ steps.upstream-lfs.outputs.key }}"}
                self.assertEqual((wanted, wanted), (restored, saved))
                self.assertRegex(restore["uses"], r"^actions/cache/restore@[0-9a-f]{40} # v\S+$")
                self.assertEqual(restore["uses"].replace("/restore@", "/save@"), save["uses"])
                self.assertEqual(("upstream-lfs-cache", "true", "true"),
                                 (restore.get("id"), restore.get("continue-on-error"), save.get("continue-on-error")))
                self.assertEqual("steps.upstream-lfs-cache.outputs.cache-hit != 'true'", save.get("if"))
                self.assertIn("\n        id: upstream-lfs\n", "".join(steps[:before]))


if __name__ == "__main__":
    unittest.main()
