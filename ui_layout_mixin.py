from qgis.PyQt.QtCore import Qt, QSize
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsApplication, QgsLayerTreeLayer, QgsRasterLayer
from PyQt5.QtWidgets import (
    QLabel,
    QSizePolicy,
    QWidget,
    QTabWidget,
    QGridLayout,
    QVBoxLayout,
    QComboBox,
    QDoubleSpinBox,
    QPushButton,
)

from .grid_options_ui import build_grid_options_controls


class UiLayoutMixin:
    def _clear_qt_layout(self, layout):
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            child_layout = item.layout()
            if child_layout is not None:
                self._clear_qt_layout(child_layout)
                try:
                    child_layout.deleteLater()
                except Exception:
                    pass
                continue
            widget = item.widget()
            if widget is not None:
                try:
                    widget.setParent(None)
                    widget.deleteLater()
                except Exception:
                    pass

    def _ensure_dialog_main_layout(self):
        if self.dlg is None:
            return
        if self.dialog_main_layout is not None:
            return
        if self.dlg.layout() is None:
            main_layout = QVBoxLayout(self.dlg)
            main_layout.setContentsMargins(2, 2, 2, 2)
            main_layout.setSpacing(4)
        else:
            main_layout = self.dlg.layout()
            main_layout.setContentsMargins(2, 2, 2, 2)
            main_layout.setSpacing(4)
        if hasattr(self.dlg, "layoutWidget"):
            self.dlg.layoutWidget.setParent(self.dlg)
            self.dlg.layoutWidget.setMinimumSize(0, 0)
            self.dlg.layoutWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            if main_layout.indexOf(self.dlg.layoutWidget) < 0:
                main_layout.addWidget(self.dlg.layoutWidget, 1)
        self.dialog_main_layout = main_layout

    def _build_tools_tabs(self):
        if self.dlg is None or self.tools_tabs is not None:
            return

        if hasattr(self, "tools_panel_widget") and self.tools_panel_widget is not None:
            try:
                self.tools_panel_widget.deleteLater()
            except Exception:
                pass
            self.tools_panel_widget = None

        tabs = QTabWidget(self.dlg.layoutWidget)
        tabs.setObjectName("toolsTabs")
        tabs.setDocumentMode(False)
        tabs.setUsesScrollButtons(False)

        group_tab = QWidget(tabs)
        group_layout = QGridLayout(group_tab)
        group_layout.setContentsMargins(8, 8, 8, 8)
        group_layout.setHorizontalSpacing(8)
        group_layout.setVerticalSpacing(8)
        if self.load_groups_button is not None:
            group_layout.addWidget(self.load_groups_button, 0, 0, 1, 1)
        group_layout.addWidget(self.dlg.zoomSelectedGroupsButton, 0, 1, 1, 1)
        group_layout.addWidget(self.import_groups_button, 1, 0, 1, 1)
        group_layout.addWidget(self.dlg.createGroupButton, 1, 1, 1, 1)
        if hasattr(self.dlg, "groupNameEdit"):
            group_layout.addWidget(self.dlg.groupNameEdit, 2, 0, 1, 2)
        group_layout.setColumnStretch(0, 1)
        group_layout.setColumnStretch(1, 1)
        group_layout.setRowStretch(0, 0)
        group_layout.setRowStretch(1, 0)
        group_layout.setRowStretch(2, 0)
        group_layout.setRowStretch(3, 0)

        image_tab = QWidget(tabs)
        image_layout = QGridLayout(image_tab)
        image_layout.setContentsMargins(8, 8, 8, 8)
        image_layout.setHorizontalSpacing(8)
        image_layout.setVerticalSpacing(8)
        image_layout.addWidget(self.enhance_minmax_button, 0, 0, 1, 1)
        image_layout.addWidget(self.enhance_batch_button, 0, 1, 1, 1)
        image_layout.addWidget(self.save_style_button, 1, 0, 1, 1)
        image_layout.addWidget(self.load_style_button, 1, 1, 1, 1)
        image_layout.setColumnStretch(0, 1)
        image_layout.setColumnStretch(1, 1)
        image_layout.setRowStretch(0, 0)
        image_layout.setRowStretch(1, 0)
        image_layout.setRowStretch(2, 0)

        export_tab = QWidget(tabs)
        export_layout = QGridLayout(export_tab)
        export_layout.setContentsMargins(8, 8, 8, 8)
        export_layout.setHorizontalSpacing(8)
        export_layout.setVerticalSpacing(7)

        export_layout.addWidget(QLabel("Output", export_tab), 0, 0, 1, 1)
        self.export_mode_combo = QComboBox(export_tab)
        self.export_mode_combo.addItems(
            [
                "Batch (single PDF)",
                "Single visible image",
            ]
        )
        export_layout.addWidget(self.export_mode_combo, 0, 1, 1, 1)

        export_layout.addWidget(QLabel("Coverage", export_tab), 1, 0, 1, 1)
        self.export_coverage_mode_combo = QComboBox(export_tab)
        self.export_coverage_mode_combo.addItems(
            [
                "Use existing (keep edits)",
                "Refresh from rasters",
                "Create new coverage",
            ]
        )
        export_layout.addWidget(self.export_coverage_mode_combo, 1, 1, 1, 1)

        export_layout.addWidget(QLabel("Map content", export_tab), 2, 0, 1, 1)
        self.export_map_content_combo = QComboBox(export_tab)
        self.export_map_content_combo.addItems(
            [
                "Raster only",
                "Current canvas view",
                "Map theme",
            ]
        )
        export_layout.addWidget(self.export_map_content_combo, 2, 1, 1, 1)

        export_layout.addWidget(QLabel("Theme", export_tab), 3, 0, 1, 1)
        self.export_theme_combo = QComboBox(export_tab)
        self.export_theme_combo.setEditable(False)
        export_layout.addWidget(self.export_theme_combo, 3, 1, 1, 1)

        export_layout.addWidget(QLabel("Page", export_tab), 4, 0, 1, 1)
        self.export_page_size_combo = QComboBox(export_tab)
        self.export_page_size_combo.addItems(["A6", "A5", "A4", "A3", "A2", "A1", "Custom"])
        self.export_page_size_combo.setCurrentText("A4")
        export_layout.addWidget(self.export_page_size_combo, 4, 1, 1, 1)

        export_layout.addWidget(QLabel("Orientation", export_tab), 5, 0, 1, 1)
        self.export_orientation_combo = QComboBox(export_tab)
        self.export_orientation_combo.addItems(["Landscape", "Portrait"])
        export_layout.addWidget(self.export_orientation_combo, 5, 1, 1, 1)

        export_layout.addWidget(QLabel("DPI", export_tab), 6, 0, 1, 1)
        self.export_dpi_combo = QComboBox(export_tab)
        self.export_dpi_combo.addItems(["150", "300", "600"])
        self.export_dpi_combo.setCurrentText("300")
        export_layout.addWidget(self.export_dpi_combo, 6, 1, 1, 1)

        export_layout.addWidget(QLabel("Scale 1:n", export_tab), 7, 0, 1, 1)
        self.export_scale_spin = QDoubleSpinBox(export_tab)
        self.export_scale_spin.setDecimals(2)
        self.export_scale_spin.setRange(1.0, 1e9)
        self.export_scale_spin.setSingleStep(100.0)
        self.export_scale_spin.setValue(1000.0)
        export_layout.addWidget(self.export_scale_spin, 7, 1, 1, 1)

        export_layout.addWidget(QLabel("Custom unit", export_tab), 8, 0, 1, 1)
        self.export_custom_unit_combo = QComboBox(export_tab)
        self.export_custom_unit_combo.addItems(["cm", "inch"])
        export_layout.addWidget(self.export_custom_unit_combo, 8, 1, 1, 1)

        export_layout.addWidget(QLabel("Custom W", export_tab), 9, 0, 1, 1)
        self.export_custom_w_spin = QDoubleSpinBox(export_tab)
        self.export_custom_w_spin.setDecimals(2)
        self.export_custom_w_spin.setRange(0.1, 5000.0)
        self.export_custom_w_spin.setValue(21.0)
        export_layout.addWidget(self.export_custom_w_spin, 9, 1, 1, 1)

        export_layout.addWidget(QLabel("Custom H", export_tab), 10, 0, 1, 1)
        self.export_custom_h_spin = QDoubleSpinBox(export_tab)
        self.export_custom_h_spin.setDecimals(2)
        self.export_custom_h_spin.setRange(0.1, 5000.0)
        self.export_custom_h_spin.setValue(29.7)
        export_layout.addWidget(self.export_custom_h_spin, 10, 1, 1, 1)

        self.generate_coverage_button = QPushButton("Generate Coverage", export_tab)
        export_layout.addWidget(self.generate_coverage_button, 11, 0, 1, 1)
        export_layout.addWidget(self.export_layout_button, 11, 1, 1, 1)
        export_layout.setColumnStretch(0, 0)
        export_layout.setColumnStretch(1, 1)
        export_layout.setRowStretch(12, 0)

        tabs.addTab(group_tab, "Groups")
        tabs.addTab(image_tab, "Images")
        tabs.addTab(export_tab, "Export")
        tabs.tabBar().setExpanding(False)
        tabs.tabBar().setElideMode(Qt.ElideRight)
        tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tabs.setMinimumHeight(120)
        tabs.setMaximumHeight(520)

        if self.group_tools_label is not None:
            self.group_tools_label.hide()
            self.dlg.gridLayout.removeWidget(self.group_tools_label)
        if self.image_tools_label is not None:
            self.image_tools_label.hide()
            self.dlg.gridLayout.removeWidget(self.image_tools_label)

        tools_panel = QWidget(self.dlg.layoutWidget)
        tools_layout = QVBoxLayout(tools_panel)
        tools_layout.setContentsMargins(12, 0, 0, 0)
        tools_layout.setSpacing(6)
        tools_layout.addWidget(tabs, 0, Qt.AlignTop)
        self.tools_panel_layout = tools_layout
        tools_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.tools_panel_widget = tools_panel
        self.tools_tabs = tabs
        self.tools_tabs.currentChanged.connect(lambda _i: self._apply_responsive_main_layout(self.dlg.width()))
        if self.export_page_size_combo is not None:
            self.export_page_size_combo.currentTextChanged.connect(self._on_export_page_size_changed)
            self._on_export_page_size_changed(self.export_page_size_combo.currentText())

        if self.export_map_content_combo is not None:
            self.export_map_content_combo.currentTextChanged.connect(self._on_export_map_content_changed)
            self._on_export_map_content_changed(self.export_map_content_combo.currentText())

        if self.generate_coverage_button is not None:
            self.generate_coverage_button.clicked.connect(self.generate_atlas_coverage_from_export_tab)

        self._refresh_export_theme_combo()
        try:
            if self.export_scale_spin is not None and self.iface is not None and self.iface.mapCanvas() is not None:
                sc = float(self.iface.mapCanvas().scale())
                if sc > 0:
                    self.export_scale_spin.setValue(sc)
        except Exception:
            pass

    def _on_export_page_size_changed(self, value):
        is_custom = str(value or "").strip().lower() == "custom"
        for w in (self.export_custom_unit_combo, self.export_custom_w_spin, self.export_custom_h_spin):
            if w is not None:
                w.setEnabled(is_custom)

    def _on_export_map_content_changed(self, value):
        is_theme = str(value or "").strip().lower().startswith("map theme")
        if self.export_theme_combo is not None:
            self.export_theme_combo.setEnabled(is_theme)

    def _refresh_export_theme_combo(self):
        combo = getattr(self, "export_theme_combo", None)
        if combo is None:
            return
        current = combo.currentText().strip()
        combo.blockSignals(True)
        combo.clear()
        names = []
        try:
            from qgis.core import QgsProject
            coll = QgsProject.instance().mapThemeCollection()
            names = list(coll.mapThemes()) if coll is not None else []
        except Exception:
            names = []
        names = [str(n).strip() for n in names if str(n).strip()]
        if not names:
            combo.addItem("<no theme>")
        else:
            combo.addItems(names)
            if current and current in names:
                combo.setCurrentText(current)
        combo.blockSignals(False)

    def _build_bottom_controls_layout(self):
        if self.dlg is None or self.bottom_controls_widget is not None:
            return

        self._ensure_dialog_main_layout()

        panel = QWidget(self.dlg)
        panel.setObjectName("gridDefinitionPanel")
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        panel.setMinimumHeight(116)
        panel.setMaximumHeight(148)
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(4, 4, 4, 4)
        panel_layout.setHorizontalSpacing(8)
        panel_layout.setVerticalSpacing(4)

        self.coord_x0_label = QLabel("x0", panel)
        self.coord_x1_label = QLabel("x1", panel)
        self.coord_y0_label = QLabel("y0", panel)
        self.coord_y1_label = QLabel("y1", panel)
        self.cell_x_label = QLabel("Cell X (m)", panel)
        self.cell_y_label = QLabel("Cell Y (m)", panel)
        self.area_name_label = QLabel("Area name | cell prefix", panel)
        for label in (
            self.coord_x0_label,
            self.coord_x1_label,
            self.coord_y0_label,
            self.coord_y1_label,
            self.cell_x_label,
            self.cell_y_label,
            self.area_name_label,
        ):
            label.setStyleSheet("color: #202020; font-size: 9pt;")

        self.dlg.selectGridPointsButton.setParent(panel)
        self.dlg.lineEditX0Y0.setParent(panel)
        self.dlg.lineEditX1Y0.setParent(panel)
        self.dlg.lineEditY0.setParent(panel)
        self.dlg.lineEditX0Y1.setParent(panel)
        self.dlg.createGridButton.setParent(panel)
        self.dlg.lineEditAreaNames.setParent(panel)
        self.dlg.lineEditDistanceX.setParent(panel)
        self.dlg.lineEditDistanceY.setParent(panel)
        if self.internal_grid_checkbox is not None:
            self.internal_grid_checkbox.setParent(panel)

        # Compact XY and cell fields to keep the drawing section tighter.
        for edit in (
            self.dlg.lineEditX0Y0,
            self.dlg.lineEditX1Y0,
            self.dlg.lineEditY0,
            self.dlg.lineEditX0Y1,
        ):
            if edit is not None:
                edit.setFixedWidth(118)
        for edit in (self.dlg.lineEditDistanceX, self.dlg.lineEditDistanceY):
            if edit is not None:
                edit.setMaximumWidth(82)
                edit.setMinimumWidth(82)
                edit.setFixedWidth(82)
        if hasattr(self.dlg, "lineEditAreaNames"):
            self.dlg.lineEditAreaNames.setMaximumWidth(170)
            self.dlg.lineEditAreaNames.setMinimumWidth(118)
            self.dlg.lineEditAreaNames.setFixedWidth(170)

        for draw_btn in (self.dlg.selectGridPointsButton, self.dlg.createGridButton):
            if draw_btn is not None:
                draw_btn.setMinimumHeight(28)
                draw_btn.setMaximumHeight(32)
                draw_btn.setMinimumWidth(130)
                draw_btn.setMaximumWidth(170)
                draw_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # Left block: Set Orientation + XY coordinates.
        orientation_block = QWidget(panel)
        orientation_block.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        orientation_layout = QGridLayout(orientation_block)
        orientation_layout.setContentsMargins(0, 0, 0, 0)
        orientation_layout.setHorizontalSpacing(6)
        orientation_layout.setVerticalSpacing(6)
        orientation_layout.addWidget(self.dlg.selectGridPointsButton, 0, 0, 1, 2)
        orientation_layout.addWidget(self.coord_x0_label, 1, 0, 1, 1)
        orientation_layout.addWidget(self.coord_x1_label, 1, 1, 1, 1)
        orientation_layout.addWidget(self.dlg.lineEditX0Y0, 2, 0, 1, 1)
        orientation_layout.addWidget(self.dlg.lineEditX1Y0, 2, 1, 1, 1)
        orientation_layout.addWidget(self.coord_y0_label, 3, 0, 1, 1)
        orientation_layout.addWidget(self.coord_y1_label, 3, 1, 1, 1)
        orientation_layout.addWidget(self.dlg.lineEditY0, 4, 0, 1, 1)
        orientation_layout.addWidget(self.dlg.lineEditX0Y1, 4, 1, 1, 1)

        # Middle block: Draw Polygon + area/cell values.
        drawing_block = QWidget(panel)
        drawing_block.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        drawing_layout = QGridLayout(drawing_block)
        drawing_layout.setContentsMargins(0, 0, 0, 0)
        drawing_layout.setHorizontalSpacing(6)
        drawing_layout.setVerticalSpacing(6)
        drawing_layout.addWidget(self.dlg.createGridButton, 0, 0, 1, 2)
        drawing_layout.addWidget(self.area_name_label, 1, 0, 1, 2)
        drawing_layout.addWidget(self.dlg.lineEditAreaNames, 2, 0, 1, 2)
        if self.internal_grid_checkbox is not None:
            drawing_layout.addWidget(self.internal_grid_checkbox, 3, 0, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)
        drawing_layout.addWidget(self.cell_x_label, 4, 0, 1, 1)
        drawing_layout.addWidget(self.cell_y_label, 4, 1, 1, 1)
        drawing_layout.addWidget(self.dlg.lineEditDistanceX, 5, 0, 1, 1)
        drawing_layout.addWidget(self.dlg.lineEditDistanceY, 5, 1, 1, 1)

        panel_layout.addWidget(orientation_block, 0, 0, 1, 1, Qt.AlignTop)
        panel_layout.addWidget(drawing_block, 0, 1, 1, 1, Qt.AlignTop)
        right_spacer = QWidget(panel)
        right_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        panel_layout.addWidget(right_spacer, 0, 2, 1, 1)

        panel_layout.setColumnStretch(0, 0)
        panel_layout.setColumnStretch(1, 0)
        panel_layout.setColumnStretch(2, 1)
        panel_layout.setRowStretch(0, 0)

        self.bottom_controls_widget = panel
        # Keep all plugin content inside gridLayout_3 to avoid dock over-expansion.
        self.dlg.gridLayout_3.addWidget(panel, 2, 0, 1, 4)

    def _swap_drawing_and_navigation_sections(self):
        """
        Keep dial/slider in left column and keep top-right area for tool tabs.
        Drawing Options are hosted in bottom panel (next to XY/Cell controls).
        """
        if self.dlg is None:
            return

        # Build left navigation widget once and host dial + slider there.
        if self.left_nav_widget is None:
            nav_widget = QWidget(self.dlg.layoutWidget)
            nav_layout = QVBoxLayout(nav_widget)
            nav_layout.setContentsMargins(0, 0, 0, 0)
            nav_layout.setSpacing(6)
            nav_layout.addWidget(self.dlg.dial2, 0, Qt.AlignHCenter)
            nav_layout.addWidget(self.dlg.Dial)
            self.left_nav_widget = nav_widget

        # Left column order: raster list, group list, navigation, name raster.
        if hasattr(self.dlg, "verticalLayout_3"):
            self.dlg.verticalLayout_3.removeWidget(self.dlg.widget)
            self.dlg.verticalLayout_3.removeWidget(self.left_nav_widget)
            self.dlg.verticalLayout_3.insertWidget(4, self.left_nav_widget)

        # Right-top hosts tabs/actions (group/image/export).
        if hasattr(self.dlg, "gridLayout_3") and getattr(self, "tools_panel_widget", None) is not None:
            self.dlg.gridLayout_3.addWidget(self.tools_panel_widget, 0, 2, 1, 2, Qt.AlignTop)
        # Drawing Options: place directly below tabs (right column).
        # IMPORTANT: this controls only the Drawing Options box (self.dlg.widget),
        # not raster/group list heights.
        if getattr(self, "tools_panel_layout", None) is not None and hasattr(self.dlg, "widget"):
            self.dlg.widget.setParent(self.tools_panel_widget)
            self.dlg.widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            self.dlg.widget.setMinimumWidth(250)
            self.dlg.widget.setMinimumHeight(170)
            self.dlg.widget.setMaximumHeight(250)
            # Keep only one instance in the right tools column.
            self.tools_panel_layout.removeWidget(self.dlg.widget)
            self.tools_panel_layout.addWidget(self.dlg.widget, 0, Qt.AlignTop)

    def _tune_visual_layout(self):
        button_style = (
            "font-size: 9pt; "
            "padding: 4px 8px;"
        )
        label_style = "color: #202020; font-size: 9pt;"

        # Make navigation controls easier to use.
        self.dlg.dial2.setMinimumSize(88, 88)
        self.dlg.dial2.setMaximumSize(110, 110)
        self.dlg.dial2.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.dlg.dial2.setNotchTarget(2.0)
        self.dlg.Dial.setMinimumHeight(18)
        self.dlg.Dial.setMaximumHeight(22)
        # Left list widths are fixed here; heights are managed only in
        # app_runtime_mixin._apply_responsive_main_layout (single source of truth).
        if hasattr(self.dlg, "rasterListWidget"):
            self.dlg.rasterListWidget.setMinimumWidth(220)
            self.dlg.rasterListWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            self.dlg.rasterListWidget.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.dlg.rasterListWidget.setStyleSheet("font-size: 9pt;")
        if hasattr(self.dlg, "groupListWidget"):
            self.dlg.groupListWidget.setMinimumWidth(220)
            self.dlg.groupListWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            self.dlg.groupListWidget.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.dlg.groupListWidget.setStyleSheet("font-size: 9pt;")

        # Improve readability/consistency of layout-managed action buttons.
        for btn in [
            getattr(self, "load_groups_button", None),
            getattr(self, "import_groups_button", None),
            getattr(self, "enhance_minmax_button", None),
            getattr(self, "enhance_batch_button", None),
            getattr(self, "save_style_button", None),
            getattr(self, "load_style_button", None),
            getattr(self, "export_layout_button", None),
            getattr(self, "generate_coverage_button", None),
            self.dlg.zoomSelectedGroupsButton,
            self.dlg.createGroupButton,
        ]:
            if btn is not None:
                btn.setFixedHeight(34)
                btn.setMinimumWidth(108)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                btn.setStyleSheet(button_style)
                btn.setIconSize(QSize(16, 16))

        # Keep bottom-left drawing buttons on dedicated sizing rules.
        for btn in (self.dlg.selectGridPointsButton, self.dlg.createGridButton):
            if btn is not None:
                btn.setMinimumHeight(28)
                btn.setMaximumHeight(32)
                btn.setMinimumWidth(130)
                btn.setMaximumWidth(170)
                btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                btn.setStyleSheet(button_style)
                btn.setIconSize(QSize(16, 16))

        # Compact right-side tools block to leave more visual room for drawing options.
        for btn in [
            getattr(self, "load_groups_button", None),
            self.dlg.zoomSelectedGroupsButton,
            getattr(self, "import_groups_button", None),
            self.dlg.createGroupButton,
            getattr(self, "enhance_minmax_button", None),
            getattr(self, "enhance_batch_button", None),
            getattr(self, "save_style_button", None),
            getattr(self, "load_style_button", None),
        ]:
            if btn is not None:
                btn.setFixedHeight(34)
                btn.setMinimumWidth(96)

        # Keep export action buttons readable: do not over-compact these.
        if getattr(self, "generate_coverage_button", None) is not None:
            self.generate_coverage_button.setFixedHeight(34)
            self.generate_coverage_button.setMinimumWidth(152)
            self.generate_coverage_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if getattr(self, "export_layout_button", None) is not None:
            self.export_layout_button.setFixedHeight(34)
            self.export_layout_button.setMinimumWidth(132)
            self.export_layout_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        if hasattr(self.dlg, "lineEditAreaNames"):
            self.dlg.lineEditAreaNames.setStyleSheet("font-size: 9pt; padding: 2px 4px;")
            self.dlg.lineEditAreaNames.setMinimumHeight(24)
        if self.internal_grid_checkbox is not None:
            self.internal_grid_checkbox.setStyleSheet(label_style)

        for edit_name in ("lineEditX0Y0", "lineEditX1Y0", "lineEditY0", "lineEditX0Y1", "lineEditDistanceX", "lineEditDistanceY", "groupNameEdit"):
            edit = getattr(self.dlg, edit_name, None)
            if edit is not None:
                edit.setMinimumHeight(24)
                edit.setStyleSheet("font-size: 9pt; padding: 2px 4px;")

        for label in (
            self.coord_x0_label,
            self.coord_x1_label,
            self.coord_y0_label,
            self.coord_y1_label,
            self.cell_x_label,
            self.cell_y_label,
        ):
            if label is not None:
                label.setStyleSheet(label_style)
        self._on_internal_grid_toggled(self.internal_grid_checkbox.isChecked() if self.internal_grid_checkbox is not None else True)

        if self.tools_tabs is not None:
            self.tools_tabs.setStyleSheet(
                "QTabBar::tab { font-size: 9pt; padding: 0px 4px; min-width: 48px; }"
            )

        for checkbox in (
            self.snap_checkbox,
            self.ortho_checkbox,
            self.ortho_base_checkbox,
            self.keep_area_checkbox,
        ):
            if checkbox is not None:
                checkbox.setStyleSheet("color: #202020; font-size: 9pt;")
        for input_widget in (
            self.snap_mode_combo,
            self.snap_tolerance_spin,
            self.snap_units_combo,
            self.dimension_mode_combo,
        ):
            if input_widget is not None:
                input_widget.setMinimumHeight(22)
                input_widget.setStyleSheet("font-size: 9pt;")
        for aux_btn in (self.help_button, self.export_button):
            if aux_btn is not None:
                aux_btn.setFixedHeight(30)
                aux_btn.setMinimumWidth(86)
                aux_btn.setStyleSheet(button_style)
        for export_widget in (
            getattr(self, "export_mode_combo", None),
            getattr(self, "export_coverage_mode_combo", None),
            getattr(self, "export_map_content_combo", None),
            getattr(self, "export_theme_combo", None),
            getattr(self, "export_page_size_combo", None),
            getattr(self, "export_orientation_combo", None),
            getattr(self, "export_dpi_combo", None),
            getattr(self, "export_scale_spin", None),
            getattr(self, "export_custom_unit_combo", None),
            getattr(self, "export_custom_w_spin", None),
            getattr(self, "export_custom_h_spin", None),
        ):
            if export_widget is not None:
                export_widget.setMinimumHeight(22)
                export_widget.setStyleSheet("font-size: 9pt; padding: 1px 3px;")

        if hasattr(self.dlg, "gridLayout"):
            self.dlg.gridLayout.setHorizontalSpacing(8)
            self.dlg.gridLayout.setVerticalSpacing(6)
            self.dlg.gridLayout.setColumnStretch(0, 1)
            self.dlg.gridLayout.setColumnStretch(1, 1)
            self.dlg.gridLayout.setContentsMargins(0, 0, 0, 0)
            self.dlg.gridLayout.setRowStretch(0, 0)
        if hasattr(self.dlg, "gridLayout_3"):
            self.dlg.gridLayout_3.setHorizontalSpacing(10)
            self.dlg.gridLayout_3.setVerticalSpacing(8)
            self.dlg.gridLayout_3.setColumnStretch(0, 8)
            self.dlg.gridLayout_3.setColumnStretch(1, 0)
            self.dlg.gridLayout_3.setColumnStretch(2, 6)
            self.dlg.gridLayout_3.setColumnStretch(3, 0)
            # Keep top-right tabs aligned; drawing options are in bottom panel.
            if getattr(self, "tools_panel_widget", None) is not None:
                self.dlg.gridLayout_3.addWidget(self.tools_panel_widget, 0, 2, 1, 2, Qt.AlignTop)
            # Keep left/right top blocks compact; free height goes to row 3 filler.
            self.dlg.gridLayout_3.setRowStretch(0, 0)
            self.dlg.gridLayout_3.setRowStretch(1, 0)
            self.dlg.gridLayout_3.setRowStretch(2, 0)
            self.dlg.gridLayout_3.setRowStretch(3, 1)
        if hasattr(self.dlg, "verticalLayout_3"):
            # Keep a visible gap between left widgets and separator line.
            self.dlg.verticalLayout_3.setContentsMargins(0, 0, 6, 0)
            self.dlg.verticalLayout_3.setSpacing(2)
            # Reset stretch policy to avoid pushing bottom controls outside viewport.
            for idx in range(self.dlg.verticalLayout_3.count()):
                try:
                    self.dlg.verticalLayout_3.setStretch(idx, 0)
                except Exception:
                    pass
        if hasattr(self.dlg, "labelSelezionaRaster"):
            self.dlg.labelSelezionaRaster.setContentsMargins(0, 0, 0, 0)
        if hasattr(self.dlg, "labelseleziona"):
            self.dlg.labelseleziona.setContentsMargins(0, 0, 0, 0)
        # Drawing Options sizing. This is the box titled "Drawing Options".
        if hasattr(self.dlg, "widget"):
            self.dlg.widget.setMinimumHeight(170)
            self.dlg.widget.setMaximumHeight(250)
            self.dlg.widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        if self.left_nav_widget is not None:
            # Keep slider visibly below the dial without overlap.
            self.left_nav_widget.setMinimumHeight(104)
            self.left_nav_widget.setMaximumHeight(124)
            self.left_nav_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        if hasattr(self.dlg, "line"):
            self.dlg.line.setFixedWidth(2)
            self.dlg.line.setStyleSheet("color: #9a9a9a;")
        if self.bottom_controls_widget is None:
            # Always use layout-managed controls; avoid absolute-position fallback.
            self._build_bottom_controls_layout()

    def _init_name_raster_panel(self):
        if self.name_raster_panel is not None or self.dlg is None:
            return

        panel = QWidget(self.dlg.layoutWidget)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(2)

        title = QLabel("Name Raster:")
        title.setStyleSheet("color: #202020; font-size: 9pt; font-weight: 600;")
        panel_layout.addWidget(title)

        lines = []
        for _ in range(4):
            row = QLabel("-")
            row.setStyleSheet("color: #202020; font-size: 9pt;")
            row.setWordWrap(True)
            panel_layout.addWidget(row)
            lines.append(row)

        self.name_raster_panel = panel
        self.name_raster_title = title
        self.name_raster_lines = lines
        panel.setMinimumHeight(30)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        if hasattr(self.dlg, "verticalLayout_3"):
            self.dlg.verticalLayout_3.addWidget(panel)
        else:
            self.dlg.gridLayout_3.addWidget(panel, 2, 0, 1, 4)
        self._render_name_raster_lines([])

    def _render_name_raster_lines(self, lines):
        if self.name_raster_panel is None:
            return
        normalized = [str(x) for x in (lines or []) if str(x).strip()]
        if not normalized:
            normalized = ["No raster loaded"]
        for idx, row in enumerate(self.name_raster_lines):
            if idx < len(normalized):
                row.setText(normalized[idx])
                row.show()
            else:
                row.setText("")
                row.hide()

    def _build_name_lines_for_selected_groups(self):
        if self.dlg is None:
            return []
        selected_group_items = self.dlg.groupListWidget.selectedItems()
        if not selected_group_items:
            return []

        value = self.dlg.Dial.value()
        lines = []
        for group_item in selected_group_items:
            group_name = group_item.text().strip()
            group = self._get_or_create_plugin_qgis_group(group_name)
            raster_nodes = [
                child for child in group.children()
                if isinstance(child, QgsLayerTreeLayer) and isinstance(child.layer(), QgsRasterLayer)
            ]
            if not raster_nodes:
                continue
            index = min(value, len(raster_nodes) - 1)
            visible_raster_name = raster_nodes[index].layer().name()
            lines.append(f"[{group_name}] {visible_raster_name}")
        return lines

    def _qgis_theme_icon(self, *names):
        for name in names:
            if not name:
                continue
            normalized = name if str(name).startswith("/") else f"/{name}"
            icon = QgsApplication.getThemeIcon(normalized)
            if icon is not None and not icon.isNull():
                return icon
        return QIcon()

    def _set_button_icon(self, button, *theme_names):
        if button is None:
            return
        icon = self._qgis_theme_icon(*theme_names)
        if not icon.isNull():
            button.setIcon(icon)
            button.setIconSize(QSize(16, 16))

    def _apply_button_icons(self):
        # Use QGIS native theme icons when available.
        self._set_button_icon(getattr(self.dlg, "createGroupButton", None), "mActionAddGroup.svg", "mActionNewVectorLayer.svg")
        self._set_button_icon(getattr(self, "load_groups_button", None), "mActionAddRasterLayer.svg", "mActionOpenTable.svg")
        self._set_button_icon(getattr(self.dlg, "zoomSelectedGroupsButton", None), "mActionZoomToSelected.svg", "mActionZoomFullExtent.svg")
        self._set_button_icon(getattr(self, "import_groups_button", None), "mActionOptions.svg", "mActionPropertiesWidget.svg")
        self._set_button_icon(getattr(self.dlg, "createGridButton", None), "mActionCapturePolygon.svg", "mActionNewVectorLayer.svg")
        self._set_button_icon(
            getattr(self.dlg, "selectGridPointsButton", None),
            "mActionCaptureLine.svg",
            "mActionMoveVertex.svg",
        )
        self._set_button_icon(getattr(self, "enhance_minmax_button", None), "mActionRasterHistogram.svg", "mActionOptions.svg")
        self._set_button_icon(getattr(self, "enhance_batch_button", None), "mActionRasterHistogram.svg", "mActionFilter2.svg")
        self._set_button_icon(getattr(self, "save_style_button", None), "mActionFileSave.svg", "mActionSaveAs.svg")
        self._set_button_icon(getattr(self, "load_style_button", None), "mActionFileOpen.svg", "mActionAddRasterLayer.svg")
        self._set_button_icon(getattr(self, "export_layout_button", None), "mActionSaveAsPDF.svg", "mActionSaveAs.svg")
        self._set_button_icon(getattr(self, "generate_coverage_button", None), "mActionAddGeometryCollection.svg", "mActionPolygonize.svg")
        self._set_button_icon(getattr(self, "help_button", None), "mActionHelpContents.svg", "mActionOptions.svg")
        self._set_button_icon(getattr(self, "export_button", None), "mActionSaveAs.svg", "mActionFileSave.svg")

    def _build_grid_options_controls(self):
        if self.dlg is None or not hasattr(self.dlg, "horizontalLayout_2"):
            return
        # Avoid duplicated controls after plugin reload/reopen.
        self._clear_qt_layout(self.dlg.horizontalLayout_2)
        self.snap_checkbox = None
        self.snap_mode_combo = None
        self.snap_tolerance_spin = None
        self.snap_units_combo = None
        self.ortho_checkbox = None
        self.ortho_base_checkbox = None
        self.keep_area_checkbox = None
        self.dimension_mode_combo = None
        self.help_button = None
        self.export_button = None
        self.base_angle_label = None
        self.length_label = None
        self.orientation_status_label = None

        (
            self.snap_checkbox,
            self.snap_mode_combo,
            self.snap_tolerance_spin,
            self.snap_units_combo,
            self.ortho_checkbox,
            self.ortho_base_checkbox,
            self.keep_area_checkbox,
            self.dimension_mode_combo,
            self.help_button,
            self.export_button,
            self.base_angle_label,
            self.length_label,
            self.orientation_status_label,
        ) = build_grid_options_controls(
            self.dlg.horizontalLayout_2,
            use_snap=self.grid_use_snap,
            snap_mode=self.grid_snap_mode,
            snap_tolerance=self.grid_snap_tolerance,
            snap_units=self.grid_snap_units,
            force_orthogonal=self.grid_force_orthogonal,
            relative_orthogonal=self.grid_relative_orthogonal,
            keep_source_polygon=self.keep_source_polygon,
            dimension_mode=self.grid_dimension_mode,
        )

    def _set_name_raster_label(self, raster_name=None):
        """Update 'Name Raster' label in GUI."""
        if raster_name:
            self._render_name_raster_lines([raster_name])
        else:
            self._render_name_raster_lines([])
