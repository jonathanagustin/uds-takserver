# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""The upgrade script names its failing line without copying the command."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPGRADE = ROOT / "tests" / "upgrade.sh"


class UpgradeFailureLineTests(unittest.TestCase):
    def test_err_trap_reports_the_line_and_not_the_command(self):
        text = UPGRADE.read_text()
        self.assertIn('msg="upgrade.sh exited ${rc} at line ${line}"', text)
        self.assertIn("::error title=Upgrade script failed::", text)
        self.assertIn('trap \'upgrade_failed "$?" "${LINENO}"\' ERR', text)
        self.assertNotIn("BASH_COMMAND", text)
        trap = next(line for line in text.splitlines() if line.startswith("trap "))
        self.assertLess(text.splitlines().index(trap), 25)


class BaselinePullFailureTests(unittest.TestCase):
    """Exercise the real pull boundary without installing or contacting UDS."""

    def run_pull(self, status, summary):
        import os
        import subprocess

        source = UPGRADE.read_text()
        block = source[source.index('pull_status=0\n'):source.index('\nreleased="')]
        script = '''set -euo pipefail
package='oci://ghcr.io/jonathanagustin/uds-takserver/takserver:0.16.7-upstream'
arch=amd64
work=/unused
uds() { return "$TEST_PULL_STATUS"; }
''' + block + '\nprintf "continued\\n"\n'
        return subprocess.run(
            ['bash', '-c', script], capture_output=True, text=True,
            env={**os.environ, 'TEST_PULL_STATUS': str(status),
                 'GITHUB_STEP_SUMMARY': str(summary) if summary else ''},
            check=False,
        )

    def test_failure_retains_status_and_does_not_continue(self):
        result = self.run_pull(42, None)
        self.assertEqual(result.returncode, 42)
        self.assertNotIn('continued', result.stdout)
        self.assertIn('remain unclassified', result.stdout)
        self.assertIn('Preserve the existing release', result.stdout)
        self.assertNotIn('Publish this release', result.stdout)

    def test_failure_retains_bounded_metadata_in_summary(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / 'summary.md'
            result = self.run_pull(7, summary)
            self.assertEqual(result.returncode, 7)
            text = summary.read_text()
        self.assertIn('architecture=amd64 exit=7', text)
        self.assertRegex(text, r'observed_utc=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
        self.assertLess(len(text), 300)
        self.assertNotIn('oci://', text)

    def test_success_continues_without_failure_summary(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / 'summary.md'
            result = self.run_pull(0, summary)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, 'continued\n')
            self.assertFalse(summary.exists())
