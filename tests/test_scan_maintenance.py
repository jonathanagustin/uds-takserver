# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
import unittest

s = importlib.util.spec_from_file_location('maintenance', '.github/scripts/scan-maintenance.py')
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


class Tests(unittest.TestCase):
    def test_only_metadata(self):

        self.assertEqual(m.snapshot({'state': 'fixed', 'message': 'private',
            'most_recent_instance': {'ref': 'refs/heads/main', 'message': 'private'}}),
            {'state': 'fixed', 'ref': 'refs/heads/main'})
    def test_refuses_unknown_state(self):

        with self.assertRaises(ValueError):
            m.snapshot({'state': 'unexpected'})

    def test_wrong_context(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                m.main()
