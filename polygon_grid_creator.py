
# -*- coding: utf-8 -*-
"""
polygon_grid_creator.py
Modulo per creare una griglia basata su un layer poligonale disegnato dall'utente.
"""

from qgis.core import (
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsField,
    QgsProject,
    QgsPointXY,
)
from qgis.PyQt.QtCore import QVariant
import numpy as np


def create_grid_from_polygon(polygon_layer, distance_x, distance_y):
    """
    Crea una griglia basata su un layer poligonale.

    Args:
        polygon_layer (QgsVectorLayer): Layer poligonale disegnato dall'utente.
        distance_x (float): Distanza tra le linee sull'asse X.
        distance_y (float): Distanza tra le linee sull'asse Y.

    Returns:
        QgsVectorLayer: Layer vettoriale della griglia.
    """
    if polygon_layer.geometryType() != QgsVectorLayer.PolygonGeometry:
        raise ValueError("Il layer selezionato non è un layer poligonale!")

    # Crea un nuovo layer di linee in memoria per la griglia
    grid_layer = QgsVectorLayer(
        f"LineString?crs={polygon_layer.crs().authid()}", "Griglia Poligonale", "memory"
    )
    pr = grid_layer.dataProvider()

    # Aggiungi campi TID/LID
    pr.addAttributes([
        QgsField("ID", QVariant.String),
        QgsField("Type", QVariant.String)
    ])
    grid_layer.updateFields()

    features = []

    # Itera attraverso i poligoni e genera la griglia per ciascuno
    for feature in polygon_layer.getFeatures():
        geom = feature.geometry()
        if not geom:
            continue

        # Ottieni i bounding box del poligono
        bbox = geom.boundingBox()
        x_min, x_max = bbox.xMinimum(), bbox.xMaximum()
        y_min, y_max = bbox.yMinimum(), bbox.yMaximum()

        # Usa numpy per generare intervalli per valori float
        x_values = np.arange(x_min, x_max + distance_x, distance_x)
        y_values = np.arange(y_min, y_max + distance_y, distance_y)

        # Crea le linee TID (verticali)
        for i, x in enumerate(x_values):
            line_geom = QgsGeometry.fromPolylineXY([
                QgsPointXY(x, y_min),
                QgsPointXY(x, y_max)
            ])
            tid_feature = QgsFeature()
            tid_feature.setGeometry(line_geom)
            tid_feature.setAttributes([f"TID_{i}", "TID"])
            features.append(tid_feature)

        # Crea le linee LID (orizzontali)
        for i, y in enumerate(y_values):
            line_geom = QgsGeometry.fromPolylineXY([
                QgsPointXY(x_min, y),
                QgsPointXY(x_max, y)
            ])
            lid_feature = QgsFeature()
            lid_feature.setGeometry(line_geom)
            lid_feature.setAttributes([f"LID_{i}", "LID"])
            features.append(lid_feature)

    # Aggiungi le feature al layer
    pr.addFeatures(features)
    grid_layer.updateExtents()

    # Aggiungi il layer al progetto
    QgsProject.instance().addMapLayer(grid_layer)
    return grid_layer
