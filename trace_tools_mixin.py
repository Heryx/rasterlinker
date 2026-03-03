# -*- coding: utf-8 -*-
"""Trace tools mixin for GeoSurvey Studio plugin."""

import os
from typing import Any, Callable, Dict, List

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QToolButton, QMessageBox, QFrame, QMenu
from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes, QgsMessageLog, Qgis
from .layer_property_utils import get_layer_property

from .project_catalog import load_catalog


class TraceToolsMixin:
    def _init_trace_tools(self) -> None:
        """Initialise trace toolbar state. Called by the plugin constructor."""
        self.trace_toolbar = None
        self.trace_info_action = None
        self.trace_toolbar_actions = {}

    def _cleanup_trace_tools(self) -> None:
        """Teardown trace toolbar state. Called by plugin unload()."""
        if self.trace_toolbar is not None:
            try:
                self.iface.mainWindow().removeToolBar(self.trace_toolbar)
            except Exception as e:
                QgsMessageLog.logMessage(str(e), "GeoSurvey Studio", Qgis.Warning)
            try:
                self.trace_toolbar.deleteLater()
            except Exception as e:
                QgsMessageLog.logMessage(str(e), "GeoSurvey Studio", Qgis.Warning)
        self.trace_toolbar = None
        self.trace_info_action = None
        self.trace_toolbar_actions = {}

    def _layer_is_in_trace_group(self, layer: Any) -> bool:
        if layer is None:
            return False
        try:
            root = QgsProject.instance().layerTreeRoot()
            node = root.findLayer(layer.id()) if root is not None else None
        except Exception:
            node = None
        if node is None:
            return False
        try:
            parent = node.parent()
            while parent is not None:
                name = str(parent.name() or "").strip().lower()
                if name in ("line traces", "linetraces"):
                    return True
                parent = parent.parent()
        except Exception:
            return False
        return False

    def _safe_feature_count(self, layer: Any) -> int:
        if layer is None:
            return 0
        try:
            return int(layer.featureCount())
        except Exception:
            return 0

    def _line_layers_in_project(self) -> List[QgsVectorLayer]:
        layers: List[QgsVectorLayer] = []
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue
            try:
                if lyr.geometryType() != QgsWkbTypes.LineGeometry:
                    continue
            except Exception:
                continue
            if not lyr.isValid():
                continue
            layers.append(lyr)
        return layers

    def _is_trace_related_line_layer(self, layer: Any) -> bool:
        if layer is None:
            return False
        # Group-based fallback for legacy projects: any line under "Line Traces"
        # is considered trace-related and can be schema-upgraded on demand.
        if self._layer_is_in_trace_group(layer):
            return True
        source_kind = str(get_layer_property(layer, "source_kind", default="") or "").strip().lower()
        if source_kind in ("trace2d", "trace2d_3d", "trace"):
            return True
        try:
            field_names = {f.name() for f in layer.fields()}
        except Exception:
            return False
        return "trace_id" in field_names or ("z_mode" in field_names and "z_source" in field_names)

    def _layer_source_file_exists(self, layer: Any) -> bool:
        if layer is None:
            return False
        src = (layer.source() or "").strip()
        if not src or src.lower().startswith("memory:"):
            return False
        base = src.split("|", 1)[0].strip()
        if not base:
            return False
        return os.path.exists(base)

    def _collect_end_to_end_workflow_status(self) -> List[Dict[str, Any]]:
        status: List[Dict[str, Any]] = []

        project_root = self._require_project_root(notify=False) if hasattr(self, "_require_project_root") else None
        project_ok = bool(project_root and os.path.isdir(project_root))
        status.append(
            {
                "step": "1) Project Manager",
                "ok": project_ok,
                "details": (
                    f"Project linked: {project_root}"
                    if project_ok
                    else "No active GeoSurvey Studio project. Open Project Manager first."
                ),
            }
        )

        groups_with_images = 0
        loaded_rasters = 0
        active_ts_id = ""
        if project_ok:
            try:
                catalog = load_catalog(project_root)
                groups_with_images = len([g for g in catalog.get("raster_groups", []) if g.get("timeslice_ids")])
            except Exception:
                groups_with_images = 0
            try:
                loaded_rasters = len(list(self._iter_plugin_raster_layers() or []))
            except Exception:
                loaded_rasters = 0
            try:
                payload = self._active_timeslice_payload() if hasattr(self, "_active_timeslice_payload") else None
                if isinstance(payload, dict):
                    active_ts_id = (payload.get("timeslice_id") or "").strip()
            except Exception:
                active_ts_id = ""

        import_ok = bool(project_ok and groups_with_images > 0 and loaded_rasters > 0 and active_ts_id)
        status.append(
            {
                "step": "2) Import Group",
                "ok": import_ok,
                "details": (
                    f"Groups with images: {groups_with_images}, loaded rasters: {loaded_rasters}, active time-slice: {active_ts_id}"
                    if import_ok
                    else (
                        f"Need imported/loaded group and active time-slice. "
                        f"(groups with images: {groups_with_images}, loaded rasters: {loaded_rasters}, active time-slice: {'yes' if active_ts_id else 'no'})"
                    )
                ),
            }
        )

        trace_layer = self._current_trace_layer(prefer_active=True, require_trace=True) if hasattr(self, "_current_trace_layer") else None
        trace_layer_name = trace_layer.name() if trace_layer is not None else ""
        trace_feature_count = self._safe_feature_count(trace_layer)
        draw_ok = bool(trace_layer is not None and trace_feature_count > 0)
        status.append(
            {
                "step": "3) Draw 2D",
                "ok": draw_ok,
                "details": (
                    f"Trace layer: {trace_layer_name}, features: {trace_feature_count}"
                    if draw_ok
                    else "No trace features detected yet. Draw at least one 2D line."
                ),
            }
        )

        has_pending_edits = False
        if trace_layer is not None:
            try:
                has_pending_edits = bool(trace_layer.isEditable() and trace_layer.isModified())
            except Exception:
                has_pending_edits = False
        save_ok = bool(draw_ok and not has_pending_edits)
        status.append(
            {
                "step": "4) Save Edits",
                "ok": save_ok,
                "details": (
                    "No pending edits on active trace layer."
                    if save_ok
                    else "Pending edits detected. Use 'Save Edits' before Build 3D."
                ),
            }
        )

        line_layers = self._line_layers_in_project()
        trace_line_layers = [lyr for lyr in line_layers if self._is_trace_related_line_layer(lyr)]
        built3d_layers = [lyr for lyr in trace_line_layers if QgsWkbTypes.hasZ(lyr.wkbType())]
        build_ok = len(built3d_layers) > 0
        status.append(
            {
                "step": "5) Build 3D",
                "ok": build_ok,
                "details": (
                    f"3D line layers found: {len(built3d_layers)}"
                    if build_ok
                    else "No 3D line layer detected. Run 'Build 3D' first."
                ),
            }
        )

        exported_layers = [lyr for lyr in trace_line_layers if self._layer_source_file_exists(lyr)]
        export_ok = len(exported_layers) > 0
        status.append(
            {
                "step": "6) Export",
                "ok": export_ok,
                "details": (
                    f"Exported file-backed line layers: {len(exported_layers)}"
                    if export_ok
                    else "No exported line file detected in project. Use 'Export Layer'."
                ),
            }
        )

        return status

    def run_end_to_end_workflow_check(self, checked: bool = False) -> None:
        status = self._collect_end_to_end_workflow_status()
        total = len(status)
        passed = len([s for s in status if s.get("ok")])
        first_missing = next((s for s in status if not s.get("ok")), None)

        lines = [
            "GeoSurvey Studio End-to-End Workflow Check",
            "",
            f"Passed: {passed}/{total}",
            "",
        ]
        for item in status:
            state = "OK" if item.get("ok") else "MISSING"
            lines.append(f"[{state}] {item.get('step')}")
            lines.append(f"    {item.get('details')}")
        if first_missing is not None:
            lines.extend(
                [
                    "",
                    f"First blocking step: {first_missing.get('step')}",
                    "Complete it, then run Workflow Check again.",
                ]
            )

        title = "Workflow Check"
        text = "\n".join(lines)
        if passed == total:
            QMessageBox.information(self._ui_parent(), title, text)
        else:
            QMessageBox.warning(self._ui_parent(), title, text)

    def _add_trace_toolbar_action(
        self,
        toolbar: Any,
        text: str,
        callback: Callable[..., Any],
        *icon_names: str,
        checkable: bool = False,
    ) -> QAction:
        icon = self._qgis_theme_icon(*icon_names)
        if icon is None or icon.isNull():
            icon = QIcon(':/plugins/geosurvey_studio/icon.png')
        action = QAction(icon, text, self.iface.mainWindow())
        action.setToolTip(text)
        action.setCheckable(bool(checkable))
        action.triggered.connect(callback)
        if toolbar is not None:
            toolbar.addAction(action)
        self.trace_toolbar_actions[text] = action
        return action

    def _ensure_trace_actions(self) -> None:
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
            checkable=True,
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
            "Clean Orphans",
            self.clean_orphan_traces,
            "mActionDeleteSelected.svg",
            "mActionDeleteAllFeatures.svg",
            "mActionRemoveLayer.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Draw Panel",
            self.open_trace_info_tab,
            "mActionOpenTable.svg",
            "mActionOpenTable.svg",
        )
        self._add_trace_toolbar_action(
            None,
            "Workflow Check",
            self.run_end_to_end_workflow_check,
            "mActionCheckValidity.svg",
            "mActionOptions.svg",
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
            "Build 3D Batch",
            self.build_trace_3d_batch,
            "mActionSelectByExpression.svg",
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
        self._add_trace_toolbar_action(
            None,
            "Generate Vertices",
            self.generate_trace_vertex_layer,
            "mActionExtractVertices.svg",
            "mActionPointLayerFromTable.svg",
            "mActionAddPointLayer.svg",
        )

    def _init_trace_toolbar(self) -> None:
        if self.trace_toolbar is not None:
            return
        self._ensure_trace_actions()
        toolbar = self.iface.addToolBar("GeoSurvey Studio Trace")
        toolbar.setObjectName("GeoSurveyStudioTraceToolbar")
        toolbar.setToolTip("GeoSurvey Studio 2D/3D trace tools")
        for name in (
            "New Line Layer",
            "Draw 2D Line",
            "Save Edits",
            "Vertex Tool",
            "Split Feature",
            "Copy",
            "Paste",
            "Delete",
            "Clean Orphans",
            "Draw Panel",
            "Workflow Check",
        ):
            action = self.trace_toolbar_actions.get(name)
            if action is not None:
                toolbar.addAction(action)
        toolbar.addSeparator()
        for name in ("Generate Vertices", "Build 3D", "Build 3D Batch", "Orthometric 3D", "Export Layer"):
            action = self.trace_toolbar_actions.get(name)
            if action is not None:
                toolbar.addAction(action)
        self.trace_toolbar = toolbar

    def _trigger_iface_action(self, *action_getters: str) -> bool:
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

    def _build_trace_info_tools_panel(self, parent: QWidget) -> QWidget:
        self._ensure_trace_actions()
        tools_widget = QWidget(parent)
        tools_layout = QHBoxLayout(tools_widget)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(3)

        def _add_button(action_name):
            action = self.trace_toolbar_actions.get(action_name)
            if action is None:
                return
            btn = QToolButton(tools_widget)
            btn.setDefaultAction(action)
            btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
            btn.setMinimumSize(26, 26)
            btn.setMaximumSize(30, 30)
            btn.setToolTip(action_name)
            tools_layout.addWidget(btn, 0)

        def _add_separator():
            sep = QFrame(tools_widget)
            sep.setFrameShape(QFrame.VLine)
            sep.setFrameShadow(QFrame.Sunken)
            sep.setLineWidth(1)
            sep.setMidLineWidth(0)
            tools_layout.addWidget(sep, 0)

        def _add_menu_button(title, action_names, icon_from=None, tooltip=None):
            menu = QMenu(tools_widget)
            has_items = False
            for action_name in action_names:
                act = self.trace_toolbar_actions.get(action_name)
                if act is None:
                    continue
                menu.addAction(act)
                has_items = True
            if not has_items:
                return
            btn = QToolButton(tools_widget)
            btn.setText(title)
            btn.setPopupMode(QToolButton.InstantPopup)
            btn.setMenu(menu)
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setMinimumHeight(26)
            btn.setToolTip(tooltip or title)
            if icon_from:
                icon_action = self.trace_toolbar_actions.get(icon_from)
                if icon_action is not None and icon_action.icon() is not None:
                    btn.setIcon(icon_action.icon())
            tools_layout.addWidget(btn, 0)

        # Core draw workflow (always visible).
        for action_name in ("New Line Layer", "Draw 2D Line", "Save Edits"):
            _add_button(action_name)
        _add_separator()

        # Advanced edit tools (collapsed in menu).
        _add_menu_button(
            "Edit Tools",
            ("Vertex Tool", "Split Feature", "Copy", "Paste", "Delete", "Clean Orphans"),
            icon_from="Vertex Tool",
            tooltip="Geometry editing and cleanup tools",
        )

        # Build/export/check utilities (collapsed in menu).
        _add_menu_button(
            "Build/Export",
            (
                "Build 3D",
                "Build 3D Batch",
                "Orthometric 3D",
                "Generate Vertices",
                "Export Layer",
                "Workflow Check",
            ),
            icon_from="Build 3D",
            tooltip="3D build, vertices generation, export and workflow checks",
        )

        tools_layout.addStretch(1)
        return tools_widget
