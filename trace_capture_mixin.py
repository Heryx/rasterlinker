# -*- coding: utf-8 -*-
"""Trace capture/core mixin for GeoSurvey Studio plugin."""

import json
import os.path
from functools import partial

from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtWidgets import (
    QMessageBox,
    QInputDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QVBoxLayout,
    QPlainTextEdit,
)
from qgis.core import (
    QgsEditorWidgetSetup,
    QgsField,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
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
from .trace_editing_mixin import TraceEditingMixin
from .trace_labeling_mixin import TraceLabelingMixin
from .trace_storage_mixin import TraceStorageMixin


class TraceCaptureMixin(TraceStorageMixin, TraceLabelingMixin, TraceEditingMixin):

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
            "field=ts_id:string(512)",
            "field=ts_name:string(1024)",
            "field=group_name:string(512)",
            "field=depth_list:string(4096)",
            "field=depth_from:double",
            "field=depth_to:double",
            "field=depth_unit:string(16)",
            "field=z_source:string(32)",
            "field=z_grid_path:string(512)",
            "field=z_mode:string(64)",
            "field=z_value:double",
            "field=created_at:string(32)",
            "field=vertex_depths:string(8192)",
            "field=notes:string(512)",
            "field=interpretation:string(512)",
            "field=comment:string(512)",
        ]
        return f"LineString?crs={crs_authid}&" + "&".join(fields)

    def _ensure_trace_layer_schema_and_form(self, layer):
        if not self._is_line_layer(layer):
            return

        # Backward-compatible schema enrichment for older projects/layers.
        to_add = (
            ("trace_id", QVariant.String, 64),
            ("ts_id", QVariant.String, 512),
            ("ts_name", QVariant.String, 1024),
            ("group_name", QVariant.String, 512),
            ("depth_list", QVariant.String, 4096),
            ("depth_from", QVariant.Double, 0),
            ("depth_to", QVariant.Double, 0),
            ("depth_unit", QVariant.String, 16),
            ("z_source", QVariant.String, 32),
            ("z_grid_path", QVariant.String, 512),
            ("z_mode", QVariant.String, 64),
            ("z_value", QVariant.Double, 0),
            ("created_at", QVariant.String, 32),
            ("vertex_depths", QVariant.String, 8192),
            ("interpretation", QVariant.String, 512),
            ("comment", QVariant.String, 512),
            ("notes", QVariant.String, 512),
        )
        provider = layer.dataProvider()
        if provider is not None:
            existing = {f.name() for f in layer.fields()}
            missing_fields = []
            for name, variant_type, length in to_add:
                if name in existing:
                    continue
                field = QgsField(name, variant_type)
                if variant_type == QVariant.String and length and length > 0:
                    field.setLength(int(length))
                missing_fields.append(field)
            if missing_fields:
                try:
                    provider.addAttributes(missing_fields)
                    layer.updateFields()
                except Exception:
                    pass
        self._backfill_trace_id_values(layer)

        # QGIS-like behavior requested: do not show popup feature form on Enter.
        # Keep metadata fields non-editable; allow interpretation/comment fields.
        try:
            form_cfg = layer.editFormConfig()
            suppress_on = getattr(form_cfg, "SuppressOn", None)
            if suppress_on is None:
                suppress_on = getattr(type(form_cfg), "SuppressOn", None)
            if suppress_on is None:
                suppress_on = 1
            form_cfg.setSuppress(suppress_on)

            readonly_fields = {
                "trace_id",
                "ts_id",
                "ts_name",
                "group_name",
                "depth_list",
                "depth_from",
                "depth_to",
                "depth_unit",
                "z_source",
                "z_grid_path",
                "z_mode",
                "z_value",
                "created_at",
                "vertex_depths",
            }
            for field_name in readonly_fields:
                idx = layer.fields().indexOf(field_name)
                if idx >= 0:
                    try:
                        form_cfg.setReadOnly(idx, True)
                    except Exception:
                        pass
            layer.setEditFormConfig(form_cfg)
        except Exception:
            pass

    def _backfill_trace_id_values(self, layer):
        if layer is None or not self._is_line_layer(layer):
            return
        idx = layer.fields().indexOf("trace_id")
        if idx < 0:
            return
        # Important for GPKG/OGR: during editing, newly added features may have
        # temporary negative FIDs (e.g. -45). Updating via provider on those IDs
        # can trigger OGR "set feature -xx" errors. Use edit-buffer API instead.
        try:
            is_editable = bool(layer.isEditable())
        except Exception:
            is_editable = False

        if is_editable:
            changed_any = False
            for feat in layer.getFeatures():
                try:
                    current = str(feat.attribute(idx) or "").strip()
                except Exception:
                    current = ""
                if current:
                    continue
                trace_id = f"tr_{feat.id()}_{utc_now_iso()}".replace(":", "").replace("+", "_")
                try:
                    if layer.changeAttributeValue(int(feat.id()), idx, trace_id):
                        changed_any = True
                except Exception:
                    continue
            if changed_any:
                try:
                    layer.triggerRepaint()
                except Exception:
                    pass
        else:
            provider = layer.dataProvider()
            if provider is not None:
                changes = {}
                for feat in layer.getFeatures():
                    try:
                        current = str(feat.attribute(idx) or "").strip()
                    except Exception:
                        current = ""
                    if current:
                        continue
                    try:
                        fid_num = int(feat.id())
                    except Exception:
                        continue
                    if fid_num < 0:
                        # Defensive: provider path must not receive temporary IDs.
                        continue
                    trace_id = f"tr_{fid_num}_{utc_now_iso()}".replace(":", "").replace("+", "_")
                    changes[fid_num] = {idx: trace_id}
                if changes:
                    try:
                        provider.changeAttributeValues(changes)
                        layer.triggerRepaint()
                    except Exception:
                        pass

        # In form view keep only interpretation fields editable/visible.
        hidden_in_form = {
            "trace_id",
            "ts_id",
            "ts_name",
            "group_name",
            "depth_list",
            "depth_from",
            "depth_to",
            "depth_unit",
            "z_source",
            "z_grid_path",
            "z_mode",
            "z_value",
            "created_at",
            "vertex_depths",
        }
        for field_name in hidden_in_form:
            idx = layer.fields().indexOf(field_name)
            if idx < 0:
                continue
            try:
                layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("Hidden", {}))
            except Exception:
                pass

        # Keep the attribute table readable for interpretation workflows.
        # Hide internal identifiers/technical storage fields.
        try:
            table_cfg = layer.attributeTableConfig()
            hidden_fields = {"trace_id", "ts_id", "z_source", "z_grid_path", "created_at", "vertex_depths"}
            columns = table_cfg.columns()
            for column in columns:
                name = str(getattr(column, "name", "") or "")
                if not name:
                    continue
                if name in hidden_fields:
                    column.hidden = True
            table_cfg.setColumns(columns)
            layer.setAttributeTableConfig(table_cfg)
        except Exception:
            pass

    def _get_or_create_trace_group(self):
        group = self._get_or_create_plugin_qgis_group("Line Traces")
        plugin_root = self._find_plugin_root_group() if hasattr(self, "_find_plugin_root_group") else None
        if plugin_root is not None:
            try:
                children = list(plugin_root.children())
                idx = children.index(group) if group in children else -1
            except Exception:
                idx = -1
            if idx > 0:
                try:
                    clone = group.clone()
                    plugin_root.insertChildNode(0, clone)
                    plugin_root.removeChildNode(group)
                    group = clone
                except Exception:
                    pass
        return group

    def _add_layer_to_trace_group_top(self, layer):
        if layer is None:
            return
        group = self._get_or_create_trace_group()
        try:
            if hasattr(group, "insertLayer"):
                group.insertLayer(0, layer)
            else:
                group.addLayer(layer)
        except Exception:
            try:
                group.addLayer(layer)
            except Exception:
                pass

    def _set_active_trace_layer(self, layer):
        if layer is None:
            return
        self._ensure_trace_layer_schema_and_form(layer)
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
        try:
            if getattr(layer, "editingStopped", None) is not None:
                layer.editingStopped.connect(lambda *args, **kwargs: self.refresh_trace_info_table())
        except Exception:
            pass
        try:
            if getattr(layer, "geometryChanged", None) is not None:
                layer.geometryChanged.connect(partial(self._on_trace_geometry_changed, lid))
        except Exception:
            pass
        try:
            if getattr(layer, "attributeValueChanged", None) is not None:
                layer.attributeValueChanged.connect(lambda *args, **kwargs: self.refresh_trace_info_table())
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
        current = self.dlg.rasterListWidget.currentItem()
        if current is not None:
            payload = current.data(Qt.UserRole)
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

    def _safe_float(self, value):
        if value in (None, ""):
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _trace_depth_pick_mode(self):
        mode = str(getattr(self, "trace_depth_pick_mode", "off") or "off").strip().lower()
        combo = getattr(self, "trace_info_depth_pick_combo", None)
        settings = getattr(self, "settings", None)
        if combo is None and settings is not None:
            try:
                if hasattr(self, "_trace_info_settings_key"):
                    key = self._trace_info_settings_key("depth_pick")
                else:
                    key = "GeoSurveyStudio/trace_info/depth_pick"
                saved = str(settings.value(key, mode) or mode).strip().lower()
                if saved:
                    mode = saved
            except Exception:
                pass
        if combo is not None:
            try:
                current = str(combo.currentData() or mode).strip().lower()
                if current:
                    mode = current
            except Exception:
                pass
        if mode not in ("off", "min", "mid", "max"):
            mode = "off"
        self.trace_depth_pick_mode = mode
        return mode

    def _depth_value_from_pair(self, d0, d1, mode=None):
        lo = self._safe_float(d0)
        hi = self._safe_float(d1)
        if lo is None and hi is None:
            return None
        if lo is None:
            return float(hi)
        if hi is None:
            return float(lo)
        lo = float(lo)
        hi = float(hi)
        if hi < lo:
            lo, hi = hi, lo
        pick_mode = str(mode or self._trace_depth_pick_mode()).strip().lower()
        if pick_mode == "min":
            return lo
        if pick_mode == "max":
            return hi
        # "off" keeps metadata stable using midpoint, while labels can be disabled.
        return (lo + hi) / 2.0

    def _format_depth_value(self, value):
        fv = self._safe_float(value)
        if fv is None:
            return ""
        txt = f"{fv:.3f}".rstrip("0").rstrip(".")
        return txt if txt else "0"

    def _format_depth_pair(self, d0, d1, unit):
        lo = self._safe_float(d0)
        hi = self._safe_float(d1)
        if lo is None and hi is None:
            return ""
        if lo is None:
            lo = hi
        if hi is None:
            hi = lo
        lo = float(lo)
        hi = float(hi)
        if hi < lo:
            lo, hi = hi, lo
        unit_txt = (str(unit or "m").strip() or "m")
        if abs(hi - lo) <= 1e-9:
            return f"{self._format_depth_value(lo)} {unit_txt}"
        return f"{self._format_depth_value(lo)}-{self._format_depth_value(hi)} {unit_txt}"

    def _trace_vertex_context_store(self):
        store = getattr(self, "trace_vertex_context_capture", None)
        if not isinstance(store, dict):
            store = {}
            self.trace_vertex_context_capture = store
        return store

    def _ensure_trace_canvas_click_filter(self):
        flt = getattr(self, "trace_canvas_click_filter", None)
        if flt is not None:
            return flt
        try:
            from .trace_canvas_click_filter import TraceCanvasClickFilter
        except Exception:
            try:
                from trace_canvas_click_filter import TraceCanvasClickFilter
            except Exception:
                return None
        try:
            canvas = self.iface.mapCanvas()
            viewport = canvas.viewport() if canvas is not None else None
        except Exception:
            canvas = None
            viewport = None
        if viewport is None and canvas is None:
            return None
        flt = TraceCanvasClickFilter(
            on_left_click=self._on_trace_canvas_left_click,
            on_wheel=self._on_trace_canvas_wheel,
            wheel_modifier_getter=self._trace_canvas_wheel_modifier,
            parent=viewport if viewport is not None else canvas,
        )
        installed = False
        try:
            if viewport is not None:
                viewport.installEventFilter(flt)
                installed = True
        except Exception:
            pass
        try:
            if canvas is not None:
                canvas.installEventFilter(flt)
                installed = True
        except Exception:
            pass
        if not installed:
            return None
        self.trace_canvas_click_filter = flt
        return flt

    def _set_trace_canvas_click_capture_enabled(self, enabled):
        flt = self._ensure_trace_canvas_click_filter()
        self.trace_canvas_click_capture_enabled = bool(enabled)
        if flt is not None:
            flt.enabled = bool(enabled)
        if not enabled:
            self.trace_pending_vertex_clicks = []

    def _on_trace_canvas_left_click(self):
        if not bool(getattr(self, "trace_canvas_click_capture_enabled", False)):
            return
        snap = self._capture_vertex_context_snapshot()
        queue = list(getattr(self, "trace_pending_vertex_clicks", []) or [])
        queue.append(snap)
        # Prevent unbounded growth in long sessions.
        if len(queue) > 500:
            queue = queue[-500:]
        self.trace_pending_vertex_clicks = queue

    def _trace_canvas_wheel_modifier(self):
        mode = str(getattr(self, "trace_canvas_wheel_modifier", "alt") or "alt").strip().lower()
        if mode not in ("alt", "shift", "ctrl"):
            mode = "alt"
        return mode

    def _set_trace_canvas_wheel_modifier(self, mode, persist=True):
        mode_txt = str(mode or "alt").strip().lower()
        if mode_txt not in ("alt", "shift", "ctrl"):
            mode_txt = "alt"
        self.trace_canvas_wheel_modifier = mode_txt
        if persist and hasattr(self, "_save_trace_info_ui_state"):
            try:
                self._save_trace_info_ui_state()
            except Exception:
                pass

    def _on_trace_canvas_wheel(self, delta):
        if not bool(getattr(self, "trace_canvas_click_capture_enabled", False)):
            return False
        state = str(getattr(self, "trace_draw_session_state", "idle") or "idle").strip().lower()
        if state != "drawing_active":
            return False
        if self.dlg is None or not hasattr(self.dlg, "rasterListWidget") or not hasattr(self.dlg, "Dial"):
            return False
        total = int(self.dlg.rasterListWidget.count())
        if total <= 0:
            return False
        current = int(self.dlg.Dial.value())
        # Wheel up -> previous slice, wheel down -> next slice.
        step = -1 if int(delta) > 0 else 1
        target = max(0, min(current + step, total - 1))
        if target == current:
            return True
        try:
            self.update_visibility_with_dial(target)
            self._update_navigation_controls(target)
        except Exception:
            try:
                self.dlg.Dial.setValue(target)
            except Exception:
                return False
        try:
            widget = self.dlg.rasterListWidget
            if 0 <= target < widget.count():
                item = widget.item(target)
                if item is not None:
                    widget.blockSignals(True)
                    widget.setCurrentItem(item)
                    widget.blockSignals(False)
        except Exception:
            pass
        return True

    def _consume_pending_click_contexts(self, vertex_count):
        try:
            n = int(vertex_count)
        except Exception:
            n = 0
        if n <= 0:
            return []
        queue = list(getattr(self, "trace_pending_vertex_clicks", []) or [])
        if not queue:
            return []
        if len(queue) >= n:
            used = queue[-n:]
            self.trace_pending_vertex_clicks = queue[:-n]
            return used
        used = list(queue)
        self.trace_pending_vertex_clicks = []
        # Pad by repeating last known snapshot when clicks are fewer than vertices.
        while len(used) < n and used:
            used.append(dict(used[-1]))
        return used

    def _trace_vertex_context_key(self, layer_id, fid):
        try:
            fid_txt = str(int(fid))
        except Exception:
            fid_txt = str(fid)
        return f"{layer_id}:{fid_txt}"

    def _get_captured_vertex_contexts(self, layer_id, fid):
        store = self._trace_vertex_context_store()
        key = self._trace_vertex_context_key(layer_id, fid)
        rows = store.get(key)
        return list(rows) if isinstance(rows, list) else []

    def _capture_vertex_context_snapshot(self):
        rec, payload = self._active_timeslice_record()
        if rec is None and isinstance(payload, dict):
            rec = self._timeslice_record_from_payload(payload)
        ts_id = ""
        ts_name = ""
        group_name = ""
        if isinstance(payload, dict):
            ts_id = str(payload.get("timeslice_id") or "").strip()
            group_name = str(payload.get("group_name") or "").strip()
        if isinstance(rec, dict):
            ts_id = ts_id or str(rec.get("id") or "").strip()
            ts_name = str(rec.get("normalized_name") or rec.get("name") or "").strip()
            group_name = group_name or str(rec.get("group_name") or "").strip()
        d0 = rec.get("depth_from") if isinstance(rec, dict) else None
        d1 = rec.get("depth_to") if isinstance(rec, dict) else None
        unit = (rec.get("unit") if isinstance(rec, dict) else "m") or "m"
        return {
            "timeslice_id": ts_id,
            "timeslice_name": ts_name,
            "group_name": group_name,
            "depth_from": d0,
            "depth_to": d1,
            "depth_unit": str(unit).strip() or "m",
            "rec": rec if isinstance(rec, dict) else None,
        }

    def _update_captured_vertex_contexts_for_geometry(self, layer_id, fid, geometry):
        vertices = self._iter_geometry_vertices_xy(geometry)
        if not vertices:
            return []
        store = self._trace_vertex_context_store()
        key = self._trace_vertex_context_key(layer_id, fid)
        rows = list(store.get(key) or [])
        # If exact contexts already captured from click stream, preserve them.
        if rows and len(rows) == len(vertices):
            return rows
        if len(rows) > len(vertices):
            rows = rows[:len(vertices)]
        while len(rows) < len(vertices):
            rows.append(self._capture_vertex_context_snapshot())
        store[key] = rows
        return rows

    def _normalize_source_path(self, path_like):
        raw = str(path_like or "").split("|", 1)[0].strip()
        if not raw:
            return ""
        try:
            return os.path.normcase(os.path.abspath(raw))
        except Exception:
            return os.path.normcase(raw)

    def _catalog_timeslice_lookup(self):
        project_root = self._require_project_root(notify=False)
        by_id = {}
        by_path = {}
        by_name = {}
        if not project_root:
            return by_id, by_path, by_name
        try:
            catalog = load_catalog(project_root)
        except Exception:
            return by_id, by_path, by_name

        for rec in catalog.get("timeslices", []):
            rec_id = str(rec.get("id") or "").strip()
            if rec_id and rec_id not in by_id:
                by_id[rec_id] = rec

            path_key = self._normalize_source_path(rec.get("project_path"))
            if path_key and path_key not in by_path:
                by_path[path_key] = rec

            name_key = str(rec.get("normalized_name") or rec.get("name") or "").strip().lower()
            if name_key and name_key not in by_name:
                by_name[name_key] = rec
        return by_id, by_path, by_name

    def _timeslice_record_from_payload(self, payload):
        if not isinstance(payload, dict):
            return None
        timeslice_id = str(payload.get("timeslice_id") or "").strip()
        if not timeslice_id:
            return None
        by_id, _by_path, _by_name = self._catalog_timeslice_lookup()
        return by_id.get(timeslice_id)

    def _loaded_plugin_raster_layer_by_path(self, project_path):
        target = self._normalize_source_path(project_path)
        if not target:
            return None
        plugin_root = self._find_plugin_root_group() if hasattr(self, "_find_plugin_root_group") else None
        if plugin_root is None:
            return None
        for group_node in plugin_root.children():
            if not isinstance(group_node, QgsLayerTreeGroup):
                continue
            for child in group_node.children():
                if not isinstance(child, QgsLayerTreeLayer):
                    continue
                layer = child.layer()
                if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
                    continue
                if self._normalize_source_path(layer.source()) == target:
                    return layer
        return None

    def _selected_timeslice_contexts_from_ui(self):
        if self.dlg is None or not hasattr(self.dlg, "rasterListWidget"):
            return []
        widget = self.dlg.rasterListWidget
        items = list(widget.selectedItems() or [])
        if not items:
            return []

        by_id, by_path, by_name = self._catalog_timeslice_lookup()
        contexts = []
        seen = set()
        for item in items:
            payload = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(payload, dict):
                continue
            ts_id = str(payload.get("timeslice_id") or "").strip()
            group_name = str(payload.get("group_name") or "").strip()
            rec = None
            if ts_id:
                rec = by_id.get(ts_id)
            if rec is None:
                project_path = payload.get("project_path")
                path_key = self._normalize_source_path(project_path)
                if path_key:
                    rec = by_path.get(path_key)
            if rec is None:
                txt_name = str(payload.get("name") or payload.get("normalized_name") or "").strip().lower()
                if txt_name:
                    rec = by_name.get(txt_name)

            ts_name = ""
            project_path = ""
            if isinstance(rec, dict):
                ts_id = ts_id or str(rec.get("id") or "").strip()
                ts_name = str(rec.get("normalized_name") or rec.get("name") or "").strip()
                project_path = str(rec.get("project_path") or "").strip()
            if not ts_name and item is not None:
                ts_name = str(item.text() or "").strip()
            if not project_path:
                project_path = str(payload.get("project_path") or "").strip()

            layer = self._loaded_plugin_raster_layer_by_path(project_path)
            if layer is None and project_path:
                try:
                    layer_name = ts_name or os.path.basename(project_path)
                    layer_try = QgsRasterLayer(project_path, layer_name)
                    if layer_try.isValid():
                        layer = layer_try
                except Exception:
                    layer = None

            key = (ts_id, self._normalize_source_path(project_path), ts_name.lower())
            if key in seen:
                continue
            seen.add(key)

            contexts.append(
                {
                    "layer": layer,
                    "group_name": group_name,
                    "timeslice_id": ts_id,
                    "timeslice_name": ts_name,
                    "rec": rec,
                }
            )
        return contexts

    def _all_timeslice_contexts_from_ui_list(self):
        if self.dlg is None or not hasattr(self.dlg, "rasterListWidget"):
            return []
        widget = self.dlg.rasterListWidget
        if widget.count() <= 0:
            return []

        by_id, by_path, by_name = self._catalog_timeslice_lookup()
        contexts = []
        seen = set()
        for idx in range(widget.count()):
            item = widget.item(idx)
            payload = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(payload, dict):
                continue
            ts_id = str(payload.get("timeslice_id") or "").strip()
            group_name = str(payload.get("group_name") or "").strip()

            rec = None
            if ts_id:
                rec = by_id.get(ts_id)
            project_path = str(payload.get("project_path") or "").strip()
            if rec is None and project_path:
                rec = by_path.get(self._normalize_source_path(project_path))
            if rec is None:
                txt_name = str(payload.get("name") or payload.get("normalized_name") or "").strip().lower()
                if txt_name:
                    rec = by_name.get(txt_name)

            ts_name = ""
            if isinstance(rec, dict):
                ts_id = ts_id or str(rec.get("id") or "").strip()
                ts_name = str(rec.get("normalized_name") or rec.get("name") or "").strip()
                project_path = project_path or str(rec.get("project_path") or "").strip()
            if not ts_name and item is not None:
                ts_name = str(item.text() or "").strip()

            layer = self._loaded_plugin_raster_layer_by_path(project_path)
            key = (ts_id, self._normalize_source_path(project_path), ts_name.lower())
            if key in seen:
                continue
            seen.add(key)
            contexts.append(
                {
                    "layer": layer,
                    "group_name": group_name,
                    "timeslice_id": ts_id,
                    "timeslice_name": ts_name,
                    "rec": rec,
                }
            )
        return contexts

    def _contexts_from_captured_vertices(self, layer_id, fid):
        captured = self._get_captured_vertex_contexts(layer_id, fid)
        if not captured:
            return [], captured
        contexts = []
        seen = set()
        for row in captured:
            if not isinstance(row, dict):
                continue
            ts_id = str(row.get("timeslice_id") or "").strip()
            ts_name = str(row.get("timeslice_name") or "").strip()
            group_name = str(row.get("group_name") or "").strip()
            rec = row.get("rec") if isinstance(row.get("rec"), dict) else None
            key = (
                ts_id,
                ts_name.lower(),
                group_name.lower(),
                str(rec.get("id") or "").strip() if isinstance(rec, dict) else "",
            )
            if key in seen:
                continue
            seen.add(key)
            contexts.append(
                {
                    "layer": None,
                    "group_name": group_name,
                    "timeslice_id": ts_id,
                    "timeslice_name": ts_name,
                    "rec": rec,
                }
            )
        return contexts, captured

    def _visible_timeslice_contexts_for_geometry(self, feature_geometry):
        plugin_root = self._find_plugin_root_group() if hasattr(self, "_find_plugin_root_group") else None
        if plugin_root is None:
            return []

        _by_id, by_path, by_name = self._catalog_timeslice_lookup()
        contexts = []
        for group_node in plugin_root.children():
            if not isinstance(group_node, QgsLayerTreeGroup):
                continue
            for child in group_node.children():
                if not isinstance(child, QgsLayerTreeLayer):
                    continue
                layer = child.layer()
                if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
                    continue
                try:
                    if not child.isVisible():
                        continue
                except Exception:
                    pass

                if feature_geometry is not None and not feature_geometry.isEmpty():
                    try:
                        extent_geom = QgsGeometry.fromRect(layer.extent())
                        if extent_geom is not None and not feature_geometry.intersects(extent_geom):
                            continue
                    except Exception:
                        continue

                source_key = self._normalize_source_path(layer.source())
                rec = by_path.get(source_key)
                if rec is None:
                    rec = by_name.get(str(layer.name() or "").strip().lower())

                ts_id = ""
                ts_name = str(layer.name() or "").strip()
                if isinstance(rec, dict):
                    ts_id = str(rec.get("id") or "").strip()
                    ts_name = str(rec.get("normalized_name") or rec.get("name") or ts_name).strip()

                contexts.append(
                    {
                        "layer": layer,
                        "group_name": str(group_node.name() or "").strip(),
                        "timeslice_id": ts_id,
                        "timeslice_name": ts_name,
                        "rec": rec,
                    }
                )
        return contexts

    def _merge_timeslice_contexts(self, contexts_primary, contexts_extra):
        merged = []
        seen = set()
        for ctx in list(contexts_primary or []) + list(contexts_extra or []):
            if not isinstance(ctx, dict):
                continue
            layer = ctx.get("layer")
            layer_id = ""
            try:
                layer_id = str(layer.id()) if layer is not None else ""
            except Exception:
                layer_id = ""
            rec = ctx.get("rec")
            rec_id = str(rec.get("id") or "").strip() if isinstance(rec, dict) else ""
            ts_id = str(ctx.get("timeslice_id") or "").strip()
            ts_name = str(ctx.get("timeslice_name") or "").strip().lower()
            key = (layer_id, rec_id, ts_id, ts_name)
            if key in seen:
                continue
            seen.add(key)
            merged.append(ctx)
        return merged

    def _vertex_context_candidates(self, point_xy, contexts):
        if not contexts:
            return []
        if point_xy is None:
            return list(contexts)
        inside = []
        no_layer = []
        for ctx in contexts or []:
            layer = ctx.get("layer")
            if layer is None or not isinstance(layer, QgsRasterLayer):
                no_layer.append(ctx)
                continue
            try:
                if layer.extent().contains(point_xy):
                    inside.append(ctx)
            except Exception:
                continue
        if inside:
            return inside + [ctx for ctx in no_layer if ctx not in inside]
        if no_layer:
            return no_layer
        return list(contexts)

    def _serialize_vertex_depths(self, vertex_rows):
        clean = []
        for row in vertex_rows or []:
            depth_val = self._safe_float(row.get("d"))
            depth_min = self._safe_float(row.get("dmin"))
            depth_max = self._safe_float(row.get("dmax"))
            clean.append(
                {
                    "i": int(row.get("i") or 0),
                    "d": depth_val,
                    "dmin": depth_min,
                    "dmax": depth_max,
                    "u": str(row.get("u") or "m"),
                }
            )
        try:
            return json.dumps(clean, ensure_ascii=True, separators=(",", ":"))
        except Exception:
            return ""

    def _build_trace_metadata_from_geometry(
        self,
        geometry,
        fallback_rec=None,
        fallback_payload=None,
        layer_id=None,
        fid=None,
    ):
        depth_pick_mode = self._trace_depth_pick_mode()
        contexts = self._visible_timeslice_contexts_for_geometry(geometry)
        contexts = self._merge_timeslice_contexts(contexts, self._selected_timeslice_contexts_from_ui())
        # Include all currently listed time-slices in UI to keep metadata coherent across
        # full working set (especially when only one raster is visible due dial state).
        contexts = self._merge_timeslice_contexts(contexts, self._all_timeslice_contexts_from_ui_list())
        captured_contexts, captured_vertices = self._contexts_from_captured_vertices(layer_id, fid)
        contexts = self._merge_timeslice_contexts(contexts, captured_contexts)
        if not contexts:
            payload = fallback_payload if isinstance(fallback_payload, dict) else {}
            rec = fallback_rec if isinstance(fallback_rec, dict) else self._timeslice_record_from_payload(payload)
            contexts = [
                {
                    "layer": None,
                    "group_name": str(payload.get("group_name") or "").strip(),
                    "timeslice_id": str(payload.get("timeslice_id") or (rec.get("id") if isinstance(rec, dict) else "")).strip(),
                    "timeslice_name": str(
                        (rec.get("normalized_name") or rec.get("name"))
                        if isinstance(rec, dict)
                        else ""
                    ).strip(),
                    "rec": rec,
                }
            ]

        touched_ids = []
        touched_names = []
        touched_groups = []
        touched_records = []
        depth_list_values = []
        depth_bounds = []
        for ctx in contexts:
            ts_id = str(ctx.get("timeslice_id") or "").strip()
            ts_name = str(ctx.get("timeslice_name") or "").strip()
            group_name = str(ctx.get("group_name") or "").strip()
            rec = ctx.get("rec")

            if ts_id and ts_id not in touched_ids:
                touched_ids.append(ts_id)
            if ts_name and ts_name not in touched_names:
                touched_names.append(ts_name)
            if group_name and group_name not in touched_groups:
                touched_groups.append(group_name)
            if isinstance(rec, dict) and rec not in touched_records:
                touched_records.append(rec)
            if isinstance(rec, dict):
                depth_txt = self._format_depth_pair(rec.get("depth_from"), rec.get("depth_to"), rec.get("unit") or "m")
                if depth_txt and depth_txt not in depth_list_values:
                    depth_list_values.append(depth_txt)

        vertices = self._iter_geometry_vertices_xy(geometry)
        vertex_rows = []
        z_values = []
        z_modes = []
        depth_unit = "m"
        for idx, point_xy in enumerate(vertices, start=1):
            point_geom = QgsGeometry.fromPointXY(point_xy) if point_xy is not None else None
            if idx <= len(captured_vertices):
                crow = captured_vertices[idx - 1]
                c_rec = crow.get("rec") if isinstance(crow.get("rec"), dict) else None
                vertex_candidates = [
                    {
                        "layer": None,
                        "group_name": str(crow.get("group_name") or ""),
                        "timeslice_id": str(crow.get("timeslice_id") or ""),
                        "timeslice_name": str(crow.get("timeslice_name") or ""),
                        "rec": c_rec,
                        "depth_from": crow.get("depth_from"),
                        "depth_to": crow.get("depth_to"),
                        "depth_unit": crow.get("depth_unit") or "m",
                    }
                ]
            else:
                vertex_candidates = self._vertex_context_candidates(point_xy, contexts)
            vertex_depth_pairs = []
            vertex_modes = []
            vertex_units = []
            vertex_ts_names = []
            vertex_ts_ids = []
            for ctx in vertex_candidates:
                rec = ctx.get("rec") if isinstance(ctx, dict) else None
                forced_d0 = ctx.get("depth_from") if isinstance(ctx, dict) else None
                forced_d1 = ctx.get("depth_to") if isinstance(ctx, dict) else None
                forced_u = ctx.get("depth_unit") if isinstance(ctx, dict) else None
                if forced_d0 is not None or forced_d1 is not None:
                    _d0, _d1, unit = forced_d0, forced_d1, (forced_u or "m")
                    z_mode = "from_timeslice_depth_range"
                    z_val = self._depth_value_from_pair(_d0, _d1, depth_pick_mode)
                else:
                    _d0, _d1, unit, z_mode, z_val = self._derive_depth_and_z_from_timeslice(rec, point_geom)
                if unit:
                    unit_txt = str(unit).strip() or "m"
                    vertex_units.append(unit_txt)
                else:
                    unit_txt = depth_unit
                if z_mode:
                    vertex_modes.append(str(z_mode))
                d0 = self._safe_float(_d0)
                d1 = self._safe_float(_d1)
                if d0 is not None or d1 is not None:
                    if d0 is None:
                        d0 = d1
                    if d1 is None:
                        d1 = d0
                    lo = min(float(d0), float(d1))
                    hi = max(float(d0), float(d1))
                    vertex_depth_pairs.append((lo, hi))
                    depth_bounds.append((lo, hi))
                    picked = self._depth_value_from_pair(lo, hi, depth_pick_mode)
                    if picked is not None:
                        z_values.append(float(picked))
                else:
                    depth_num = self._safe_float(z_val)
                    if depth_num is not None:
                        vertex_depth_pairs.append((depth_num, depth_num))
                        depth_bounds.append((depth_num, depth_num))
                        z_values.append(depth_num)
                ts_name = str((ctx or {}).get("timeslice_name") or "").strip()
                ts_id = str((ctx or {}).get("timeslice_id") or "").strip()
                if ts_name and ts_name not in vertex_ts_names:
                    vertex_ts_names.append(ts_name)
                if ts_id and ts_id not in vertex_ts_ids:
                    vertex_ts_ids.append(ts_id)
                interval_txt = self._format_depth_pair(_d0, _d1, unit_txt)
                if interval_txt and interval_txt not in depth_list_values:
                    depth_list_values.append(interval_txt)

            if vertex_units:
                depth_unit = vertex_units[0]
            if vertex_modes:
                z_modes.extend(vertex_modes)

            if vertex_depth_pairs:
                v_min = min(p[0] for p in vertex_depth_pairs)
                v_max = max(p[1] for p in vertex_depth_pairs)
                v_mid = self._depth_value_from_pair(v_min, v_max, depth_pick_mode)
            else:
                v_min = None
                v_max = None
                v_mid = None

            vertex_rows.append(
                {
                    "i": idx,
                    "d": v_mid,
                    "dmin": v_min,
                    "dmax": v_max,
                    "u": depth_unit,
                    "ts": "|".join(vertex_ts_names),
                    "id": "|".join(vertex_ts_ids),
                }
            )

        if depth_bounds:
            depth_from = min(lo for lo, _hi in depth_bounds)
            depth_to = max(hi for _lo, hi in depth_bounds)
        else:
            depth_from = None
            depth_to = None

        if depth_from is not None or depth_to is not None:
            z_value = self._depth_value_from_pair(depth_from, depth_to, depth_pick_mode)
        elif z_values:
            if depth_pick_mode == "min":
                z_value = min(z_values)
            elif depth_pick_mode == "max":
                z_value = max(z_values)
            else:
                z_value = sum(z_values) / float(len(z_values))
        else:
            z_value = None

        z_grid_path = ""
        for rec in touched_records:
            candidate = str(rec.get("z_grid_project_path") or "").strip()
            if candidate:
                z_grid_path = candidate
                break

        if len(touched_names) > 1:
            z_mode = "multi_timeslice"
        elif z_modes:
            norm_modes = [m.strip().lower() for m in z_modes if m]
            if any(m.startswith("missing") for m in norm_modes) and any(not m.startswith("missing") for m in norm_modes):
                z_mode = "partial_missing_z"
            elif all(m.startswith("missing") for m in norm_modes):
                z_mode = "missing_z"
            elif any("surfer_grid" in m for m in norm_modes):
                z_mode = "from_surfer_grid_sample"
            else:
                z_mode = z_modes[0]
        else:
            z_mode = "missing_z"

        # Stable ordering for depth list by numeric lower bound.
        def _depth_sort_key(txt):
            s = str(txt or "").strip()
            if not s:
                return (999999.0, s)
            first = s.split(" ", 1)[0]
            lo = first.split("-", 1)[0]
            try:
                return (float(lo), s)
            except Exception:
                return (999999.0, s)

        depth_list_values = sorted(set(depth_list_values), key=_depth_sort_key)

        z_source = "surfer_grid" if any(self._has_linked_z_grid(rec) for rec in touched_records) else "depth_range"
        ts_id_text = ";".join(touched_ids)
        ts_name_text = " | ".join(touched_names)
        group_name_text = " | ".join(touched_groups)
        depth_list_text = " | ".join(depth_list_values)

        return {
            "ts_id": ts_id_text,
            "ts_name": ts_name_text,
            "group_name": group_name_text,
            "depth_list": depth_list_text,
            "depth_from": depth_from,
            "depth_to": depth_to,
            "depth_unit": depth_unit,
            "z_source": z_source,
            "z_grid_path": z_grid_path,
            "z_mode": z_mode,
            "z_value": z_value,
            "vertex_depths": self._serialize_vertex_depths(vertex_rows),
        }

    def _derive_depth_and_z_from_timeslice(self, rec, geometry=None):
        if not isinstance(rec, dict):
            return None, None, "m", "missing_z", None
        depth_from = rec.get("depth_from")
        depth_to = rec.get("depth_to")
        unit = (rec.get("unit") or "m").strip() or "m"
        depth_pick_mode = self._trace_depth_pick_mode()
        z_mode = (
            "from_timeslice_depth_min"
            if depth_pick_mode == "min"
            else ("from_timeslice_depth_max" if depth_pick_mode == "max" else "from_timeslice_depth_mid")
        )
        z_value = None
        try:
            if depth_from is not None and depth_to is not None:
                z_value = self._depth_value_from_pair(depth_from, depth_to, depth_pick_mode)
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
        # If current time-slice has no Z, but another visible time-slice can provide it,
        # allow drawing without showing the warning.
        try:
            ctxs = self._visible_timeslice_contexts_for_geometry(None)
            ctxs = self._merge_timeslice_contexts(ctxs, self._selected_timeslice_contexts_from_ui())
            if len(ctxs) <= 1:
                ctxs = self._merge_timeslice_contexts(ctxs, self._all_timeslice_contexts_from_ui_list())
            for ctx in ctxs:
                rec_ctx = ctx.get("rec") if isinstance(ctx, dict) else None
                if not isinstance(rec_ctx, dict):
                    continue
                _cd0, _cd1, _cu, _cz_mode, ctx_z = self._derive_depth_and_z_from_timeslice(rec_ctx)
                if ctx_z is not None:
                    return True
        except Exception:
            pass
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

    def _on_trace_geometry_changed(self, layer_id, fid, geometry):
        layer = QgsProject.instance().mapLayer(layer_id)
        if not self._is_trace_layer(layer):
            return
        try:
            self._update_captured_vertex_contexts_for_geometry(layer_id, fid, geometry)
        except Exception:
            pass
        self.refresh_trace_info_table()

    def _set_feature_attr(self, layer, fid, field_name, value):
        idx = layer.fields().indexOf(field_name)
        if idx < 0:
            return
        try:
            if isinstance(value, str):
                fld = layer.fields()[idx]
                max_len = int(getattr(fld, "length", lambda: 0)() or 0)
                if max_len > 0 and len(value) > max_len:
                    value = value[:max_len]
        except Exception:
            pass
        layer.changeAttributeValue(fid, idx, value)

    def _trace_feature_postprocess_key(self, layer_id, fid):
        try:
            return f"{str(layer_id)}:{int(fid)}"
        except Exception:
            return f"{str(layer_id)}:{str(fid)}"

    def _trace_postprocess_sets(self):
        inflight = getattr(self, "trace_postprocess_inflight", None)
        if not isinstance(inflight, set):
            inflight = set()
            self.trace_postprocess_inflight = inflight
        done = getattr(self, "trace_postprocess_done", None)
        if not isinstance(done, set):
            done = set()
            self.trace_postprocess_done = done
        prompted = getattr(self, "trace_interpretation_prompted_keys", None)
        if not isinstance(prompted, set):
            prompted = set()
            self.trace_interpretation_prompted_keys = prompted
        prompted_trace_ids = getattr(self, "trace_interpretation_prompted_trace_ids", None)
        if not isinstance(prompted_trace_ids, set):
            prompted_trace_ids = set()
            self.trace_interpretation_prompted_trace_ids = prompted_trace_ids
        done_trace_ids = getattr(self, "trace_postprocess_done_trace_ids", None)
        if not isinstance(done_trace_ids, set):
            done_trace_ids = set()
            self.trace_postprocess_done_trace_ids = done_trace_ids
        return inflight, done, prompted, prompted_trace_ids, done_trace_ids

    def _reset_trace_postprocess_cache(self, layer_id=None):
        inflight, done, prompted, prompted_trace_ids, done_trace_ids = self._trace_postprocess_sets()
        if layer_id in (None, ""):
            inflight.clear()
            done.clear()
            prompted.clear()
            prompted_trace_ids.clear()
            done_trace_ids.clear()
            return
        prefix = f"{str(layer_id)}:"
        for store in (inflight, done, prompted):
            stale = [k for k in store if str(k).startswith(prefix)]
            for key in stale:
                store.discard(key)

    def _begin_trace_feature_postprocess(self, layer_id, fid):
        key = self._trace_feature_postprocess_key(layer_id, fid)
        inflight, done, _prompted, _prompted_trace_ids, _done_trace_ids = self._trace_postprocess_sets()
        if key in done or key in inflight:
            return None, key
        inflight.add(key)
        return key, key

    def _end_trace_feature_postprocess(self, token, success):
        inflight, done, _prompted, _prompted_trace_ids, _done_trace_ids = self._trace_postprocess_sets()
        if token in inflight:
            inflight.discard(token)
        if success:
            done.add(token)
        else:
            done.discard(token)

    def _trace_interpretation_fields_for_layer(self, layer):
        if layer is None:
            return []
        fields = []
        for name in ("notes", "interpretation", "comment"):
            idx = layer.fields().indexOf(name)
            if idx >= 0:
                fields.append((name, idx))
        return fields

    def _prompt_trace_interpretation_fields(self, layer, fid):
        fields = self._trace_interpretation_fields_for_layer(layer)
        if not fields:
            return

        feature = None
        try:
            feature = layer.getFeature(fid)
        except Exception:
            feature = None

        values = {}
        for name, _idx in fields:
            val = ""
            if feature is not None and feature.isValid():
                try:
                    val = feature.attribute(name)
                except Exception:
                    val = ""
            values[name] = "" if val in (None, "") else str(val)

        dlg = QDialog(self._ui_parent())
        dlg.setWindowTitle("Trace Interpretation")
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        form = QFormLayout()
        form.setContentsMargins(6, 6, 6, 6)
        form.setSpacing(8)

        editors = {}
        label_map = {
            "notes": "Notes",
            "interpretation": "Interpretation",
            "comment": "Comment",
        }
        for name, _idx in fields:
            editor = QPlainTextEdit(dlg)
            editor.setTabChangesFocus(True)
            editor.setMinimumHeight(70)
            editor.setPlainText(values.get(name, ""))
            editor.setPlaceholderText(f"Add {label_map.get(name, name).lower()}...")
            form.addRow(f"{label_map.get(name, name)}:", editor)
            editors[name] = editor

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
        try:
            ok_btn = buttons.button(QDialogButtonBox.Ok)
            if ok_btn is not None:
                ok_btn.setText("Save")
            cancel_btn = buttons.button(QDialogButtonBox.Cancel)
            if cancel_btn is not None:
                cancel_btn.setText("Skip")
        except Exception:
            pass
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec_() != QDialog.Accepted:
            return

        for name, idx in fields:
            try:
                new_val = str(editors[name].toPlainText() or "").strip()
            except Exception:
                new_val = ""
            old_val = str(values.get(name, "") or "").strip()
            if new_val == old_val:
                continue
            try:
                layer.changeAttributeValue(fid, idx, new_val)
            except Exception:
                continue

    def _on_trace_feature_added(self, layer_id, fid):
        layer = QgsProject.instance().mapLayer(layer_id)
        if not self._is_trace_layer(layer):
            return
        token, key = self._begin_trace_feature_postprocess(layer_id, fid)
        if token is None:
            # Duplicate signal for same feature, ignore side-effects.
            self.refresh_trace_info_table()
            return

        prev_state = str(getattr(self, "trace_draw_session_state", "idle") or "idle").strip().lower()
        if hasattr(self, "_set_trace_draw_session_state"):
            try:
                self._set_trace_draw_session_state("postprocess")
            except Exception:
                pass

        success = False
        self._ensure_trace_layer_schema_and_form(layer)
        if not layer.isEditable():
            try:
                layer.startEditing()
            except Exception:
                self._end_trace_feature_postprocess(token, False)
                if hasattr(self, "_set_trace_draw_session_state"):
                    self._set_trace_draw_session_state("idle")
                return

        try:
            # Fast skip path for duplicated provider-side re-emissions on save/commit.
            existing_trace_id = ""
            existing_created_at = ""
            idx_trace_id = layer.fields().indexOf("trace_id")
            idx_created_at = layer.fields().indexOf("created_at")
            try:
                feat_existing = layer.getFeature(fid)
                if feat_existing is not None and feat_existing.isValid():
                    if idx_trace_id >= 0:
                        existing_trace_id = str(feat_existing.attribute(idx_trace_id) or "").strip()
                    if idx_created_at >= 0:
                        existing_created_at = str(feat_existing.attribute(idx_created_at) or "").strip()
            except Exception:
                existing_trace_id = ""
                existing_created_at = ""

            _inflight, done_keys, prompted_keys, prompted_trace_ids, done_trace_ids = self._trace_postprocess_sets()
            if existing_trace_id and (existing_trace_id in done_trace_ids or existing_trace_id in prompted_trace_ids):
                done_keys.add(key)
                success = True
                self.refresh_trace_info_table()
                return
            if existing_trace_id and existing_created_at:
                # Feature already enriched once (typical temp-fid -> provider-fid save transition).
                done_keys.add(key)
                prompted_keys.add(key)
                done_trace_ids.add(existing_trace_id)
                prompted_trace_ids.add(existing_trace_id)
                success = True
                self.refresh_trace_info_table()
                return

            rec = None
            payload = None
            if isinstance(self.trace_capture_context, dict):
                rec = self.trace_capture_context.get("timeslice")
                payload = self.trace_capture_context.get("payload")

            feat_geom = None
            try:
                feat = layer.getFeature(fid)
                if feat is not None and feat.isValid():
                    feat_geom = feat.geometry()
            except Exception:
                feat_geom = None

            # Prefer true click-time contexts when available (one snapshot per vertex click).
            try:
                verts = self._iter_geometry_vertices_xy(feat_geom)
                used_rows = self._consume_pending_click_contexts(len(verts))
                if used_rows:
                    store = self._trace_vertex_context_store()
                    ctx_key = self._trace_vertex_context_key(layer_id, fid)
                    store[ctx_key] = used_rows
            except Exception:
                pass

            metadata = self._build_trace_metadata_from_geometry(
                feat_geom,
                rec,
                payload,
                layer_id=layer_id,
                fid=fid,
            )
            trace_id = f"tr_{fid}_{utc_now_iso()}".replace(":", "").replace("+", "_")

            self._set_feature_attr(layer, fid, "trace_id", trace_id)
            self._set_feature_attr(layer, fid, "ts_id", metadata.get("ts_id"))
            self._set_feature_attr(layer, fid, "ts_name", metadata.get("ts_name"))
            self._set_feature_attr(layer, fid, "group_name", metadata.get("group_name"))
            self._set_feature_attr(layer, fid, "depth_list", metadata.get("depth_list"))
            self._set_feature_attr(layer, fid, "depth_from", metadata.get("depth_from"))
            self._set_feature_attr(layer, fid, "depth_to", metadata.get("depth_to"))
            self._set_feature_attr(layer, fid, "depth_unit", metadata.get("depth_unit"))
            self._set_feature_attr(layer, fid, "z_source", metadata.get("z_source"))
            self._set_feature_attr(layer, fid, "z_grid_path", metadata.get("z_grid_path"))
            self._set_feature_attr(layer, fid, "z_mode", metadata.get("z_mode"))
            self._set_feature_attr(layer, fid, "z_value", metadata.get("z_value"))
            self._set_feature_attr(layer, fid, "vertex_depths", metadata.get("vertex_depths"))
            self._set_feature_attr(layer, fid, "created_at", utc_now_iso())

            # Prompt (optional) only once per newly processed feature.
            _, _, prompted_keys, prompted_trace_ids, done_trace_ids = self._trace_postprocess_sets()
            should_prompt = key not in prompted_keys and trace_id not in prompted_trace_ids
            prompted_keys.add(key)
            if trace_id:
                prompted_trace_ids.add(trace_id)
                done_trace_ids.add(trace_id)
            # Critical rule: never prompt while save/commit transition is in progress.
            # Allow popup also when draw state is not strictly "drawing_active"
            # (e.g. external/QGIS-driven digitize sessions), but keep it disabled
            # for commit/save transitions to avoid re-appearing on Save.
            can_prompt_in_state = prev_state != "saving"
            if (
                should_prompt
                and can_prompt_in_state
                and bool(getattr(self, "trace_prompt_interpretation_popup", False))
            ):
                self._prompt_trace_interpretation_fields(layer, fid)

            layer.triggerRepaint()
            self._sync_trace_vertex_depth_labels(layer)
            self.refresh_trace_info_table()
            success = True
        finally:
            self._end_trace_feature_postprocess(token, success)
            if hasattr(self, "_set_trace_draw_session_state"):
                try:
                    # Restore state deterministically after postprocess.
                    if prev_state in ("idle", "drawing_active", "saving", "postprocess"):
                        if prev_state == "postprocess":
                            prev_state = "drawing_active" if bool(layer and layer.isEditable()) else "idle"
                        self._set_trace_draw_session_state(prev_state)
                    else:
                        self._set_trace_draw_session_state("drawing_active" if bool(layer and layer.isEditable()) else "idle")
                except Exception:
                    pass
