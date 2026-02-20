from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAbstractItemView, QDockWidget
from PyQt5.QtWidgets import QPushButton, QSizePolicy, QCheckBox

from .geosurvey_studio_dialog import GeoSurveyStudioDialog
from .project_manager_dialog import ProjectManagerDialog


class AppRuntimeMixin:
    def _split_setting_list(self, value):
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return [str(v).strip() for v in value if str(v).strip()]
        text = str(value).strip()
        if not text:
            return []
        return [v.strip() for v in text.split("||") if v.strip()]

    def run(self):
        """Esegue il plugin."""
        if self.first_start:
            self.first_start = False
            self.dlg = GeoSurveyStudioDialog()
            self.dlg.on_resized = self._on_dialog_resized
            self.dock_widget = QDockWidget(self.tr(u"GeoSurvey Studio"), self.iface.mainWindow())
            self.dock_widget.setObjectName("GeoSurveyStudioDockWidget")
            self.dock_widget.setFeatures(
                QDockWidget.DockWidgetMovable
                | QDockWidget.DockWidgetFloatable
                | QDockWidget.DockWidgetClosable
            )
            self.dock_widget.setWidget(self.dlg)
            self.dock_widget.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock_widget)
            self._tabify_with_existing_right_dock()
            self._connect_main_dock_persistence_signals()
            self._ensure_dialog_main_layout()
            self._init_name_raster_panel()
            self.populate_group_list()
            self.dlg.groupListWidget.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.dlg.createGridButton.setEnabled(True)
            self.dlg.selectGridPointsButton.setEnabled(True)
            self.dlg.selectGridPointsButton.setText("Set Orientation")
            self.dlg.selectGridPointsButton.setMinimumWidth(120)
            self.dlg.selectGridPointsButton.setToolTip(
                "Set grid orientation with 3 clicks: P0 (origin), X1 (X direction), Y1 (Y direction)."
            )
            if hasattr(self.dlg, "Ok"):
                self.dlg.Ok.hide()
            if hasattr(self.dlg, "openButton"):
                self.dlg.openButton.hide()
            self.dlg.lineEditDistanceX.setEnabled(True)
            self.dlg.lineEditDistanceY.setEnabled(True)
            self.dlg.lineEditAreaNames.setEnabled(True)
            self.dlg.lineEditX0Y0.setEnabled(True)
            self.dlg.lineEditX1Y0.setEnabled(True)
            self.dlg.lineEditY0.setEnabled(True)
            self.dlg.lineEditX0Y1.setEnabled(True)
            if self.internal_grid_checkbox is None:
                self.internal_grid_checkbox = QCheckBox("Internal grid", self.dlg)
                self.internal_grid_checkbox.setToolTip(
                    "If enabled, create internal cells using Cell X/Cell Y. "
                    "If disabled, create only the base area from x0/x1/y0/y1."
                )
                self.internal_grid_checkbox.toggled.connect(self._on_internal_grid_toggled)

            # Collega i segnali ai metodi
            self.dlg.createGroupButton.clicked.connect(self.create_group)
            #self.dlg.moveRasterButton.clicked.connect(self.move_rasters)
            #self.dlg.groupListWidget.itemClicked.connect(self.on_group_selected)
            self.dlg.groupListWidget.itemSelectionChanged.connect(self.on_group_selection_changed)
            if hasattr(self.dlg, "openButton"):
                self.dlg.openButton.clicked.connect(self.load_raster)
            self.dlg.selectGridPointsButton.clicked.connect(self.activate_grid_selection_tool)
            if hasattr(self.dlg, "moveRasterButton"):
                self.dlg.moveRasterButton.clicked.connect(self.move_rasters)

            self.dlg.createGridButton.clicked.connect(self.create_grid_from_polygon_layer)

            self.dlg.zoomSelectedGroupsButton.clicked.connect(self.zoom_to_selected_groups)
            self.dlg.zoomSelectedGroupsButton.setText("Zoom Groups")
            self.dlg.zoomSelectedGroupsButton.setToolTip(
                "Zoom map canvas to the extent of selected plugin groups."
            )
            self.dlg.zoomSelectedGroupsButton.setStatusTip("Zoom selected plugin groups")
            self.load_groups_button = QPushButton("Load Groups")
            self.load_groups_button.setToolTip("Load selected plugin groups into the QGIS layer tree.")
            self.load_groups_button.setStatusTip("Load selected plugin groups")
            self.load_groups_button.clicked.connect(lambda: self.load_raster(show_message=True))
            self.dlg.gridLayout.addWidget(self.load_groups_button, 8, 0, 1, 1)
            self.import_groups_button = QPushButton("Manage")
            self.import_groups_button.setToolTip("Open group manager to load/unload plugin groups.")
            self.import_groups_button.setStatusTip("Open group manager")
            self.import_groups_button.clicked.connect(self.open_group_import_dialog)
            self.dlg.gridLayout.addWidget(self.import_groups_button, 8, 1, 1, 1)
            self.enhance_minmax_button = QPushButton("Enhance Range")
            self.enhance_minmax_button.setToolTip("Apply Min/Max enhancement to loaded rasters.")
            self.enhance_minmax_button.setStatusTip("Apply min/max enhancement")
            self.enhance_minmax_button.clicked.connect(self.enhance_loaded_images_minmax)
            self.dlg.gridLayout.addWidget(self.enhance_minmax_button, 5, 0, 1, 1)
            self.enhance_batch_button = QPushButton("Batch Enhance")
            self.enhance_batch_button.setToolTip("Apply batch enhancement with selected method.")
            self.enhance_batch_button.setStatusTip("Apply batch enhancement")
            self.enhance_batch_button.clicked.connect(self.enhance_batch_options)
            self.dlg.gridLayout.addWidget(self.enhance_batch_button, 5, 1, 1, 1)
            self.save_style_button = QPushButton("Save Style")
            self.save_style_button.setToolTip("Save style for active group.")
            self.save_style_button.setStatusTip("Save style for active group")
            self.save_style_button.clicked.connect(self.save_selected_group_style)
            self.dlg.gridLayout.addWidget(self.save_style_button, 6, 0, 1, 1)
            self.load_style_button = QPushButton("Load Style")
            self.load_style_button.setToolTip("Load saved style for active group.")
            self.load_style_button.setStatusTip("Load style for active group")
            self.load_style_button.clicked.connect(self.load_selected_group_style)
            self.dlg.gridLayout.addWidget(self.load_style_button, 6, 1, 1, 1)
            self.export_layout_button = QPushButton("Export PDF")
            self.export_layout_button.setToolTip("Quick PDF export for selected group.")
            self.export_layout_button.setStatusTip("Export quick layout PDF")
            self.export_layout_button.clicked.connect(self.export_group_layout_quick)
            self.dlg.gridLayout.addWidget(self.export_layout_button, 7, 0, 1, 2)

            if hasattr(self.dlg, "groupNameEdit"):
                self.dlg.groupNameEdit.setPlaceholderText("New group name")
                self.dlg.groupNameEdit.setToolTip("Enter a name and click Create Group.")
            self.dlg.dial2.setToolTip("Navigation dial: switch visible rasters in selected groups.")
            self.dlg.Dial.setToolTip("Navigation slider: switch visible rasters in selected groups.")

            # Preimposta valori predefiniti
            self.dlg.lineEditDistanceX.setText("1.0")  # Valore predefinito per distanza X
            self.dlg.lineEditDistanceY.setText("1.0")  # Valore predefinito per distanza Y
            self.dlg.lineEditAreaNames.setPlaceholderText("Area name | cell prefix")
            self.dlg.lineEditAreaNames.setToolTip(
                "Area naming field. Format: AreaName|CellPrefix (prefix optional)."
            )
            self.dlg.lineEditAreaNames.setAccessibleName("lineEditAreaNames")
            self._build_tools_tabs()
            self._build_bottom_controls_layout()
            self._load_ui_settings()
            if self.internal_grid_checkbox is not None:
                self.internal_grid_checkbox.setChecked(bool(self.grid_internal_enabled))
            self._build_grid_options_controls()
            self._swap_drawing_and_navigation_sections()
            self._sync_grid_options_from_controls()
            self._connect_persistent_fields()
            if hasattr(self, "_restore_group_selection_from_settings"):
                try:
                    self._restore_group_selection_from_settings(trigger_update=True)
                except Exception:
                    pass
            self._tune_visual_layout()
            self._apply_responsive_main_layout(self.dlg.width())
            self._apply_button_icons()
            self.refresh_trace_info_table()

            # Collega il dial alla funzione di aggiornamento
            self.dlg.Dial.valueChanged.connect(self.update_visibility_with_dial)
            self.dlg.dial2.valueChanged.connect(self.update_visibility_with_dial)

        self.dock_widget.show()
        restored = self._restore_main_dock_state()
        if not restored:
            self._ensure_dock_in_right_area()
            self._tabify_with_existing_right_dock()
        self.dock_widget.raise_()
        self._apply_dock_constraints()
        if self.dlg is not None:
            self._apply_responsive_main_layout(self.dlg.width())
        if self.dlg is not None:
            self._apply_button_icons()
            self.refresh_trace_info_table()
        if not self._active_project_root():
            self._notify_info(
                "No active project linked. Open 'GeoSurvey Studio Project Manager' and create/open a project.",
                duration=8,
            )
        if hasattr(self, "maybe_check_for_updates_on_start"):
            try:
                self.maybe_check_for_updates_on_start()
            except Exception:
                pass

    def open_project_manager(self):
        if self.project_manager_dialog is None:
            self.project_manager_dialog = ProjectManagerDialog(
                self.iface,
                self.iface.mainWindow(),
                on_project_updated=self._on_project_manager_updated,
            )
        self.project_manager_dialog.show()
        self.project_manager_dialog.raise_()
        if self.dlg is not None:
            self.populate_group_list()

    def _on_project_manager_updated(self):
        if self.dlg is not None:
            self.populate_group_list()
            self.populate_raster_list_from_selected_groups()
            self.refresh_trace_info_table()

    def _apply_dock_constraints(self):
        if self.dock_widget is None or self.dlg is None:
            return
        # Keep constraints lightweight to avoid disturbing QGIS global dock layout.
        self.dlg.setMinimumWidth(340)
        self.dlg.setMinimumHeight(0)
        self.dlg.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.dock_widget.setMinimumWidth(340)
        self.dock_widget.setMinimumHeight(0)

    def _on_dialog_resized(self, size):
        width = size.width() if hasattr(size, "width") else (self.dlg.width() if self.dlg is not None else 0)
        self._apply_responsive_main_layout(width)

    def _apply_responsive_main_layout(self, width):
        if self.dlg is None or not hasattr(self.dlg, "gridLayout_3"):
            return
        is_narrow = width < 760
        if self._is_narrow_layout is is_narrow:
            return

        gl3 = self.dlg.gridLayout_3
        if is_narrow:
            # Stack sections to avoid clipping on narrow/half-screen layouts.
            gl3.addLayout(self.dlg.verticalLayout_3, 0, 0, 1, 1)
            gl3.addWidget(self.dlg.widget, 1, 0, 1, 1)
            gl3.addLayout(self.dlg.gridLayout, 2, 0, 1, 1)
            if hasattr(self.dlg, "line"):
                self.dlg.line.hide()
            gl3.setColumnStretch(0, 1)
            gl3.setColumnStretch(1, 0)
            gl3.setColumnStretch(2, 0)
            gl3.setColumnStretch(3, 0)
            gl3.setRowStretch(0, 1)
            gl3.setRowStretch(1, 0)
            gl3.setRowStretch(2, 0)
            gl3.setRowStretch(3, 0)
            if hasattr(self.dlg, "rasterListWidget"):
                self.dlg.rasterListWidget.setMinimumWidth(160)
            if hasattr(self.dlg, "groupListWidget"):
                self.dlg.groupListWidget.setMinimumWidth(160)
            self.dlg.dial2.setMinimumSize(130, 130)
            self.dlg.dial2.setMaximumSize(170, 170)
            self.dlg.Dial.setMinimumHeight(34)
            self.dlg.Dial.setMaximumHeight(38)
            if hasattr(self.dlg, "widget"):
                self.dlg.widget.setMinimumHeight(0)
        else:
            # Restore wide two-column layout.
            gl3.addLayout(self.dlg.verticalLayout_3, 0, 0, 2, 1)
            gl3.addWidget(self.dlg.widget, 0, 2, 1, 2)
            gl3.addLayout(self.dlg.gridLayout, 1, 2, 1, 2)
            if hasattr(self.dlg, "line"):
                self.dlg.line.show()
                gl3.addWidget(self.dlg.line, 0, 1, 2, 1)
            gl3.setColumnStretch(0, 8)
            gl3.setColumnStretch(1, 0)
            gl3.setColumnStretch(2, 6)
            gl3.setColumnStretch(3, 0)
            gl3.setRowStretch(0, 0)
            gl3.setRowStretch(1, 0)
            gl3.setRowStretch(2, 0)
            gl3.setRowStretch(3, 1)
            if hasattr(self.dlg, "rasterListWidget"):
                self.dlg.rasterListWidget.setMinimumWidth(220)
            if hasattr(self.dlg, "groupListWidget"):
                self.dlg.groupListWidget.setMinimumWidth(220)
            self.dlg.dial2.setMinimumSize(160, 160)
            self.dlg.dial2.setMaximumSize(220, 220)
            self.dlg.Dial.setMinimumHeight(42)
            self.dlg.Dial.setMaximumHeight(46)
            if hasattr(self.dlg, "widget"):
                self.dlg.widget.setMinimumHeight(220)

        self._is_narrow_layout = is_narrow

    def _ensure_dock_in_right_area(self):
        if self.dock_widget is None:
            return
        if self.dock_widget.isFloating():
            return
        main_window = self.iface.mainWindow()
        try:
            area = main_window.dockWidgetArea(self.dock_widget)
            if area != Qt.RightDockWidgetArea:
                main_window.removeDockWidget(self.dock_widget)
                main_window.addDockWidget(Qt.RightDockWidgetArea, self.dock_widget)
        except Exception:
            pass

    def _tabify_with_existing_right_dock(self):
        if self.dock_widget is None:
            return
        main_window = self.iface.mainWindow()
        try:
            main_window.setDockNestingEnabled(True)
        except Exception:
            pass

        candidates = []
        for dock in main_window.findChildren(QDockWidget):
            if dock is self.dock_widget:
                continue
            if dock.isFloating():
                continue
            if main_window.dockWidgetArea(dock) != Qt.RightDockWidgetArea:
                continue
            candidates.append(dock)

        if not candidates:
            return

        tokens = (
            "browser",
            "processing",
            "style",
            "layer",
            "strumenti di processing",
            "stile layer",
        )

        def dock_score(dock):
            text = f"{dock.windowTitle()} {dock.objectName()}".lower()
            score = 0
            for token in tokens:
                if token in text:
                    score += 10
            if dock.isVisible():
                score += 3
            return score

        target = sorted(candidates, key=dock_score, reverse=True)[0]
        try:
            main_window.tabifyDockWidget(target, self.dock_widget)
        except Exception:
            # Fallback: keep standard dock placement.
            return

    def _settings_key(self, key):
        return f"{self.settings_group}/{key}"

    def _dock_area_to_text(self, area):
        if area == Qt.LeftDockWidgetArea:
            return "left"
        return "right"

    def _dock_text_to_area(self, text):
        if str(text or "").strip().lower() == "left":
            return Qt.LeftDockWidgetArea
        return Qt.RightDockWidgetArea

    def _connect_main_dock_persistence_signals(self):
        if self.dock_widget is None:
            return
        try:
            self.dock_widget.topLevelChanged.connect(self._on_main_dock_top_level_changed)
        except Exception:
            pass
        try:
            self.dock_widget.dockLocationChanged.connect(self._on_main_dock_location_changed)
        except Exception:
            pass

    def _on_main_dock_top_level_changed(self, _is_floating):
        self._save_main_dock_state()

    def _on_main_dock_location_changed(self, _area):
        self._save_main_dock_state()

    def _save_main_dock_state(self):
        if self.dock_widget is None:
            return
        try:
            main_window = self.iface.mainWindow()
            area = main_window.dockWidgetArea(self.dock_widget)
            self.settings.setValue(
                self._settings_key("ui/main_dock/area"),
                self._dock_area_to_text(area),
            )
            self.settings.setValue(
                self._settings_key("ui/main_dock/was_floating"),
                bool(self.dock_widget.isFloating()),
            )
        except Exception:
            pass

    def _restore_main_dock_state(self):
        if self.dock_widget is None:
            return False
        restore_on_open = self.settings.value(
            self._settings_key("ui/main_dock/restore_on_open"),
            True,
            type=bool,
        )
        if not restore_on_open:
            return False

        area_text = str(self.settings.value(self._settings_key("ui/main_dock/area"), "") or "").strip()
        if not area_text:
            return False

        target_area = self._dock_text_to_area(area_text)
        try:
            main_window = self.iface.mainWindow()
            try:
                main_window.removeDockWidget(self.dock_widget)
            except Exception:
                pass
            main_window.addDockWidget(target_area, self.dock_widget)
            self.dock_widget.setFloating(False)
            self.dock_widget.show()
            if target_area == Qt.RightDockWidgetArea:
                self._tabify_with_existing_right_dock()
            return True
        except Exception:
            return False

    def _load_ui_settings(self):
        self.grid_use_snap = self.settings.value(self._settings_key("grid/use_snap"), True, type=bool)
        self.grid_snap_mode = self.settings.value(self._settings_key("grid/snap_mode"), "all")
        self.grid_snap_tolerance = self.settings.value(self._settings_key("grid/snap_tolerance"), 12.0, type=float)
        self.grid_snap_units = self.settings.value(self._settings_key("grid/snap_units"), "pixels")
        self.grid_force_orthogonal = self.settings.value(self._settings_key("grid/force_orthogonal"), False, type=bool)
        self.grid_relative_orthogonal = self.settings.value(self._settings_key("grid/relative_orthogonal"), False, type=bool)
        self.keep_source_polygon = self.settings.value(self._settings_key("grid/keep_source_polygon"), True, type=bool)
        mode = self.settings.value(self._settings_key("grid/dimension_mode"), "ask")
        self.grid_dimension_mode = mode if mode in ("ask", "manual", "canvas") else "ask"
        self.grid_internal_enabled = self.settings.value(
            self._settings_key("grid/internal_enabled"),
            True,
            type=bool,
        )

        self.dlg.lineEditDistanceX.setText(
            self.settings.value(self._settings_key("grid/distance_x"), self.dlg.lineEditDistanceX.text())
        )
        self.dlg.lineEditDistanceY.setText(
            self.settings.value(self._settings_key("grid/distance_y"), self.dlg.lineEditDistanceY.text())
        )
        self.dlg.lineEditAreaNames.setText(
            self.settings.value(self._settings_key("grid/area_names"), "")
        )

        # Persisted UI state (groups/navigation/tools tab).
        self._saved_group_selection_ids = self._split_setting_list(
            self.settings.value(self._settings_key("ui/selected_group_ids"), "")
        )
        self._saved_current_group_id = str(
            self.settings.value(self._settings_key("ui/current_group_id"), "") or ""
        ).strip()
        self._saved_selected_timeslice_ids = self._split_setting_list(
            self.settings.value(self._settings_key("ui/selected_timeslice_ids"), "")
        )
        self._saved_current_timeslice_id = str(
            self.settings.value(self._settings_key("ui/current_timeslice_id"), "") or ""
        ).strip()
        saved_raster_row = self.settings.value(self._settings_key("ui/current_raster_row"), -1)
        try:
            self._saved_current_raster_row = int(saved_raster_row)
        except Exception:
            self._saved_current_raster_row = -1
        nav_index = self.settings.value(self._settings_key("ui/navigation_index"), 0)
        try:
            nav_index = int(nav_index)
        except Exception:
            nav_index = 0
        nav_index = max(0, nav_index)
        self.dlg.Dial.setValue(nav_index)
        self.dlg.dial2.setValue(nav_index)

        tools_tab_index = self.settings.value(self._settings_key("ui/tools_tab_index"), 0)
        try:
            tools_tab_index = int(tools_tab_index)
        except Exception:
            tools_tab_index = 0
        if self.tools_tabs is not None and self.tools_tabs.count() > 0:
            self.tools_tabs.setCurrentIndex(max(0, min(tools_tab_index, self.tools_tabs.count() - 1)))

    def _save_ui_settings(self):
        self._sync_grid_options_from_controls()
        self.settings.setValue(self._settings_key("grid/distance_x"), self.dlg.lineEditDistanceX.text().strip())
        self.settings.setValue(self._settings_key("grid/distance_y"), self.dlg.lineEditDistanceY.text().strip())
        self.settings.setValue(self._settings_key("grid/area_names"), self.dlg.lineEditAreaNames.text().strip())
        self.settings.setValue(self._settings_key("grid/use_snap"), self.grid_use_snap)
        self.settings.setValue(self._settings_key("grid/snap_mode"), self.grid_snap_mode)
        self.settings.setValue(self._settings_key("grid/snap_tolerance"), float(self.grid_snap_tolerance))
        self.settings.setValue(self._settings_key("grid/snap_units"), self.grid_snap_units)
        self.settings.setValue(self._settings_key("grid/force_orthogonal"), self.grid_force_orthogonal)
        self.settings.setValue(self._settings_key("grid/relative_orthogonal"), self.grid_relative_orthogonal)
        self.settings.setValue(self._settings_key("grid/keep_source_polygon"), self.keep_source_polygon)
        self.settings.setValue(self._settings_key("grid/dimension_mode"), self.grid_dimension_mode)
        self.settings.setValue(self._settings_key("grid/internal_enabled"), self.grid_internal_enabled)

        selected_group_ids = []
        current_group_id = ""
        if self.dlg is not None and hasattr(self.dlg, "groupListWidget"):
            selected_group_ids = [
                str(item.data(Qt.UserRole)).strip()
                for item in self.dlg.groupListWidget.selectedItems()
                if item is not None and str(item.data(Qt.UserRole) or "").strip()
            ]
            current_item = self.dlg.groupListWidget.currentItem()
            if current_item is not None:
                current_group_id = str(current_item.data(Qt.UserRole) or "").strip()
        self.settings.setValue(
            self._settings_key("ui/selected_group_ids"),
            "||".join(dict.fromkeys(selected_group_ids)),
        )
        self.settings.setValue(self._settings_key("ui/current_group_id"), current_group_id)
        selected_timeslice_ids = []
        current_timeslice_id = ""
        current_raster_row = -1
        if self.dlg is not None and hasattr(self.dlg, "rasterListWidget"):
            for item in self.dlg.rasterListWidget.selectedItems():
                payload = item.data(Qt.UserRole) if item is not None else None
                if isinstance(payload, dict):
                    tid = str(payload.get("timeslice_id") or "").strip()
                    if tid:
                        selected_timeslice_ids.append(tid)
            current_item = self.dlg.rasterListWidget.currentItem()
            if current_item is not None:
                payload = current_item.data(Qt.UserRole)
                if isinstance(payload, dict):
                    current_timeslice_id = str(payload.get("timeslice_id") or "").strip()
                current_raster_row = int(self.dlg.rasterListWidget.currentRow())
        self.settings.setValue(
            self._settings_key("ui/selected_timeslice_ids"),
            "||".join(dict.fromkeys(selected_timeslice_ids)),
        )
        self.settings.setValue(self._settings_key("ui/current_timeslice_id"), current_timeslice_id)
        self.settings.setValue(self._settings_key("ui/current_raster_row"), int(current_raster_row))
        # Keep in-memory snapshot aligned with current UI.
        self._saved_group_selection_ids = list(dict.fromkeys(selected_group_ids))
        self._saved_current_group_id = current_group_id
        self._saved_selected_timeslice_ids = list(dict.fromkeys(selected_timeslice_ids))
        self._saved_current_timeslice_id = current_timeslice_id
        self._saved_current_raster_row = int(current_raster_row)
        if self.dlg is not None:
            self.settings.setValue(self._settings_key("ui/navigation_index"), int(self.dlg.Dial.value()))
        if self.tools_tabs is not None:
            self.settings.setValue(self._settings_key("ui/tools_tab_index"), int(self.tools_tabs.currentIndex()))
        self._save_main_dock_state()
        if hasattr(self, "_save_trace_info_ui_state"):
            try:
                self._save_trace_info_ui_state()
            except Exception:
                pass

    def _connect_persistent_fields(self):
        self.dlg.lineEditDistanceX.editingFinished.connect(self._save_ui_settings)
        self.dlg.lineEditDistanceY.editingFinished.connect(self._save_ui_settings)
        self.dlg.lineEditAreaNames.editingFinished.connect(self._save_ui_settings)
        if self.internal_grid_checkbox is not None:
            self.internal_grid_checkbox.toggled.connect(self._save_ui_settings)
        if self.snap_checkbox is not None:
            self.snap_checkbox.toggled.connect(self._save_ui_settings)
        if self.snap_mode_combo is not None:
            self.snap_mode_combo.currentIndexChanged.connect(self._save_ui_settings)
        if self.snap_tolerance_spin is not None:
            self.snap_tolerance_spin.valueChanged.connect(self._save_ui_settings)
        if self.snap_units_combo is not None:
            self.snap_units_combo.currentIndexChanged.connect(self._save_ui_settings)
        if self.ortho_checkbox is not None:
            self.ortho_checkbox.toggled.connect(self._save_ui_settings)
        if self.ortho_base_checkbox is not None:
            self.ortho_base_checkbox.toggled.connect(self._save_ui_settings)
        if self.keep_area_checkbox is not None:
            self.keep_area_checkbox.toggled.connect(self._save_ui_settings)
        if self.dimension_mode_combo is not None:
            self.dimension_mode_combo.currentIndexChanged.connect(self._save_ui_settings)
        if self.help_button is not None:
            self.help_button.clicked.connect(self.show_grid_help)
        if self.export_button is not None:
            self.export_button.clicked.connect(self.export_last_grid_to_gpkg)
        if self.tools_tabs is not None:
            self.tools_tabs.currentChanged.connect(self._save_ui_settings)
        if hasattr(self.dlg, "groupListWidget"):
            self.dlg.groupListWidget.itemSelectionChanged.connect(self._save_ui_settings)
        if hasattr(self.dlg, "rasterListWidget"):
            self.dlg.rasterListWidget.itemSelectionChanged.connect(self._save_ui_settings)
            self.dlg.rasterListWidget.currentRowChanged.connect(self._save_ui_settings)
        if hasattr(self.dlg, "Dial"):
            self.dlg.Dial.valueChanged.connect(self._save_ui_settings)
        if hasattr(self.dlg, "dial2"):
            self.dlg.dial2.valueChanged.connect(self._save_ui_settings)

