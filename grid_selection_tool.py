from qgis.gui import QgsMapTool
from qgis.core import QgsPointXY
from PyQt5.QtWidgets import QMessageBox


class GridSelectionTool(QgsMapTool):
    """
    Tool per selezionare i punti della griglia cliccando sulla mappa.
    """
    def __init__(self, canvas, parent_plugin):
        super().__init__(canvas)
        self.canvas = canvas
        self.parent_plugin = parent_plugin  # Riferimento al plugin principale
        self.points = []  # Lista per salvare i punti cliccati

    def canvasPressEvent(self, event):
        """
        Gestisce l'evento di clic sulla mappa.
        """
        # Ottieni le coordinate del clic
        point = self.toMapCoordinates(event.pos())

        if len(self.points) == 0:
            # Primo clic: definisce (x0, y0)
            self.points.append(point)
            QMessageBox.information(None, "Selezione Punto", f"Punto origine (x0, y0): {point.x()}, {point.y()}")
        elif len(self.points) == 1:
            # Secondo clic: definisce (x1, y0)
            self.points.append(point)
            QMessageBox.information(None, "Selezione Punto", f"Estremità asse X (x1, y0): {point.x()}, {point.y()}")
        elif len(self.points) == 2:
            # Terzo clic: definisce (x0, y1)
            self.points.append(point)
            QMessageBox.information(None, "Selezione Punto", f"Estremità asse Y (x0, y1): {point.x()}, {point.y()}")

            # Passa i punti al plugin principale per generare la griglia
            self.parent_plugin.set_grid_points(self.points)
            self.points = []  # Resetta la lista per la prossima selezione

            # Disattiva il tool
            self.canvas.setMapTool(None)
        else:
            QMessageBox.warning(None, "Errore", "Sono richiesti solo 3 clic per definire la griglia.")
            self.points = []  # Resetta la lista in caso di errore

