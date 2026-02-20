from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
from qgis.core import (
    Qgis,
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

    def _persist_project_vector_layer_if_needed(self, layer, storage_mode=None):
        if layer is None:
            return None
        mode = storage_mode or self._resolve_vector_storage_mode_for_grid()
        if mode != "gpkg":
            return layer

        persist_fn = getattr(self, "_persist_vector_layer_to_project_gpkg", None)
        if not callable(persist_fn):
            return layer

        persisted, out_path, err = persist_fn(layer, layer.name() or "layer")
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
            # Activate polygon drawing tool
            self.polygon_draw_tool = PolygonDrawTool(self.iface.mapCanvas(), self)
            self.iface.mapCanvas().setMapTool(self.polygon_draw_tool)

            self._notify_info(
                "Draw area: left-click vertices, right-click/Enter to close, ESC to cancel. "
                "Oriented rectangle: first click origin, then D/middle-click for dimensions. "
                "Dock: Snap, Ortho 0/90, Mode. Name: Area|Prefix."
            )
        except Exception as e:
            QMessageBox.critical(self.dlg, "Error", f"Error while activating drawing tool: {e}")

    
    def create_grid_from_drawn_polygon(self, polygon_layer):
        """
        Create a grid based on the polygon drawn by the user.
        """
        try:
            storage_mode = self._resolve_vector_storage_mode_for_grid()
            # Read spacing values from UI
            distance_x = float(self.dlg.lineEditDistanceX.text().strip())
            distance_y = float(self.dlg.lineEditDistanceY.text().strip())
            area_name, cell_prefix = self._get_grid_names_from_ui()
            polygon_layer.setName(area_name)
            polygon_layer = self._persist_project_vector_layer_if_needed(polygon_layer, storage_mode=storage_mode)
            if not self._confirm_planar_units_for_grid():
                return

            # Create grid
            grid_layer = create_grid_from_polygon(
                polygon_layer,
                distance_x,
                distance_y,
                area_name=area_name,
                cell_prefix=cell_prefix,
                max_cells=120000,
            )
            grid_layer = self._persist_project_vector_layer_if_needed(grid_layer, storage_mode=storage_mode)
            self.last_area_layer = polygon_layer
            self.last_grid_layer = grid_layer

            if not self.keep_source_polygon and QgsProject.instance().mapLayer(polygon_layer.id()) is not None:
                QgsProject.instance().removeMapLayer(polygon_layer.id())

            self._notify_info(f"Area '{area_name}' created. Cells with prefix '{cell_prefix}'.")
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
        self.iface.messageBar().pushMessage("RasterLinker", message, level=Qgis.Info, duration=duration)

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
            "- Dimension Input: Ask / Manual / Canvas for length-width input"
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
            QMessageBox.warning(
                self.dlg,
                "Missing Grid Dimensions",
                (
                    "Insert valid numeric values in x0, x1, y0, y1 before setting orientation.\n"
                    "Example: x0=0, x1=10, y0=0, y1=10."
                ),
            )
            return
        QMessageBox.information(
            self.dlg,
            "Set Grid Orientation",
            (
                "Orientation mode is active.\n\n"
                "1) First click: grid origin (0,0)\n"
                "2) Second click: X-axis direction\n"
                "3) Third click: Y-axis direction\n\n"
                "Grid size comes from x0/x1/y0/y1 values.\n"
                "Internal cells are optional and controlled by 'Internal grid'."
            ),
        )
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
            grid_layer = self._persist_project_vector_layer_if_needed(grid_layer, storage_mode=storage_mode)
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
        except ValueError as ve:
            QMessageBox.warning(self.dlg, "Error", f"Invalid values: {ve}")
        except Exception as e:
            QMessageBox.critical(self.dlg, "Error", f"Grid creation failed: {str(e)}")
        finally:
            self.pending_vector_storage_mode = None
