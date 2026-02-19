from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAbstractItemView, QDockWidget
from PyQt5.QtWidgets import QPushButton, QSizePolicy, QCheckBox

from .gpr_linker_dialog import RasterLinkerDialog
from .project_manager_dialog import ProjectManagerDialog


class AppRuntimeMixin:
    def run(self):
        """Esegue il plugin."""
        if self.first_start:
            self.first_start = False
            self.dlg = RasterLinkerDialog()
            self.dlg.on_resized = self._on_dialog_resized
            self.dock_widget = QDockWidget(self.tr(u"Raster Linker"), self.iface.mainWindow())
            self.dock_widget.setObjectName("RasterLinkerDockWidget")
            self.dock_widget.setFeatures(
                QDockWidget.DockWidgetMovable
                | QDockWidget.DockWidgetFloatable
                | QDockWidget.DockWidgetClosable
            )
            self.dock_widget.setWidget(self.dlg)
            self.dock_widget.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock_widget)
            self._tabify_with_existing_right_dock()
            self._ensure_dialog_main_layout()
            self._init_name_raster_panel()
            self.populate_group_list()
            self.dlg.groupListWidget.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.dlg.createGridButton.setEnabled(True)
            self.dlg.selectGridPointsButton.setEnabled(True)
            self.dlg.selectGridPointsButton.setText("Set Orientation")
            self.dlg.selectGridPointsButton.setMinimumWidth(120)
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
            self._tune_visual_layout()
            self._apply_responsive_main_layout(self.dlg.width())
            self._apply_button_icons()
            self.refresh_trace_info_table()

            # Collega il dial alla funzione di aggiornamento
            self.dlg.Dial.valueChanged.connect(self.update_visibility_with_dial)
            self.dlg.dial2.valueChanged.connect(self.update_visibility_with_dial)

        self.dock_widget.show()
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
                "No active project linked. Open 'RasterLinker Project Manager' and create/open a project.",
                duration=8,
            )

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

