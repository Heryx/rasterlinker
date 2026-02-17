from qgis.gui import QgsMapTool
from qgis.core import QgsPointXY
from PyQt5.QtWidgets import QMessageBox


class GridSelectionTool(QgsMapTool):
    """
    Tool to select grid orientation points by clicking on the map.
    """
    def __init__(self, canvas, parent_plugin):
        super().__init__(canvas)
        self.canvas = canvas
        self.parent_plugin = parent_plugin  # Riferimento al plugin principale
        self.points = []  # List used to store clicked points

    def canvasPressEvent(self, event):
        """
        Handles map click events.
        """
        # Get click coordinates
        point = self.toMapCoordinates(event.pos())

        if len(self.points) == 0:
            # First click: defines (x0, y0)
            self.points.append(point)
            QMessageBox.information(None, "Point Selection", f"Origin point (x0, y0): {point.x()}, {point.y()}")
        elif len(self.points) == 1:
            # Second click: defines (x1, y0)
            self.points.append(point)
            QMessageBox.information(None, "Point Selection", f"EstremitÃ  asse X (x1, y0): {point.x()}, {point.y()}")
        elif len(self.points) == 2:
            # Third click: defines (x0, y1)
            self.points.append(point)
            QMessageBox.information(None, "Point Selection", f"EstremitÃ  asse Y (x0, y1): {point.x()}, {point.y()}")

            # Pass points to the main plugin to generate the grid
            self.parent_plugin.set_grid_points(self.points)
            self.points = []  # Resetta la lista per la prossima selezione

            # Disattiva il tool
            self.canvas.setMapTool(None)
        else:
            QMessageBox.warning(None, "Error", "Only 3 clicks are required to define the grid.")
            self.points = []  # Resetta la lista in caso di errore


