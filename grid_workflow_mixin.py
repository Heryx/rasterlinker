from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QDialog,
    QLabel,
    QFormLayout,
    QLineEdit,
    QInputDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
)
from qgis.core import (
    Qgis,
    QgsLayerTreeGroup,
    QgsPointLocator,
    QgsProject,
    QgsSnappingConfig,
    QgsTolerance,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

from .grid_creator import create_oriented_grid
from .grid_selection_tool import GridSelectionTool
from .polygon_grid_creator import create_grid_from_polygon
from .polygon_draw_tool import PolygonDrawTool


class GridWorkflowMixin:
    def _orientation_main_fields_map(self):
        dlg = getattr(self, "dlg", None)
        if dlg is None:
            return {}
        return {
            "x0": getattr(dlg, "lineEditX0Y0", None),
            "x1": getattr(dlg, "lineEditX1Y0", None),
            "y0": getattr(dlg, "lineEditY0", None),
            "y1": getattr(dlg, "lineEditX0Y1", None),
        }

    def _sync_orientation_helper_fields_from_main(self):
        edits = getattr(self, "orientation_helper_edits", None)
        if not isinstance(edits, dict):
            return
        if getattr(self, "_orientation_helper_syncing", False):
            return
        self._orientation_helper_syncing = True
        try:
            for key, widget in self._orientation_main_fields_map().items():
                helper_edit = edits.get(key)
                if helper_edit is None or widget is None:
                    continue
                txt = widget.text()
                if helper_edit.text() != txt:
                    helper_edit.setText(txt)
        finally:
            self._orientation_helper_syncing = False

    def _sync_main_field_from_orientation_helper(self, key, value):
        if getattr(self, "_orientation_helper_syncing", False):
            return
        target = self._orientation_main_fields_map().get(key)
        if target is None:
            return
        self._orientation_helper_syncing = True
        try:
            text_value = "" if value is None else str(value)
            if target.text() != text_value:
                target.setText(text_value)
        finally:
            self._orientation_helper_syncing = False

    def _get_or_create_cell_grids_group(self):
        """
        Return a dedicated group for generated grid/cell layers, separated from time-slice groups.
        """
        root_group_getter = getattr(self, "_get_plugin_root_group", None)
        if not callable(root_group_getter):
            return None
        plugin_root = root_group_getter()
        if plugin_root is None:
            return None
        target_name = "Cell Grids"
        group = next(
            (
                g for g in plugin_root.children()
                if isinstance(g, QgsLayerTreeGroup) and g.name() == target_name
            ),
            None,
        )
        if group is None:
            group = plugin_root.addGroup(target_name)
        return group

    def _place_layer_in_cell_grids_group(self, layer):
        """
        Move a layer-tree node into GeoSurvey Studio > Cell Grids.
        Keeps the map layer itself intact and only re-parents the tree node.
        """
        if layer is None:
            return
        group = self._get_or_create_cell_grids_group()
        if group is None:
            return
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        node = root.findLayer(layer.id())
        if node is None:
            # Ensure layer is present in project, then attach it under the dedicated group.
            project.addMapLayer(layer, False)
            group.addLayer(layer)
            return
        if node.parent() is group:
            return
        parent = node.parent()
        clone = node.clone()
        group.addChildNode(clone)
        if parent is not None:
            parent.removeChildNode(node)

    def _resolve_vector_storage_mode_for_grid(self):
        mode = getattr(self, "pending_vector_storage_mode", None)
        if mode in ("memory", "gpkg"):
            return mode
        if hasattr(self, "_trace_vector_storage_mode"):
            try:
                return self._trace_vector_storage_mode()
            except Exception:
                pass
        return "memory"

    def _choose_vector_storage_mode_for_grid(self):
        chooser = getattr(self, "_prompt_trace_vector_storage_mode", None)
        if callable(chooser):
            mode = chooser("Grid/Area Vector Storage")
            if mode in ("memory", "gpkg"):
                self.pending_vector_storage_mode = mode
                return mode
            return None
        self.pending_vector_storage_mode = "memory"
        return "memory"

    def _persist_project_vector_layer_if_needed(self, layer, storage_mode=None, source_kind=None):
        if layer is None:
            return None
        mode = storage_mode or self._resolve_vector_storage_mode_for_grid()
        if mode != "gpkg":
            return layer

        persist_fn = getattr(self, "_persist_vector_layer_to_project_gpkg", None)
        if not callable(persist_fn):
            return layer

        kind = (source_kind or "").strip()
        if not kind:
            try:
                kind = str(layer.customProperty("rasterlinker/source_kind", "") or "").strip()
            except Exception:
                kind = ""
        if not kind:
            kind = "grid_vector"

        persisted, out_path, err = persist_fn(layer, layer.name() or "layer", source_kind=kind)
        if persisted is None:
            QMessageBox.warning(
                self._ui_parent(),
                "Persistent Layer",
                (
                    "Unable to create GeoPackage layer in project folder.\n"
                    f"Reason: {err}\n\n"
                    "Continuing with temporary memory layer."
                ),
            )
            return layer

        # Keep layer placement close to original position/group when possible.
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        old_node = root.findLayer(layer.id()) if layer is not None else None
        parent_group = old_node.parent() if old_node is not None else None
        insert_index = -1
        if parent_group is not None:
            try:
                insert_index = parent_group.children().index(old_node)
            except Exception:
                insert_index = -1

        project.addMapLayer(persisted, False)
        if parent_group is not None:
            if insert_index >= 0:
                parent_group.insertLayer(insert_index, persisted)
            else:
                parent_group.addLayer(persisted)
        else:
            project.layerTreeRoot().addLayer(persisted)

        if layer is not None and project.mapLayer(layer.id()) is not None:
            project.removeMapLayer(layer.id())

        if out_path:
            self._notify_info(f"Saved persistent layer: {out_path}", duration=6)
        return persisted

    def create_grid_from_polygon_layer(self):
        """
        Draw a polygon interactively and create a grid from it.
        """
        try:
            self._sync_grid_options_from_controls()
            chosen = self._choose_vector_storage_mode_for_grid()
            if chosen is None:
                return
            # Resolve "Ask" once before drawing starts, so no modal popup interrupts clicks.
            session_mode = None
            if str(getattr(self, "grid_dimension_mode", "ask")).strip().lower() == "ask":
                options = ["Canvas (Free)", "Manual input"]
                selected, ok = QInputDialog.getItem(
                    self.dlg,
                    "Dimension input",
                    "Choose how to define dimensions for this draw session:",
                    options,
                    0,
                    False,
                )
                if not ok:
                    return
                session_mode = "manual" if selected == "Manual input" else "canvas"
            # Activate polygon drawing tool
            self.polygon_draw_tool = PolygonDrawTool(
                self.iface.mapCanvas(),
                self,
                session_dimension_mode=session_mode,
            )
            self.iface.mapCanvas().setMapTool(self.polygon_draw_tool)

            mode_label = session_mode or str(getattr(self, "grid_dimension_mode", "ask")).strip().lower()
            self._notify_info(
                "Draw area: left-click vertices, right-click/Enter to close, ESC to cancel. "
                "Oriented rectangle: first click origin, then D/middle-click for dimensions. "
                f"Dock: Snap, Ortho 0/90, Mode={mode_label}. Name: Area|Prefix."
            )
        except Exception as e:
            QMessageBox.critical(self.dlg, "Error", f"Error while activating drawing tool: {e}")

    
    def create_grid_from_drawn_polygon(self, polygon_layer):
        """
        Create a grid based on the polygon drawn by the user.
        """
        try:
            storage_mode = self._resolve_vector_storage_mode_for_grid()
            internal_enabled = bool(self.internal_grid_checkbox.isChecked()) if self.internal_grid_checkbox is not None else True
            grid_layer = None
            area_name, cell_prefix = self._get_grid_names_from_ui()
            polygon_layer.setName(area_name)
            polygon_layer.setCustomProperty("rasterlinker/source_kind", "grid_area")
            polygon_layer = self._persist_project_vector_layer_if_needed(
                polygon_layer,
                storage_mode=storage_mode,
                source_kind="grid_area",
            )
            if not self._confirm_planar_units_for_grid():
                return

            if internal_enabled:
                # Read spacing values from UI only when internal grid is enabled.
                distance_x = float(self.dlg.lineEditDistanceX.text().strip())
                distance_y = float(self.dlg.lineEditDistanceY.text().strip())
                # Create grid
                grid_layer = create_grid_from_polygon(
                    polygon_layer,
                    distance_x,
                    distance_y,
                    area_name=area_name,
                    cell_prefix=cell_prefix,
                    max_cells=120000,
                )
                if grid_layer is not None:
                    grid_layer.setCustomProperty("rasterlinker/source_kind", "grid_cells")
                grid_layer = self._persist_project_vector_layer_if_needed(
                    grid_layer,
                    storage_mode=storage_mode,
                    source_kind="grid_cells",
                )
                self._place_layer_in_cell_grids_group(grid_layer)
                self.last_grid_layer = grid_layer
            else:
                self.last_grid_layer = None

            # Safety rule:
            # when Internal grid is disabled, keep the area layer visible even if
            # "Keep area" is unchecked, otherwise no output layer remains.
            keep_area_in_project = bool(self.keep_source_polygon) or not internal_enabled
            if not keep_area_in_project and QgsProject.instance().mapLayer(polygon_layer.id()) is not None:
                QgsProject.instance().removeMapLayer(polygon_layer.id())
                self.last_area_layer = None
            else:
                # Keep area layer in the same dedicated Cell Grids container.
                self._place_layer_in_cell_grids_group(polygon_layer)
                self.last_area_layer = polygon_layer

            if internal_enabled:
                self._notify_info(f"Area '{area_name}' created. Cells with prefix '{cell_prefix}'.")
            else:
                self._notify_info(
                    f"Area '{area_name}' created. Internal grid is disabled, so only the area polygon was generated."
                )
        except ValueError as ve:
            QMessageBox.warning(self.dlg, "Error", f"Error: {ve}")
        except Exception as e:
            QMessageBox.critical(self.dlg, "Error", f"Error while creating grid: {e}")
        finally:
            self.pending_vector_storage_mode = None

    def _get_grid_names_from_ui(self):
        """
        Estrae i nomi da lineEditAreaNames.
        Formato supportato: "NomeArea|PrefissoCelle"
        Se manca il prefisso: "NomeArea" -> prefisso automatico.
        """
        raw_value = self.dlg.lineEditAreaNames.text().strip()
        if not raw_value:
            area_name = "Area_Indagine"
            return area_name, f"{area_name}_cell"

        if "|" in raw_value:
            parts = [p.strip() for p in raw_value.split("|", 1)]
            area_name = parts[0] or "Area_Indagine"
            cell_prefix = parts[1] or f"{area_name}_cell"
            return area_name, cell_prefix

        area_name = raw_value
        return area_name, f"{area_name}_cell"


    def _notify_info(self, message, duration=6):
        self.iface.messageBar().pushMessage("GeoSurvey Studio", message, level=Qgis.Info, duration=duration)

    def _ui_parent(self):
        return self.dlg if self.dlg is not None else self.iface.mainWindow()

    def _sync_grid_options_from_controls(self):
        if self.snap_checkbox is not None:
            self.grid_use_snap = bool(self.snap_checkbox.isChecked())
        if self.snap_mode_combo is not None:
            mode = self.snap_mode_combo.currentData()
            self.grid_snap_mode = mode if mode in ("all", "vertex_segment", "vertex", "segment", "intersection") else "all"
        if self.snap_tolerance_spin is not None:
            self.grid_snap_tolerance = float(self.snap_tolerance_spin.value())
        if self.snap_units_combo is not None:
            unit = self.snap_units_combo.currentData()
            self.grid_snap_units = unit if unit in ("pixels", "mm", "cm", "map_units") else "pixels"
        if self.ortho_checkbox is not None:
            self.grid_force_orthogonal = bool(self.ortho_checkbox.isChecked())
        if self.ortho_base_checkbox is not None:
            self.grid_relative_orthogonal = bool(self.ortho_base_checkbox.isChecked())
        if self.keep_area_checkbox is not None:
            self.keep_source_polygon = bool(self.keep_area_checkbox.isChecked())
        if self.dimension_mode_combo is not None:
            mode = self.dimension_mode_combo.currentData()
            self.grid_dimension_mode = mode if mode in ("ask", "manual", "canvas") else "ask"
        if self.internal_grid_checkbox is not None:
            self.grid_internal_enabled = bool(self.internal_grid_checkbox.isChecked())
        self._apply_grid_snapping_config()

    def _on_internal_grid_toggled(self, checked):
        enabled = bool(checked)
        if hasattr(self.dlg, "lineEditDistanceX"):
            self.dlg.lineEditDistanceX.setEnabled(enabled)
        if hasattr(self.dlg, "lineEditDistanceY"):
            self.dlg.lineEditDistanceY.setEnabled(enabled)
        label_style = "color: #202020; font-size: 9pt;" if enabled else "color: #6d6d6d; font-size: 9pt;"
        for label in (self.cell_x_label, self.cell_y_label):
            if label is not None:
                label.setStyleSheet(label_style)

    def get_snap_filter(self):
        edge_flag = getattr(QgsPointLocator, "Edge", getattr(QgsPointLocator, "Segment", 0))
        intersection_flag = getattr(QgsPointLocator, "Intersection", 0)
        vertex_flag = getattr(QgsPointLocator, "Vertex", 0)
        all_flag = getattr(QgsPointLocator, "All", vertex_flag | edge_flag | intersection_flag)
        mode = getattr(self, "grid_snap_mode", "all")
        if mode == "vertex":
            return vertex_flag
        if mode == "segment":
            return edge_flag
        if mode == "vertex_segment":
            return vertex_flag | edge_flag
        if mode == "intersection":
            return intersection_flag
        return all_flag

    def _snap_tolerance_pixels(self):
        value = float(getattr(self, "grid_snap_tolerance", 12.0))
        units = getattr(self, "grid_snap_units", "pixels")
        if units == "pixels":
            return value
        if units in ("mm", "cm"):
            dpi = float(self.iface.mapCanvas().logicalDpiX() or 96.0)
            mm_value = value if units == "mm" else value * 10.0
            return mm_value * dpi / 25.4
        return value

    def _apply_grid_snapping_config(self):
        """Apply plugin snap options to the current QGIS snapping config."""
        try:
            project = QgsProject.instance()
            config = QgsSnappingConfig(project.snappingConfig())
            config.setEnabled(bool(self.grid_use_snap))

            if hasattr(QgsSnappingConfig, "AllLayers"):
                config.setMode(QgsSnappingConfig.AllLayers)
            elif hasattr(QgsSnappingConfig, "SnappingMode"):
                config.setMode(QgsSnappingConfig.SnappingMode.AllLayers)

            snap_filter = self.get_snap_filter()
            if hasattr(config, "setTypeFlag"):
                config.setTypeFlag(snap_filter)
            elif hasattr(config, "setType"):
                config.setType(snap_filter)

            if self.grid_snap_units == "map_units":
                config.setUnits(QgsTolerance.MapUnits)
                config.setTolerance(float(self.grid_snap_tolerance))
            else:
                config.setUnits(QgsTolerance.Pixels)
                config.setTolerance(float(self._snap_tolerance_pixels()))

            project.setSnappingConfig(config)
        except Exception:
            # Keep plugin functional even if API differs across QGIS versions.
            pass

    def update_draw_indicators(self, angle_rad=None, length=None):
        if self.base_angle_label is None:
            return
        if angle_rad is None:
            self.base_angle_label.setText("Base: --")
        else:
            angle_deg = (angle_rad * 180.0 / 3.141592653589793) % 360.0
            self.base_angle_label.setText(f"Base: {angle_deg:.1f}deg")

        if self.length_label is not None:
            if length is None:
                self.length_label.setText("Len: --")
            else:
                self.length_label.setText(f"Len: {length:.2f}")

    # Backward compatibility with existing calls.
    def update_base_angle_indicator(self, angle_rad=None):
        self.update_draw_indicators(angle_rad=angle_rad, length=None)

    def _set_orientation_status(self, text):
        status_text = str(text or "").strip() or "Orientation: idle"
        label = getattr(self, "orientation_status_label", None)
        if label is not None:
            label.setText(status_text)
        helper_label = getattr(self, "orientation_helper_status_label", None)
        if helper_label is not None:
            helper_label.setText(status_text)

    def _ensure_orientation_helper_dialog(self):
        dialog = getattr(self, "orientation_helper_dialog", None)
        if dialog is not None:
            return dialog

        parent = self._ui_parent()
        dialog = QDialog(parent)
        dialog.setWindowTitle("Orientation Assistant")
        dialog.setModal(False)
        dialog.setWindowFlag(Qt.Tool, True)
        dialog.setMinimumWidth(360)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        intro = QLabel(
            "Set Orientation workflow:\n"
            "1) Click P0 (origin)\n"
            "2) Click X1 (X direction)\n"
            "3) Click Y1 (Y direction)"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        status_label = QLabel("Orientation: idle")
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        dims_title = QLabel("Grid dimensions (editable):")
        layout.addWidget(dims_title)

        dims_form = QFormLayout()
        dims_form.setContentsMargins(0, 0, 0, 0)
        dims_form.setHorizontalSpacing(8)
        dims_form.setVerticalSpacing(6)
        helper_edits = {}
        for key, label_txt in (("x0", "x0"), ("x1", "x1"), ("y0", "y0"), ("y1", "y1")):
            edit = QLineEdit(dialog)
            edit.setPlaceholderText(label_txt)
            edit.setToolTip(f"Edit {label_txt} and it will sync with the main panel.")
            edit.textEdited.connect(lambda txt, k=key: self._sync_main_field_from_orientation_helper(k, txt))
            dims_form.addRow(f"{label_txt}:", edit)
            helper_edits[key] = edit
        layout.addLayout(dims_form)

        hint_label = QLabel(
            "Tip: dimensions are computed from |x1-x0| and |y1-y0| before grid creation."
        )
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: #606060;")
        layout.addWidget(hint_label)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        cancel_btn = QPushButton("Cancel Tool")
        close_btn = QPushButton("Close")
        buttons.addWidget(cancel_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        def _cancel_tool():
            try:
                if getattr(self, "grid_selection_tool", None) is not None:
                    self.iface.mapCanvas().unsetMapTool(self.grid_selection_tool)
            except Exception:
                pass
            self.on_grid_orientation_cancelled()

        cancel_btn.clicked.connect(_cancel_tool)
        close_btn.clicked.connect(dialog.hide)

        self.orientation_helper_dialog = dialog
        self.orientation_helper_status_label = status_label
        self.orientation_helper_edits = helper_edits
        self._orientation_helper_syncing = False

        # Keep helper fields aligned with the main panel values.
        for widget in self._orientation_main_fields_map().values():
            if widget is not None:
                widget.textChanged.connect(self._sync_orientation_helper_fields_from_main)
        self._sync_orientation_helper_fields_from_main()
        return dialog

    def _show_orientation_helper_dialog(self):
        dialog = self._ensure_orientation_helper_dialog()
        if dialog is None:
            return
        self._sync_orientation_helper_fields_from_main()
        self._set_orientation_status(
            getattr(self, "orientation_status_label", None).text()
            if getattr(self, "orientation_status_label", None) is not None
            else "Orientation: active"
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _hide_orientation_helper_dialog(self):
        dialog = getattr(self, "orientation_helper_dialog", None)
        if dialog is not None:
            dialog.hide()

    def _fmt_orientation_point(self, point):
        if point is None:
            return "(--, --)"
        try:
            return f"({point.x():.3f}, {point.y():.3f})"
        except Exception:
            return "(--, --)"

    def on_grid_orientation_point_captured(self, step_index, point):
        """
        Non-blocking progress updates for Set Orientation tool.
        step_index: 1..3
        """
        pt_txt = self._fmt_orientation_point(point)
        if step_index == 1:
            self._set_orientation_status(
                f"Orientation | P0 set {pt_txt}. Next: click X1 (X direction) [2/3]."
            )
        elif step_index == 2:
            self._set_orientation_status(
                f"Orientation | X1 set {pt_txt}. Next: click Y1 (Y direction) [3/3]."
            )
        elif step_index == 3:
            self._set_orientation_status(
                f"Orientation | Y1 set {pt_txt}. Building grid..."
            )

    def on_grid_orientation_cancelled(self):
        self._set_orientation_status("Orientation cancelled.")
        self.update_draw_indicators(None, None)
        self._hide_orientation_helper_dialog()

    def show_grid_help(self):
        help_text = (
            "Draw workflow:\n"
            "- Left click: add vertex\n"
            "- Right click / Enter: finish polygon\n"
            "- ESC: cancel\n"
            "- D or middle-click: lock orientation and choose dimensions\n"
            "- X: toggle ortho lock from keyboard\n"
            "- Ortho base: orthogonal snapping relative to drawn base\n"
            "- Snap to: All / Vertex / Segment / Intersection\n"
            "- Tol: snap tolerance (px, mm, cm, or map units)\n"
            "- Dimension Input:\n"
            "  Ask = choose method once when starting Draw Polygon\n"
            "  Manual = numeric rectangle\n"
            "  Canvas (Free) = free polygon drawing (D/middle-click for rectangle mode)"
        )
        QMessageBox.information(self.dlg, "Grid Help", help_text)

    def export_last_grid_to_gpkg(self):
        if self.last_grid_layer is None:
            QMessageBox.warning(self.dlg, "Export", "No generated grid found to export.")
            return

        output_path, _ = QFileDialog.getSaveFileName(
            self.dlg,
            "Export Area + Grid",
            "",
            "GeoPackage (*.gpkg)",
        )
        if not output_path:
            return
        if not output_path.lower().endswith(".gpkg"):
            output_path += ".gpkg"

        area_layer = self.last_area_layer
        grid_layer = self.last_grid_layer
        export_targets = []
        if area_layer is not None:
            export_targets.append((area_layer, area_layer.name() or "area"))
        export_targets.append((grid_layer, grid_layer.name() or "grid"))

        transform_context = QgsProject.instance().transformContext()
        try:
            for idx, (layer, layer_name) in enumerate(export_targets):
                opts = QgsVectorFileWriter.SaveVectorOptions()
                opts.driverName = "GPKG"
                opts.fileEncoding = "UTF-8"
                opts.layerName = layer_name
                opts.actionOnExistingFile = (
                    QgsVectorFileWriter.CreateOrOverwriteFile
                    if idx == 0
                    else QgsVectorFileWriter.CreateOrOverwriteLayer
                )
                result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, output_path, transform_context, opts)
                err_code = result[0] if isinstance(result, tuple) else result
                err_msg = result[1] if isinstance(result, tuple) and len(result) > 1 and isinstance(result[1], str) else ""
                if err_code != QgsVectorFileWriter.NoError:
                    raise RuntimeError(err_msg or f"Error code {err_code} while exporting {layer_name}.")

            for _, layer_name in export_targets:
                gpkg_layer = QgsVectorLayer(f"{output_path}|layername={layer_name}", layer_name, "ogr")
                if gpkg_layer.isValid():
                    QgsProject.instance().addMapLayer(gpkg_layer)
                else:
                    self._notify_info(f"Exported layer '{layer_name}' but reload failed.", duration=8)

            self._notify_info(f"Exported to {output_path} and loaded in project.", duration=8)
        except Exception as e:
            QMessageBox.critical(self.dlg, "Export Error", f"Unable to export GeoPackage: {e}")

    def _confirm_planar_units_for_grid(self):
        project_crs = QgsProject.instance().crs()
        if not project_crs.isGeographic():
            return True
        answer = QMessageBox.question(
            self.dlg,
            "CRS Warning",
            (
                f"Project CRS is geographic ({project_crs.authid()}). "
                "X/Y lengths are interpreted in degrees, not meters.\n\n"
                "Continue anyway?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes
### Create Grid with picked points
    def activate_grid_selection_tool(self):
        """
        Activate tool to orient the grid from 3 canvas picks.
        """
        self._sync_grid_options_from_controls()
        chosen = self._choose_vector_storage_mode_for_grid()
        if chosen is None:
            return
        if self._get_grid_dimensions_from_fields() is None:
            self._notify_info(
                "Set Orientation active: enter valid x0/x1/y0/y1 before the final click.",
                duration=8,
            )
        self._set_orientation_status(
            "Orientation active | Step 1/3: click P0 (origin), then X1, then Y1."
        )
        self._notify_info(
            "Set Orientation active: click P0 -> X1 -> Y1. "
            "No popup: follow status in Drawing Options.",
            duration=7,
        )
        self._show_orientation_helper_dialog()
        self.grid_selection_tool = GridSelectionTool(self.iface.mapCanvas(), self)
        self.iface.mapCanvas().setMapTool(self.grid_selection_tool)

    def _get_grid_dimensions_from_fields(self):
        try:
            x0_val = float(self.dlg.lineEditX0Y0.text().strip())
            x1_val = float(self.dlg.lineEditX1Y0.text().strip())
            y0_val = float(self.dlg.lineEditY0.text().strip())
            y1_val = float(self.dlg.lineEditX0Y1.text().strip())
        except Exception:
            return None
        size_x = abs(x1_val - x0_val)
        size_y = abs(y1_val - y0_val)
        if size_x <= 0 or size_y <= 0:
            return None
        return size_x, size_y

    def set_grid_points(self, points):
        """
        Receive 3 orientation points and create grid using dimensions from x0/x1/y0/y1.
        """
        try:
            storage_mode = self._resolve_vector_storage_mode_for_grid()
            dims = self._get_grid_dimensions_from_fields()
            if dims is None:
                QMessageBox.warning(self.dlg, "Missing Grid Dimensions", "Invalid x0/x1/y0/y1 values.")
                return
            grid_length_x, grid_length_y = dims

            internal_enabled = bool(self.internal_grid_checkbox.isChecked()) if self.internal_grid_checkbox is not None else True
            if internal_enabled:
                cell_x = float(self.dlg.lineEditDistanceX.text().strip())
                cell_y = float(self.dlg.lineEditDistanceY.text().strip())
                if cell_x <= 0 or cell_y <= 0:
                    QMessageBox.warning(self.dlg, "Invalid Cell Size", "Cell X and Cell Y must be positive.")
                    return
            else:
                cell_x = grid_length_x
                cell_y = grid_length_y

            x0, y0 = points[0].x(), points[0].y()
            x1, y1 = points[1].x(), points[1].y()
            y_axis_point = (points[2].x(), points[2].y())

            raster_crs = QgsProject.instance().crs()
            grid_layer = create_oriented_grid(
                x0,
                y0,
                x1,
                y1,
                cell_x,
                cell_y,
                raster_crs,
                grid_length_x=grid_length_x,
                grid_length_y=grid_length_y,
                y_axis_point=y_axis_point,
            )
            if grid_layer is not None:
                grid_layer.setCustomProperty("rasterlinker/source_kind", "grid_oriented")
            grid_layer = self._persist_project_vector_layer_if_needed(
                grid_layer,
                storage_mode=storage_mode,
                source_kind="grid_oriented",
            )
            self._place_layer_in_cell_grids_group(grid_layer)
            self.last_grid_layer = grid_layer

            QMessageBox.information(
                self.dlg,
                "Success",
                (
                    f"Oriented grid created.\n"
                    f"Grid size: {grid_length_x:.3f} x {grid_length_y:.3f}\n"
                    f"Cell size: {cell_x:.3f} x {cell_y:.3f}\n"
                    f"Internal grid: {'enabled' if internal_enabled else 'disabled'}"
                ),
            )
            self._set_orientation_status("Orientation completed. Grid created.")
            self._hide_orientation_helper_dialog()
        except ValueError as ve:
            self._set_orientation_status("Orientation failed: invalid values.")
            QMessageBox.warning(self.dlg, "Error", f"Invalid values: {ve}")
        except Exception as e:
            self._set_orientation_status("Orientation failed during grid creation.")
            QMessageBox.critical(self.dlg, "Error", f"Grid creation failed: {str(e)}")
        finally:
            self.pending_vector_storage_mode = None
