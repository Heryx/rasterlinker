# -*- coding: utf-8 -*-
"""Trace info panel mixin for RasterLinker plugin."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMessageBox, QDockWidget, QTableWidget, QTableWidgetItem, QAbstractItemView
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QComboBox,
    QToolButton,
    QHeaderView,
    QStackedWidget,
    QFormLayout,
    QLabel,
    QSizePolicy,
    QTabWidget,
)


class TraceInfoMixin:
    def _trace_info_settings_key(self, key):
        if hasattr(self, "_settings_key"):
            return self._settings_key(f"trace_info/{key}")
        return f"RasterLinker/trace_info/{key}"

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

        query_visible = bool(self.trace_info_query_panel is not None and self.trace_info_query_panel.isVisible())
        help_visible = bool(self.trace_info_help_panel is not None and self.trace_info_help_panel.isVisible())
        settings.setValue(self._trace_info_settings_key("query_visible"), query_visible)
        settings.setValue(self._trace_info_settings_key("help_visible"), help_visible)

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
        mode_filter = str(settings.value(self._trace_info_settings_key("mode_filter"), "all") or "all").strip()
        sort_field = str(settings.value(self._trace_info_settings_key("sort_field"), "fid") or "fid").strip()
        sort_order_txt = str(settings.value(self._trace_info_settings_key("sort_order"), "asc") or "asc").strip().lower()
        query_visible = settings.value(self._trace_info_settings_key("query_visible"), False, type=bool)
        help_visible = settings.value(self._trace_info_settings_key("help_visible"), False, type=bool)
        dock_floating = settings.value(self._trace_info_settings_key("dock/floating"), True, type=bool)
        dock_area_txt = str(settings.value(self._trace_info_settings_key("dock/area"), "right") or "right").strip().lower()

        if self.trace_info_filter_edit is not None:
            self.trace_info_filter_edit.setText(filter_text)
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

        self._toggle_trace_query_panel(bool(query_visible))
        self._toggle_trace_help_panel(bool(help_visible))
        self._set_trace_info_view_mode(view_mode, persist=False)

        if self.trace_info_dock is not None:
            area_map = {
                "left": Qt.LeftDockWidgetArea,
                "right": Qt.RightDockWidgetArea,
                "top": Qt.TopDockWidgetArea,
                "bottom": Qt.BottomDockWidgetArea,
            }
            target_area = area_map.get(dock_area_txt, Qt.RightDockWidgetArea)
            try:
                if dock_floating:
                    self.trace_info_dock.setFloating(True)
                else:
                    self._dock_trace_info_to(target_area)
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
        if use_form:
            self._update_trace_info_form_from_table_selection()
        if persist:
            self._save_trace_info_ui_state()

    def _on_trace_info_table_selection_changed(self):
        if self.trace_info_selection_guard:
            return
        row = None
        if self.trace_info_table is not None and self.trace_info_table.selectionModel() is not None:
            selected = self.trace_info_table.selectionModel().selectedRows()
            if selected:
                row = selected[0].row()
        if row is None:
            return
        self.trace_info_selection_guard = True
        try:
            if (
                self.trace_info_form_list is not None
                and row >= 0
                and row < self.trace_info_form_list.rowCount()
            ):
                self.trace_info_form_list.selectRow(row)
        finally:
            self.trace_info_selection_guard = False
        self._update_trace_info_form_from_table_selection()

    def _on_trace_info_form_list_selection_changed(self):
        if self.trace_info_selection_guard:
            return
        row = None
        if self.trace_info_form_list is not None and self.trace_info_form_list.selectionModel() is not None:
            selected = self.trace_info_form_list.selectionModel().selectedRows()
            if selected:
                row = selected[0].row()
        if row is None:
            return
        self.trace_info_selection_guard = True
        try:
            if self.trace_info_table is not None and row >= 0 and row < self.trace_info_table.rowCount():
                self.trace_info_table.selectRow(row)
        finally:
            self.trace_info_selection_guard = False
        self._update_trace_info_form_from_table_selection()

    def _select_trace_info_row(self, row_idx):
        if row_idx is None or row_idx < 0:
            return
        self.trace_info_selection_guard = True
        try:
            if self.trace_info_table is not None and row_idx < self.trace_info_table.rowCount():
                self.trace_info_table.selectRow(row_idx)
            if self.trace_info_form_list is not None and row_idx < self.trace_info_form_list.rowCount():
                self.trace_info_form_list.selectRow(row_idx)
        finally:
            self.trace_info_selection_guard = False
        self._update_trace_info_form_from_table_selection()

    def _on_trace_info_top_level_changed(self, is_floating):
        self.trace_info_is_docked = not bool(is_floating)
        self._save_trace_info_ui_state()

    def _dock_trace_info_to(self, area):
        if self.trace_info_dock is None:
            return
        try:
            self.iface.addDockWidget(area, self.trace_info_dock)
            self.trace_info_dock.setFloating(False)
            self.trace_info_dock.show()
            self.trace_info_dock.raise_()
            self.trace_info_is_docked = True
            self._save_trace_info_ui_state()
        except Exception:
            try:
                self.trace_info_dock.setFloating(True)
                self.trace_info_is_docked = False
                self._save_trace_info_ui_state()
            except Exception:
                pass

    def _undock_trace_info(self):
        if self.trace_info_dock is None:
            return
        try:
            self.trace_info_dock.setFloating(True)
            self.trace_info_dock.show()
            self.trace_info_dock.raise_()
            self.trace_info_is_docked = False
            self._save_trace_info_ui_state()
        except Exception:
            pass

    def _toggle_trace_query_panel(self, checked):
        visible = bool(checked)
        if self.trace_info_query_panel is not None:
            self.trace_info_query_panel.setVisible(visible)
        if self.trace_info_query_btn is not None:
            self.trace_info_query_btn.setChecked(visible)
        self._save_trace_info_ui_state()

    def _toggle_trace_help_panel(self, checked):
        visible = bool(checked)
        if self.trace_info_help_panel is not None:
            self.trace_info_help_panel.setVisible(visible)
        if self.trace_info_help_btn is not None and self.trace_info_help_btn.isChecked() != visible:
            self.trace_info_help_btn.setChecked(visible)
        self._save_trace_info_ui_state()

    def _show_build3d_modes_help(self):
        # Backward compatibility: if called, open the inline help panel.
        self._toggle_trace_help_panel(True)

    def _build_trace_help_panel(self, parent):
        help_tabs = QTabWidget(parent)
        help_tabs.setTabPosition(QTabWidget.North)
        help_tabs.setUsesScrollButtons(True)
        help_tabs.setElideMode(Qt.ElideRight)
        help_tabs.setDocumentMode(True)
        help_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        help_tabs.setMinimumHeight(124)
        help_tabs.setMaximumHeight(170)

        sections = (
            (
                "Quick Start",
                (
                    "1) Open/create a project in Project Manager.\n"
                    "2) Import time-slices and load the target group.\n"
                    "3) Open Line Info from the pencil icon.\n"
                    "4) Use Draw 2D Line to capture traces."
                ),
            ),
            (
                "Draw / Edit",
                (
                    "Pencil: start 2D trace drawing in active line layer.\n"
                    "Vertex Tool: move/add/remove vertices.\n"
                    "Delete: removes selected traces only.\n"
                    "New Line Layer: creates a clean editable trace layer."
                ),
            ),
            (
                "Filter / Query",
                (
                    "Search box filters by id, time-slice, z mode, length.\n"
                    "Funnel icon toggles advanced filter/sort controls.\n"
                    "Mode: All / Only Missing Z / Only With Z.\n"
                    "Sort by field and order (Asc/Desc)."
                ),
            ),
            (
                "Build 3D",
                (
                    "Build 3D: Constant Z or Linked z-grid.\n"
                    "Orthometric 3D: Z = DTM - depth for each vertex.\n"
                    "If Z data is missing, drawing can continue in missing_z mode.\n"
                    "Use form view to inspect row attributes quickly."
                ),
            ),
            (
                "Export",
                (
                    "Export Layer saves traces to GPKG/SHP.\n"
                    "Use Refresh after edits/imports.\n"
                    "Save edits to stabilize feature IDs.\n"
                    "Use dock menu to dock/undock this panel."
                ),
            ),
        )

        for title, text in sections:
            page = QWidget(help_tabs)
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(8, 6, 8, 6)
            page_layout.setSpacing(4)
            label = QLabel(text, page)
            label.setWordWrap(True)
            label.setTextFormat(Qt.PlainText)
            label.setStyleSheet("color: #505050;")
            page_layout.addWidget(label, 1)
            help_tabs.addTab(page, title)

        help_tabs.setVisible(False)
        return help_tabs

    def _update_trace_info_form_from_table_selection(self):
        if not self.trace_info_form_fields:
            return
        row_data = None
        selected_rows = []
        if self.trace_info_table is not None:
            row = None
            selected_rows = self.trace_info_table.selectionModel().selectedRows() if self.trace_info_table.selectionModel() else []
            if selected_rows:
                row = selected_rows[0].row()
            elif self.trace_info_table.rowCount() > 0 and self.trace_info_form_list is None:
                row = 0
            if row is not None and row >= 0:
                item0 = self.trace_info_table.item(row, 0)
                if item0 is not None:
                    payload = item0.data(Qt.UserRole)
                    if isinstance(payload, dict):
                        row_data = payload
                    if not selected_rows and self.trace_info_form_list is None:
                        self._select_trace_info_row(row)

        # In form view, allow picking from the left list even when table page is hidden.
        if row_data is None and self.trace_info_form_list is not None:
            form_selected_rows = (
                self.trace_info_form_list.selectionModel().selectedRows()
                if self.trace_info_form_list.selectionModel()
                else []
            )
            if form_selected_rows:
                item0 = self.trace_info_form_list.item(form_selected_rows[0].row(), 0)
                if item0 is not None:
                    payload = item0.data(Qt.UserRole)
                    if isinstance(payload, dict):
                        row_data = payload

        # Ensure one selected row when data exists.
        if row_data is None and self.trace_info_table is not None and self.trace_info_table.rowCount() > 0:
            self._select_trace_info_row(0)
            item0 = self.trace_info_table.item(0, 0)
            if item0 is not None:
                payload = item0.data(Qt.UserRole)
                if isinstance(payload, dict):
                    row_data = payload

        key_map = {
            "FID": "fid",
            "Trace ID": "trace_id",
            "Time-slice": "timeslice",
            "Depth": "depth_text",
            "Z mode": "z_mode",
            "Length": "length_text",
            "Group": "group_name",
            "Z source": "z_source",
            "Z grid path": "z_grid_path",
        }
        for label, widget in self.trace_info_form_fields.items():
            value = ""
            if isinstance(row_data, dict):
                value = row_data.get(key_map.get(label, ""), "")
            widget.setText("" if value is None else str(value))

    def _ensure_trace_info_dock(self):
        if self.trace_info_dock is not None and self.trace_info_table is not None:
            return
        self._ensure_trace_actions()

        main_window = self.iface.mainWindow()
        dock = QDockWidget("RasterLinker Line Info", main_window)
        dock.setObjectName("RasterLinkerTraceInfoDock")
        dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        dock.setAllowedAreas(
            Qt.LeftDockWidgetArea
            | Qt.RightDockWidgetArea
            | Qt.BottomDockWidgetArea
            | Qt.TopDockWidgetArea
        )
        dock.setMinimumWidth(300)
        dock.setMinimumHeight(220)
        dock.setToolTip("Drag the title bar to dock this panel on left/right/top/bottom.")
        try:
            dock.topLevelChanged.connect(self._on_trace_info_top_level_changed)
        except Exception:
            pass

        container = QWidget(dock)
        main_layout = QHBoxLayout(container)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        left_widget = QWidget(container)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)
        filter_edit = QLineEdit(left_widget)
        filter_edit.setPlaceholderText("Filter traces...")
        top_row.addWidget(filter_edit, 1)

        table_view_btn = QToolButton(left_widget)
        table_view_btn.clicked.connect(lambda: self._set_trace_info_view_mode("table"))
        table_view_btn.setToolTip("Switch to table view")
        table_view_btn.setAutoRaise(True)

        form_view_btn = QToolButton(left_widget)
        form_view_btn.clicked.connect(lambda: self._set_trace_info_view_mode("form"))
        form_view_btn.setToolTip("Switch to form view")
        form_view_btn.setAutoRaise(True)

        icon_table = self._qgis_theme_icon("mActionOpenTable.svg", "mActionTable.svg")
        if icon_table is not None and not icon_table.isNull():
            table_view_btn.setIcon(icon_table)
        else:
            table_view_btn.setText("Tbl")
        icon_form = self._qgis_theme_icon("mActionFormView.svg", "mActionOptions.svg")
        if icon_form is not None and not icon_form.isNull():
            form_view_btn.setIcon(icon_form)
        else:
            form_view_btn.setText("Frm")

        query_btn = QToolButton(left_widget)
        query_btn.setAutoRaise(True)
        query_btn.setCheckable(True)
        query_btn.setChecked(False)
        query_btn.setToolTip("Toggle advanced query/sort controls")
        query_btn.toggled.connect(self._toggle_trace_query_panel)
        icon_query = self._qgis_theme_icon("mActionFilter2.svg", "mActionFilterExpression.svg", "mActionFilter.svg")
        if icon_query is not None and not icon_query.isNull():
            query_btn.setIcon(icon_query)
        else:
            query_btn.setText("?")
        top_row.addWidget(query_btn, 0)

        refresh_btn = QToolButton(left_widget)
        refresh_btn.clicked.connect(self.refresh_trace_info_table)
        refresh_btn.setAutoRaise(True)
        refresh_btn.setToolTip("Refresh")
        icon_refresh = self._qgis_theme_icon("mActionRefresh.svg", "mActionReload.svg")
        if icon_refresh is not None and not icon_refresh.isNull():
            refresh_btn.setIcon(icon_refresh)
        else:
            refresh_btn.setText("Ref")
        top_row.addWidget(refresh_btn, 0)

        help_btn = QToolButton(left_widget)
        help_btn.setCheckable(True)
        help_btn.setChecked(False)
        help_btn.toggled.connect(self._toggle_trace_help_panel)
        help_btn.setAutoRaise(True)
        help_btn.setToolTip("Show/hide help")
        icon_help = self._qgis_theme_icon("mActionHelpContents.svg", "mActionHelp.svg", "mIconInfo.svg")
        if icon_help is not None and not icon_help.isNull():
            help_btn.setIcon(icon_help)
        else:
            help_btn.setText("?")
        top_row.addWidget(help_btn, 0)
        left_layout.addLayout(top_row)

        tools_row_widget = self._build_trace_info_tools_panel(left_widget)
        left_layout.addWidget(tools_row_widget, 0)

        query_panel = QWidget(left_widget)
        query_row = QHBoxLayout(query_panel)
        query_row.setContentsMargins(0, 0, 0, 0)
        query_row.setSpacing(6)

        mode_combo = QComboBox(query_panel)
        mode_combo.addItem("All", "all")
        mode_combo.addItem("Only Missing Z", "missing_z")
        mode_combo.addItem("Only With Z", "with_z")
        mode_combo.setMinimumWidth(110)
        mode_combo.setMaximumWidth(150)
        query_row.addWidget(mode_combo, 0)

        sort_field_combo = QComboBox(query_panel)
        sort_field_combo.addItem("Sort: FID", "fid")
        sort_field_combo.addItem("Sort: Trace ID", "trace_id")
        sort_field_combo.addItem("Sort: Time-slice", "timeslice")
        sort_field_combo.addItem("Sort: Depth", "depth")
        sort_field_combo.addItem("Sort: Z mode", "z_mode")
        sort_field_combo.addItem("Sort: Length", "length")
        sort_field_combo.setMinimumWidth(120)
        sort_field_combo.setMaximumWidth(180)
        query_row.addWidget(sort_field_combo, 0)

        sort_order_combo = QComboBox(query_panel)
        sort_order_combo.addItem("Asc", Qt.AscendingOrder)
        sort_order_combo.addItem("Desc", Qt.DescendingOrder)
        sort_order_combo.setMinimumWidth(70)
        sort_order_combo.setMaximumWidth(90)
        query_row.addWidget(sort_order_combo, 0)
        query_row.addStretch(1)
        query_panel.setVisible(False)
        left_layout.addWidget(query_panel, 0)

        help_panel = self._build_trace_help_panel(left_widget)
        left_layout.addWidget(help_panel, 0)

        stack = QStackedWidget(left_widget)

        table_page = QWidget(stack)
        table_page_layout = QVBoxLayout(table_page)
        table_page_layout.setContentsMargins(0, 0, 0, 0)
        table_page_layout.setSpacing(0)

        table = QTableWidget(0, 6, table_page)
        table.setHorizontalHeaderLabels(["FID", "Trace ID", "Time-slice", "Depth", "Z mode", "Length"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideRight)
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # FID
        table.setColumnWidth(1, 170)  # Trace ID
        table.setColumnWidth(2, 240)  # Time-slice
        table.setColumnWidth(3, 90)   # Depth
        table.setColumnWidth(4, 90)   # Z mode
        table.setColumnWidth(5, 80)   # Length
        table_page_layout.addWidget(table, 1)
        stack.addWidget(table_page)

        form_page = QWidget(stack)
        form_page_layout = QHBoxLayout(form_page)
        form_page_layout.setContentsMargins(0, 0, 0, 0)
        form_page_layout.setSpacing(6)

        form_list = QTableWidget(0, 2, form_page)
        form_list.setHorizontalHeaderLabels(["FID", "Trace ID"])
        form_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        form_list.setSelectionMode(QAbstractItemView.SingleSelection)
        form_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        form_list.setWordWrap(False)
        form_list.setTextElideMode(Qt.ElideRight)
        form_list_header = form_list.horizontalHeader()
        form_list_header.setStretchLastSection(False)
        form_list_header.setSectionResizeMode(QHeaderView.Interactive)
        form_list_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        form_list.setColumnWidth(1, 180)
        form_list.setMinimumWidth(240)
        form_list.setMaximumWidth(330)
        form_page_layout.addWidget(form_list, 0)

        form_right_widget = QWidget(form_page)
        form_layout = QFormLayout(form_right_widget)
        form_layout.setContentsMargins(4, 8, 8, 8)
        form_layout.setSpacing(8)
        form_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form_fields = {}
        for label_text in (
            "FID",
            "Trace ID",
            "Time-slice",
            "Depth",
            "Z mode",
            "Length",
            "Group",
            "Z source",
            "Z grid path",
        ):
            field = QLineEdit(form_right_widget)
            field.setReadOnly(True)
            form_layout.addRow(f"{label_text}:", field)
            form_fields[label_text] = field
        form_page_layout.addWidget(form_right_widget, 1)
        stack.addWidget(form_page)
        left_layout.addWidget(stack, 1)

        view_switch_row = QHBoxLayout()
        view_switch_row.setContentsMargins(0, 0, 0, 0)
        view_switch_row.setSpacing(4)
        view_switch_row.addStretch(1)
        view_switch_row.addWidget(table_view_btn, 0)
        view_switch_row.addWidget(form_view_btn, 0)
        left_layout.addLayout(view_switch_row, 0)

        filter_edit.returnPressed.connect(self.refresh_trace_info_table)
        filter_edit.textChanged.connect(self._save_trace_info_ui_state)
        mode_combo.currentIndexChanged.connect(self.refresh_trace_info_table)
        mode_combo.currentIndexChanged.connect(self._save_trace_info_ui_state)
        sort_field_combo.currentIndexChanged.connect(self.refresh_trace_info_table)
        sort_field_combo.currentIndexChanged.connect(self._save_trace_info_ui_state)
        sort_order_combo.currentIndexChanged.connect(self.refresh_trace_info_table)
        sort_order_combo.currentIndexChanged.connect(self._save_trace_info_ui_state)
        table.itemSelectionChanged.connect(self._on_trace_info_table_selection_changed)
        form_list.itemSelectionChanged.connect(self._on_trace_info_form_list_selection_changed)

        main_layout.addWidget(left_widget, 1)

        dock.setWidget(container)
        # Start fully detached: do not call addDockWidget until user explicitly docks.
        dock.setFloating(True)
        dock.resize(700, 460)
        try:
            p = main_window.pos()
            dock.move(p.x() + 120, p.y() + 120)
        except Exception:
            pass

        self.trace_info_dock = dock
        self.trace_info_is_docked = False
        self.trace_info_table = table
        self.trace_info_filter_edit = filter_edit
        self.trace_info_mode_combo = mode_combo
        self.trace_info_sort_field_combo = sort_field_combo
        self.trace_info_sort_order_combo = sort_order_combo
        self.trace_info_stack = stack
        self.trace_info_form_list = form_list
        self.trace_info_form_fields = form_fields
        self.trace_info_view_table_btn = table_view_btn
        self.trace_info_view_form_btn = form_view_btn
        self.trace_info_query_btn = query_btn
        self.trace_info_query_panel = query_panel
        self.trace_info_help_btn = help_btn
        self.trace_info_help_panel = help_panel
        self._set_trace_info_view_mode("table", persist=False)
        self._apply_trace_info_ui_state()

    def open_trace_info_tab(self, checked=False):
        self._ensure_trace_info_dock()
        if self.trace_info_dock is not None:
            self.trace_info_dock.show()
            self.trace_info_dock.raise_()
            self.trace_info_dock.activateWindow()
        self.refresh_trace_info_table()

    def refresh_trace_info_table(self, checked=False):
        if self.trace_info_table is None:
            return
        layer = self._current_trace_layer(prefer_active=True, require_trace=False)
        selected_fid = None
        if self.trace_info_table.selectionModel() is not None:
            selected_rows = self.trace_info_table.selectionModel().selectedRows()
            if selected_rows:
                row = selected_rows[0].row()
                item0 = self.trace_info_table.item(row, 0)
                if item0 is not None:
                    payload = item0.data(Qt.UserRole)
                    if isinstance(payload, dict):
                        selected_fid = payload.get("fid")
        self.trace_info_table.setRowCount(0)
        if self.trace_info_form_list is not None:
            self.trace_info_form_list.setRowCount(0)
        if not self._is_line_layer(layer):
            self._update_trace_info_form_from_table_selection()
            return

        filter_text = ""
        if self.trace_info_filter_edit is not None:
            filter_text = (self.trace_info_filter_edit.text() or "").strip().lower()

        mode_filter = "all"
        if self.trace_info_mode_combo is not None:
            mode_filter = self.trace_info_mode_combo.currentData() or "all"

        sort_field = "fid"
        if self.trace_info_sort_field_combo is not None:
            sort_field = self.trace_info_sort_field_combo.currentData() or "fid"

        sort_desc = False
        if self.trace_info_sort_order_combo is not None:
            sort_desc = self.trace_info_sort_order_combo.currentData() == Qt.DescendingOrder

        rows = []
        for feat in layer.getFeatures():
            fid = feat.id()
            trace_id = feat.attribute("trace_id") if layer.fields().indexOf("trace_id") >= 0 else ""
            ts_name = feat.attribute("ts_name") if layer.fields().indexOf("ts_name") >= 0 else ""
            ts_id = feat.attribute("ts_id") if layer.fields().indexOf("ts_id") >= 0 else ""
            group_name = feat.attribute("group_name") if layer.fields().indexOf("group_name") >= 0 else ""
            depth_from = feat.attribute("depth_from") if layer.fields().indexOf("depth_from") >= 0 else None
            depth_to = feat.attribute("depth_to") if layer.fields().indexOf("depth_to") >= 0 else None
            depth_unit = feat.attribute("depth_unit") if layer.fields().indexOf("depth_unit") >= 0 else "m"
            z_source = feat.attribute("z_source") if layer.fields().indexOf("z_source") >= 0 else ""
            z_grid_path = feat.attribute("z_grid_path") if layer.fields().indexOf("z_grid_path") >= 0 else ""
            z_mode = feat.attribute("z_mode") if layer.fields().indexOf("z_mode") >= 0 else ""
            depth_txt = ""
            depth_num = None
            try:
                if depth_from not in (None, "") and depth_to not in (None, ""):
                    depth_txt = f"{float(depth_from):.3f}-{float(depth_to):.3f} {depth_unit}"
                    depth_num = (float(depth_from) + float(depth_to)) / 2.0
                elif depth_from not in (None, ""):
                    depth_txt = f"from {float(depth_from):.3f} {depth_unit}"
                    depth_num = float(depth_from)
                elif depth_to not in (None, ""):
                    depth_txt = f"to {float(depth_to):.3f} {depth_unit}"
                    depth_num = float(depth_to)
            except Exception:
                depth_txt = str(depth_from or depth_to or "")
            length_val = feat.geometry().length() if feat.geometry() is not None else 0.0
            ts_label = ts_name or ts_id or ""

            z_mode_text = str(z_mode or "")
            missing_z = z_mode_text.lower().startswith("missing")
            if mode_filter == "missing_z" and not missing_z:
                continue
            if mode_filter == "with_z" and missing_z:
                continue

            trace_text = trace_id or f"fid_{fid}"
            if filter_text:
                hay = f"{fid} {trace_text} {ts_label} {depth_txt} {z_mode_text} {length_val:.2f}".lower()
                if filter_text not in hay:
                    continue

            rows.append(
                {
                    "fid": fid,
                    "trace_id": trace_text,
                    "timeslice": ts_label,
                    "depth_text": depth_txt,
                    "depth_num": depth_num,
                    "z_mode": z_mode_text,
                    "length_num": float(length_val),
                    "length_text": f"{float(length_val):.2f}",
                    "group_name": group_name or "",
                    "z_source": z_source or "",
                    "z_grid_path": z_grid_path or "",
                }
            )

        if sort_field in ("fid", "depth", "length"):
            value_key = {"fid": "fid", "depth": "depth_num", "length": "length_num"}[sort_field]
            with_val = [r for r in rows if r.get(value_key) is not None]
            without_val = [r for r in rows if r.get(value_key) is None]
            with_val.sort(key=lambda r: r.get(value_key), reverse=sort_desc)
            rows = with_val + without_val
        else:
            value_key = {"trace_id": "trace_id", "timeslice": "timeslice", "z_mode": "z_mode"}.get(sort_field, "trace_id")
            rows.sort(key=lambda r: str(r.get(value_key) or "").lower(), reverse=sort_desc)

        self.trace_info_table.setRowCount(len(rows))
        if self.trace_info_form_list is not None:
            self.trace_info_form_list.setRowCount(len(rows))
        for row_idx, row_data in enumerate(rows):
            row_vals = (
                row_data.get("fid"),
                row_data.get("trace_id"),
                row_data.get("timeslice"),
                row_data.get("depth_text"),
                row_data.get("z_mode"),
                f"{row_data.get('length_num', 0.0):.2f}",
            )
            for col_idx, val in enumerate(row_vals):
                item = QTableWidgetItem(str(val))
                if col_idx == 0:
                    item.setData(Qt.UserRole, row_data)
                self.trace_info_table.setItem(row_idx, col_idx, item)
            if self.trace_info_form_list is not None:
                fid_item = QTableWidgetItem(str(row_data.get("fid")))
                fid_item.setData(Qt.UserRole, row_data)
                trace_item = QTableWidgetItem(str(row_data.get("trace_id")))
                self.trace_info_form_list.setItem(row_idx, 0, fid_item)
                self.trace_info_form_list.setItem(row_idx, 1, trace_item)

        target_row = 0
        if selected_fid is not None:
            for idx, row_data in enumerate(rows):
                if row_data.get("fid") == selected_fid:
                    target_row = idx
                    break
        if rows:
            self._select_trace_info_row(target_row)
        else:
            self._update_trace_info_form_from_table_selection()
