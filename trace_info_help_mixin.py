# -*- coding: utf-8 -*-
"""Help and query-panel behaviors for Trace Info panel."""

from qgis.PyQt.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QSizePolicy, QTabWidget, QVBoxLayout, QWidget


class TraceInfoHelpMixin:
    def _toggle_trace_query_panel(self, checked):
        visible = bool(checked)
        if self.trace_info_query_panel is not None:
            self.trace_info_query_panel.setVisible(visible)
        if self.trace_info_query_btn is not None and self.trace_info_query_btn.isCheckable():
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
                    "3) Open 2D/3D Draw Panel from the pencil icon.\n"
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
                    "Build 3D Batch: runs selected mode on all trace layers.\n"
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
