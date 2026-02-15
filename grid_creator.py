# -*- coding: utf-8 -*-
"""
grid_creator.py
Modulo per creare una griglia orientata basata su tre punti definiti dall'utente.
"""

from qgis.core import (
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsField,
    QgsPointXY,
    QgsProject,
)
from qgis.PyQt.QtCore import QVariant
import math


def create_oriented_grid(x0, y0, x1, y1, distance_x, distance_y, raster_crs):
    """
    Crea una griglia orientata basata su distanze separate per X e Y.

    Args:
        x0, y0 (float): Coordinate del punto di origine.
        x1, y1 (float): Coordinate del punto sull'asse X.
        distance_x (float): Distanza tra le linee sull'asse X (TID).
        distance_y (float): Distanza tra le linee sull'asse Y (LID).
        raster_crs (QgsCoordinateReferenceSystem): CRS del raster.

    Returns:
        QgsVectorLayer: Layer vettoriale della griglia.
    """
    # Calcola l'angolo di orientamento
    delta_x = x1 - x0
    delta_y = y1 - y0
    angle = math.atan2(delta_y, delta_x)

    # Calcola la lunghezza della griglia
    grid_length_x = math.sqrt(delta_x**2 + delta_y**2)
    grid_length_y = grid_length_x  # Può essere personalizzato in futuro

    num_lines_x = int(grid_length_x / distance_x) + 1
    num_lines_y = int(grid_length_y / distance_y) + 1

    # Crea un layer di linea in memoria
    grid_layer = QgsVectorLayer(f"LineString?crs={raster_crs.authid()}", "Griglia Orientata", "memory")
    pr = grid_layer.dataProvider()

    # Aggiungi campi per identificare TID e LID
    pr.addAttributes([
        QgsField("ID", QVariant.String),   # Identificativo della linea
        QgsField("Type", QVariant.String) # Tipo: TID o LID
    ])
    grid_layer.updateFields()

    features = []

    # Funzione per ruotare un punto attorno al punto (x0, y0)
    def rotate_point(px, py, cx, cy, angle):
        dx, dy = px - cx, py - cy
        new_x = cx + dx * math.cos(angle) - dy * math.sin(angle)
        new_y = cy + dx * math.sin(angle) + dy * math.cos(angle)
        return QgsPointXY(new_x, new_y)

    # Genera linee trasversali (TID)
    for i in range(-num_lines_y, num_lines_y + 1):
        offset = i * distance_y
        p1 = rotate_point(x0 - grid_length_x, y0 + offset, x0, y0, angle)
        p2 = rotate_point(x0 + grid_length_x, y0 + offset, x0, y0, angle)

        line_tid = QgsGeometry.fromPolylineXY([p1, p2])
        feature_tid = QgsFeature()
        feature_tid.setGeometry(line_tid)
        feature_tid.setAttributes([f"TID_{i}", "TID"])
        features.append(feature_tid)

    # Genera linee longitudinali (LID)
    for i in range(-num_lines_x, num_lines_x + 1):
        offset = i * distance_x
        p1 = rotate_point(x0 + offset, y0 - grid_length_y, x0, y0, angle)
        p2 = rotate_point(x0 + offset, y0 + grid_length_y, x0, y0, angle)

        line_lid = QgsGeometry.fromPolylineXY([p1, p2])
        feature_lid = QgsFeature()
        feature_lid.setGeometry(line_lid)
        feature_lid.setAttributes([f"LID_{i}", "LID"])
        features.append(feature_lid)

    # Aggiungi tutte le feature al layer
    pr.addFeatures(features)
    grid_layer.updateExtents()

    # Aggiungi il layer al progetto
    QgsProject.instance().addMapLayer(grid_layer)

    return grid_layer

