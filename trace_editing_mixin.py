# -*- coding: utf-8 -*-
"""Trace editing/capture mixin for GeoSurvey Studio plugin."""

import time

from qgis.PyQt.QtWidgets import QMessageBox, QInputDialog
from qgis.core import QgsProject, QgsVectorLayer

from .layer_property_utils import set_layer_property


class TraceEditingMixin:
    def _set_trace_draw_session_state(self, state):
        state_txt = str(state or "idle").strip().lower()
        allowed = {"idle", "drawing_active", "saving", "postprocess"}
        if state_txt not in allowed:
            state_txt = "idle"
        self.trace_draw_session_state = state_txt

    def _set_trace_draw_state_from_layer(self, layer):
        is_editing = bool(layer is not None and getattr(layer, "isEditable", lambda: False)())
        self._set_trace_draw_session_state("drawing_active" if is_editing else "idle")

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
            set_layer_property(layer, "storage_mode", "memory")

        QgsProject.instance().addMapLayer(layer, False)
        if hasattr(self, "_add_layer_to_trace_group_top"):
            self._add_layer_to_trace_group_top(layer)
        else:
            self._get_or_create_trace_group().addLayer(layer)
        self._set_active_trace_layer(layer)
        if hasattr(self, "_reset_trace_postprocess_cache"):
            try:
                self._reset_trace_postprocess_cache(layer.id())
            except Exception:
                pass
        self._set_trace_draw_session_state("idle")
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

    def _collect_trace_sync_targets_pre_commit(self, layer):
        """Collect trace IDs/FIDs likely affected by upcoming commit."""
        fids = set()
        trace_ids = set()
        if layer is None:
            return fids, trace_ids
        try:
            edit_buffer = layer.editBuffer()
        except Exception:
            edit_buffer = None
        if edit_buffer is None:
            return fids, trace_ids

        try:
            for raw_fid in (edit_buffer.changedGeometries() or {}).keys():
                try:
                    fid = int(raw_fid)
                except Exception:
                    continue
                if fid >= 0:
                    fids.add(fid)
        except Exception:
            pass

        try:
            for raw_fid in (edit_buffer.changedAttributeValues() or {}).keys():
                try:
                    fid = int(raw_fid)
                except Exception:
                    continue
                if fid >= 0:
                    fids.add(fid)
        except Exception:
            pass

        idx_trace_id = -1
        try:
            idx_trace_id = layer.fields().indexOf("trace_id")
        except Exception:
            idx_trace_id = -1

        try:
            for raw_fid, feat in (edit_buffer.addedFeatures() or {}).items():
                try:
                    fid = int(raw_fid)
                except Exception:
                    fid = -1
                if fid >= 0:
                    fids.add(fid)
                if idx_trace_id >= 0 and feat is not None:
                    try:
                        tid = str(feat.attribute(idx_trace_id) or "").strip()
                    except Exception:
                        tid = ""
                    if tid:
                        trace_ids.add(tid)
        except Exception:
            pass

        return fids, trace_ids

    def _resolve_trace_sync_fids_post_commit(self, layer, pre_fids, pre_trace_ids):
        """Resolve final provider FIDs to sync after commit completed."""
        resolved = set()
        if layer is None:
            return resolved

        # Existing provider FIDs that remained stable.
        for raw_fid in (pre_fids or set()):
            try:
                fid = int(raw_fid)
            except Exception:
                continue
            if fid < 0:
                continue
            try:
                feat = layer.getFeature(fid)
                if feat is not None and feat.isValid():
                    resolved.add(fid)
            except Exception:
                continue

        # Newly committed features from temporary IDs: match by trace_id.
        trace_ids = {str(t).strip() for t in (pre_trace_ids or set()) if str(t).strip()}
        if trace_ids:
            idx_trace_id = layer.fields().indexOf("trace_id")
            if idx_trace_id >= 0:
                for feat in layer.getFeatures():
                    try:
                        tid = str(feat.attribute(idx_trace_id) or "").strip()
                    except Exception:
                        tid = ""
                    if not tid or tid not in trace_ids:
                        continue
                    try:
                        resolved.add(int(feat.id()))
                    except Exception:
                        continue
        return resolved

    def _sync_vertex_layer_after_commit_targets(self, layer, sync_fids):
        """Sync derived vertex layer for changed traces only."""
        if layer is None or not hasattr(self, "_sync_trace_vertex_depth_labels"):
            return 0
        if not sync_fids:
            return 0
        t0 = time.perf_counter()
        try:
            self._sync_trace_vertex_depth_labels(
                layer,
                create_if_missing=False,
                only_fids=sorted(int(fid) for fid in sync_fids),
            )
        except Exception:
            return -1
        return int((time.perf_counter() - t0) * 1000)

    def start_trace_capture(self, checked=None):
        toggle_on = True if checked is None else bool(checked)
        layer = self._ensure_trace_layer_for_capture()
        if layer is None:
            self._set_trace_draw_session_state("idle")
            self._set_draw_action_checked(False)
            return
        self._set_active_trace_layer(layer)

        # QGIS-like toggle: same pencil turns editing ON/OFF.
        if not toggle_on:
            if layer.isEditable():
                self.stop_trace_layer_editing()
            if hasattr(self, "_set_trace_canvas_click_capture_enabled"):
                self._set_trace_canvas_click_capture_enabled(False)
            self._set_trace_draw_session_state("idle")
            self._sync_draw_action_checked_for_layer(layer)
            return

        if not layer.isEditable():
            try:
                layer.startEditing()
            except Exception:
                QMessageBox.warning(self._ui_parent(), "Draw 2D Line", "Unable to start editing on target layer.")
                self._set_trace_draw_session_state("idle")
                self._set_draw_action_checked(False)
                return

        if hasattr(self, "_reset_trace_postprocess_cache"):
            try:
                self._reset_trace_postprocess_cache(layer.id())
            except Exception:
                pass

        self._sync_draw_action_checked_for_layer(layer)
        self._set_trace_draw_session_state("drawing_active")
        rec, payload = self._active_timeslice_record()
        if rec is not None and not self._confirm_missing_z_for_capture(rec):
            if hasattr(self, "_set_trace_canvas_click_capture_enabled"):
                self._set_trace_canvas_click_capture_enabled(False)
            self._set_draw_action_checked(False)
            self._set_trace_draw_session_state("idle")
            return
        if hasattr(self, "_sync_qgis_group_visibility_with_selection"):
            try:
                self._sync_qgis_group_visibility_with_selection()
            except Exception:
                pass
        if hasattr(self, "_get_or_create_trace_group"):
            try:
                trace_group = self._get_or_create_trace_group()
                if trace_group is not None and hasattr(trace_group, "setItemVisibilityChecked"):
                    trace_group.setItemVisibilityChecked(True)
            except Exception:
                pass
        self.trace_capture_context = {"timeslice": rec, "payload": payload}
        if hasattr(self, "_set_trace_canvas_click_capture_enabled"):
            self._set_trace_canvas_click_capture_enabled(True)
        if rec is None:
            self._notify_info(
                "No active time-slice selected. The new line will have empty time-slice metadata.",
                duration=6,
            )
        if not self._trigger_iface_action("actionAddFeature"):
            QMessageBox.warning(self._ui_parent(), "Draw 2D Line", "Unable to activate Add Feature tool.")
            if hasattr(self, "_set_trace_canvas_click_capture_enabled"):
                self._set_trace_canvas_click_capture_enabled(False)
            self._set_trace_draw_session_state("idle")
            self._set_draw_action_checked(False)
            return
        self._notify_info("Digitize line on canvas (right-click to finish).", duration=5)

    def save_trace_layer_edits(self, checked=False):
        t0 = time.perf_counter()
        layer = self._current_trace_layer(prefer_active=True, require_trace=True)
        if layer is None:
            layer = self._select_line_layer_dialog(require_trace=True)
        if layer is None:
            return
        self._set_active_trace_layer(layer)

        if not layer.isEditable():
            self._set_trace_draw_state_from_layer(layer)
            self._notify_info("Layer is not in edit mode; nothing to save.", duration=5)
            return

        try:
            modified = layer.isModified()
        except Exception:
            modified = True
        if not modified:
            self._set_trace_draw_state_from_layer(layer)
            self._notify_info("No pending edits to save.", duration=4)
            return

        self._set_trace_draw_session_state("saving")
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
            self._set_trace_draw_state_from_layer(layer)
            return

        if not kept_editing:
            try:
                layer.startEditing()
                kept_editing = True
            except Exception:
                kept_editing = False

        self._notify_info(
            "Trace edits saved."
            + (" Editing session is still active." if kept_editing else "")
            + f" ({int((time.perf_counter() - t0) * 1000)} ms)",
            duration=5,
        )
        self._set_trace_draw_session_state("drawing_active" if kept_editing else "idle")
        if self.trace_info_dock is not None and self.trace_info_dock.isVisible():
            self.refresh_trace_info_table()

    def stop_trace_layer_editing(self, checked=False):
        t0 = time.perf_counter()
        layer = self._current_trace_layer(prefer_active=True, require_trace=True)
        if layer is None:
            layer = self._select_line_layer_dialog(require_trace=True)
        if layer is None:
            self._set_draw_action_checked(False)
            self._set_trace_draw_session_state("idle")
            return False
        self._set_active_trace_layer(layer)

        if not layer.isEditable():
            self._notify_info("Layer is not in edit mode.", duration=4)
            self._set_draw_action_checked(False)
            self._set_trace_draw_session_state("idle")
            return False

        try:
            modified = bool(layer.isModified())
        except Exception:
            modified = True

        if not modified:
            closed = False
            try:
                # Fast path after explicit Save: just close edit mode.
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
                self._set_trace_draw_state_from_layer(layer)
                self._sync_draw_action_checked_for_layer(layer)
                return False
            self._notify_info(f"Editing stopped. ({int((time.perf_counter() - t0) * 1000)} ms)", duration=4)
            if self.trace_info_dock is not None and self.trace_info_dock.isVisible():
                self.refresh_trace_info_table()
            self._set_draw_action_checked(False)
            self._set_trace_draw_session_state("idle")
            if hasattr(self, "_set_trace_canvas_click_capture_enabled"):
                self._set_trace_canvas_click_capture_enabled(False)
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
            self._set_trace_draw_state_from_layer(layer)
            return False

        if clicked == save_btn:
            pre_sync_fids, pre_sync_trace_ids = self._collect_trace_sync_targets_pre_commit(layer)
            self._set_trace_draw_session_state("saving")
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
                self._set_trace_draw_state_from_layer(layer)
                return False
            # Issue #20: keep vertex layer optional and sync only changed traces.
            resolved_sync_fids = self._resolve_trace_sync_fids_post_commit(
                layer,
                pre_sync_fids,
                pre_sync_trace_ids,
            )
            sync_ms = self._sync_vertex_layer_after_commit_targets(layer, resolved_sync_fids)
            sync_note = ""
            if sync_ms > 0:
                sync_note = f" Vertex sync: {len(resolved_sync_fids)} trace(s) in {sync_ms} ms."
            elif sync_ms < 0:
                sync_note = " Vertex sync skipped due to an update error."
            self._notify_info(
                f"Edits saved and editing stopped. ({int((time.perf_counter() - t0) * 1000)} ms){sync_note}",
                duration=5,
            )
            if self.trace_info_dock is not None and self.trace_info_dock.isVisible():
                self.refresh_trace_info_table()
            self._set_draw_action_checked(False)
            self._set_trace_draw_session_state("idle")
            if hasattr(self, "_set_trace_canvas_click_capture_enabled"):
                self._set_trace_canvas_click_capture_enabled(False)
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
            self._set_trace_draw_state_from_layer(layer)
            return False
        self._notify_info(
            f"Edits discarded and editing stopped. ({int((time.perf_counter() - t0) * 1000)} ms)",
            duration=5,
        )
        if self.trace_info_dock is not None and self.trace_info_dock.isVisible():
            self.refresh_trace_info_table()
        self._set_draw_action_checked(False)
        self._set_trace_draw_session_state("idle")
        if hasattr(self, "_set_trace_canvas_click_capture_enabled"):
            self._set_trace_canvas_click_capture_enabled(False)
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

    def clean_orphan_traces(self, checked=False):
        t0 = time.perf_counter()
        layer = self._current_trace_layer(prefer_active=True, require_trace=True)
        if layer is None:
            layer = self._select_line_layer_dialog(require_trace=True)
        if layer is None:
            return
        self._set_active_trace_layer(layer)

        idx_ts = layer.fields().indexOf("ts_id")
        if idx_ts < 0:
            QMessageBox.warning(
                self._ui_parent(),
                "Clean Orphans",
                "The selected layer has no 'ts_id' field.",
            )
            return

        orphan_fids = []
        total = 0
        try:
            for feat in layer.getFeatures():
                total += 1
                ts_val = str(feat.attribute(idx_ts) or "").strip()
                if not ts_val:
                    orphan_fids.append(int(feat.id()))
        except Exception as exc:
            QMessageBox.warning(
                self._ui_parent(),
                "Clean Orphans",
                f"Unable to scan layer features.\n{exc}",
            )
            return

        if not orphan_fids:
            self._notify_info("No orphan traces found (all features have ts_id).", duration=4)
            return

        answer = QMessageBox.question(
            self._ui_parent(),
            "Clean Orphans",
            (
                f"Found {len(orphan_fids)} orphan trace(s) out of {total} feature(s).\n"
                "Orphan = empty time-slice id (ts_id).\n\n"
                "Delete these features now?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        was_editing = bool(layer.isEditable())
        started_here = False
        if not was_editing:
            try:
                started_here = bool(layer.startEditing())
            except Exception:
                started_here = False
            if not started_here:
                QMessageBox.warning(
                    self._ui_parent(),
                    "Clean Orphans",
                    "Unable to start editing on the selected layer.",
                )
                return

        deleted_count = 0
        try:
            if layer.deleteFeatures(orphan_fids):
                deleted_count = len(orphan_fids)
            else:
                for fid in orphan_fids:
                    try:
                        if layer.deleteFeature(fid):
                            deleted_count += 1
                    except Exception:
                        continue
        except Exception:
            for fid in orphan_fids:
                try:
                    if layer.deleteFeature(fid):
                        deleted_count += 1
                except Exception:
                    continue

        if started_here:
            if deleted_count > 0:
                ok = False
                try:
                    ok = bool(layer.commitChanges())
                except Exception:
                    ok = False
                if not ok:
                    err_text = ""
                    try:
                        errs = layer.commitErrors() or []
                        err_text = "\n".join(str(e) for e in errs if e)
                    except Exception:
                        err_text = ""
                    QMessageBox.warning(
                        self._ui_parent(),
                        "Clean Orphans",
                        "Unable to save orphan cleanup changes."
                        + (f"\n{err_text}" if err_text else ""),
                    )
                    self._set_trace_draw_state_from_layer(layer)
                    self._sync_draw_action_checked_for_layer(layer)
                    return
            else:
                try:
                    layer.rollBack()
                except Exception:
                    pass
        else:
            try:
                layer.triggerRepaint()
            except Exception:
                pass

        if hasattr(self, "_sync_trace_vertex_depth_labels"):
            try:
                # Update existing vertex layer if present, do not auto-create it.
                self._sync_trace_vertex_depth_labels(layer, create_if_missing=False)
            except Exception:
                pass

        if self.trace_info_dock is not None and self.trace_info_dock.isVisible():
            self.refresh_trace_info_table()

        if was_editing:
            self._set_trace_draw_session_state("drawing_active")
        else:
            self._set_trace_draw_session_state("idle")
        self._sync_draw_action_checked_for_layer(layer)

        self._notify_info(
            f"Cleaned orphan traces: {deleted_count}/{len(orphan_fids)} deleted. "
            f"({int((time.perf_counter() - t0) * 1000)} ms)",
            duration=6,
        )

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

    def generate_trace_vertex_layer(self, checked=False):
        layer = self._current_trace_layer(prefer_active=True, require_trace=True)
        if layer is None:
            layer = self._select_line_layer_dialog(require_trace=True)
        if layer is None:
            return
        self._set_active_trace_layer(layer)

        if not hasattr(self, "_sync_trace_vertex_depth_labels"):
            QMessageBox.warning(
                self._ui_parent(),
                "Generate Vertices",
                "Vertex layer generation is not available in this build.",
            )
            return

        try:
            self._sync_trace_vertex_depth_labels(layer, create_if_missing=True)
            self.refresh_trace_info_table()
            self._notify_info(
                f"Vertex layer generated/updated for '{layer.name()}'.",
                duration=5,
            )
        except Exception as exc:
            QMessageBox.warning(
                self._ui_parent(),
                "Generate Vertices",
                f"Unable to generate vertex layer.\n{exc}",
            )
