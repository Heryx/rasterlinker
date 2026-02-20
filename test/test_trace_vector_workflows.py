# -*- coding: utf-8 -*-
"""Workflow tests for vector storage/persistence and draw toggle behavior."""

import tempfile
import unittest


try:
    from qgis.PyQt.QtCore import QSettings
    from qgis.core import QgsApplication, QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer

    from catalog_tools_mixin import CatalogToolsMixin
    from grid_workflow_mixin import GridWorkflowMixin
    from trace_build3d_mixin import TraceBuild3DMixin
    from trace_capture_mixin import TraceCaptureMixin

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


class _ActionStub:
    def __init__(self):
        self.checked = False
        self.block_calls = []

    def isCheckable(self):
        return True

    def blockSignals(self, block):
        self.block_calls.append(bool(block))
        return False

    def setChecked(self, checked):
        self.checked = bool(checked)


if HAS_QGIS:
    class _DrawHarness(TraceCaptureMixin):
        def __init__(self, iface):
            self.iface = iface
            self.settings = QSettings()
            self.trace_toolbar_actions = {"Draw 2D Line": _ActionStub()}
            self.trace_line_layer_id = None
            self.trace_connected_layer_ids = set()
            self.trace_z_grid_cache = {}
            self.trace_capture_context = {}
            self.trace_missing_z_prompt_shown = True
            self.trace_allow_missing_z_for_session = True
            self.dlg = None

        def _ui_parent(self):
            return None

        def _notify_info(self, _message, duration=0):
            return None

        def refresh_trace_info_table(self):
            return None

        def _active_timeslice_record(self):
            return None, None

        def _trigger_iface_action(self, *_action_getters):
            return True

    class _GridHarness(GridWorkflowMixin):
        def __init__(self):
            self.iface = _IfaceStub()
            self.pending_vector_storage_mode = "gpkg"
            self.persist_calls = []
            self._persisted = QgsVectorLayer("Polygon?crs=EPSG:4326", "grid_cells_saved", "memory")
            self._last_info = ""

        def _ui_parent(self):
            return None

        def _notify_info(self, message, duration=0):
            self._last_info = f"{message} ({duration})"

        def _persist_vector_layer_to_project_gpkg(self, layer, layer_name, source_kind="generic"):
            self.persist_calls.append(
                {
                    "layer_id": layer.id(),
                    "layer_name": layer_name,
                    "source_kind": source_kind,
                }
            )
            return self._persisted, "C:/tmp/grid_cells_saved.gpkg", ""

    class _Build3DHarness(CatalogToolsMixin, TraceCaptureMixin, TraceBuild3DMixin):
        def __init__(self, iface):
            self.iface = iface
            self.settings = QSettings()
            self.plugin_layer_root_name = "GeoSurvey Studio"
            self.project_manager_dialog = None
            self.settings_key_active_project = "GeoSurveyStudio/active_project_root"
            self.settings_key_default_import_crs = "GeoSurveyStudio/default_import_crs_authid"
            self.trace_line_layer_id = None
            self.trace_connected_layer_ids = set()
            self.trace_z_grid_cache = {}
            self.dlg = None
            self.persist_calls = []

        def _ui_parent(self):
            return None

        def _trace_vector_storage_mode(self):
            return "gpkg"

        def _persist_vector_layer_to_project_gpkg(self, layer, layer_name, source_kind="generic"):
            self.persist_calls.append(
                {
                    "layer_name": layer_name,
                    "source_kind": source_kind,
                }
            )
            persisted = QgsVectorLayer("LineStringZ?crs=EPSG:4326", f"{layer_name}_saved", "memory")
            return persisted, "C:/tmp/trace3d_saved.gpkg", ""


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is not available")
class TraceVectorWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._qgis_app = _ensure_qgis_app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        QgsProject.instance().removeAllMapLayers()
        self.iface = _IfaceStub()

    def tearDown(self):
        QgsProject.instance().removeAllMapLayers()
        self.tmp.cleanup()

    def _new_trace_layer(self, harness, name="Trace2D"):
        layer = QgsVectorLayer(harness._trace_layer_uri("EPSG:4326"), name, "memory")
        self.assertTrue(layer.isValid())
        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry.fromWkt("LINESTRING (0 0, 1 1)"))
        layer.dataProvider().addFeatures([feat])
        layer.updateExtents()
        QgsProject.instance().addMapLayer(layer)
        return layer

    def test_trace_draw_pencil_toggle_on_off(self):
        harness = _DrawHarness(self.iface)
        layer = self._new_trace_layer(harness)
        self.iface.setActiveLayer(layer)
        harness.trace_line_layer_id = layer.id()

        harness.start_trace_capture(True)
        self.assertTrue(layer.isEditable(), "Expected editing ON after Draw 2D toggle ON.")
        self.assertTrue(
            harness.trace_toolbar_actions["Draw 2D Line"].checked,
            "Draw 2D action should be checked in editing mode.",
        )

        harness.start_trace_capture(False)
        self.assertFalse(layer.isEditable(), "Expected editing OFF after Draw 2D toggle OFF.")
        self.assertFalse(
            harness.trace_toolbar_actions["Draw 2D Line"].checked,
            "Draw 2D action should be unchecked when editing is stopped.",
        )

    def test_grid_gpkg_persistence_calls_persist_with_source_kind(self):
        harness = _GridHarness()
        source = QgsVectorLayer("Polygon?crs=EPSG:4326", "grid_cells_tmp", "memory")
        self.assertTrue(source.isValid())
        QgsProject.instance().addMapLayer(source)

        persisted = harness._persist_project_vector_layer_if_needed(
            source,
            storage_mode="gpkg",
            source_kind="grid_cells",
        )
        self.assertIsNotNone(persisted)
        self.assertEqual(len(harness.persist_calls), 1)
        self.assertEqual(harness.persist_calls[0]["source_kind"], "grid_cells")
        self.assertIsNone(QgsProject.instance().mapLayer(source.id()))
        self.assertIsNotNone(QgsProject.instance().mapLayer(persisted.id()))

    def test_build3d_gpkg_persistence_passes_trace3d_source_kind(self):
        harness = _Build3DHarness(self.iface)
        source_layer = QgsVectorLayer("LineString?crs=EPSG:4326", "Trace2D_Source", "memory")
        self.assertTrue(source_layer.isValid())
        QgsProject.instance().addMapLayer(source_layer)

        out = harness._create_3d_output_layer(source_layer, "Trace3D_Out")
        self.assertIsNotNone(out)
        self.assertEqual(len(harness.persist_calls), 1)
        self.assertEqual(harness.persist_calls[0]["source_kind"], "trace3d")
        self.assertIsNotNone(QgsProject.instance().mapLayer(out.id()))


if __name__ == "__main__":
    unittest.main()
