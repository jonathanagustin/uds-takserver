# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Run the chart 1.0.1 config on the packaged NATS version and exercise core pub/sub."""

import importlib.util
import json
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

import yaml


SPEC = importlib.util.spec_from_file_location(
    "nats_roundtrip", Path(__file__).with_name("nats-roundtrip.py")
)
nats = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nats)

ROOT = Path(__file__).resolve().parents[1]
NATS_VALUES = yaml.safe_load((ROOT / "values/common-values.yaml").read_text())["nats"]
SERVER = NATS_VALUES["container"]["image"]
SERVER_IMAGE = f'{SERVER["repository"]}:{SERVER["tag"]}'
SERVER_VERSION = SERVER["tag"].removesuffix("-alpine")
RELOADER = NATS_VALUES["reloader"]["image"]
RELOADER_IMAGE = f'{RELOADER["repository"]}:{RELOADER["tag"]}'


def _docker(*args, check=True):
    return subprocess.run(
        ["docker", *args], check=check, capture_output=True, text=True
    )


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class NatsRoundtripTests(unittest.TestCase):
    def test_chart_config_survives_a_restart(self):
        if shutil.which("docker") is None or _docker("info", check=False).returncode != 0:
            self.skipTest("docker is not available")
        suffix = uuid.uuid4().hex
        name = "nats-roundtrip-test-" + suffix
        reloader = "nats-reloader-test-" + suffix
        port = _free_port()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "nats.conf"
            config.write_text(nats.CHART_CONFIG)
            pid_dir = root / "pid"
            pid_dir.mkdir()
            pid_dir.chmod(0o777)
            _docker(
                "run",
                "-d",
                "--name",
                name,
                "-p",
                "127.0.0.1:%d:4222" % port,
                "-e",
                "SERVER_NAME=nats-0",
                "-v",
                "%s:/etc/nats-config/nats.conf:ro" % config,
                "-v",
                "%s:/var/run/nats" % pid_dir,
                SERVER_IMAGE,
                "--config",
                "/etc/nats-config/nats.conf",
            )
            try:
                self._wait_ready(name)
                info = nats.roundtrip("127.0.0.1", port)
                self.assertIn("server_id", info)
                # Keep loopback monitoring out of any configured HTTP proxy.
                varz = _docker(
                    "exec",
                    name,
                    "wget",
                    "-Y",
                    "off",
                    "-qO-",
                    "http://127.0.0.1:8222/varz",
                ).stdout
                self.assertEqual(json.loads(varz)["version"], SERVER_VERSION)
                _docker("restart", name)
                self._wait_ready(name)
                nats.roundtrip("127.0.0.1", port)
                _docker(
                    "run",
                    "-d",
                    "--name",
                    reloader,
                    "--network",
                    "container:%s" % name,
                    "-v",
                    "%s:/etc/nats-config/nats.conf:ro" % config,
                    "-v",
                    "%s:/var/run/nats" % pid_dir,
                    RELOADER_IMAGE,
                    "-pid",
                    "/var/run/nats/nats.pid",
                    "-config",
                    "/etc/nats-config/nats.conf",
                )
                deadline = time.time() + 15
                logs = ""
                while time.time() < deadline:
                    result = _docker("logs", reloader, check=False)
                    logs = result.stdout + result.stderr
                    if "ready to kick" in logs:
                        break
                    time.sleep(0.5)
                self.assertIn("ready to kick", logs)
                status = _docker("inspect", "-f", "{{.State.Status}}", reloader)
                self.assertEqual(status.stdout.strip(), "running")
            finally:
                _docker("rm", "-f", name, reloader, check=False)

    def _wait_ready(self, name):
        deadline = time.time() + 20
        while time.time() < deadline:
            state = _docker("inspect", "-f", "{{.State.Status}}", name).stdout.strip()
            logs = _docker("logs", name, check=False)
            text = logs.stderr + logs.stdout
            if state != "running":
                self.fail("nats exited: %s" % text)
            if "Server is ready" in text:
                return
            time.sleep(0.3)
        self.fail("nats did not become ready")


if __name__ == "__main__":
    unittest.main()
