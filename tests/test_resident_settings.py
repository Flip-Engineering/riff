"""Resident lifetime settings retain opt-in defaults and the existing reserve."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import platform_support


class ResidentSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        data=patch.object(platform_support,'DATA',Path(self.temp.name))
        data.start();self.addCleanup(data.stop)

    def test_release_when_empty_defaults_off_and_persists_independently_of_warm_mode(self):
        self.assertFalse(platform_support.settings()['warm_engine_release_when_empty'])
        platform_support.configure({'warm_engine':True,'warm_engine_release_when_empty':True})
        self.assertTrue(platform_support.settings()['warm_engine'])
        self.assertTrue(platform_support.settings()['warm_engine_release_when_empty'])
        platform_support.configure({'threads':2})
        self.assertTrue(platform_support.settings()['warm_engine_release_when_empty'])

    def test_release_setting_requires_a_boolean(self):
        for value in (None,1,'true'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                platform_support.configure({'warm_engine_release_when_empty':value})

    def test_studio_reports_the_existing_environment_reserve_without_a_second_setting(self):
        with patch.dict(os.environ,{'RIFF_MEMORY_RESERVE_MB':'256'}), patch('run.engine_capabilities',return_value={}):
            self.assertEqual(platform_support.readiness()['memory_reserve_mb'],256)
            self.assertNotIn('memory_reserve_mb',platform_support.settings())
