import math

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand, QgsVertexMarker
from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsProject, QgsWkbTypes, QgsPointXY, Qgis
from PyQt5.QtWidgets import QMessageBox, QInputDialog, QApplication


class PolygonDrawTool(QgsMapToolEmitPoint):
    """
    Tool to draw a polygon.

    Mode 1 (default): free drawing with left click and close with right click/Enter.
    Mode 2 (oriented): click first point, orient with mouse, then press D
    (or middle click) to lock
    orientation and enter numeric rectangle length/width.
    """

    def __init__(self, canvas, parent_plugin):
        super().__init__(canvas)
        self.canvas = canvas
        self.parent_plugin = parent_plugin
        self.points = []
        self.current_mouse_point = None
        self.vertex_markers = []
        self.last_total_length = 20.0
        self.last_total_width = 20.0
        self.locked_angle = None
        self.dimension_pick_mode = None
        self.pending_length = None
        self.orthogonal_lock_enabled = False

        self.line_rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry)
        self.line_rubber_band.setColor(QColor(255, 140, 0))
        self.line_rubber_band.setWidth(2)

        self.polygon_rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.polygon_rubber_band.setStrokeColor(QColor(255, 140, 0))
        self.polygon_rubber_band.setFillColor(QColor(255, 140, 0, 50))
        self.polygon_rubber_band.setWidth(1)
        self.snap_marker = QgsVertexMarker(self.canvas)
        self.snap_marker.setIconType(QgsVertexMarker.ICON_BOX)
        self.snap_marker.setIconSize(10)
        self.snap_marker.setPenWidth(2)
        self.snap_marker.setColor(QColor(0, 180, 0))
        self.snap_marker.hide()

        if not self.canvas:
            QMessageBox.critical(None, "Error", "Invalid canvas.")

    def canvasReleaseEvent(self, event):
        try:
            shift_active = self._shift_active(event)
            axis_constraint = shift_active or self.orthogonal_lock_enabled or self._plugin_force_orthogonal()
            if event.button() == Qt.MiddleButton:
                self._lock_orientation_and_build_rectangle()
                return
            if event.button() == Qt.LeftButton:
                point, snapped = self._map_point_with_snap(event)
                self._update_snap_marker(point if snapped else None)
                if len(self.points) >= 1 and axis_constraint:
                    point = self._constraint_snapped_point(self.points[0], point)
                if self.dimension_pick_mode is not None:
                    self._handle_canvas_dimension_pick(point)
                    return
                if len(self.points) >= 1 and axis_constraint:
                    angle = self._compute_angle(self.points[0], point)
                    if angle is None:
                        QMessageBox.warning(None, "Orientation", "Invalid orientation.")
                        return
                    self.current_mouse_point = point
                    self.locked_angle = angle
                    self._begin_dimension_mode_selection()
                    return
                self.points.append(point)
                self._add_vertex_marker(point)
                self._update_preview()
            elif event.button() == Qt.RightButton:
                self.finish_polygon()
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Error while adding point: {e}")

    def canvasMoveEvent(self, event):
        if not self.points:
            point, snapped = self._map_point_with_snap(event)
            self._update_snap_marker(point if snapped else None)
            return
        point, snapped = self._map_point_with_snap(event)
        self._update_snap_marker(point if snapped else None)
        if len(self.points) >= 1 and (self._shift_active(event) or self.orthogonal_lock_enabled or self._plugin_force_orthogonal()):
            point = self._constraint_snapped_point(self.points[0], point)
        self.current_mouse_point = point
        self._update_preview()

    def keyPressEvent(self, event):
        event.accept()
        if event.key() == Qt.Key_X:
            self.orthogonal_lock_enabled = not self.orthogonal_lock_enabled
            if self.points and self.current_mouse_point is not None:
                self._update_preview()
            return

        if event.key() == Qt.Key_D or (event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_D):
            self._lock_orientation_and_build_rectangle()
            return

        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish_polygon()
        elif event.key() == Qt.Key_Escape:
            self.reset()
            self._notify_info("Polygon drawing canceled.")
            return

    def _lock_orientation_and_build_rectangle(self):
        if not self.points:
            self._notify_info("Click the origin point first.")
            return

        origin = self.points[0]
        reference = self.current_mouse_point
        if reference is None and len(self.points) > 1:
            reference = self.points[1]

        if reference is None:
            self._notify_info("Move the mouse to orient the base, then press D or middle-click.")
            return

        angle = self._compute_angle(origin, reference)
        if angle is None:
            QMessageBox.warning(None, "Orientation", "Invalid orientation: choose a different direction.")
            return
        self.locked_angle = angle
        self._begin_dimension_mode_selection()

    def _begin_dimension_mode_selection(self):
        preferred_mode = self._plugin_dimension_mode()
        if preferred_mode == "manual":
            self._build_rectangle_from_dialog()
            return
        if preferred_mode == "canvas":
            self.dimension_pick_mode = "length"
            self.pending_length = None
            self._notify_info("Canvas mode: click 1 for length, click 2 for width.")
            return

        choice = QMessageBox(None)
        choice.setWindowTitle("Area dimensions")
        choice.setText("How do you want to define length and width?")
        manual_btn = choice.addButton("Manual input", QMessageBox.AcceptRole)
        canvas_btn = choice.addButton("Pick from canvas", QMessageBox.ActionRole)
        cancel_btn = choice.addButton(QMessageBox.Cancel)
        choice.exec_()

        clicked = choice.clickedButton()
        if clicked == cancel_btn:
            return
        if clicked == manual_btn:
            self._build_rectangle_from_dialog()
            return
        if clicked == canvas_btn:
            self.dimension_pick_mode = "length"
            self.pending_length = None
            self._notify_info("Canvas mode: click 1 for length, click 2 for width.")

    def _compute_angle(self, origin, reference):
        dx = reference.x() - origin.x()
        dy = reference.y() - origin.y()
        if math.hypot(dx, dy) < 1e-9:
            return None
        return math.atan2(dy, dx)

    def _axis_snapped_point(self, origin, point):
        dx = point.x() - origin.x()
        dy = point.y() - origin.y()
        if abs(dx) >= abs(dy):
            return QgsPointXY(point.x(), origin.y())
        return QgsPointXY(origin.x(), point.y())

    def _constraint_snapped_point(self, origin, point):
        """
        Applica vincolo ortogonale assoluto (0/90) o relativo alla base disegnata.
        """
        if self._plugin_relative_orthogonal():
            base_angle = self._base_orientation_angle()
            if base_angle is not None:
                return self._relative_axis_snapped_point(origin, point, base_angle)
        return self._axis_snapped_point(origin, point)

    def _relative_axis_snapped_point(self, origin, point, base_angle):
        ux = (math.cos(base_angle), math.sin(base_angle))
        uy = (-math.sin(base_angle), math.cos(base_angle))
        vx = point.x() - origin.x()
        vy = point.y() - origin.y()
        proj_u = vx * ux[0] + vy * ux[1]
        proj_v = vx * uy[0] + vy * uy[1]
        if abs(proj_u) >= abs(proj_v):
            return QgsPointXY(origin.x() + proj_u * ux[0], origin.y() + proj_u * ux[1])
        return QgsPointXY(origin.x() + proj_v * uy[0], origin.y() + proj_v * uy[1])

    def _base_orientation_angle(self):
        if len(self.points) >= 2:
            return self._compute_angle(self.points[0], self.points[1])
        if self.locked_angle is not None:
            return self.locked_angle
        if self.current_mouse_point is not None and self.points:
            return self._compute_angle(self.points[0], self.current_mouse_point)
        return None

    def _shift_active(self, event=None):
        """
        Rilevamento robusto di Shift: evento mouse + stato tastiera globale.
        """
        if event is not None and (event.modifiers() & Qt.ShiftModifier):
            return True
        return bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)

    def _build_rectangle_from_dialog(self):
        length, ok_len = QInputDialog.getDouble(
            None,
            "Lunghezza totale area",
            "Enter total length:",
            self.last_total_length,
            0.0001,
            1e12,
            3,
        )
        if not ok_len:
            return

        width, ok_wid = QInputDialog.getDouble(
            None,
            "Larghezza totale area",
            "Enter total width:",
            self.last_total_width,
            0.0001,
            1e12,
            3,
        )
        if not ok_wid:
            return

        self.last_total_length = length
        self.last_total_width = width
        self._build_rectangle_from_values(length, width)

    def _handle_canvas_dimension_pick(self, point):
        if not self.points or self.locked_angle is None:
            return

        origin = self.points[0]
        ux = (math.cos(self.locked_angle), math.sin(self.locked_angle))
        uy = (-math.sin(self.locked_angle), math.cos(self.locked_angle))

        vx = point.x() - origin.x()
        vy = point.y() - origin.y()

        if self.dimension_pick_mode == "length":
            length = abs(vx * ux[0] + vy * ux[1])
            if length <= 0:
                QMessageBox.warning(None, "Invalid value", "Invalid length, try again.")
                return
            self.pending_length = length
            self.dimension_pick_mode = "width"
            self._notify_info("Now click to set width.")
            return

        if self.dimension_pick_mode == "width":
            width = abs(vx * uy[0] + vy * uy[1])
            if width <= 0:
                QMessageBox.warning(None, "Invalid value", "Invalid width, try again.")
                return
            self.last_total_length = self.pending_length
            self.last_total_width = width
            self.dimension_pick_mode = None
            self._build_rectangle_from_values(self.pending_length, width)

    def _build_rectangle_from_values(self, length, width):
        if not self.points or self.locked_angle is None:
            return

        origin = self.points[0]
        ux = (math.cos(self.locked_angle), math.sin(self.locked_angle))
        uy = (-math.sin(self.locked_angle), math.cos(self.locked_angle))

        p1 = origin
        p2 = QgsPointXY(p1.x() + length * ux[0], p1.y() + length * ux[1])
        p3 = QgsPointXY(p2.x() + width * uy[0], p2.y() + width * uy[1])
        p4 = QgsPointXY(p1.x() + width * uy[0], p1.y() + width * uy[1])

        self._set_points([p1, p2, p3, p4])
        self.finish_polygon()

    def finish_polygon(self):
        if len(self.points) < 3:
            QMessageBox.warning(None, "Error", "A polygon must have at least 3 vertices.")
            return

        try:
            project_crs = QgsProject.instance().crs()
            polygon_layer = QgsVectorLayer(f"Polygon?crs={project_crs.authid()}", "Drawn Polygon", "memory")
            if not polygon_layer.isValid():
                raise Exception("Error while creating polygon layer.")

            pr = polygon_layer.dataProvider()
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromPolygonXY([self.points]))
            pr.addFeature(feature)
            polygon_layer.updateExtents()
            QgsProject.instance().addMapLayer(polygon_layer)

            if hasattr(self.parent_plugin, "create_grid_from_drawn_polygon"):
                self.parent_plugin.create_grid_from_drawn_polygon(polygon_layer)

            self._notify_info("Polygon created.")
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Error while creating polygon: {e}")
        finally:
            self.reset()
            self.canvas.unsetMapTool(self)

    def _set_points(self, points):
        self.points = list(points)
        self.current_mouse_point = None
        for marker in self.vertex_markers:
            self.canvas.scene().removeItem(marker)
        self.vertex_markers = []
        for point in self.points:
            self._add_vertex_marker(point)
        self._update_preview()

    def _map_point_with_snap(self, event):
        """
        Use QGIS snapping if available/valid, fallback to free coordinates.
        """
        if not self._plugin_use_snap():
            return self.toMapCoordinates(event.pos()), False
        try:
            snap_utils = self.canvas.snappingUtils()
            if snap_utils is not None:
                get_filter = getattr(self.parent_plugin, "get_snap_filter", None)
                if callable(get_filter):
                    try:
                        match = snap_utils.snapToMap(event.pos(), get_filter())
                    except TypeError:
                        match = snap_utils.snapToMap(event.pos())
                else:
                    match = snap_utils.snapToMap(event.pos())
                if match.isValid():
                    return match.point(), True
        except Exception:
            pass
        return self.toMapCoordinates(event.pos()), False

    def _update_snap_marker(self, point):
        if point is None:
            self.snap_marker.hide()
            return
        self.snap_marker.setCenter(QgsPointXY(point))
        self.snap_marker.show()

    def _plugin_use_snap(self):
        return bool(getattr(self.parent_plugin, "grid_use_snap", True))

    def _plugin_force_orthogonal(self):
        return bool(getattr(self.parent_plugin, "grid_force_orthogonal", False))

    def _plugin_relative_orthogonal(self):
        return bool(getattr(self.parent_plugin, "grid_relative_orthogonal", False))

    def _plugin_dimension_mode(self):
        mode = getattr(self.parent_plugin, "grid_dimension_mode", "ask")
        return mode if mode in ("ask", "manual", "canvas") else "ask"

    def _notify_info(self, message, duration=5):
        iface = getattr(self.parent_plugin, "iface", None)
        if iface is not None and hasattr(iface, "messageBar"):
            iface.messageBar().pushMessage("RasterLinker", message, level=Qgis.Info, duration=duration)
            return
        QMessageBox.information(None, "Info", message)

    def _add_vertex_marker(self, point):
        marker = QgsVertexMarker(self.canvas)
        marker.setCenter(point)
        marker.setColor(QColor(220, 20, 60))
        marker.setIconType(QgsVertexMarker.ICON_CROSS)
        marker.setIconSize(10)
        marker.setPenWidth(2)
        self.vertex_markers.append(marker)

    def _update_preview(self):
        self.line_rubber_band.reset(QgsWkbTypes.LineGeometry)
        for p in self.points:
            self.line_rubber_band.addPoint(p, False)
        if self.current_mouse_point is not None:
            self.line_rubber_band.addPoint(self.current_mouse_point, True)
        elif self.points:
            self.line_rubber_band.addPoint(self.points[-1], True)

        self.polygon_rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        ring = list(self.points)
        if self.current_mouse_point is not None:
            ring.append(self.current_mouse_point)
        if len(ring) >= 3:
            closed_ring = ring + [ring[0]]
            self.polygon_rubber_band.setToGeometry(QgsGeometry.fromPolygonXY([closed_ring]), None)
        self._publish_base_angle()

    def reset(self):
        self.points = []
        self.current_mouse_point = None
        self.locked_angle = None
        self.dimension_pick_mode = None
        self.pending_length = None
        self.orthogonal_lock_enabled = False
        self.line_rubber_band.reset(QgsWkbTypes.LineGeometry)
        self.polygon_rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        for marker in self.vertex_markers:
            self.canvas.scene().removeItem(marker)
        self.vertex_markers = []
        self._update_snap_marker(None)
        self._publish_base_angle()

    def _publish_base_angle(self):
        callback = getattr(self.parent_plugin, "update_draw_indicators", None)
        if callback is None:
            callback = getattr(self.parent_plugin, "update_base_angle_indicator", None)
        if callback is None:
            return
        angle = None
        length = None
        if len(self.points) >= 2:
            angle = self._compute_angle(self.points[0], self.points[1])
            length = math.hypot(
                self.points[1].x() - self.points[0].x(),
                self.points[1].y() - self.points[0].y(),
            )
        elif len(self.points) == 1 and self.current_mouse_point is not None:
            angle = self._compute_angle(self.points[0], self.current_mouse_point)
            length = math.hypot(
                self.current_mouse_point.x() - self.points[0].x(),
                self.current_mouse_point.y() - self.points[0].y(),
            )

        try:
            callback(angle, length)
        except TypeError:
            callback(angle)

    def deactivate(self):
        self.reset()
        super().deactivate()

