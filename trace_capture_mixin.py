# -*- coding: utf-8 -*-
"""Trace capture/core mixin for RasterLinker plugin."""

import os.path
from functools import partial

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMessageBox, QInputDialog
from qgis.core import (
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsFeature,
    QgsGeometry,
)
from PyQt5.QtWidgets import QCheckBox

from .project_catalog import load_catalog, utc_now_iso
from .trace_labeling_mixin import TraceLabelingMixin
from .trace_storage_mixin import TraceStorageMixin


class TraceCaptureMixin(TraceStorageMixin, TraceLabelingMixin):

    def _is_line_layer(self, layer):
        return (
            isinstance(layer, QgsVectorLayer)
            and layer.isValid()
            and layer.geometryType() == QgsWkbTypes.LineGeometry
        )

    def _is_trace_layer(self, layer):
        if not self._is_line_layer(layer):
            return False
        field_names = {f.name() for f in layer.fields()}
        required = {"trace_id", "ts_id", "z_mode", "z_value"}
        return required.issubset(field_names)

    def _trace_layer_uri(self, crs_authid):
        fields = [
            "field=trace_id:string(64)",
            "field=ts_id:string(128)",
            "field=ts_name:string(128)",
            "field=group_name:string(128)",
            "field=depth_from:double",
            "field=depth_to:double",
            "field=depth_unit:string(16)",
            "field=z_source:string(32)",
            "field=z_grid_path:string(255)",
            "field=z_mode:string(64)",
            "field=z_value:double",
            "field=created_at:string(32)",
            "field=notes:string(255)",
        ]
        return f"LineString?crs={crs_authid}&" + "&".join(fields)

    def _get_or_create_trace_group(self):
        return self._get_or_create_plugin_qgis_group("Line Traces")

    def _set_active_trace_layer(self, layer):
        if layer is None:
            return
        self.trace_line_layer_id = layer.id()
        self.iface.setActiveLayer(layer)
        self._connect_trace_layer_signals(layer)
        self.refresh_trace_info_table()

    def _connect_trace_layer_signals(self, layer):
        if layer is None:
            return
        lid = layer.id()
        if lid in self.trace_connected_layer_ids:
            return
        try:
            layer.featureAdded.connect(partial(self._on_trace_feature_added, lid))
        except Exception:
            pass
        for signal_name in ("editingStopped", "geometryChanged", "attributeValueChanged"):
            try:
                signal = getattr(layer, signal_name, None)
                if signal is not None:
                    signal.connect(lambda *args, **kwargs: self.refresh_trace_info_table())
            except Exception:
                pass
        self.trace_connected_layer_ids.add(lid)

    def _current_trace_layer(self, prefer_active=True, require_trace=False):
        if prefer_active:
            active = self.iface.activeLayer()
            if self._is_line_layer(active):
                if not require_trace or self._is_trace_layer(active):
                    return active
        if self.trace_line_layer_id:
            layer = QgsProject.instance().mapLayer(self.trace_line_layer_id)
            if self._is_line_layer(layer):
                if not require_trace or self._is_trace_layer(layer):
                    return layer
        return None

    def _select_line_layer_dialog(self, require_trace=False):
        layers = []
        for lyr in QgsProject.instance().mapLayers().values():
            if not self._is_line_layer(lyr):
                continue
            if require_trace and not self._is_trace_layer(lyr):
                continue
            layers.append(lyr)
        if not layers:
            QMessageBox.warning(self._ui_parent(), "Line Layer", "No suitable line layer found.")
            return None
        if len(layers) == 1:
            return layers[0]
        labels = [f"{lyr.name()} [{lyr.id()}]" for lyr in layers]
        label, ok = QInputDialog.getItem(
            self._ui_parent(),
            "Select line layer",
            "Layer:",
            labels,
            0,
            False,
        )
        if not ok:
            return None
        idx = labels.index(label)
        return layers[idx]

    def _active_timeslice_payload(self):
        if self.dlg is None or not hasattr(self.dlg, "rasterListWidget"):
            return None
        current = self.dlg.rasterListWidget.currentItem()
        if current is not None:
            payload = current.data(Qt.UserRole)
            if isinstance(payload, dict) and payload.get("timeslice_id"):
                return payload
        count = self.dlg.rasterListWidget.count()
        if count <= 0:
            return None
        idx = 0
        if hasattr(self.dlg, "Dial"):
            idx = max(0, min(int(self.dlg.Dial.value()), count - 1))
        item = self.dlg.rasterListWidget.item(idx)
        payload = item.data(Qt.UserRole) if item is not None else None
        if isinstance(payload, dict) and payload.get("timeslice_id"):
            return payload
        return None

    def _active_timeslice_record(self):
        payload = self._active_timeslice_payload()
        project_root = self._require_project_root(notify=False)
        if not payload or not project_root:
            return None, payload
        tid = payload.get("timeslice_id")
        if not tid:
            return None, payload
        catalog = load_catalog(project_root)
        rec = next((t for t in catalog.get("timeslices", []) if t.get("id") == tid), None)
        return rec, payload

    def _has_linked_z_grid(self, rec):
        if not isinstance(rec, dict):
            return False
        path = (rec.get("z_grid_project_path") or "").strip()
        return bool(path and os.path.exists(path))

    def _get_cached_raster_layer_by_path(self, path):
        p = (path or "").strip()
        if not p:
            return None
        abs_path = os.path.abspath(p)
        if not os.path.exists(abs_path):
            return None
        key = os.path.normcase(abs_path)
        cached = self.trace_z_grid_cache.get(key)
        if cached is not None and cached.isValid():
            return cached
        layer = QgsRasterLayer(abs_path, os.path.basename(abs_path))
        if not layer.isValid():
            return None
        self.trace_z_grid_cache[key] = layer
        return layer

    def _get_timeslice_z_grid_layer(self, rec):
        if not self._has_linked_z_grid(rec):
            return None
        return self._get_cached_raster_layer_by_path(rec.get("z_grid_project_path"))

    def _first_xy_from_geometry(self, geometry):
        if geometry is None or geometry.isEmpty():
            return None
        try:
            if geometry.isMultipart():
                parts = geometry.asMultiPolyline()
                if parts and parts[0]:
                    pt = parts[0][0]
                    return QgsPointXY(pt.x(), pt.y())
            else:
                pts = geometry.asPolyline()
                if pts:
                    pt = pts[0]
                    return QgsPointXY(pt.x(), pt.y())
        except Exception:
            pass
        try:
            c = geometry.centroid()
            if c is not None and not c.isEmpty():
                pt = c.asPoint()
                return QgsPointXY(pt.x(), pt.y())
        except Exception:
            return None
        return None

    def _sample_z_from_timeslice_grid(self, rec, geometry):
        z_grid_layer = self._get_timeslice_z_grid_layer(rec)
        if z_grid_layer is None:
            return None
        point_xy = self._first_xy_from_geometry(geometry)
        if point_xy is None:
            return None
        band = rec.get("z_grid_band")
        try:
            band_idx = int(band) if band is not None else 1
        except Exception:
            band_idx = 1
        return self._sample_raster_value(z_grid_layer, point_xy, band_idx)

    def _derive_depth_and_z_from_timeslice(self, rec, geometry=None):
        if not isinstance(rec, dict):
            return None, None, "m", "missing_z", None
        depth_from = rec.get("depth_from")
        depth_to = rec.get("depth_to")
        unit = (rec.get("unit") or "m").strip() or "m"
        z_mode = "from_timeslice_depth_mid"
        z_value = None
        try:
            if depth_from is not None and depth_to is not None:
                z_value = (float(depth_from) + float(depth_to)) / 2.0
            elif depth_from is not None:
                z_value = float(depth_from)
            elif depth_to is not None:
                z_value = float(depth_to)
        except Exception:
            z_value = None
        if z_value is None:
            z_grid_sample = self._sample_z_from_timeslice_grid(rec, geometry)
            if z_grid_sample is not None:
                z_mode = "from_surfer_grid_sample"
                z_value = float(z_grid_sample)
            else:
                z_mode = "missing_z"
        return depth_from, depth_to, unit, z_mode, z_value

    def _confirm_missing_z_for_capture(self, rec):
        if self.trace_allow_missing_z_for_session:
            return True
        if self._has_linked_z_grid(rec):
            return True
        _d0, _d1, _u, _z_mode, z_value = self._derive_depth_and_z_from_timeslice(rec)
        if z_value is not None:
            return True
        if self.trace_missing_z_prompt_shown:
            return True

        box = QMessageBox(self._ui_parent())
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Missing Z Value")
        box.setText("The active time-slice has no Z/depth value.")
        box.setInformativeText(
            "Line attributes will be auto-filled, but z_value will remain empty. Continue drawing?"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        remember = QCheckBox("Continue without Z for this session (do not ask again)")
        box.setCheckBox(remember)
        res = box.exec_()
        if res != QMessageBox.Yes:
            return False
        self.trace_missing_z_prompt_shown = True
        if remember.isChecked():
            self.trace_allow_missing_z_for_session = True
        return True

    def _set_feature_attr(self, layer, fid, field_name, value):
        idx = layer.fields().indexOf(field_name)
        if idx < 0:
            return
        layer.changeAttributeValue(fid, idx, value)

    def _on_trace_feature_added(self, layer_id, fid):
        layer = QgsProject.instance().mapLayer(layer_id)
        if not self._is_trace_layer(layer):
            return
        if not layer.isEditable():
            try:
                layer.startEditing()
            except Exception:
                return

        rec = None
        payload = None
        if isinstance(self.trace_capture_context, dict):
            rec = self.trace_capture_context.get("timeslice")
            payload = self.trace_capture_context.get("payload")

        ts_id = ""
        ts_name = ""
        group_name = ""
        if isinstance(payload, dict):
            ts_id = payload.get("timeslice_id") or ""
            group_name = payload.get("group_name") or ""
        if isinstance(rec, dict):
            ts_id = rec.get("id") or ts_id
            ts_name = rec.get("normalized_name") or rec.get("name") or ts_name
        feat_geom = None
        try:
            feat = layer.getFeature(fid)
            if feat is not None and feat.isValid():
                feat_geom = feat.geometry()
        except Exception:
            feat_geom = None

        depth_from, depth_to, depth_unit, z_mode, z_value = self._derive_depth_and_z_from_timeslice(rec, feat_geom)
        z_source = ""
        z_grid_path = ""
        if isinstance(rec, dict):
            z_source = rec.get("z_source") or ""
            z_grid_path = rec.get("z_grid_project_path") or ""
        if not z_source:
            z_source = "surfer_grid" if self._has_linked_z_grid(rec) else "depth_range"
        trace_id = f"tr_{fid}_{utc_now_iso()}".replace(":", "").replace("+", "_")

        self._set_feature_attr(layer, fid, "trace_id", trace_id)
        self._set_feature_attr(layer, fid, "ts_id", ts_id)
        self._set_feature_attr(layer, fid, "ts_name", ts_name)
        self._set_feature_attr(layer, fid, "group_name", group_name)
        self._set_feature_attr(layer, fid, "depth_from", depth_from)
        self._set_feature_attr(layer, fid, "depth_to", depth_to)
        self._set_feature_attr(layer, fid, "depth_unit", depth_unit)
        self._set_feature_attr(layer, fid, "z_source", z_source)
        self._set_feature_attr(layer, fid, "z_grid_path", z_grid_path)
        self._set_feature_attr(layer, fid, "z_mode", z_mode)
        self._set_feature_attr(layer, fid, "z_value", z_value)
        self._set_feature_attr(layer, fid, "created_at", utc_now_iso())
        layer.triggerRepaint()
        self._sync_trace_vertex_depth_labels(layer)
        self.refresh_trace_info_table()

    def create_trace_line_layer(self, checked=False):
        default_name = "Trace2D"
        name, ok = QInputDialog.getText(
            self._ui_parent(),
            "Create 2D Line Layer",
            "Layer name:",
            text=default_name,
        )
        if not ok:
            return None
        layer_name = (name or "").strip()
        if not layer_name:
            QMessageBox.warning(self._ui_parent(), "Create 2D Line Layer", "Layer name cannot be empty.")
            return None

        storage_mode = self._prompt_trace_vector_storage_mode("Create 2D Line Layer")
        if storage_mode is None:
            return None

        project_crs = QgsProject.instance().crs()
        crs_authid = project_crs.authid() if project_crs is not None and project_crs.isValid() else "EPSG:4326"
        mem_layer = QgsVectorLayer(self._trace_layer_uri(crs_authid), layer_name, "memory")
        if not mem_layer.isValid():
            QMessageBox.critical(self._ui_parent(), "Create 2D Line Layer", "Unable to create line layer.")
            return None

        layer = mem_layer
        created_path = ""
        if storage_mode == "gpkg":
            persisted, created_path, err = self._persist_vector_layer_to_project_gpkg(
                mem_layer,
                layer_name,
                source_kind="trace2d",
            )
            if persisted is None:
                fallback = QMessageBox.question(
                    self._ui_parent(),
                    "Create 2D Line Layer",
                    (
                        "Unable to create persistent GeoPackage layer.\n"
                        f"Reason: {err}\n\n"
                        "Create a temporary memory layer instead?"
                    ),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if fallback != QMessageBox.Yes:
                    return None
                layer = mem_layer
            else:
                layer = persisted
        else:
            layer.setCustomProperty("rasterlinker/storage_mode", "memory")

        QgsProject.instance().addMapLayer(layer, False)
        self._get_or_create_trace_group().addLayer(layer)
        self._set_active_trace_layer(layer)
        if storage_mode == "gpkg" and created_path:
            self._notify_info(
                f"Line layer '{layer_name}' created in GeoPackage: {created_path}",
                duration=8,
            )
        else:
            self._notify_info(
                f"Line layer '{layer_name}' created (temporary). Use 'Draw 2D Line' to digitize.",
                duration=7,
            )
        return layer

    def _ensure_trace_layer_for_capture(self):
        layer = self._current_trace_layer(prefer_active=True, require_trace=True)
        if layer is not None:
            return layer
        answer = QMessageBox.question(
            self._ui_parent(),
            "2D Line Layer Required",
            "No active trace line layer found. Create a new one now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return None
        return self.create_trace_line_layer()

    def _set_draw_action_checked(self, checked):
        actions = getattr(self, "trace_toolbar_actions", {}) or {}
        action = actions.get("Draw 2D Line")
        if action is None or not action.isCheckable():
            return
        blocked = action.blockSignals(True)
        action.setChecked(bool(checked))
        action.blockSignals(blocked)

    def _sync_draw_action_checked_for_layer(self, layer=None):
        if layer is None:
            layer = self._current_trace_layer(prefer_active=True, require_trace=False)
        is_editing = bool(layer is not None and getattr(layer, "isEditable", lambda: False)())
        self._set_draw_action_checked(is_editing)

    def start_trace_capture(self, checked=None):
        toggle_on = True if checked is None else bool(checked)
        layer = self._ensure_trace_layer_for_capture()
        if layer is None:
            self._set_draw_action_checked(False)
            return
        self._set_active_trace_layer(layer)

        # QGIS-like toggle: same pencil turns editing ON/OFF.
        if not toggle_on:
            if layer.isEditable():
                self.stop_trace_layer_editing()
            self._sync_draw_action_checked_for_layer(layer)
            return

        if not layer.isEditable():
            try:
                layer.startEditing()
            except Exception:
                QMessageBox.warning(self._ui_parent(), "Draw 2D Line", "Unable to start editing on target layer.")
                self._set_draw_action_checked(False)
                return

        self._sync_draw_action_checked_for_layer(layer)
        rec, payload = self._active_timeslice_record()
        if rec is not None and not self._confirm_missing_z_for_capture(rec):
            self._sync_draw_action_checked_for_layer(layer)
            return
        self.trace_capture_context = {"timeslice": rec, "payload": payload}
        if rec is None:
            self._notify_info(
                "No active time-slice selected. The new line will have empty time-slice metadata.",
                duration=6,
            )
        if not self._trigger_iface_action("actionAddFeature"):
            QMessageBox.warning(self._ui_parent(), "Draw 2D Line", "Unable to activate Add Feature tool.")
            self._sync_draw_action_checked_for_layer(layer)
            return
        self._notify_info("Digitize line on canvas (right-click to finish).", duration=5)

    def save_trace_layer_edits(self, checked=False):
        layer = self._current_trace_layer(prefer_active=True, require_trace=True)
        if layer is None:
            layer = self._select_line_layer_dialog(require_trace=True)
        if layer is None:
            return
        self._set_active_trace_layer(layer)

        if not layer.isEditable():
            self._notify_info("Layer is not in edit mode; nothing to save.", duration=5)
            return

        try:
            modified = layer.isModified()
        except Exception:
            modified = True
        if not modified:
            self._notify_info("No pending edits to save.", duration=4)
            return

        ok = False
        kept_editing = False
        try:
            ok = bool(layer.commitChanges(False))
            kept_editing = layer.isEditable()
        except TypeError:
            ok = bool(layer.commitChanges())
        except Exception:
            ok = False

        if not ok:
            err_text = ""
            try:
                errors = layer.commitErrors()
                if errors:
                    err_text = "\n".join(str(e) for e in errors if e)
            except Exception:
                err_text = ""
            QMessageBox.warning(
                self._ui_parent(),
                "Save Edits",
                "Unable to save layer edits." + (f"\n{err_text}" if err_text else ""),
            )
            return

        if not kept_editing:
            try:
                layer.startEditing()
                kept_editing = True
            except Exception:
                kept_editing = False

        self._notify_info(
            "Trace edits saved." + (" Editing session is still active." if kept_editing else ""),
            duration=5,
        )
        self._sync_trace_vertex_depth_labels(layer)
        self.refresh_trace_info_table()

    def stop_trace_layer_editing(self, checked=False):
        layer = self._current_trace_layer(prefer_active=True, require_trace=True)
        if layer is None:
            layer = self._select_line_layer_dialog(require_trace=True)
        if layer is None:
            self._set_draw_action_checked(False)
            return False
        self._set_active_trace_layer(layer)

        if not layer.isEditable():
            self._notify_info("Layer is not in edit mode.", duration=4)
            self._set_draw_action_checked(False)
            return False

        try:
            modified = bool(layer.isModified())
        except Exception:
            modified = True

        if not modified:
            closed = False
            try:
                closed = bool(layer.rollBack())
            except Exception:
                closed = False
            if not closed:
                try:
                    closed = bool(layer.commitChanges())
                except Exception:
                    closed = False
            if not closed:
                QMessageBox.warning(
                    self._ui_parent(),
                    "Stop Editing",
                    "Unable to close edit mode for the active layer.",
                )
                self._sync_draw_action_checked_for_layer(layer)
                return False
            self._notify_info("Editing stopped.", duration=4)
            self.refresh_trace_info_table()
            self._set_draw_action_checked(False)
            return True

        msg = QMessageBox(self._ui_parent())
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle("Stop Editing")
        msg.setText("Save changes before stopping edit mode?")
        save_btn = msg.addButton("Save", QMessageBox.AcceptRole)
        discard_btn = msg.addButton("Discard", QMessageBox.DestructiveRole)
        cancel_btn = msg.addButton("Cancel", QMessageBox.RejectRole)
        msg.setDefaultButton(save_btn)
        msg.exec_()

        clicked = msg.clickedButton()
        if clicked == cancel_btn or clicked is None:
            self._set_draw_action_checked(True)
            return False

        if clicked == save_btn:
            ok = False
            try:
                ok = bool(layer.commitChanges())
            except Exception:
                ok = False
            if not ok:
                err_text = ""
                try:
                    errors = layer.commitErrors()
                    if errors:
                        err_text = "\n".join(str(e) for e in errors if e)
                except Exception:
                    err_text = ""
                QMessageBox.warning(
                    self._ui_parent(),
                    "Stop Editing",
                    "Unable to save and stop editing."
                    + (f"\n{err_text}" if err_text else ""),
                )
                self._set_draw_action_checked(True)
                return False
            self._sync_trace_vertex_depth_labels(layer)
            self._notify_info("Edits saved and editing stopped.", duration=5)
            self.refresh_trace_info_table()
            self._set_draw_action_checked(False)
            return True

        # Discard path
        ok = False
        try:
            ok = bool(layer.rollBack())
        except Exception:
            ok = False
        if not ok:
            QMessageBox.warning(
                self._ui_parent(),
                "Stop Editing",
                "Unable to discard edits and stop editing.",
            )
            self._set_draw_action_checked(True)
            return False
        self._notify_info("Edits discarded and editing stopped.", duration=5)
        self.refresh_trace_info_table()
        self._set_draw_action_checked(False)
        return True

    def activate_trace_vertex_tool(self, checked=False):
        if not self._trigger_iface_action("actionVertexTool", "actionNodeTool"):
            QMessageBox.warning(self._ui_parent(), "Vertex Tool", "Unable to activate vertex editing tool.")

    def split_trace_feature(self, checked=False):
        if not self._trigger_iface_action("actionSplitFeatures", "actionSplitParts"):
            QMessageBox.warning(self._ui_parent(), "Split Feature", "Unable to activate split feature tool.")

    def copy_trace_features(self, checked=False):
        if not self._trigger_iface_action("actionCopyFeatures", "actionEditCopy"):
            QMessageBox.warning(self._ui_parent(), "Copy", "Unable to copy selected feature(s).")

    def paste_trace_features(self, checked=False):
        if not self._trigger_iface_action("actionPasteFeatures", "actionEditPaste"):
            QMessageBox.warning(self._ui_parent(), "Paste", "Unable to paste feature(s).")

    def delete_trace_features(self, checked=False):
        if not self._trigger_iface_action("actionDeleteSelected", "actionDeleteSelectedFeatures"):
            QMessageBox.warning(self._ui_parent(), "Delete", "Unable to delete selected feature(s).")

    def open_trace_attribute_table(self, checked=False):
        layer = self._current_trace_layer(prefer_active=True, require_trace=False)
        if layer is None:
            layer = self._select_line_layer_dialog(require_trace=False)
        if layer is None:
            return
        self._set_active_trace_layer(layer)
        try:
            self.iface.showAttributeTable(layer)
        except Exception:
            if not self._trigger_iface_action("actionOpenTable"):
                QMessageBox.warning(self._ui_parent(), "Attribute Table", "Unable to open attribute table.")
