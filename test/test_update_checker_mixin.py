# -*- coding: utf-8 -*-
"""Unit tests for update checker helpers."""

import os
import shutil
import tempfile
import unittest

from update_checker_mixin import UpdateCheckerMixin


class _MemSettings:
    def __init__(self):
        self._store = {}

    def value(self, key, default=None, type=None):
        val = self._store.get(key, default)
        if type is bool:
            return bool(val)
        if type is str:
            return str(val) if val is not None else ""
        return val

    def setValue(self, key, value):
        self._store[key] = value


class _DummyPlugin(UpdateCheckerMixin):
    def __init__(self, plugin_dir):
        self.plugin_dir = plugin_dir
        self.settings = _MemSettings()
        self.settings_group = "GeoSurveyStudio"
        self._metadata_general_cache = None
        self._update_checked_this_session = False

    def _settings_key(self, key):
        return f"{self.settings_group}/{key}"

    def _ui_parent(self):
        return None

    class _Iface:
        class _Bar:
            def pushInfo(self, *_a, **_kw):
                return None

            def pushWarning(self, *_a, **_kw):
                return None

        def messageBar(self):
            return _DummyPlugin._Iface._Bar()

    iface = _Iface()


class UpdateCheckerMixinTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="geosurvey_update_test_")
        self.meta_path = os.path.join(self.tmpdir, "metadata.txt")
        with open(self.meta_path, "w", encoding="utf-8") as f:
            f.write(
                "[general]\n"
                "name=GeoSurvey Studio\n"
                "version=1.0.1\n"
                "update_repository=https://github.com/Heryx/geosurvey-studio.git\n"
                "repository=https://github.com/Heryx/rasterlinker\n"
            )
        self.plugin = _DummyPlugin(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_github_owner_repo_parsing_prefers_update_repository(self):
        owner, repo = self.plugin._github_owner_repo()
        self.assertEqual(owner, "Heryx")
        self.assertEqual(repo, "geosurvey-studio")

    def test_extract_github_owner_repo_supports_short_notation(self):
        owner, repo = self.plugin._extract_github_owner_repo("OrgName/my-plugin")
        self.assertEqual(owner, "OrgName")
        self.assertEqual(repo, "my-plugin")

    def test_version_normalization(self):
        self.assertEqual(self.plugin._normalize_version_text("v1.2.3"), "1.2.3")
        self.assertEqual(self.plugin._normalize_version_text("release-2.0.0"), "2.0.0")
        self.assertEqual(self.plugin._normalize_version_text("no-version"), "")

    def test_version_comparison(self):
        self.assertTrue(self.plugin._is_remote_version_newer("1.0.1", "1.0.2"))
        self.assertTrue(self.plugin._is_remote_version_newer("1.0.1", "v1.1.0"))
        self.assertFalse(self.plugin._is_remote_version_newer("1.0.1", "1.0.1"))
        self.assertFalse(self.plugin._is_remote_version_newer("1.2.0", "1.1.9"))


if __name__ == "__main__":
    unittest.main()
