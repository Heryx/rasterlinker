from qgis.gui import QgsMapTool, QgsVertexMarker
from qgis.core import QgsPointXY
from qgis.PyQt.QtGui import QColor
from PyQt5.QtWidgets import QMessageBox


class GridSelectionTool(QgsMapTool):
    """
    Tool to select grid orientation points by clicking on the map.
    """

    def __init__(self, canvas, parent_plugin):
        super().__init__(canvas)
        self.canvas = canvas
        self.parent_plugin = parent_plugin
        self.points = []
        self.snap_marker = QgsVertexMarker(self.canvas)
        self.snap_marker.setIconType(QgsVertexMarker.ICON_BOX)
        self.snap_marker.setIconSize(10)
        self.snap_marker.setPenWidth(2)
        self.snap_marker.setColor(QColor(0, 180, 0))
        self.snap_marker.hide()

    def canvasPressEvent(self, event):
        point, snapped = self._map_point_with_snap(event)
        self._update_snap_marker(point if snapped else None)

        if len(self.points) == 0:
            self.points.append(point)
            QMessageBox.information(None, "Point Selection", f"Origin point (x0, y0): {point.x()}, {point.y()}")
        elif len(self.points) == 1:
            self.points.append(point)
            QMessageBox.information(None, "Point Selection", f"X-axis endpoint (x1, y0): {point.x()}, {point.y()}")
        elif len(self.points) == 2:
            self.points.append(point)
            QMessageBox.information(None, "Point Selection", f"Y-axis endpoint (x0, y1): {point.x()}, {point.y()}")
            self.parent_plugin.set_grid_points(self.points)
            self.points = []
            self._update_snap_marker(None)
            self.canvas.setMapTool(None)
        else:
            QMessageBox.warning(None, "Error", "Only 3 clicks are required to define the grid.")
            self.points = []

    def canvasMoveEvent(self, event):
        point, snapped = self._map_point_with_snap(event)
        self._update_snap_marker(point if snapped else None)

    def _map_point_with_snap(self, event):
        if not bool(getattr(self.parent_plugin, "grid_use_snap", True)):
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

    def deactivate(self):
        self._update_snap_marker(None)
        super().deactivate()
