# -*- coding: utf-8 -*-
"""State/persistence helpers for the Trace Info panel."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QHeaderView


class TraceInfoStateMixin:
    def _set_combo_current_data(self, combo, value):
        if combo is None:
            return
        idx = combo.findData(value)
        if idx >= 0 and combo.currentIndex() != idx:
            combo.setCurrentIndex(idx)

    def _trace_info_payload_from_table_row(self, row):
        if self.trace_info_table is None:
            return None
        model = self.trace_info_table.model()
        if model is None:
            return None
        if row < 0 or row >= model.rowCount():
            return None
        index = model.index(row, 0)
        if not index.isValid():
            return None
        payload = model.data(index, Qt.UserRole)
        if isinstance(payload, dict):
            return payload
        return None

    def _trace_info_settings_key(self, key):
        if hasattr(self, "_settings_key"):
            return self._settings_key(f"trace_info/{key}")
        return f"GeoSurveyStudio/trace_info/{key}"

    def _save_trace_info_ui_state(self):
        settings = getattr(self, "settings", None)
        if settings is None:
            return
        view_mode = "table"
        if self.trace_info_stack is not None and self.trace_info_stack.currentIndex() == 1:
            view_mode = "form"
        settings.setValue(self._trace_info_settings_key("view_mode"), view_mode)

        filter_text = ""
        if self.trace_info_filter_edit is not None:
            filter_text = self.trace_info_filter_edit.text() or ""
        settings.setValue(self._trace_info_settings_key("filter_text"), filter_text)

        filter_field = "all"
        if self.trace_info_filter_field_combo is not None:
            filter_field = self.trace_info_filter_field_combo.currentData() or "all"
        settings.setValue(self._trace_info_settings_key("filter_field"), filter_field)

        mode_filter = "all"
        if self.trace_info_mode_combo is not None:
            mode_filter = self.trace_info_mode_combo.currentData() or "all"
        settings.setValue(self._trace_info_settings_key("mode_filter"), mode_filter)

        sort_field = "fid"
        if self.trace_info_sort_field_combo is not None:
            sort_field = self.trace_info_sort_field_combo.currentData() or "fid"
        settings.setValue(self._trace_info_settings_key("sort_field"), sort_field)

        sort_order = "asc"
        if self.trace_info_sort_order_combo is not None:
            sort_order = "desc" if self.trace_info_sort_order_combo.currentData() == Qt.DescendingOrder else "asc"
        settings.setValue(self._trace_info_settings_key("sort_order"), sort_order)

        form_preview_key = str(getattr(self, "trace_info_form_preview_key", "timeslice") or "timeslice")
        settings.setValue(self._trace_info_settings_key("form_preview_key"), form_preview_key)

        query_visible = bool(self.trace_info_query_panel is not None and self.trace_info_query_panel.isVisible())
        help_visible = bool(self.trace_info_help_panel is not None and self.trace_info_help_panel.isVisible())
        settings.setValue(self._trace_info_settings_key("query_visible"), query_visible)
        settings.setValue(self._trace_info_settings_key("help_visible"), help_visible)

        selected_fid = ""
        selected_trace_id = ""
        if self.trace_info_table is not None and self.trace_info_table.selectionModel() is not None:
            selected_rows = self.trace_info_table.selectionModel().selectedRows()
            if selected_rows:
                payload = self._trace_info_payload_from_table_row(selected_rows[0].row())
                if isinstance(payload, dict):
                    fid_val = payload.get("fid")
                    trace_id_val = payload.get("trace_id")
                    selected_fid = "" if fid_val in (None, "") else str(fid_val)
                    selected_trace_id = "" if trace_id_val in (None, "") else str(trace_id_val)
        settings.setValue(self._trace_info_settings_key("selected_fid"), selected_fid)
        settings.setValue(self._trace_info_settings_key("selected_trace_id"), selected_trace_id)

        if self.trace_info_dock is not None:
            try:
                is_floating = bool(self.trace_info_dock.isFloating())
                settings.setValue(self._trace_info_settings_key("dock/floating"), is_floating)
                if not is_floating:
                    area = self.iface.mainWindow().dockWidgetArea(self.trace_info_dock)
                    area_text = {
                        Qt.LeftDockWidgetArea: "left",
                        Qt.RightDockWidgetArea: "right",
                        Qt.TopDockWidgetArea: "top",
                        Qt.BottomDockWidgetArea: "bottom",
                    }.get(area, "right")
                    settings.setValue(self._trace_info_settings_key("dock/area"), area_text)
            except Exception:
                pass

    def _apply_trace_info_ui_state(self):
        settings = getattr(self, "settings", None)
        if settings is None:
            return

        view_mode = str(settings.value(self._trace_info_settings_key("view_mode"), "table") or "table").strip().lower()
        filter_text = str(settings.value(self._trace_info_settings_key("filter_text"), "") or "")
        filter_field = str(settings.value(self._trace_info_settings_key("filter_field"), "all") or "all").strip()
        mode_filter = str(settings.value(self._trace_info_settings_key("mode_filter"), "all") or "all").strip()
        sort_field = str(settings.value(self._trace_info_settings_key("sort_field"), "fid") or "fid").strip()
        sort_order_txt = str(settings.value(self._trace_info_settings_key("sort_order"), "asc") or "asc").strip().lower()
        form_preview_key = str(
            settings.value(self._trace_info_settings_key("form_preview_key"), "timeslice") or "timeslice"
        ).strip().lower()
        # Legacy flag kept for backward compatibility; query controls are now menu-based.
        _query_visible = settings.value(self._trace_info_settings_key("query_visible"), False, type=bool)
        help_visible = settings.value(self._trace_info_settings_key("help_visible"), False, type=bool)
        saved_selected_fid = str(settings.value(self._trace_info_settings_key("selected_fid"), "") or "").strip()
        saved_selected_trace_id = str(
            settings.value(self._trace_info_settings_key("selected_trace_id"), "") or ""
        ).strip()
        dock_floating = settings.value(self._trace_info_settings_key("dock/floating"), True, type=bool)
        dock_area_txt = str(settings.value(self._trace_info_settings_key("dock/area"), "right") or "right").strip().lower()
        # Safety default: do not auto-dock on open to avoid breaking QGIS layout.
        restore_dock_on_open = settings.value(
            self._trace_info_settings_key("dock/restore_on_open"),
            False,
            type=bool,
        )

        if self.trace_info_filter_edit is not None:
            self.trace_info_filter_edit.setText(filter_text)
        if self.trace_info_filter_field_combo is not None:
            idx = self.trace_info_filter_field_combo.findData(filter_field)
            if idx >= 0:
                self.trace_info_filter_field_combo.setCurrentIndex(idx)
        if self.trace_info_mode_combo is not None:
            idx = self.trace_info_mode_combo.findData(mode_filter)
            if idx >= 0:
                self.trace_info_mode_combo.setCurrentIndex(idx)
        if self.trace_info_sort_field_combo is not None:
            idx = self.trace_info_sort_field_combo.findData(sort_field)
            if idx >= 0:
                self.trace_info_sort_field_combo.setCurrentIndex(idx)
        if self.trace_info_sort_order_combo is not None:
            order_value = Qt.DescendingOrder if sort_order_txt == "desc" else Qt.AscendingOrder
            idx = self.trace_info_sort_order_combo.findData(order_value)
            if idx >= 0:
                self.trace_info_sort_order_combo.setCurrentIndex(idx)

        self._toggle_trace_query_panel(False)
        self._toggle_trace_help_panel(bool(help_visible))
        self._set_trace_info_view_mode(view_mode, persist=False)
        self._set_trace_info_form_preview_column(form_preview_key, persist=False)
        try:
            self.trace_info_saved_selected_fid = int(saved_selected_fid) if saved_selected_fid else None
        except Exception:
            self.trace_info_saved_selected_fid = None
        self.trace_info_saved_selected_trace_id = saved_selected_trace_id

        if self.trace_info_dock is not None:
            area_map = {
                "left": Qt.LeftDockWidgetArea,
                "right": Qt.RightDockWidgetArea,
                "top": Qt.TopDockWidgetArea,
                "bottom": Qt.BottomDockWidgetArea,
            }
            target_area = area_map.get(dock_area_txt, Qt.RightDockWidgetArea)
            try:
                if restore_dock_on_open and not dock_floating:
                    self._dock_trace_info_to(target_area)
                else:
                    self.trace_info_dock.setFloating(True)
                    self.trace_info_is_docked = False
            except Exception:
                pass

    def _set_trace_info_view_mode(self, mode, persist=True):
        if self.trace_info_stack is None:
            return
        use_form = str(mode).strip().lower() == "form"
        self.trace_info_stack.setCurrentIndex(1 if use_form else 0)
        if self.trace_info_view_form_btn is not None:
            self.trace_info_view_form_btn.setEnabled(not use_form)
        if self.trace_info_view_table_btn is not None:
            self.trace_info_view_table_btn.setEnabled(use_form)
        if self.trace_info_form_preview_combo is not None:
            self.trace_info_form_preview_combo.setEnabled(use_form)
        if use_form:
            self._update_trace_info_form_from_table_selection()
        if persist:
            self._save_trace_info_ui_state()

    def _set_trace_info_form_preview_column(self, key, persist=True):
        key_txt = str(key or "timeslice").strip().lower()
        allowed = ("timeslice", "depth", "z_mode", "length")
        if key_txt not in allowed:
            key_txt = "timeslice"
        self.trace_info_form_preview_key = key_txt

        col_map = {
            "timeslice": 2,
            "depth": 3,
            "z_mode": 4,
            "length": 5,
        }
        target_col = col_map.get(key_txt, 2)
        if self.trace_info_form_list is not None:
            for col in range(6):
                self.trace_info_form_list.setColumnHidden(col, col != target_col)
            try:
                hdr = self.trace_info_form_list.horizontalHeader()
                if hdr is not None:
                    hdr.setStretchLastSection(True)
                    hdr.setSectionResizeMode(target_col, QHeaderView.Stretch)
            except Exception:
                pass

        if self.trace_info_form_preview_combo is not None:
            idx = self.trace_info_form_preview_combo.findData(key_txt)
            if idx >= 0 and self.trace_info_form_preview_combo.currentIndex() != idx:
                blocked = self.trace_info_form_preview_combo.blockSignals(True)
                self.trace_info_form_preview_combo.setCurrentIndex(idx)
                self.trace_info_form_preview_combo.blockSignals(blocked)

        if persist:
            self._save_trace_info_ui_state()
