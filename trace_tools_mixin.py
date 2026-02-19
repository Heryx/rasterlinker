# -*- coding: utf-8 -*-
"""Trace tools mixin for RasterLinker plugin."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QToolButton


class TraceToolsMixin:
    def _add_trace_toolbar_action(self, toolbar, text, callback, *icon_names):
        icon = self._qgis_theme_icon(*icon_names)
        if icon is None or icon.isNull():
            icon = QIcon(':/plugins/gpr_linker/icon.png')
        action = QAction(icon, text, self.iface.mainWindow())
        action.setToolTip(text)
        action.triggered.connect(callback)
        if toolbar is not None:
            toolbar.addAction(action)
        self.trace_toolbar_actions[text] = action
        return action

    def _ensure_trace_actions(self):
        if self.trace_toolbar_actions:
            return
        self._add_trace_toolbar_action(
            None,
            "New Line Layer",
            self.create_trace_line_layer,
            "mActionNewVectorLayer.svg",
            "mActionAddGroup.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Draw 2D Line",
            self.start_trace_capture,
            "mActionToggleEditing.svg",
            "mActionAddFeature.svg",
            "mActionCaptureLine.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Save Edits",
            self.save_trace_layer_edits,
            "mActionSaveEdits.svg",
            "mActionFileSave.svg",
            "mActionSaveAs.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Vertex Tool",
            self.activate_trace_vertex_tool,
            "mActionVertexTool.svg",
            "mActionNodeTool.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Split Feature",
            self.split_trace_feature,
            "mActionSplitFeatures.svg",
            "mActionSplitParts.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Copy",
            self.copy_trace_features,
            "mActionEditCopy.svg",
            "mActionCopySelected.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Paste",
            self.paste_trace_features,
            "mActionEditPaste.svg",
            "mActionPasteFeatures.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Delete",
            self.delete_trace_features,
            "mActionDeleteSelected.svg",
            "mActionDeleteSelectedFeatures.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Line Info",
            self.open_trace_info_tab,
            "mActionOpenTable.svg",
            "mActionOpenTable.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Build 3D",
            self.build_trace_3d_from_depth,
            "mActionTransform.svg",
            "mAction3DMap.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Orthometric 3D",
            self.build_trace_3d_orthometric,
            "mActionRasterCalculator.svg",
            "mAction3DMap.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Export Layer",
            self.export_active_trace_layer,
            "mActionFileSaveAs.svg",
            "mActionSaveAs.svg",
        )

    def _init_trace_toolbar(self):
        if self.trace_toolbar is not None:
            return
        self._ensure_trace_actions()
        toolbar = self.iface.addToolBar("RasterLinker Trace")
        toolbar.setObjectName("RasterLinkerTraceToolbar")
        toolbar.setToolTip("RasterLinker 2D/3D trace tools")
        for name in (
            "New Line Layer",
            "Draw 2D Line",
            "Save Edits",
            "Vertex Tool",
            "Split Feature",
            "Copy",
            "Paste",
            "Delete",
            "Line Info",
        ):
            action = self.trace_toolbar_actions.get(name)
            if action is not None:
                toolbar.addAction(action)
        toolbar.addSeparator()
        for name in ("Build 3D", "Orthometric 3D", "Export Layer"):
            action = self.trace_toolbar_actions.get(name)
            if action is not None:
                toolbar.addAction(action)
        self.trace_toolbar = toolbar

    def _trigger_iface_action(self, *action_getters):
        for getter_name in action_getters:
            getter = getattr(self.iface, getter_name, None)
            if not callable(getter):
                continue
            try:
                qaction = getter()
                if qaction is None:
                    continue
                qaction.trigger()
                return True
            except Exception:
                continue
        return False

    def _build_trace_info_tools_panel(self, parent):
        self._ensure_trace_actions()
        tools_widget = QWidget(parent)
        tools_layout = QHBoxLayout(tools_widget)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(3)

        action_order = (
            "New Line Layer",
            "Draw 2D Line",
            "Save Edits",
            "Vertex Tool",
            "Split Feature",
            "Copy",
            "Paste",
            "Delete",
            "Build 3D",
            "Orthometric 3D",
            "Export Layer",
        )
        for action_name in action_order:
            action = self.trace_toolbar_actions.get(action_name)
            if action is None:
                continue
            btn = QToolButton(tools_widget)
            btn.setDefaultAction(action)
            btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
            btn.setMinimumSize(26, 26)
            btn.setMaximumSize(30, 30)
            btn.setToolTip(action_name)
            tools_layout.addWidget(btn, 0)

        tools_layout.addStretch(1)
        return tools_widget

