# -*- coding: utf-8 -*-
"""Unit tests for layer customProperty namespace helpers."""

import unittest

from layer_property_utils import get_layer_property, namespaced_property_key, set_layer_property


class _LayerStub:
    def __init__(self):
        self._props = {}

    def customProperty(self, key, default=None):
        return self._props.get(key, default)

    def setCustomProperty(self, key, value):
        self._props[key] = value


class LayerPropertyUtilsTest(unittest.TestCase):
    def test_set_writes_new_namespace(self):
        layer = _LayerStub()
        key = set_layer_property(layer, "source_kind", "grid_cells")
        self.assertEqual(key, "geosurvey_studio/source_kind")
        self.assertEqual(layer.customProperty("geosurvey_studio/source_kind"), "grid_cells")
        self.assertIsNone(layer.customProperty("rasterlinker/source_kind"))

    def test_get_reads_new_namespace(self):
        layer = _LayerStub()
        layer.setCustomProperty("geosurvey_studio/storage_mode", "gpkg")
        value = get_layer_property(layer, "storage_mode", default="memory")
        self.assertEqual(value, "gpkg")

    def test_get_fallback_reads_legacy_slash_and_migrates(self):
        layer = _LayerStub()
        layer.setCustomProperty("rasterlinker/source_kind", "trace2d")
        value = get_layer_property(layer, "source_kind", default="")
        self.assertEqual(value, "trace2d")
        self.assertEqual(layer.customProperty("geosurvey_studio/source_kind"), "trace2d")

    def test_get_fallback_reads_legacy_underscore_and_migrates(self):
        layer = _LayerStub()
        layer.setCustomProperty("rasterlinker_vertex_labels", "1")
        value = get_layer_property(layer, "vertex_labels", default="0")
        self.assertEqual(value, "1")
        self.assertEqual(layer.customProperty("geosurvey_studio/vertex_labels"), "1")

    def test_get_returns_default_when_missing(self):
        layer = _LayerStub()
        self.assertEqual(get_layer_property(layer, "trace_layer_id", default=""), "")

    def test_namespaced_property_key(self):
        self.assertEqual(namespaced_property_key("source_kind"), "geosurvey_studio/source_kind")
        self.assertEqual(namespaced_property_key("/source_kind/"), "geosurvey_studio/source_kind")
        self.assertEqual(namespaced_property_key(""), "")


if __name__ == "__main__":
    unittest.main()
