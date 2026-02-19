from qgis.PyQt.QtCore import Qt, QSize
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsApplication, QgsLayerTreeLayer, QgsRasterLayer
from PyQt5.QtWidgets import QLabel, QSizePolicy, QWidget, QTabWidget, QGridLayout, QVBoxLayout

from .grid_options_ui import build_grid_options_controls


class UiLayoutMixin:
    def _ensure_dialog_main_layout(self):
        if self.dlg is None:
            return
        if self.dialog_main_layout is not None:
            return
        if self.dlg.layout() is None:
            main_layout = QVBoxLayout(self.dlg)
            main_layout.setContentsMargins(6, 6, 6, 6)
            main_layout.setSpacing(10)
        else:
            main_layout = self.dlg.layout()
        if hasattr(self.dlg, "layoutWidget"):
            self.dlg.layoutWidget.setParent(self.dlg)
            self.dlg.layoutWidget.setMinimumSize(0, 0)
            self.dlg.layoutWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            main_layout.addWidget(self.dlg.layoutWidget, 1)
        self.dialog_main_layout = main_layout

    def _build_tools_tabs(self):
        if self.dlg is None or self.tools_tabs is not None:
            return

        for legacy_widget_name in ("openButton", "Ok"):
            legacy_widget = getattr(self.dlg, legacy_widget_name, None)
            if legacy_widget is not None:
                legacy_widget.hide()
                self.dlg.gridLayout.removeWidget(legacy_widget)

        tabs = QTabWidget(self.dlg.layoutWidget)
        tabs.setObjectName("toolsTabs")
        tabs.setDocumentMode(False)
        tabs.setUsesScrollButtons(False)

        group_tab = QWidget(tabs)
        group_layout = QGridLayout(group_tab)
        group_layout.setContentsMargins(6, 6, 6, 6)
        group_layout.setHorizontalSpacing(8)
        group_layout.setVerticalSpacing(6)
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
        group_layout.setRowStretch(3, 1)

        image_tab = QWidget(tabs)
        image_layout = QGridLayout(image_tab)
        image_layout.setContentsMargins(6, 6, 6, 6)
        image_layout.setHorizontalSpacing(8)
        image_layout.setVerticalSpacing(6)
        image_layout.addWidget(self.enhance_minmax_button, 0, 0, 1, 1)
        image_layout.addWidget(self.enhance_batch_button, 0, 1, 1, 1)
        image_layout.addWidget(self.save_style_button, 1, 0, 1, 1)
        image_layout.addWidget(self.load_style_button, 1, 1, 1, 1)
        image_layout.addWidget(self.export_layout_button, 2, 0, 1, 2)
        image_layout.setColumnStretch(0, 1)
        image_layout.setColumnStretch(1, 1)
        image_layout.setRowStretch(0, 0)
        image_layout.setRowStretch(1, 0)
        image_layout.setRowStretch(2, 0)
        image_layout.setRowStretch(3, 0)

        tabs.addTab(group_tab, "Groups")
        tabs.addTab(image_tab, "Images")
        tabs.tabBar().setExpanding(False)
        tabs.tabBar().setElideMode(Qt.ElideRight)
        tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        tabs.setMinimumHeight(145)
        tabs.setMaximumHeight(220)

        if self.group_tools_label is not None:
            self.group_tools_label.hide()
            self.dlg.gridLayout.removeWidget(self.group_tools_label)
        if self.image_tools_label is not None:
            self.image_tools_label.hide()
            self.dlg.gridLayout.removeWidget(self.image_tools_label)

        self.dlg.gridLayout.addWidget(tabs, 0, 0, 1, 2)
        self.tools_tabs = tabs

    def _build_bottom_controls_layout(self):
        if self.dlg is None or self.bottom_controls_widget is not None:
            return

        self._ensure_dialog_main_layout()

        for legacy_label_name in ("labelx", "labelx_2", "labelx_3", "labelx_4", "labelx_5", "labelx_6"):
            legacy_label = getattr(self.dlg, legacy_label_name, None)
            if legacy_label is not None:
                legacy_label.hide()

        panel = QWidget(self.dlg)
        panel.setObjectName("gridDefinitionPanel")
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        panel.setMinimumHeight(185)
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(8, 8, 8, 10)
        panel_layout.setHorizontalSpacing(10)
        panel_layout.setVerticalSpacing(8)

        self.coord_x0_label = QLabel("x0", panel)
        self.coord_x1_label = QLabel("x1", panel)
        self.coord_y0_label = QLabel("y0", panel)
        self.coord_y1_label = QLabel("y1", panel)
        self.cell_x_label = QLabel("Cell X (m)", panel)
        self.cell_y_label = QLabel("Cell Y (m)", panel)
        for label in (
            self.coord_x0_label,
            self.coord_x1_label,
            self.coord_y0_label,
            self.coord_y1_label,
            self.cell_x_label,
            self.cell_y_label,
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

        panel_layout.addWidget(self.dlg.selectGridPointsButton, 0, 0, 2, 1)
        panel_layout.addWidget(self.coord_x0_label, 0, 1, 1, 1)
        panel_layout.addWidget(self.coord_x1_label, 0, 2, 1, 1)
        panel_layout.addWidget(self.dlg.lineEditX0Y0, 1, 1, 1, 1)
        panel_layout.addWidget(self.dlg.lineEditX1Y0, 1, 2, 1, 1)

        panel_layout.addWidget(self.coord_y0_label, 2, 1, 1, 1)
        panel_layout.addWidget(self.coord_y1_label, 2, 2, 1, 1)
        panel_layout.addWidget(self.dlg.lineEditY0, 3, 1, 1, 1)
        panel_layout.addWidget(self.dlg.lineEditX0Y1, 3, 2, 1, 1)

        panel_layout.addWidget(self.dlg.createGridButton, 4, 0, 1, 1)
        panel_layout.addWidget(self.dlg.lineEditAreaNames, 4, 1, 1, 1)
        if self.internal_grid_checkbox is not None:
            panel_layout.addWidget(self.internal_grid_checkbox, 4, 2, 1, 1, Qt.AlignLeft | Qt.AlignVCenter)

        panel_layout.addWidget(self.cell_x_label, 5, 1, 1, 1)
        panel_layout.addWidget(self.cell_y_label, 5, 2, 1, 1)
        panel_layout.addWidget(self.dlg.lineEditDistanceX, 6, 1, 1, 1)
        panel_layout.addWidget(self.dlg.lineEditDistanceY, 6, 2, 1, 1)

        panel_layout.setColumnMinimumWidth(0, 132)
        panel_layout.setColumnStretch(0, 0)
        panel_layout.setColumnStretch(1, 1)
        panel_layout.setColumnStretch(2, 1)
        panel_layout.setRowMinimumHeight(0, 18)
        panel_layout.setRowMinimumHeight(1, 32)
        panel_layout.setRowMinimumHeight(2, 18)
        panel_layout.setRowMinimumHeight(3, 32)
        panel_layout.setRowMinimumHeight(4, 34)
        panel_layout.setRowMinimumHeight(5, 18)
        panel_layout.setRowMinimumHeight(6, 32)

        self.bottom_controls_widget = panel
        if self.dialog_main_layout is not None:
            self.dialog_main_layout.addWidget(panel, 0)
        else:
            self.dlg.gridLayout_3.addWidget(panel, 2, 0, 1, 4)

    def _swap_drawing_and_navigation_sections(self):
        """
        Put Drawing Options on the right-top block and move Dial/Slider to the left column.
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

        # Right-top hosts drawing options.
        if hasattr(self.dlg, "gridLayout_3"):
            self.dlg.gridLayout_3.addWidget(self.dlg.widget, 0, 2, 1, 2)

    def _tune_visual_layout(self):
        # Remove obsolete instruction block to recover vertical space.
        if hasattr(self.dlg, "textBrowser"):
            self.dlg.textBrowser.hide()
        if hasattr(self.dlg, "lebelstep"):
            self.dlg.lebelstep.hide()

        button_style = (
            "font-size: 9pt; "
            "padding: 4px 8px;"
        )
        label_style = "color: #202020; font-size: 9pt;"

        # Make navigation controls easier to use.
        self.dlg.dial2.setMinimumSize(160, 160)
        self.dlg.dial2.setMaximumSize(220, 220)
        self.dlg.dial2.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.dlg.dial2.setNotchTarget(2.0)
        self.dlg.Dial.setMinimumHeight(42)
        self.dlg.Dial.setMaximumHeight(46)
        if hasattr(self.dlg, "rasterListWidget"):
            self.dlg.rasterListWidget.setMinimumWidth(220)
            self.dlg.rasterListWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.dlg.rasterListWidget.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.dlg.rasterListWidget.setStyleSheet("font-size: 9pt;")
        if hasattr(self.dlg, "groupListWidget"):
            self.dlg.groupListWidget.setMinimumWidth(220)
            self.dlg.groupListWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
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
            self.dlg.zoomSelectedGroupsButton,
            self.dlg.createGroupButton,
            self.dlg.selectGridPointsButton,
            self.dlg.createGridButton,
        ]:
            if btn is not None:
                btn.setMinimumHeight(30)
                btn.setMinimumWidth(108)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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
            getattr(self, "export_layout_button", None),
        ]:
            if btn is not None:
                btn.setMinimumHeight(28)
                btn.setMinimumWidth(96)

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
                input_widget.setMinimumHeight(26)
                input_widget.setStyleSheet("font-size: 9pt;")
        for aux_btn in (self.help_button, self.export_button):
            if aux_btn is not None:
                aux_btn.setMinimumHeight(32)
                aux_btn.setMinimumWidth(86)
                aux_btn.setStyleSheet(button_style)

        if hasattr(self.dlg, "gridLayout"):
            self.dlg.gridLayout.setHorizontalSpacing(8)
            self.dlg.gridLayout.setVerticalSpacing(6)
            self.dlg.gridLayout.setColumnStretch(0, 1)
            self.dlg.gridLayout.setColumnStretch(1, 1)
            self.dlg.gridLayout.setContentsMargins(12, 0, 0, 0)
            self.dlg.gridLayout.setRowStretch(0, 0)
        if hasattr(self.dlg, "gridLayout_3"):
            self.dlg.gridLayout_3.setHorizontalSpacing(10)
            self.dlg.gridLayout_3.setVerticalSpacing(8)
            self.dlg.gridLayout_3.setColumnStretch(0, 8)
            self.dlg.gridLayout_3.setColumnStretch(1, 0)
            self.dlg.gridLayout_3.setColumnStretch(2, 6)
            self.dlg.gridLayout_3.setColumnStretch(3, 0)
            # Keep top-right controls and tools aligned in a stable layout.
            self.dlg.gridLayout_3.addWidget(self.dlg.widget, 0, 2, 1, 2)
            self.dlg.gridLayout_3.addLayout(self.dlg.gridLayout, 1, 2, 1, 2)
            self.dlg.gridLayout_3.setRowStretch(0, 0)
            self.dlg.gridLayout_3.setRowStretch(1, 0)
            self.dlg.gridLayout_3.setRowStretch(2, 0)
            self.dlg.gridLayout_3.setRowStretch(3, 1)
        if hasattr(self.dlg, "verticalLayout_3"):
            # Keep a visible gap between left widgets and separator line.
            self.dlg.verticalLayout_3.setContentsMargins(0, 0, 10, 0)
            self.dlg.verticalLayout_3.setSpacing(8)
            # Ensure Drawing Options keeps enough vertical room at different DPI scales.
            self.dlg.verticalLayout_3.setStretch(0, 0)
            self.dlg.verticalLayout_3.setStretch(1, 3)
            self.dlg.verticalLayout_3.setStretch(2, 0)
            self.dlg.verticalLayout_3.setStretch(3, 3)
            self.dlg.verticalLayout_3.setStretch(4, 4)
        if hasattr(self.dlg, "widget"):
            self.dlg.widget.setMinimumHeight(220)
            self.dlg.widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        if self.left_nav_widget is not None:
            self.left_nav_widget.setMinimumHeight(180)
            self.left_nav_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        if hasattr(self.dlg, "line"):
            self.dlg.line.setFixedWidth(2)
            self.dlg.line.setStyleSheet("color: #9a9a9a;")
        if self.bottom_controls_widget is None:
            self._reposition_bottom_controls()

    def _init_name_raster_panel(self):
        if self.name_raster_panel is not None or self.dlg is None:
            return

        if hasattr(self.dlg, "nomeraster"):
            self.dlg.nomeraster.hide()

        panel = QWidget(self.dlg.layoutWidget)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(2, 2, 2, 2)
        panel_layout.setSpacing(4)

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
        panel.setMinimumHeight(56)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        if hasattr(self.dlg, "verticalLayout_3"):
            self.dlg.verticalLayout_3.addWidget(panel)
        else:
            self.dlg.gridLayout_3.addWidget(panel, 2, 0, 1, 4)
        self._render_name_raster_lines([])

    def _reposition_bottom_controls(self):
        """Place legacy absolute-position controls directly under the main layout block."""
        if self.dlg is None or not hasattr(self.dlg, "layoutWidget"):
            return
        lw = self.dlg.layoutWidget.geometry()
        base_y = lw.y() + lw.height() + 10

        # Column anchors used by the legacy bottom section.
        x_btn = 180
        x_col1 = 300
        x_col2 = 420

        y_row1 = base_y + 22
        y_row1_lbl = y_row1 - 20
        y_row2 = y_row1 + 50
        y_row2_lbl = y_row2 - 20
        y_row3 = y_row2 + 50
        y_row3_lbl = y_row3 - 20

        # Row 1: orientation + x0/x1
        if hasattr(self.dlg, "selectGridPointsButton"):
            self.dlg.selectGridPointsButton.move(x_btn, y_row1)
        if hasattr(self.dlg, "lineEditX0Y0"):
            self.dlg.lineEditX0Y0.move(x_col1, y_row1)
        if hasattr(self.dlg, "lineEditX1Y0"):
            self.dlg.lineEditX1Y0.move(x_col2, y_row1)

        # Row 2: y0/y1
        if hasattr(self.dlg, "lineEditY0"):
            self.dlg.lineEditY0.move(x_col1, y_row2)
        if hasattr(self.dlg, "lineEditX0Y1"):
            self.dlg.lineEditX0Y1.move(x_col2, y_row2)

        # Row 3: draw polygon + name + cell sizes
        if hasattr(self.dlg, "createGridButton"):
            self.dlg.createGridButton.move(90, y_row3)
        if hasattr(self.dlg, "lineEditAreaNames"):
            self.dlg.lineEditAreaNames.move(208, y_row3)
        if hasattr(self.dlg, "lineEditDistanceX"):
            self.dlg.lineEditDistanceX.move(x_col1, y_row3)
        if hasattr(self.dlg, "lineEditDistanceY"):
            self.dlg.lineEditDistanceY.move(x_col2, y_row3)
        if self.internal_grid_checkbox is not None:
            self.internal_grid_checkbox.move(208, y_row3_lbl - 2)

        # Labels aligned to related fields.
        if hasattr(self.dlg, "labelx_3"):
            self.dlg.labelx_3.move(x_col1, y_row2_lbl)
        if hasattr(self.dlg, "labelx_4"):
            self.dlg.labelx_4.move(x_col2, y_row2_lbl)
        if hasattr(self.dlg, "labelx_6"):
            self.dlg.labelx_6.move(x_col1, y_row3_lbl)
            self.dlg.labelx_6.setFixedWidth(86)
            self.dlg.labelx_6.setText("Cell X (m)")
            self.dlg.labelx_6.setStyleSheet("color: #202020; font-size: 9pt;")
        if hasattr(self.dlg, "labelx_5"):
            self.dlg.labelx_5.move(x_col2, y_row3_lbl)
            self.dlg.labelx_5.setFixedWidth(86)
            self.dlg.labelx_5.setText("Cell Y (m)")
            self.dlg.labelx_5.setStyleSheet("color: #202020; font-size: 9pt;")

        # Hide legacy x0/x1 labels in the top layout and create aligned labels near fields.
        if hasattr(self.dlg, "labelx"):
            self.dlg.labelx.hide()
        if hasattr(self.dlg, "labelx_2"):
            self.dlg.labelx_2.hide()
        if not hasattr(self, "_coord_x0_label"):
            self._coord_x0_label = QLabel("x0", self.dlg)
            self._coord_x1_label = QLabel("x1", self.dlg)
            self._coord_x0_label.setStyleSheet("color: #202020; font-size: 9pt;")
            self._coord_x1_label.setStyleSheet("color: #202020; font-size: 9pt;")
        self._coord_x0_label.setGeometry(x_col1, y_row1_lbl, 40, 16)
        self._coord_x1_label.setGeometry(x_col2, y_row1_lbl, 40, 16)
        self._coord_x0_label.show()
        self._coord_x1_label.show()

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
        self._set_button_icon(getattr(self.dlg, "selectGridPointsButton", None), "mActionCapturePoint.svg", "mActionMoveVertex.svg")
        self._set_button_icon(getattr(self, "enhance_minmax_button", None), "mActionRasterHistogram.svg", "mActionOptions.svg")
        self._set_button_icon(getattr(self, "enhance_batch_button", None), "mActionRasterHistogram.svg", "mActionFilter2.svg")
        self._set_button_icon(getattr(self, "save_style_button", None), "mActionFileSave.svg", "mActionSaveAs.svg")
        self._set_button_icon(getattr(self, "load_style_button", None), "mActionFileOpen.svg", "mActionAddRasterLayer.svg")
        self._set_button_icon(getattr(self, "export_layout_button", None), "mActionSaveAsPDF.svg", "mActionSaveAs.svg")
        self._set_button_icon(getattr(self, "help_button", None), "mActionHelpContents.svg", "mActionOptions.svg")
        self._set_button_icon(getattr(self, "export_button", None), "mActionSaveAs.svg", "mActionFileSave.svg")

    def _build_grid_options_controls(self):
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

