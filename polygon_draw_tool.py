from qgis.gui import QgsMapToolEmitPoint
from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsProject, QgsPointXY
from PyQt5.QtWidgets import QMessageBox

class PolygonDrawTool(QgsMapToolEmitPoint):
    """
    Strumento per disegnare un poligono interattivamente sulla mappa.
    """
    def __init__(self, canvas, parent_plugin):
        super().__init__(canvas)
        self.canvas = canvas
        self.parent_plugin = parent_plugin
        self.points = []  # Lista dei vertici del poligono

        # Controlla se il canvas è valido
        if not self.canvas:
            QMessageBox.critical(None, "Errore", "Canvas non valido!")

    def canvasReleaseEvent(self, event):
        """
        Aggiunge un vertice al poligono quando si clicca sulla mappa.
        """
        try:
            point = self.toMapCoordinates(event.pos())
            self.points.append(point)
            QMessageBox.information(None, "Debug", f"Punto aggiunto: {point.x()}, {point.y()}")
        except Exception as e:
            QMessageBox.critical(None, "Errore", f"Errore durante l'aggiunta del punto: {e}")



    def keyPressEvent(self, event):
        """
        Completa il poligono quando l'utente preme Invio.
        """
        if event.key() == Qt.Key_Return:  # Invio
            if len(self.points) < 3:
                QMessageBox.warning(None, "Errore", "Un poligono deve avere almeno 3 vertici!")
                return

            QMessageBox.information(None, "Debug", f"Creazione del poligono con {len(self.points)} vertici")

            try:
                # Verifica il CRS del progetto
                project_crs = QgsProject.instance().crs()
                QMessageBox.information(None, "Debug", f"CRS del progetto: {project_crs.authid()}")

                # Crea un layer vettoriale in memoria con lo stesso CRS del progetto
                polygon_layer = QgsVectorLayer(f"Polygon?crs={project_crs.authid()}", "Poligono Disegnato", "memory")
                if not polygon_layer.isValid():
                    raise Exception("Errore nella creazione del layer poligonale!")

                pr = polygon_layer.dataProvider()

                # Debug dei punti raccolti
                for i, point in enumerate(self.points):
                    QMessageBox.information(None, "Debug", f"Punto {i}: {point.x()}, {point.y()}")

                # Crea il poligono
                feature = QgsFeature()
                feature.setGeometry(QgsGeometry.fromPolygonXY([self.points]))
                pr.addFeature(feature)
                polygon_layer.updateExtents()

                # Aggiungi il layer al progetto
                QgsProject.instance().addMapLayer(polygon_layer)
                QMessageBox.information(None, "Successo", "Poligono creato e aggiunto alla mappa!")

                # Resetta il tool
                self.points = []
                self.canvas.unsetMapTool()
            except Exception as e:
                QMessageBox.critical(None, "Errore", f"Errore durante la creazione del poligono: {e}")




    def reset(self):
        """
        Resetta i punti selezionati.
        """
        self.points = []
