import os
import re

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QFileDialog, QInputDialog, QMessageBox
from qgis.core import (
    QgsContrastEnhancement,
    QgsCoordinateTransform,
    Qgis,
    QgsLayoutExporter,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsPrintLayout,
    QgsProject,
    QgsRasterBandStats,
    QgsRasterLayer,
    QgsMessageLog,
    QgsRectangle,
    QgsUnitTypes,
)

from .group_import_dialog import GroupImportDialog
from .project_catalog import load_catalog, update_raster_group


class CatalogToolsMixin:
    def _active_project_root(self):
        if self.project_manager_dialog is not None and self.project_manager_dialog.project_root:
            return self.project_manager_dialog.project_root
        if self.project_manager_dialog is not None:
            candidate = self.project_manager_dialog.path_edit.text().strip()
            if candidate:
                return candidate
        stored = self.settings.value(self.settings_key_active_project, "", type=str)
        if stored:
            return stored
        return ""

    def _get_preferred_import_crs(self):
        authid = (self.settings.value(self.settings_key_default_import_crs, "", type=str) or "").strip()
        if authid:
            try:
                from qgis.core import QgsCoordinateReferenceSystem
                crs = QgsCoordinateReferenceSystem(authid)
                if crs.isValid():
                    return crs
            except Exception:
                pass
        return QgsProject.instance().crs()

    def _require_project_root(self, notify=True):
        project_root = self._active_project_root()
        if not project_root:
            if notify:
                QMessageBox.warning(
                    self.dlg,
                    "Project Required",
                    "Open RasterLinker Project Manager and create/open a project first.",
                )
            return None
        return project_root

    def _get_plugin_root_group(self):
        root = QgsProject.instance().layerTreeRoot()
        group = next(
            (
                g for g in root.children()
                if isinstance(g, QgsLayerTreeGroup) and g.name() == self.plugin_layer_root_name
            ),
            None,
        )
        if group is None:
            group = root.addGroup(self.plugin_layer_root_name)
        return group

    def _get_or_create_plugin_qgis_group(self, group_name):
        plugin_root = self._get_plugin_root_group()
        group = next(
            (
                g for g in plugin_root.children()
                if isinstance(g, QgsLayerTreeGroup) and g.name() == group_name
            ),
            None,
        )
        if group is None:
            group = plugin_root.addGroup(group_name)
        return group

    def _find_plugin_root_group(self):
        root = QgsProject.instance().layerTreeRoot()
        return next(
            (
                g for g in root.children()
                if isinstance(g, QgsLayerTreeGroup) and g.name() == self.plugin_layer_root_name
            ),
            None,
        )

    def _remove_plugin_qgis_group(self, group_name):
        plugin_root = self._find_plugin_root_group()
        if plugin_root is None:
            return
        target = next(
            (
                g for g in plugin_root.children()
                if isinstance(g, QgsLayerTreeGroup) and g.name() == group_name
            ),
            None,
        )
        if target is not None:
            plugin_root.removeChildNode(target)

    def _iter_plugin_raster_layers(self):
        plugin_root = self._find_plugin_root_group()
        if plugin_root is None:
            return

        for group in plugin_root.children():
            if not isinstance(group, QgsLayerTreeGroup):
                continue
            for child in group.children():
                if not isinstance(child, QgsLayerTreeLayer):
                    continue
                layer = child.layer()
                if isinstance(layer, QgsRasterLayer):
                    yield layer

    def _apply_minmax_to_layer(self, layer, return_reason=False):
        def _result(ok, reason):
            if return_reason:
                return bool(ok), str(reason or "")
            return bool(ok)

        if layer is None:
            return _result(False, "Layer is None.")

        direct_error = ""
        # Try direct layer API first (best compatibility when available).
        if hasattr(layer, "setContrastEnhancement"):
            try:
                layer.setContrastEnhancement(
                    QgsContrastEnhancement.StretchToMinimumMaximum,
                )
                layer.triggerRepaint()
                return _result(True, "Applied via layer.setContrastEnhancement().")
            except Exception as e1:
                direct_error = str(e1)
                try:
                    from qgis.core import QgsRasterMinMaxOrigin
                    layer.setContrastEnhancement(
                        QgsContrastEnhancement.StretchToMinimumMaximum,
                        QgsRasterMinMaxOrigin.MinMax,
                    )
                    layer.triggerRepaint()
                    return _result(True, "Applied via setContrastEnhancement(..., MinMax).")
                except Exception as e2:
                    direct_error = f"{direct_error} | fallback MinMax failed: {e2}"

        # Fallback: set renderer min/max based on band statistics.
        provider = layer.dataProvider()
        if provider is None:
            return _result(False, f"No data provider. Direct API error: {direct_error}")

        renderer = layer.renderer()
        if renderer is None:
            return _result(False, f"No renderer. Direct API error: {direct_error}")

        def _band_minmax(band_idx):
            try:
                stats = provider.bandStatistics(int(band_idx), QgsRasterBandStats.Min | QgsRasterBandStats.Max)
                mn = float(stats.minimumValue)
                mx = float(stats.maximumValue)
                if mx <= mn:
                    return None
                return mn, mx
            except Exception:
                return None

        applied = False

        first_band_range = _band_minmax(1)
        try:
            if first_band_range is not None:
                minimum, maximum = first_band_range
                if hasattr(renderer, "setClassificationMin"):
                    renderer.setClassificationMin(float(minimum))
                    applied = True
                if hasattr(renderer, "setClassificationMax"):
                    renderer.setClassificationMax(float(maximum))
                    applied = True

            if hasattr(renderer, "contrastEnhancement"):
                ce = renderer.contrastEnhancement()
                if ce is not None and first_band_range is not None:
                    minimum, maximum = first_band_range
                    ce.setMinimumValue(float(minimum))
                    ce.setMaximumValue(float(maximum))
                    ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum, True)
                    applied = True

            rgb_defs = (
                ("redBand", "redContrastEnhancement", "setRedContrastEnhancement"),
                ("greenBand", "greenContrastEnhancement", "setGreenContrastEnhancement"),
                ("blueBand", "blueContrastEnhancement", "setBlueContrastEnhancement"),
            )
            for band_getter_name, ce_getter_name, ce_setter_name in rgb_defs:
                if not hasattr(renderer, band_getter_name) or not hasattr(renderer, ce_getter_name):
                    continue
                try:
                    band_idx = int(getattr(renderer, band_getter_name)())
                except Exception:
                    continue
                if band_idx <= 0:
                    continue
                rng = _band_minmax(band_idx)
                if rng is None:
                    continue
                ce = getattr(renderer, ce_getter_name)()
                if ce is None:
                    continue
                mn, mx = rng
                ce.setMinimumValue(float(mn))
                ce.setMaximumValue(float(mx))
                ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum, True)
                setter = getattr(renderer, ce_setter_name, None)
                if callable(setter):
                    setter(ce)
                applied = True
        except Exception as e:
            return _result(False, f"Renderer enhancement error: {e}")

        if applied:
            layer.triggerRepaint()
            return _result(True, "Applied via renderer contrast enhancement.")
        reason = "No supported renderer enhancement path."
        if first_band_range is None:
            reason = "Unable to compute valid min/max statistics (band 1)."
        if direct_error:
            reason = f"{reason} Direct API error: {direct_error}"
        return _result(False, reason)

    def _catalog_groups_by_name(self, project_root):
        catalog = load_catalog(project_root)
        groups = catalog.get("raster_groups", [])
        return {g.get("name"): g for g in groups if g.get("name")}

    def _visible_plugin_group_names(self):
        plugin_root = self._find_plugin_root_group()
        if plugin_root is None:
            return []
        return [g.name() for g in plugin_root.children() if isinstance(g, QgsLayerTreeGroup)]

    def _apply_group_visibility_selection(self, group_names):
        project_root = self._require_project_root()
        if not project_root:
            return
        by_name = self._catalog_groups_by_name(project_root)
        selected = [name for name in group_names if name in by_name]

        for existing in self._visible_plugin_group_names():
            if existing not in selected:
                self._remove_plugin_qgis_group(existing)

        for name in selected:
            self._get_or_create_plugin_qgis_group(name)

        self.populate_group_list()
        if self.dlg.groupListWidget.count() > 0:
            self.dlg.groupListWidget.setCurrentRow(0)
        self.load_raster(show_message=False)

    def open_group_import_dialog(self):
        project_root = self._require_project_root()
        if not project_root:
            return
        by_name = self._catalog_groups_by_name(project_root)
        groups = [g for g in by_name.values() if g.get("timeslice_ids")]
        if not groups:
            QMessageBox.information(self.dlg, "Import Groups", "No groups with images found in this project.")
            return

        dlg = GroupImportDialog(groups, self._visible_plugin_group_names(), self.dlg)
        if dlg.exec_() != dlg.Accepted:
            return
        self._apply_group_visibility_selection(dlg.selected_group_names())

    def enhance_loaded_images_minmax(self):
        total = 0
        enhanced = 0
        failed = []
        for layer in self._iter_plugin_raster_layers() or []:
            total += 1
            ok, reason = self._apply_minmax_to_layer(layer, return_reason=True)
            if ok:
                enhanced += 1
            else:
                layer_name = layer.name() if layer is not None else "Unknown layer"
                detail = f"{layer_name}: {reason or 'unknown reason'}"
                failed.append(detail)
                QgsMessageLog.logMessage(detail, "RasterLinker", level=Qgis.Warning)

        if total == 0:
            QMessageBox.information(
                self.dlg,
                "Enhance Min/Max",
                "No loaded images found in RasterLinker groups.",
            )
            return

        self.iface.messageBar().pushInfo(
            "RasterLinker",
            f"Enhance Min/Max applied: {enhanced}/{total} layers.",
        )
        if failed:
            self.iface.messageBar().pushWarning(
                "RasterLinker",
                f"Enhance Min/Max skipped {len(failed)} layer(s). See Log Messages for details.",
            )

    def _iter_group_raster_layers(self, group_name):
        group = self._get_or_create_plugin_qgis_group(group_name)
        for child in group.children():
            if isinstance(child, QgsLayerTreeLayer) and isinstance(child.layer(), QgsRasterLayer):
                yield child.layer()

    def _selected_group_names(self):
        if self.dlg is None:
            return []
        return [it.text().strip() for it in self.dlg.groupListWidget.selectedItems() if it.text().strip()]

    def _apply_value_range_to_layer(self, layer, minimum, maximum):
        if minimum is None or maximum is None:
            return False
        if float(maximum) <= float(minimum):
            return False

        renderer = layer.renderer()
        if renderer is None:
            return False
        applied = False
        try:
            if hasattr(renderer, "setClassificationMin"):
                renderer.setClassificationMin(float(minimum))
                applied = True
            if hasattr(renderer, "setClassificationMax"):
                renderer.setClassificationMax(float(maximum))
                applied = True
            if hasattr(renderer, "contrastEnhancement"):
                ce = renderer.contrastEnhancement()
                if ce is not None:
                    ce.setMinimumValue(float(minimum))
                    ce.setMaximumValue(float(maximum))
                    ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum, True)
                    applied = True
            if applied:
                layer.triggerRepaint()
            return applied
        except Exception:
            return False

    def _range_contains_zero(self, rng):
        try:
            min_attr = getattr(rng, "min", None)
            if callable(min_attr):
                mn = min_attr()
            else:
                min_attr = getattr(rng, "minimumValue", None)
                mn = min_attr() if callable(min_attr) else min_attr

            max_attr = getattr(rng, "max", None)
            if callable(max_attr):
                mx = max_attr()
            else:
                max_attr = getattr(rng, "maximumValue", None)
                mx = max_attr() if callable(max_attr) else max_attr

            mn = float(mn)
            mx = float(mx)
            return mn <= 0.0 <= mx
        except Exception:
            return False

    def _disable_zero_nodata_on_layer(self, layer):
        provider = layer.dataProvider()
        if provider is None:
            return False

        changed = False
        band_count = 0
        try:
            band_count = int(provider.bandCount())
        except Exception:
            band_count = 0

        for band in range(1, band_count + 1):
            try:
                if hasattr(provider, "userNoDataValues") and hasattr(provider, "setUserNoDataValue"):
                    ranges = list(provider.userNoDataValues(band) or [])
                    filtered = [r for r in ranges if not self._range_contains_zero(r)]
                    if len(filtered) != len(ranges):
                        provider.setUserNoDataValue(band, filtered)
                        changed = True
            except Exception:
                pass

            try:
                if hasattr(provider, "sourceNoDataValue") and hasattr(provider, "setUseSourceNoDataValue"):
                    src_no_data = provider.sourceNoDataValue(band)
                    if src_no_data is not None and abs(float(src_no_data)) < 1e-12:
                        provider.setUseSourceNoDataValue(band, False)
                        changed = True
            except Exception:
                pass

        if changed:
            try:
                layer.triggerRepaint()
            except Exception:
                pass
        return changed

    def enhance_batch_options(self):
        options = ["No enhancement (NoData only)", "Min/Max", "Percent Clip (2%)", "StdDev (2 sigma)"]
        mode, ok = QInputDialog.getItem(
            self.dlg,
            "Enhance Batch",
            "Enhancement mode:",
            options,
            0,
            False,
        )
        if not ok:
            return

        nodata_options = ["Keep current NoData", "Disable NoData=0"]
        nodata_mode, nodata_ok = QInputDialog.getItem(
            self.dlg,
            "Enhance Batch",
            "NoData handling:",
            nodata_options,
            0,
            False,
        )
        if not nodata_ok:
            return
        disable_zero_nodata = nodata_mode == "Disable NoData=0"

        selected_groups = self._selected_group_names()
        layers = []
        if selected_groups:
            for name in selected_groups:
                layers.extend(list(self._iter_group_raster_layers(name)))
        else:
            layers = list(self._iter_plugin_raster_layers() or [])

        if not layers:
            QMessageBox.information(self.dlg, "Enhance Batch", "No loaded raster layers found.")
            return

        enhanced = 0
        nodata_updated = 0
        for layer in layers:
            provider = layer.dataProvider()
            if provider is None:
                continue
            if mode != "No enhancement (NoData only)":
                try:
                    if mode == "StdDev (2 sigma)":
                        stats = provider.bandStatistics(
                            1,
                            QgsRasterBandStats.Mean | QgsRasterBandStats.StdDev,
                        )
                        mn = float(stats.mean) - 2.0 * float(stats.stdDev)
                        mx = float(stats.mean) + 2.0 * float(stats.stdDev)
                    else:
                        stats = provider.bandStatistics(
                            1,
                            QgsRasterBandStats.Min | QgsRasterBandStats.Max,
                        )
                        mn = float(stats.minimumValue)
                        mx = float(stats.maximumValue)
                        if mode == "Percent Clip (2%)":
                            span = mx - mn
                            mn = mn + 0.02 * span
                            mx = mx - 0.02 * span
                    if self._apply_value_range_to_layer(layer, mn, mx):
                        enhanced += 1
                except Exception:
                    pass

            if disable_zero_nodata:
                try:
                    if self._disable_zero_nodata_on_layer(layer):
                        nodata_updated += 1
                except Exception:
                    pass

        msg = f"Enhance Batch ({mode}) applied: {enhanced}/{len(layers)} layers."
        if disable_zero_nodata:
            msg += f" NoData=0 disabled: {nodata_updated}/{len(layers)} layers."
        self.iface.messageBar().pushInfo("RasterLinker", msg)

    def _active_group_item(self):
        if self.dlg is None:
            return None
        return self.dlg.groupListWidget.currentItem()

    def _active_group_record(self):
        project_root = self._require_project_root()
        if not project_root:
            return None, None
        item = self._active_group_item()
        if item is None:
            return project_root, None
        group_id = item.data(Qt.UserRole)
        catalog = load_catalog(project_root)
        rec = next((g for g in catalog.get("raster_groups", []) if g.get("id") == group_id), None)
        return project_root, rec

    def save_selected_group_style(self):
        project_root, group = self._active_group_record()
        if not project_root or group is None:
            QMessageBox.warning(self.dlg, "Save Group Style", "Select one active group first.")
            return
        group_name = group.get("name", "Group")
        layers = list(self._iter_group_raster_layers(group_name))
        if not layers:
            QMessageBox.warning(self.dlg, "Save Group Style", "No loaded layers found for the selected group.")
            return
        style_dir = os.path.join(project_root, "metadata", "group_styles")
        os.makedirs(style_dir, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_\-]+", "_", group_name).strip("_") or "group"
        style_path = os.path.join(style_dir, f"{safe_name}.qml")
        ok_msg = layers[0].saveNamedStyle(style_path)
        if isinstance(ok_msg, tuple):
            ok = bool(ok_msg[0])
        else:
            ok = bool(ok_msg)
        if not ok:
            QMessageBox.warning(self.dlg, "Save Group Style", "Unable to save style file.")
            return
        update_raster_group(project_root, group.get("id"), {"style_qml_path": style_path})
        self.iface.messageBar().pushInfo("RasterLinker", f"Group style saved: {style_path}")

    def load_selected_group_style(self):
        project_root, group = self._active_group_record()
        if not project_root or group is None:
            QMessageBox.warning(self.dlg, "Load Group Style", "Select one active group first.")
            return
        style_path = (group.get("style_qml_path") or "").strip()
        if not style_path or not os.path.exists(style_path):
            QMessageBox.warning(self.dlg, "Load Group Style", "No saved style found for this group.")
            return
        group_name = group.get("name", "Group")
        layers = list(self._iter_group_raster_layers(group_name))
        if not layers:
            QMessageBox.warning(self.dlg, "Load Group Style", "No loaded layers found for the selected group.")
            return
        applied = 0
        for lyr in layers:
            try:
                result = lyr.loadNamedStyle(style_path)
                ok = bool(result[0]) if isinstance(result, tuple) else bool(result)
                if ok:
                    lyr.triggerRepaint()
                    applied += 1
            except Exception:
                continue
        self.iface.messageBar().pushInfo("RasterLinker", f"Group style loaded: {applied}/{len(layers)} layers.")

    def export_group_layout_quick(self):
        project_root, group = self._active_group_record()
        if not project_root or group is None:
            QMessageBox.warning(self.dlg, "Export Group Layout", "Select one active group first.")
            return
        group_name = group.get("name", "Group")
        layers = list(self._iter_group_raster_layers(group_name))
        if not layers:
            QMessageBox.warning(self.dlg, "Export Group Layout", "No loaded layers for the selected group.")
            return
        out_dir = QFileDialog.getExistingDirectory(self.dlg, "Select output folder for PDF export")
        if not out_dir:
            return

        project = QgsProject.instance()
        layout_manager = project.layoutManager()
        layout_name = "_RasterLinker_QuickExport"
        old = layout_manager.layoutByName(layout_name)
        if old is not None:
            layout_manager.removeLayout(old)

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName(layout_name)
        layout_manager.addLayout(layout)

        map_item = QgsLayoutItemMap(layout)
        map_item.attemptMove(QgsLayoutPoint(10, 20, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(277, 170, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(map_item)

        label_item = QgsLayoutItemLabel(layout)
        label_item.attemptMove(QgsLayoutPoint(10, 8, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(label_item)

        exported = 0
        for lyr in layers:
            try:
                map_item.setLayers([lyr])
                map_item.zoomToExtent(lyr.extent())
                label_item.setText(f"{group_name} - {lyr.name()}")
                label_item.adjustSizeToText()
                safe = re.sub(r"[^A-Za-z0-9_\-]+", "_", lyr.name()).strip("_") or "layer"
                pdf_path = os.path.join(out_dir, f"{group_name}_{safe}.pdf")
                exporter = QgsLayoutExporter(layout)
                result = exporter.exportToPdf(pdf_path, QgsLayoutExporter.PdfExportSettings())
                if result == QgsLayoutExporter.Success:
                    exported += 1
            except Exception:
                continue

        layout_manager.removeLayout(layout)
        self.iface.messageBar().pushInfo(
            "RasterLinker",
            f"Quick layout export completed: {exported}/{len(layers)} PDFs.",
        )

