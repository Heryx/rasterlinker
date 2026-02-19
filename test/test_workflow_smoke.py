# -*- coding: utf-8 -*-
"""Smoke tests for RasterLinker end-to-end workflow status checks."""

import json
import os
import tempfile
import unittest


try:
    from qgis.PyQt.QtCore import QSettings
    from qgis.core import QgsApplication, QgsFeature, QgsGeometry, QgsProject, QgsRasterLayer, QgsVectorLayer

    from catalog_tools_mixin import CatalogToolsMixin
    from project_catalog import ensure_project_structure, load_catalog, save_catalog
    from trace_capture_mixin import TraceCaptureMixin
    from trace_tools_mixin import TraceToolsMixin

    HAS_QGIS = True
except Exception:
    HAS_QGIS = False


_QGIS_APP = None


def _ensure_qgis_app():
    global _QGIS_APP
    if not HAS_QGIS:
        return None
    app = QgsApplication.instance()
    if app is None:
        _QGIS_APP = QgsApplication([], False)
        _QGIS_APP.initQgis()
        app = _QGIS_APP
    return app


class _IfaceStub:
    def __init__(self):
        self._active_layer = None

    def activeLayer(self):
        return self._active_layer

    def setActiveLayer(self, layer):
        self._active_layer = layer


if HAS_QGIS:
    class _WorkflowHarness(CatalogToolsMixin, TraceCaptureMixin, TraceToolsMixin):
        def __init__(self, iface, project_root, payload):
            self.iface = iface
            self.settings = QSettings()
            self.settings_group = "RasterLinker"
            self.settings_key_active_project = "RasterLinker/active_project_root"
            self.settings_key_default_import_crs = "RasterLinker/default_import_crs_authid"
            self.settings.setValue(self.settings_key_active_project, project_root)
            self.project_manager_dialog = None
            self.plugin_layer_root_name = "RasterLinker"
            self.trace_line_layer_id = None
            self.trace_connected_layer_ids = set()
            self.trace_z_grid_cache = {}
            self.trace_missing_z_prompt_shown = False
            self.trace_allow_missing_z_for_session = False
            self.dlg = None
            self._payload = payload or {}

        def _active_timeslice_payload(self):
            return self._payload

        def _ui_parent(self):
            return None

        def refresh_trace_info_table(self):
            return None

        def _notify_info(self, _message, duration=0):
            return None


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is not available")
class WorkflowSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._qgis_app = _ensure_qgis_app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project_root = self.tmp.name
        ensure_project_structure(self.project_root)
        QgsProject.instance().removeAllMapLayers()

        fixture_raster = os.path.join(os.path.dirname(__file__), "tenbytenraster.asc")
        if not os.path.exists(fixture_raster):
            self.skipTest("Missing raster fixture: tenbytenraster.asc")
        self.fixture_raster = fixture_raster

        # Minimal catalog state with one time-slice in one group.
        catalog = load_catalog(self.project_root)
        catalog["timeslices"] = [
            {
                "id": "ts_smoke_001",
                "name": "Smoke Slice",
                "normalized_name": "smoke_slice",
                "project_path": self.fixture_raster,
                "crs": "EPSG:4326",
            }
        ]
        catalog["raster_groups"] = [
            {
                "id": "grp_smoke_001",
                "name": "SmokeGroup",
                "radargram_ids": [],
                "timeslice_ids": ["ts_smoke_001"],
            }
        ]
        save_catalog(self.project_root, catalog)

        self.iface = _IfaceStub()
        self.harness = _WorkflowHarness(
            self.iface,
            self.project_root,
            payload={"timeslice_id": "ts_smoke_001", "group_name": "SmokeGroup"},
        )

        # Simulate loaded raster inside RasterLinker group tree.
        raster_layer = QgsRasterLayer(self.fixture_raster, "SmokeRaster")
        if not raster_layer.isValid():
            self.skipTest("Raster fixture is not valid in this QGIS runtime.")
        QgsProject.instance().addMapLayer(raster_layer, False)
        self.harness._get_or_create_plugin_qgis_group("SmokeGroup").addLayer(raster_layer)

        # Simulate a saved 2D trace layer with at least one feature.
        trace_layer = QgsVectorLayer(self.harness._trace_layer_uri("EPSG:4326"), "Trace2D", "memory")
        self.assertTrue(trace_layer.isValid())
        f2d = QgsFeature(trace_layer.fields())
        f2d.setGeometry(QgsGeometry.fromWkt("LINESTRING (0 0, 1 1)"))
        trace_layer.dataProvider().addFeatures([f2d])
        trace_layer.updateExtents()
        QgsProject.instance().addMapLayer(trace_layer)
        self.iface.setActiveLayer(trace_layer)
        self.harness.trace_line_layer_id = trace_layer.id()

        # Simulate a built 3D line layer.
        line3d = QgsVectorLayer(
            "LineStringZ?crs=EPSG:4326&field=trace_id:string(64)&field=z_mode:string(64)&field=z_source:string(32)",
            "Trace3D",
            "memory",
        )
        self.assertTrue(line3d.isValid())
        f3d = QgsFeature(line3d.fields())
        f3d.setAttributes(["tr3d_001", "build3d_constant_depth", "depth_range"])
        f3d.setGeometry(QgsGeometry.fromWkt("LINESTRING Z (0 0 1, 1 1 1)"))
        line3d.dataProvider().addFeatures([f3d])
        line3d.updateExtents()
        QgsProject.instance().addMapLayer(line3d)

        # Simulate an exported file-backed line layer.
        export_geojson = os.path.join(self.project_root, "exports", "trace_export.geojson")
        os.makedirs(os.path.dirname(export_geojson), exist_ok=True)
        with open(export_geojson, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"trace_id": "tr_exp_001"},
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[0.0, 0.0], [2.0, 2.0]],
                            },
                        }
                    ],
                },
                f,
                ensure_ascii=True,
            )
        exported_layer = QgsVectorLayer(export_geojson, "TraceExport", "ogr")
        self.assertTrue(exported_layer.isValid())
        QgsProject.instance().addMapLayer(exported_layer)

    def tearDown(self):
        QgsProject.instance().removeAllMapLayers()
        self.tmp.cleanup()

    def test_workflow_status_all_steps_ok(self):
        status = self.harness._collect_end_to_end_workflow_status()
        self.assertEqual(len(status), 6)
        failing = [step for step in status if not step.get("ok")]
        self.assertEqual(failing, [], f"Workflow smoke check failed: {failing}")


if __name__ == "__main__":
    unittest.main()
