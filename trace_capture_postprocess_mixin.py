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


class TraceCapturePostprocessMixin:
    def _build_trace_metadata_from_geometry(
        self,
        geometry,
        fallback_rec=None,
        fallback_payload=None,
        layer_id=None,
        fid=None,
    ):
        depth_pick_mode = self._trace_depth_pick_mode()
        source_crs = self._trace_source_crs(layer_id)
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
                captured_ctx = {
                    "layer": None,
                    "group_name": str(crow.get("group_name") or ""),
                    "timeslice_id": str(crow.get("timeslice_id") or ""),
                    "timeslice_name": str(crow.get("timeslice_name") or ""),
                    "project_path": str(crow.get("project_path") or ""),
                    "rec": c_rec,
                    "depth_from": crow.get("depth_from"),
                    "depth_to": crow.get("depth_to"),
                    "depth_unit": crow.get("depth_unit") or "m",
                }
                # Backlog local Issue 10:
                # even when a click context exists, assign depth metadata only if
                # the corresponding raster has a valid pixel hit at this vertex.
                captured_layer = self._context_raster_layer(captured_ctx)
                if self._raster_point_has_pixel_hit_with_crs(captured_layer, point_xy, source_crs=source_crs):
                    if captured_layer is not None:
                        captured_ctx["layer"] = captured_layer
                    vertex_candidates = [captured_ctx]
                else:
                    # Fallback: try visible contexts at this vertex position.
                    vertex_candidates = self._vertex_context_candidates(point_xy, contexts, source_crs=source_crs)
            else:
                vertex_candidates = self._vertex_context_candidates(point_xy, contexts, source_crs=source_crs)
            try:
                x_txt = f"{float(point_xy.x()):.3f}" if point_xy is not None else "nan"
                y_txt = f"{float(point_xy.y()):.3f}" if point_xy is not None else "nan"
                cand_names = [str((c or {}).get("timeslice_name") or (c or {}).get("timeslice_id") or "").strip() for c in (vertex_candidates or [])]
                cand_names = [c for c in cand_names if c]
                self._trace_debug_log(
                    f"Trace metadata debug [fid={fid}] vertex#{idx} at ({x_txt}, {y_txt}) -> hits={len(vertex_candidates)} {cand_names}"
                )
            except Exception:
                pass
            vertex_depth_pairs = []
            vertex_modes = []
            vertex_units = []
            vertex_ts_names = []
            vertex_ts_ids = []
            for ctx in vertex_candidates:
                rec = ctx.get("rec") if isinstance(ctx, dict) else None
                ts_id = str((ctx or {}).get("timeslice_id") or "").strip()
                ts_name = str((ctx or {}).get("timeslice_name") or "").strip()
                group_name = str((ctx or {}).get("group_name") or "").strip()
                if ts_id and ts_id not in touched_ids:
                    touched_ids.append(ts_id)
                if ts_name and ts_name not in touched_names:
                    touched_names.append(ts_name)
                if group_name and group_name not in touched_groups:
                    touched_groups.append(group_name)
                if isinstance(rec, dict) and rec not in touched_records:
                    touched_records.append(rec)
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
                v_status = "hit"
            else:
                v_min = None
                v_max = None
                v_mid = None
                v_status = "no_raster_hit"

            vertex_rows.append(
                {
                    "i": idx,
                    "d": v_mid,
                    "dmin": v_min,
                    "dmax": v_max,
                    "u": depth_unit,
                    "s": v_status,
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
        try:
            self._trace_debug_log(
                f"Trace metadata summary [fid={fid}] ts='{ts_name_text}' depth='{depth_list_text}' z_mode='{z_mode}' z_value={z_value}"
            )
        except Exception:
            pass

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
        self._trace_debug_log(f"Trace postprocess start: layer_id={layer_id}, fid={fid}")
        layer = QgsProject.instance().mapLayer(layer_id)
        if not self._is_trace_layer(layer):
            self._trace_debug_log(f"Trace postprocess skipped: layer not trace-compatible (layer_id={layer_id})")
            return
        token, key = self._begin_trace_feature_postprocess(layer_id, fid)
        if token is None:
            # Duplicate signal for same feature, ignore side-effects.
            self._trace_debug_log(
                f"Trace postprocess skipped: duplicate/inflight key={key} (layer_id={layer_id}, fid={fid})"
            )
            self.refresh_trace_info_table()
            return

        prev_state = str(getattr(self, "trace_draw_session_state", "idle") or "idle").strip().lower()
        if hasattr(self, "_set_trace_draw_session_state"):
            try:
                self._set_trace_draw_session_state("postprocess")
            except Exception:
                pass

        success = False
        try:
            self._ensure_trace_layer_schema_and_form(layer)
        except Exception as exc:
            self._trace_debug_log(
                f"Trace postprocess aborted: schema/form setup failed (layer_id={layer_id}, fid={fid}, error={exc})",
                Qgis.Critical,
            )
            self._end_trace_feature_postprocess(token, False)
            if hasattr(self, "_set_trace_draw_session_state"):
                self._set_trace_draw_session_state("idle")
            return
        if not layer.isEditable():
            try:
                layer.startEditing()
            except Exception as exc:
                self._trace_debug_log(
                    f"Trace postprocess aborted: unable to start editing (layer_id={layer_id}, fid={fid}, error={exc})",
                    Qgis.Critical,
                )
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
                self._trace_debug_log(
                    f"Trace postprocess skipped: trace_id already done/prompted ({existing_trace_id})"
                )
                done_keys.add(key)
                success = True
                self.refresh_trace_info_table()
                return
            if existing_trace_id and existing_created_at:
                # Feature already enriched once (typical temp-fid -> provider-fid save transition).
                self._trace_debug_log(
                    f"Trace postprocess skipped: feature already enriched trace_id={existing_trace_id} created_at={existing_created_at}"
                )
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
            has_raster_hit = bool(str(metadata.get("ts_id") or "").strip())
            discard_outside = self._trace_discard_outside_raster_enabled()
            self._trace_debug_log(
                "Trace raster-hit policy "
                f"[fid={fid}] has_hit={has_raster_hit} discard_outside={discard_outside} "
                f"ts='{metadata.get('ts_name')}' z_mode='{metadata.get('z_mode')}'"
            )

            # Optional strict mode: discard traces fully outside valid raster pixels.
            # Default behavior keeps them with missing-z metadata.
            if (not has_raster_hit) and discard_outside:
                try:
                    layer.deleteFeature(fid)
                except Exception:
                    pass
                try:
                    if hasattr(self, "_notify_info"):
                        self._notify_info(
                            "Trace discarded: no raster pixel hit detected (Discard outside raster is ON).",
                            duration=5,
                        )
                except Exception:
                    pass
                self._trace_debug_log(
                    f"Trace discarded: no raster pixel hit detected (layer_id={layer_id}, fid={fid})",
                    Qgis.Warning,
                )
                self.refresh_trace_info_table()
                success = True
                return
            if not has_raster_hit:
                self._trace_debug_log(
                    f"Trace kept without raster-hit metadata (layer_id={layer_id}, fid={fid})",
                    Qgis.Warning,
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
            self._trace_debug_log(
                "Trace metadata summary "
                f"[fid={fid}] ts='{metadata.get('ts_name')}' depth='{metadata.get('depth_list')}' "
                f"z_mode='{metadata.get('z_mode')}' z_value={metadata.get('z_value')}"
            )

            # Prompt (optional) only once per newly processed feature.
            _, _, prompted_keys, prompted_trace_ids, done_trace_ids = self._trace_postprocess_sets()
            should_prompt = key not in prompted_keys and trace_id not in prompted_trace_ids
            prompted_keys.add(key)
            if trace_id:
                prompted_trace_ids.add(trace_id)
                done_trace_ids.add(trace_id)
            # Prompt once per new feature when enabled.
            # We intentionally do not block on "saving" state because some providers
            # can emit featureAdded in save/commit transitions.
            if should_prompt and bool(getattr(self, "trace_prompt_interpretation_popup", False)):
                self._prompt_trace_interpretation_fields(layer, fid)

            layer.triggerRepaint()
            # Issue #20: do not auto-create vertex layer during line capture.
            # Only refresh it if it already exists.
            self._sync_trace_vertex_depth_labels(layer, create_if_missing=False, only_fids=[fid])
            self.refresh_trace_info_table()
            success = True
        except Exception as exc:
            self._trace_debug_log(
                f"Trace postprocess exception: layer_id={layer_id}, fid={fid}, error={exc}",
                Qgis.Critical,
            )
        finally:
            self._end_trace_feature_postprocess(token, success)
            self._trace_debug_log(
                f"Trace postprocess end: layer_id={layer_id}, fid={fid}, success={success}"
            )
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
