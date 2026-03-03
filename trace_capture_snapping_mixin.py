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


class TraceCaptureSnappingMixin:
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
                    "project_path": project_path,
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
                    "project_path": project_path,
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
            project_path = str(row.get("project_path") or "").strip()
            rec = row.get("rec") if isinstance(row.get("rec"), dict) else None
            if not project_path and isinstance(rec, dict):
                project_path = str(rec.get("project_path") or "").strip()
            layer = self._loaded_plugin_raster_layer_by_path(project_path) if project_path else None
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
                    "layer": layer,
                    "group_name": group_name,
                    "timeslice_id": ts_id,
                    "timeslice_name": ts_name,
                    "project_path": project_path,
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
                        "project_path": str(rec.get("project_path") or "").strip() if isinstance(rec, dict) else str(layer.source() or "").split("|", 1)[0].strip(),
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

    def _context_raster_layer(self, ctx):
        if not isinstance(ctx, dict):
            return None
        layer = ctx.get("layer")
        if isinstance(layer, QgsRasterLayer) and layer.isValid():
            return layer
        project_path = str(ctx.get("project_path") or "").strip()
        rec = ctx.get("rec") if isinstance(ctx.get("rec"), dict) else None
        if not project_path and isinstance(rec, dict):
            project_path = str(rec.get("project_path") or "").strip()
        if not project_path:
            return None
        return self._loaded_plugin_raster_layer_by_path(project_path)

    def _point_in_raster_grid(self, layer, point_xy):
        """True only when world point maps inside raster pixel grid.

        This is stricter than extent().contains() and correctly handles rotated rasters.
        """
        if layer is None or not isinstance(layer, QgsRasterLayer) or not layer.isValid() or point_xy is None:
            return False

        provider = None
        try:
            provider = layer.dataProvider()
        except Exception:
            provider = None
        if provider is None:
            return False

        # Try affine transform based check first (works for rotated grids).
        try:
            gt = provider.geoTransform()
        except Exception:
            gt = None
        try:
            if gt and len(gt) >= 6:
                g0, g1, g2, g3, g4, g5 = [float(v) for v in list(gt)[:6]]
                det = (g1 * g5) - (g2 * g4)
                if abs(det) > 1e-18:
                    dx = float(point_xy.x()) - g0
                    dy = float(point_xy.y()) - g3
                    col = ((dx * g5) - (dy * g2)) / det
                    row = ((dy * g1) - (dx * g4)) / det
                    try:
                        w = int(provider.xSize())
                        h = int(provider.ySize())
                    except Exception:
                        w = int(getattr(layer, "width", lambda: 0)() or 0)
                        h = int(getattr(layer, "height", lambda: 0)() or 0)
                    if w > 0 and h > 0:
                        inside_grid = (col >= 0.0) and (row >= 0.0) and (col < float(w)) and (row < float(h))
                        if not inside_grid:
                            return False
                        # Additional strict check against true raster footprint polygon.
                        # This prevents false positives caused by rotated rasters/bounding approximations.
                        def _xy(c, r):
                            return QgsPointXY(g0 + c * g1 + r * g2, g3 + c * g4 + r * g5)

                        p0 = _xy(0.0, 0.0)
                        p1 = _xy(float(w), 0.0)
                        p2 = _xy(float(w), float(h))
                        p3 = _xy(0.0, float(h))
                        ring = [p0, p1, p2, p3, p0]
                        footprint = QgsGeometry.fromPolygonXY([ring])
                        pt_geom = QgsGeometry.fromPointXY(point_xy)
                        try:
                            return bool(footprint.intersects(pt_geom))
                        except Exception:
                            return inside_grid
        except Exception:
            pass

        # Fallback: use provider identify (do NOT fallback to extent bbox,
        # which can produce false positives with rotated/no-data scenarios).
        try:
            ident = provider.identify(point_xy, QgsRaster.IdentifyFormatValue)
            if ident is None or not ident.isValid():
                return False
            results = ident.results()
            return bool(results)
        except Exception:
            return False

    def _trace_source_crs(self, layer_id=None):
        layer = None
        if layer_id:
            try:
                layer = QgsProject.instance().mapLayer(layer_id)
            except Exception:
                layer = None
        if layer is None and hasattr(self, "_current_trace_layer"):
            try:
                layer = self._current_trace_layer(prefer_active=True, require_trace=False)
            except Exception:
                layer = None
        try:
            crs = layer.crs() if layer is not None else None
            if crs is not None and crs.isValid():
                return crs
        except Exception:
            pass
        return None

    def _transform_point_to_layer_crs(self, point_xy, target_layer, source_crs=None):
        if point_xy is None or target_layer is None:
            return None
        try:
            target_crs = target_layer.crs()
            if target_crs is None or not target_crs.isValid():
                return point_xy
        except Exception:
            return point_xy
        if source_crs is None:
            source_crs = self._trace_source_crs()
        try:
            if source_crs is None or not source_crs.isValid():
                return point_xy
            if source_crs == target_crs:
                return point_xy
            xform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
            return xform.transform(point_xy)
        except Exception:
            return None

    def _raster_point_has_pixel_hit(self, layer, point_xy):
        return self._raster_point_has_pixel_hit_with_crs(layer, point_xy, source_crs=None)

    def _raster_point_has_pixel_hit_with_crs(self, layer, point_xy, source_crs=None):
        if layer is None or not isinstance(layer, QgsRasterLayer) or not layer.isValid() or point_xy is None:
            self._trace_debug_log("Trace hit-check: invalid layer/point -> FALSE")
            return False
        point_on_raster = self._transform_point_to_layer_crs(point_xy, layer, source_crs=source_crs)
        if point_on_raster is None:
            self._trace_debug_log(
                f"Trace hit-check: CRS transform failed for layer='{layer.name()}' -> FALSE"
            )
            return False
        try:
            inside_grid = bool(self._point_in_raster_grid(layer, point_on_raster))
            if not inside_grid:
                self._trace_debug_log(
                    f"Trace hit-check: outside raster grid layer='{layer.name()}' point=({point_on_raster.x():.3f},{point_on_raster.y():.3f}) -> FALSE"
                )
                return False
        except Exception:
            self._trace_debug_log(
                f"Trace hit-check: grid check exception layer='{layer.name()}' -> FALSE"
            )
            return False

        provider = None
        try:
            provider = layer.dataProvider()
        except Exception:
            provider = None
        if provider is None:
            self._trace_debug_log(
                f"Trace hit-check: missing provider layer='{layer.name()}' -> FALSE"
            )
            return False

        # Identify guard first: if provider says no valid identify at this point,
        # treat as no raster hit.
        try:
            ident = provider.identify(point_on_raster, QgsRaster.IdentifyFormatValue)
            if ident is None or not ident.isValid():
                self._trace_debug_log(
                    f"Trace hit-check: identify invalid layer='{layer.name()}' point=({point_on_raster.x():.3f},{point_on_raster.y():.3f}) -> FALSE"
                )
                return False
            ident_results = ident.results() or {}
            if not ident_results:
                self._trace_debug_log(
                    f"Trace hit-check: identify empty layer='{layer.name()}' point=({point_on_raster.x():.3f},{point_on_raster.y():.3f}) -> FALSE"
                )
                return False
        except Exception:
            self._trace_debug_log(
                f"Trace hit-check: identify exception layer='{layer.name()}' -> FALSE"
            )
            return False

        try:
            band_count = max(1, int(layer.bandCount()))
        except Exception:
            band_count = 1

        # If raster has an alpha band and alpha is 0 at point, treat as no hit.
        alpha_band = 0
        for b in range(1, band_count + 1):
            ci_name = ""
            try:
                ci_name = str(provider.colorInterpretationName(b) or "").strip().lower()
            except Exception:
                ci_name = ""
            if "alpha" in ci_name:
                alpha_band = b
                break
        if alpha_band > 0:
            try:
                a_sample = provider.sample(point_on_raster, alpha_band)
                a_ok = True
                a_val = a_sample
                if isinstance(a_sample, (tuple, list)):
                    if len(a_sample) >= 2:
                        a_val = a_sample[0]
                        a_ok = bool(a_sample[1])
                    elif len(a_sample) == 1:
                        a_val = a_sample[0]
                a_num = self._safe_float(a_val)
                if (not a_ok) or (a_num is None) or (float(a_num) <= 0.0):
                    self._trace_debug_log(
                        f"Trace hit-check: alpha=0/no-data layer='{layer.name()}' alpha_band={alpha_band} -> FALSE"
                    )
                    return False
            except Exception:
                # If alpha sampling fails, continue with normal checks.
                pass

        def _is_nodata_value(band_idx, value_num):
            # Source NoData
            try:
                if provider.sourceHasNoDataValue(band_idx):
                    nd = self._safe_float(provider.sourceNoDataValue(band_idx))
                    if nd is not None and abs(float(value_num) - float(nd)) <= 1e-12:
                        return True
            except Exception:
                pass

            # User NoData ranges (layer/provider-side)
            try:
                ranges = provider.userNoDataValues(band_idx)
            except Exception:
                ranges = None
            if ranges:
                for rng in ranges:
                    try:
                        lo = float(rng.min())
                        hi = float(rng.max())
                    except Exception:
                        continue
                    if lo <= float(value_num) <= hi:
                        return True
            return False

        for band in range(1, band_count + 1):
            try:
                sampled = provider.sample(point_on_raster, band)
            except Exception:
                continue

            ok = True
            value = sampled
            if isinstance(sampled, (tuple, list)):
                if len(sampled) >= 2:
                    value = sampled[0]
                    ok = bool(sampled[1])
                elif len(sampled) == 1:
                    value = sampled[0]
            if not ok:
                continue

            num = self._safe_float(value)
            if num is None:
                continue
            try:
                if math.isnan(float(num)):
                    continue
            except Exception:
                pass

            if _is_nodata_value(band, num):
                continue

            try:
                src_crs_txt = source_crs.authid() if source_crs is not None and source_crs.isValid() else "unknown"
            except Exception:
                src_crs_txt = "unknown"
            try:
                dst_crs_txt = layer.crs().authid() if layer.crs().isValid() else "unknown"
            except Exception:
                dst_crs_txt = "unknown"
            self._trace_debug_log(
                "Trace hit-check: TRUE "
                f"layer='{layer.name()}' band={band} value={float(num):.6f} "
                f"src=({point_xy.x():.3f},{point_xy.y():.3f})[{src_crs_txt}] "
                f"dst=({point_on_raster.x():.3f},{point_on_raster.y():.3f})[{dst_crs_txt}]"
            )
            return True
        self._trace_debug_log(
            f"Trace hit-check: all sampled values are NoData layer='{layer.name()}' -> FALSE"
        )
        return False

    def _vertex_context_candidates(self, point_xy, contexts, source_crs=None):
        # Issue 10 (local backlog): assign time-slice/depth only on true raster pixel hit.
        if not contexts or point_xy is None:
            return []
        hits = []
        attempted = 0
        attempted_labels = []
        for ctx in contexts or []:
            attempted += 1
            layer = self._context_raster_layer(ctx)
            ts_lbl = str((ctx or {}).get("timeslice_name") or (ctx or {}).get("timeslice_id") or "").strip()
            lyr_lbl = ""
            try:
                lyr_lbl = str(layer.name() or "")
            except Exception:
                lyr_lbl = ""
            if ts_lbl or lyr_lbl:
                attempted_labels.append(f"{ts_lbl or '?'}->{lyr_lbl or 'no_layer'}")
            hit = self._raster_point_has_pixel_hit_with_crs(layer, point_xy, source_crs=source_crs)
            if not hit:
                continue
            if layer is not None and (not isinstance(ctx.get("layer"), QgsRasterLayer) or not ctx.get("layer").isValid()):
                ctx = dict(ctx)
                ctx["layer"] = layer
            hits.append(ctx)
        if not hits and attempted > 0:
            try:
                src_txt = source_crs.authid() if source_crs is not None and source_crs.isValid() else "unknown"
            except Exception:
                src_txt = "unknown"
            try:
                x_txt = f"{float(point_xy.x()):.3f}"
                y_txt = f"{float(point_xy.y()):.3f}"
            except Exception:
                x_txt = "nan"
                y_txt = "nan"
            self._trace_debug_log(
                f"Trace metadata debug: no raster-hit for vertex at ({x_txt}, {y_txt}); src_crs={src_txt}; attempted={attempted}; contexts={attempted_labels}"
            )
        return hits

    def _serialize_vertex_depths(self, vertex_rows):
        clean = []
        for row in vertex_rows or []:
            depth_val = self._safe_float(row.get("d"))
            depth_min = self._safe_float(row.get("dmin"))
            depth_max = self._safe_float(row.get("dmax"))
            depth_status = str(row.get("s") or "").strip().lower()
            if depth_status not in ("hit", "no_raster_hit"):
                if depth_val is None and depth_min is None and depth_max is None:
                    depth_status = "no_raster_hit"
                else:
                    depth_status = "hit"
            clean.append(
                {
                    "i": int(row.get("i") or 0),
                    "d": depth_val,
                    "dmin": depth_min,
                    "dmax": depth_max,
                    "u": str(row.get("u") or "m"),
                    "s": depth_status,
                }
            )
        try:
            return json.dumps(clean, ensure_ascii=True, separators=(",", ":"))
        except Exception:
            return ""

