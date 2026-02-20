# -*- coding: utf-8 -*-
"""Tests for group visibility sync and polygon draw/grid mode behavior."""

import tempfile
import unittest


try:
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import QCheckBox, QLineEdit, QListWidget, QListWidgetItem
    from qgis.gui import QgsMapCanvas
    from qgis.core import (
        QgsApplication,
        QgsFeature,
        QgsGeometry,
        QgsLayerTreeGroup,
        QgsLayerTreeLayer,
        QgsProject,
        QgsVectorLayer,
    )

    from catalog_group_mixin import CatalogGroupMixin
    from catalog_tools_mixin import CatalogToolsMixin
    from grid_workflow_mixin import GridWorkflowMixin
    from polygon_draw_tool import PolygonDrawTool

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


class _MessageBarStub:
    def pushMessage(self, *_args, **_kwargs):
        return None


class _IfaceStub:
    def __init__(self):
        self._canvas = QgsMapCanvas() if HAS_QGIS else None
        self._msg = _MessageBarStub()

    def mapCanvas(self):
        return self._canvas

    def messageBar(self):
        return self._msg


if HAS_QGIS:
    class _DialogStub:
        def __init__(self):
            self.groupListWidget = QListWidget()
            self.groupListWidget.setSelectionMode(QListWidget.ExtendedSelection)
            self.rasterListWidget = QListWidget()
            self.lineEditDistanceX = QLineEdit("1")
            self.lineEditDistanceY = QLineEdit("1")
            self.lineEditAreaNames = QLineEdit("AreaA|CellA")

    class _GroupVisibilityHarness(CatalogToolsMixin, CatalogGroupMixin):
        def __init__(self, iface):
            self.iface = iface
            self.plugin_layer_root_name = "GeoSurvey Studio"
            self.dlg = _DialogStub()

        def _save_ui_settings(self):
            return None

        def _build_name_lines_for_selected_groups(self):
            return []

        def _render_name_raster_lines(self, _lines):
            return None

        def load_raster(self, show_message=True):
            return None

        def _update_navigation_controls(self, value=None):
            return None

    class _GridHarness(GridWorkflowMixin, CatalogToolsMixin):
        def __init__(self, iface):
            self.iface = iface
            self.plugin_layer_root_name = "GeoSurvey Studio"
            self.pending_vector_storage_mode = "memory"
            self.keep_source_polygon = False
            self.internal_grid_checkbox = QCheckBox()
            self.internal_grid_checkbox.setChecked(False)
            self.dlg = _DialogStub()
            self.last_area_layer = None
            self.last_grid_layer = None
            self._info_messages = []

        def _ui_parent(self):
            return None

        def _notify_info(self, message, duration=0):
            self._info_messages.append((str(message), int(duration)))

        def _confirm_planar_units_for_grid(self):
            return True

    class _PolygonPluginStub:
        def __init__(self, iface):
            self.iface = iface
            self.grid_use_snap = False
            self.grid_force_orthogonal = False
            self.grid_relative_orthogonal = False
            self.grid_dimension_mode = "canvas"

        def update_draw_indicators(self, _angle=None, _length=None):
            return None

        def update_base_angle_indicator(self, _angle=None):
            return None


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is not available")
class GroupVisibilityAndGridModesTest(unittest.TestCase):
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

    def _group_visible(self, group):
        try:
            return bool(group.itemVisibilityChecked())
        except Exception:
            try:
                return bool(group.isVisible())
            except Exception:
                return False

    def test_group_visibility_single_and_multi_selection(self):
        harness = _GroupVisibilityHarness(self.iface)
        g1 = harness._get_or_create_plugin_qgis_group("G1")
        g2 = harness._get_or_create_plugin_qgis_group("G2")
        item1 = QListWidgetItem("G1")
        item2 = QListWidgetItem("G2")
        harness.dlg.groupListWidget.addItem(item1)
        harness.dlg.groupListWidget.addItem(item2)

        item2.setSelected(True)
        harness._sync_qgis_group_visibility_with_selection()
        self.assertFalse(self._group_visible(g1))
        self.assertTrue(self._group_visible(g2))

        item1.setSelected(True)
        harness._sync_qgis_group_visibility_with_selection()
        self.assertTrue(self._group_visible(g1))
        self.assertTrue(self._group_visible(g2))

    def test_internal_grid_off_creates_only_area_in_cell_grids_group(self):
        harness = _GridHarness(self.iface)

        area_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "Drawn Polygon", "memory")
        self.assertTrue(area_layer.isValid())
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromWkt("POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))"))
        area_layer.dataProvider().addFeatures([feat])
        area_layer.updateExtents()
        QgsProject.instance().addMapLayer(area_layer)

        harness.create_grid_from_drawn_polygon(area_layer)

        self.assertIsNotNone(harness.last_area_layer)
        self.assertIsNone(harness.last_grid_layer)
        self.assertIsNotNone(QgsProject.instance().mapLayer(harness.last_area_layer.id()))

        cell_group = harness._get_or_create_cell_grids_group()
        self.assertIsInstance(cell_group, QgsLayerTreeGroup)
        layer_ids = []
        for child in cell_group.children():
            if isinstance(child, QgsLayerTreeLayer) and child.layer() is not None:
                layer_ids.append(child.layer().id())
        self.assertIn(harness.last_area_layer.id(), layer_ids)

    def test_polygon_canvas_mode_keeps_free_form_click_flow(self):
        plugin = _PolygonPluginStub(self.iface)
        tool = PolygonDrawTool(self.iface.mapCanvas(), plugin)

        click_points = [
            QgsGeometry.fromWkt("POINT (0 0)").asPoint(),
            QgsGeometry.fromWkt("POINT (10 0)").asPoint(),
            QgsGeometry.fromWkt("POINT (10 5)").asPoint(),
        ]
        point_iter = iter(click_points)
        tool._map_point_with_snap = lambda _event: (next(point_iter), False)
        tool._update_snap_marker = lambda _point: None
        tool._add_vertex_marker = lambda _point: None
        tool._update_preview = lambda: None

        class _Evt:
            def button(self):
                return Qt.LeftButton

            def modifiers(self):
                return Qt.NoModifier

            def pos(self):
                return None

        ev = _Evt()
        tool.canvasReleaseEvent(ev)
        tool.canvasReleaseEvent(ev)
        tool.canvasReleaseEvent(ev)

        self.assertEqual(len(tool.points), 3)
        self.assertIsNone(tool.dimension_pick_mode)


if __name__ == "__main__":
    unittest.main()
