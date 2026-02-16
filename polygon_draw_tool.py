import math

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand, QgsVertexMarker
from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsProject, QgsWkbTypes, QgsPointXY
from PyQt5.QtWidgets import QMessageBox, QInputDialog, QApplication


class PolygonDrawTool(QgsMapToolEmitPoint):
    """
    Strumento per disegnare un poligono.

    Modalita 1 (default): disegno libero con click sinistro e chiusura con destro/Invio.
    Modalita 2 (orientata): click primo punto, orienta col mouse, poi tasto D
    (o click centrale) per bloccare
    orientamento e inserire lunghezza/larghezza numeriche del rettangolo.
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

        if not self.canvas:
            QMessageBox.critical(None, "Errore", "Canvas non valido.")

    def canvasReleaseEvent(self, event):
        try:
            shift_active = self._shift_active(event)
            axis_constraint = shift_active or self.orthogonal_lock_enabled or self._plugin_force_orthogonal()
            if event.button() == Qt.MiddleButton:
                self._lock_orientation_and_build_rectangle()
                return
            if event.button() == Qt.LeftButton:
                point = self._map_point_with_snap(event)
                if len(self.points) >= 1 and axis_constraint:
                    point = self._axis_snapped_point(self.points[0], point)
                if self.dimension_pick_mode is not None:
                    self._handle_canvas_dimension_pick(point)
                    return
                if len(self.points) >= 1 and axis_constraint:
                    angle = self._compute_angle(self.points[0], point)
                    if angle is None:
                        QMessageBox.warning(None, "Orientamento", "Orientamento non valido.")
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
            QMessageBox.critical(None, "Errore", f"Errore durante l'aggiunta del punto: {e}")

    def canvasMoveEvent(self, event):
        if not self.points:
            return
        point = self._map_point_with_snap(event)
        if len(self.points) >= 1 and (self._shift_active(event) or self.orthogonal_lock_enabled or self._plugin_force_orthogonal()):
            point = self._axis_snapped_point(self.points[0], point)
        self.current_mouse_point = point
        self._update_preview()

    def keyPressEvent(self, event):
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
            self.canvas.unsetMapTool(self)
            QMessageBox.information(None, "Annullato", "Disegno poligono annullato.")

    def _lock_orientation_and_build_rectangle(self):
        if not self.points:
            QMessageBox.information(None, "Orientamento", "Fai prima click sul punto di origine.")
            return

        origin = self.points[0]
        reference = self.current_mouse_point
        if reference is None and len(self.points) > 1:
            reference = self.points[1]

        if reference is None:
            QMessageBox.information(
                None,
                "Orientamento",
                "Muovi il mouse per orientare la base, poi premi D (oppure click centrale)."
            )
            return

        angle = self._compute_angle(origin, reference)
        if angle is None:
            QMessageBox.warning(None, "Orientamento", "Orientamento non valido: scegli una direzione diversa.")
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
            QMessageBox.information(
                None,
                "Da canvas",
                "Click 1: imposta la lunghezza lungo l'orientamento bloccato. "
                "Click 2: imposta la larghezza."
            )
            return

        choice = QMessageBox(None)
        choice.setWindowTitle("Dimensioni area")
        choice.setText("Come vuoi definire lunghezza e larghezza?")
        manual_btn = choice.addButton("Inserimento manuale", QMessageBox.AcceptRole)
        canvas_btn = choice.addButton("Da canvas", QMessageBox.ActionRole)
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
            QMessageBox.information(
                None,
                "Da canvas",
                "Click 1: imposta la lunghezza lungo l'orientamento bloccato. "
                "Click 2: imposta la larghezza."
            )

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
            "Inserisci lunghezza totale:",
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
            "Inserisci larghezza totale:",
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
                QMessageBox.warning(None, "Valore non valido", "Lunghezza non valida, riprova.")
                return
            self.pending_length = length
            self.dimension_pick_mode = "width"
            QMessageBox.information(None, "Da canvas", "Ora clicca per impostare la larghezza.")
            return

        if self.dimension_pick_mode == "width":
            width = abs(vx * uy[0] + vy * uy[1])
            if width <= 0:
                QMessageBox.warning(None, "Valore non valido", "Larghezza non valida, riprova.")
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
            QMessageBox.warning(None, "Errore", "Un poligono deve avere almeno 3 vertici.")
            return

        try:
            project_crs = QgsProject.instance().crs()
            polygon_layer = QgsVectorLayer(f"Polygon?crs={project_crs.authid()}", "Poligono Disegnato", "memory")
            if not polygon_layer.isValid():
                raise Exception("Errore nella creazione del layer poligonale.")

            pr = polygon_layer.dataProvider()
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromPolygonXY([self.points]))
            pr.addFeature(feature)
            polygon_layer.updateExtents()
            QgsProject.instance().addMapLayer(polygon_layer)

            if hasattr(self.parent_plugin, "create_grid_from_drawn_polygon"):
                self.parent_plugin.create_grid_from_drawn_polygon(polygon_layer)

            QMessageBox.information(None, "Successo", "Poligono creato.")
        except Exception as e:
            QMessageBox.critical(None, "Errore", f"Errore durante la creazione del poligono: {e}")
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
        Usa lo snapping di QGIS se disponibile/valido; fallback su coordinate libere.
        """
        if not self._plugin_use_snap():
            return self.toMapCoordinates(event.pos())
        try:
            snap_utils = self.canvas.snappingUtils()
            if snap_utils is not None:
                match = snap_utils.snapToMap(event.pos())
                if match.isValid():
                    return match.point()
        except Exception:
            pass
        return self.toMapCoordinates(event.pos())

    def _plugin_use_snap(self):
        return bool(getattr(self.parent_plugin, "grid_use_snap", True))

    def _plugin_force_orthogonal(self):
        return bool(getattr(self.parent_plugin, "grid_force_orthogonal", False))

    def _plugin_dimension_mode(self):
        mode = getattr(self.parent_plugin, "grid_dimension_mode", "ask")
        return mode if mode in ("ask", "manual", "canvas") else "ask"

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

    def deactivate(self):
        self.reset()
        super().deactivate()
