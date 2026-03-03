# -*- coding: utf-8 -*-
"""Trace capture helpers for GeoSurvey Studio plugin."""

import json
import math
import os
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
    QgsCoordinateTransform,
    QgsEditorWidgetSetup,
    QgsField,
    QgsMessageLog,
    Qgis,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRaster,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsFeature,
    QgsGeometry,
    QgsPolygon,
)
from PyQt5.QtWidgets import QCheckBox

from .project_catalog import load_catalog, utc_now_iso


class TraceCaptureCoreMixin:
    def _init_trace_capture(self):
        """Initialise trace capture state. Called by the plugin constructor."""
        self.trace_line_layer_id = None
        self.trace_connected_layer_ids = set()
        self.trace_capture_context = None
        self.trace_vertex_context_capture = {}
        self.trace_z_grid_cache = {}
        self.trace_missing_z_prompt_shown = False
        self.trace_allow_missing_z_for_session = False
        self.trace_prompt_interpretation_popup = False
        self.trace_interpretation_prompted_keys = set()
        self.trace_interpretation_prompted_trace_ids = set()
        self.trace_draw_session_state = "idle"
        self.trace_postprocess_inflight = set()
        self.trace_postprocess_done = set()
        self.trace_postprocess_done_trace_ids = set()
        self.trace_canvas_click_filter = None
        self.trace_canvas_click_capture_enabled = False
        self.trace_pending_vertex_clicks = []
        self.trace_canvas_wheel_modifier = "alt"
        self.trace_discard_outside_raster = False

    def _cleanup_trace_capture(self):
        """Teardown trace capture runtime state. Called by plugin unload()."""
        if self.trace_canvas_click_filter is not None:
            try:
                canvas = self.iface.mapCanvas()
                if canvas is not None:
                    if canvas.viewport() is not None:
                        canvas.viewport().removeEventFilter(self.trace_canvas_click_filter)
                    canvas.removeEventFilter(self.trace_canvas_click_filter)
            except Exception as e:
                QgsMessageLog.logMessage(str(e), "GeoSurvey Studio", Qgis.Warning)
        self.trace_canvas_click_filter = None
        self.trace_canvas_click_capture_enabled = False
        self.trace_pending_vertex_clicks = []
        self.trace_canvas_wheel_modifier = "alt"
        self.trace_interpretation_prompted_keys = set()
        self.trace_interpretation_prompted_trace_ids = set()
        self.trace_draw_session_state = "idle"
        self.trace_postprocess_inflight = set()
        self.trace_postprocess_done = set()
        self.trace_postprocess_done_trace_ids = set()
        self.trace_z_grid_cache = {}

    def _trace_debug_enabled(self):
        try:
            raw_local = getattr(self, "trace_debug_logging", None)
            if isinstance(raw_local, bool):
                return raw_local
            if raw_local is not None:
                txt_local = str(raw_local).strip().lower()
                if txt_local in ("1", "true", "yes", "on", "debug"):
                    return True
                if txt_local in ("0", "false", "no", "off", ""):
                    return False
        except Exception:
            pass
        try:
            raw = str(os.environ.get("GEOSURVEY_TRACE_DEBUG", "") or "").strip().lower()
            return raw in ("1", "true", "yes", "on", "debug")
        except Exception:
            return False

    def _trace_debug_log(self, message, level=Qgis.Info):
        if level != Qgis.Critical and not self._trace_debug_enabled():
            return
        try:
            QgsMessageLog.logMessage(str(message), "GeoSurvey Studio", level)
        except Exception:
            pass

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

    def _find_trace_group(self):
        """Return existing 'Line Traces' group if present, without creating anything."""
        try:
            plugin_root = self._find_plugin_root_group() if hasattr(self, "_find_plugin_root_group") else None
            if plugin_root is None:
                return None
            return next(
                (
                    g for g in plugin_root.children()
                    if isinstance(g, QgsLayerTreeGroup) and str(g.name() or "").strip() == "Line Traces"
                ),
                None,
            )
        except Exception:
            return None

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

    def _is_trace_like_line_layer(self, layer):
        if not self._is_line_layer(layer):
            return False
        try:
            if self._is_trace_layer(layer):
                return True
        except Exception:
            pass
        if hasattr(self, "_is_trace_related_line_layer"):
            try:
                if self._is_trace_related_line_layer(layer):
                    return True
            except Exception:
                pass
        try:
            lname = str(layer.name() or "").strip().lower()
        except Exception:
            lname = ""
        if lname.startswith("trace2d") or "trace" in lname:
            return True
        try:
            field_names = {f.name() for f in layer.fields()}
        except Exception:
            field_names = set()
        hints = {"trace_id", "ts_id", "ts_name", "z_mode", "depth_from", "depth_to"}
        return bool(field_names.intersection(hints))

    def _bootstrap_trace_layer_from_project(self):
        """
        Recover trace layer after plugin restart even when active layer/id is not set.
        Also keeps trace-like line layers grouped under "Line Traces" for consistency.
        """
        try:
            all_layers = list(QgsProject.instance().mapLayers().values())
        except Exception:
            all_layers = []
        if not all_layers:
            return None

        candidates = [lyr for lyr in all_layers if self._is_trace_like_line_layer(lyr)]
        if not candidates:
            return None

        valid = []
        for lyr in candidates:
            try:
                self._ensure_trace_layer_schema_and_form(lyr)
            except Exception:
                pass
            try:
                if self._is_trace_layer(lyr):
                    valid.append(lyr)
            except Exception:
                continue
        if not valid:
            return None

        # Move recognized trace layers under dedicated trace group (non-destructive in data).
        try:
            trace_group = self._get_or_create_trace_group()
            root = QgsProject.instance().layerTreeRoot()
        except Exception:
            trace_group = None
            root = None
        if trace_group is not None and root is not None:
            for lyr in valid:
                try:
                    node = root.findLayer(lyr.id())
                    if node is None:
                        continue
                    parent = node.parent()
                    if parent is trace_group:
                        continue
                    clone = node.clone()
                    trace_group.insertChildNode(0, clone)
                    if parent is not None:
                        parent.removeChildNode(node)
                except Exception:
                    continue

        # Pick stable preferred layer:
        # active line -> id-linked -> highest feature count.
        chosen = None
        try:
            active = self.iface.activeLayer()
            if active in valid:
                chosen = active
        except Exception:
            pass
        if chosen is None and self.trace_line_layer_id:
            try:
                by_id = QgsProject.instance().mapLayer(self.trace_line_layer_id)
                if by_id in valid:
                    chosen = by_id
            except Exception:
                pass
        if chosen is None:
            def _count(lyr):
                try:
                    return int(lyr.featureCount())
                except Exception:
                    return 0
            valid.sort(key=_count, reverse=True)
            chosen = valid[0]

        try:
            self.trace_line_layer_id = chosen.id()
        except Exception:
            pass
        try:
            self._connect_trace_layer_signals(chosen)
        except Exception:
            pass
        return chosen

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
                if require_trace and hasattr(self, "_is_trace_related_line_layer"):
                    try:
                        if self._is_trace_related_line_layer(active):
                            self._ensure_trace_layer_schema_and_form(active)
                            if self._is_trace_layer(active):
                                self.trace_line_layer_id = active.id()
                                return active
                    except Exception:
                        pass
        if self.trace_line_layer_id:
            layer = QgsProject.instance().mapLayer(self.trace_line_layer_id)
            if self._is_line_layer(layer):
                if not require_trace or self._is_trace_layer(layer):
                    return layer
                if require_trace and hasattr(self, "_is_trace_related_line_layer"):
                    try:
                        if self._is_trace_related_line_layer(layer):
                            self._ensure_trace_layer_schema_and_form(layer)
                            if self._is_trace_layer(layer):
                                self.trace_line_layer_id = layer.id()
                                return layer
                    except Exception:
                        pass

        # Fallback discovery from project on plugin restart.
        picked = self._bootstrap_trace_layer_from_project()
        if picked is not None:
            if not require_trace:
                return picked
            try:
                if self._is_trace_layer(picked):
                    return picked
            except Exception:
                pass
        return None

    def _select_line_layer_dialog(self, require_trace=False):
        layers = []
        for lyr in QgsProject.instance().mapLayers().values():
            if not self._is_line_layer(lyr):
                continue
            if require_trace and not self._is_trace_layer(lyr):
                promoted = False
                if hasattr(self, "_is_trace_related_line_layer"):
                    try:
                        if self._is_trace_related_line_layer(lyr):
                            self._ensure_trace_layer_schema_and_form(lyr)
                            promoted = self._is_trace_layer(lyr)
                    except Exception:
                        promoted = False
                if not promoted:
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

    def _trace_discard_outside_raster_enabled(self):
        raw_local = getattr(self, "trace_discard_outside_raster", False)
        if isinstance(raw_local, bool):
            return raw_local
        txt_local = str(raw_local).strip().lower()
        enabled = txt_local in ("1", "true", "yes", "on")
        self.trace_discard_outside_raster = bool(enabled)
        return bool(enabled)

    def _set_trace_discard_outside_raster_enabled(self, enabled, persist=True):
        state = bool(enabled)
        self.trace_discard_outside_raster = state
        act = getattr(self, "trace_info_discard_outside_raster_action", None)
        if act is not None and act.isChecked() != state:
            blocked = act.blockSignals(True)
            act.setChecked(state)
            act.blockSignals(blocked)
        if persist:
            if hasattr(self, "_save_trace_info_ui_state"):
                try:
                    self._save_trace_info_ui_state()
                    return
                except Exception:
                    pass
            settings = getattr(self, "settings", None)
            if settings is not None:
                try:
                    if hasattr(self, "_trace_info_settings_key"):
                        key = self._trace_info_settings_key("discard_outside_raster")
                    else:
                        key = "GeoSurveyStudio/trace_info/discard_outside_raster"
                    settings.setValue(key, "1" if state else "0")
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
        project_path = ""
        if isinstance(payload, dict):
            ts_id = str(payload.get("timeslice_id") or "").strip()
            group_name = str(payload.get("group_name") or "").strip()
            project_path = str(payload.get("project_path") or "").strip()
        if isinstance(rec, dict):
            ts_id = ts_id or str(rec.get("id") or "").strip()
            ts_name = str(rec.get("normalized_name") or rec.get("name") or "").strip()
            group_name = group_name or str(rec.get("group_name") or "").strip()
            project_path = project_path or str(rec.get("project_path") or "").strip()
        d0 = rec.get("depth_from") if isinstance(rec, dict) else None
        d1 = rec.get("depth_to") if isinstance(rec, dict) else None
        unit = (rec.get("unit") if isinstance(rec, dict) else "m") or "m"
        return {
            "timeslice_id": ts_id,
            "timeslice_name": ts_name,
            "group_name": group_name,
            "project_path": project_path,
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

