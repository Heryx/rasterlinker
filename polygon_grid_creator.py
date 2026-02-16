# -*- coding: utf-8 -*-
"""
polygon_grid_creator.py
Genera una griglia di celle poligonali orientate in base al poligono disegnato.
"""

import math
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant


def _largest_edge_angle_rad(geometry):
    """
    Calcola l'orientamento (radianti) dal lato più lungo del minimum rotated rectangle.
    """
    ombb = geometry.orientedMinimumBoundingBox()
    box_geom = ombb[0] if isinstance(ombb, (tuple, list)) else ombb

    polygon = box_geom.asPolygon()
    if not polygon or not polygon[0] or len(polygon[0]) < 2:
        return 0.0

    ring = polygon[0]
    best_len = -1.0
    best_angle = 0.0
    for i in range(len(ring) - 1):
        p1 = ring[i]
        p2 = ring[i + 1]
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        seg_len = math.hypot(dx, dy)
        if seg_len > best_len:
            best_len = seg_len
            best_angle = math.atan2(dy, dx)
    return best_angle


def _build_rotated_cell(u0, u1, v0, v1, ux, uy):
    """
    Crea la geometria di una cella usando coordinate locali (u, v) ruotate.
    """
    p1 = QgsPointXY(u0 * ux[0] + v0 * uy[0], u0 * ux[1] + v0 * uy[1])
    p2 = QgsPointXY(u1 * ux[0] + v0 * uy[0], u1 * ux[1] + v0 * uy[1])
    p3 = QgsPointXY(u1 * ux[0] + v1 * uy[0], u1 * ux[1] + v1 * uy[1])
    p4 = QgsPointXY(u0 * ux[0] + v1 * uy[0], u0 * ux[1] + v1 * uy[1])
    return QgsGeometry.fromPolygonXY([[p1, p2, p3, p4, p1]])


def _axis_breaks(min_value, max_value, step):
    """
    Genera breakpoints ancorati al bordo minimo dell'area (no snap a griglia globale).
    """
    if max_value <= min_value:
        return [min_value, max_value]

    values = [min_value]
    current = min_value
    eps = step * 1e-9
    while (current + step) < (max_value - eps):
        current += step
        values.append(current)
    values.append(max_value)
    return values


def create_grid_from_polygon(
    polygon_layer,
    distance_x,
    distance_y,
    area_name=None,
    cell_prefix=None,
    max_cells=120000,
):
    """
    Crea celle poligonali orientate e ritagliate sul poligono di input.

    Args:
        polygon_layer (QgsVectorLayer): Layer poligonale.
        distance_x (float): Passo lungo asse principale.
        distance_y (float): Passo lungo asse secondario.
        area_name (str): Nome area principale.
        cell_prefix (str): Prefisso celle.
        max_cells (int): Numero massimo di celle candidate prima del clip.

    Returns:
        QgsVectorLayer: Layer poligonale delle celle.
    """
    if distance_x <= 0 or distance_y <= 0:
        raise ValueError("X lenght e Y lenght devono essere maggiori di zero.")

    input_geoms = [f.geometry() for f in polygon_layer.getFeatures() if f.geometry() and not f.geometry().isEmpty()]
    if not input_geoms:
        raise ValueError("Nessuna geometria valida nel layer poligonale.")

    union_geom = QgsGeometry.unaryUnion(input_geoms)
    if union_geom is None or union_geom.isEmpty():
        raise ValueError("Impossibile costruire la geometria unita dell'area.")

    area_name = (area_name or "Area_Indagine").strip()
    cell_prefix = (cell_prefix or f"{area_name}_cell").strip()

    angle = _largest_edge_angle_rad(union_geom)
    ux = (math.cos(angle), math.sin(angle))
    uy = (-math.sin(angle), math.cos(angle))

    projections_u = []
    projections_v = []
    for vertex in union_geom.vertices():
        x = vertex.x()
        y = vertex.y()
        projections_u.append(x * ux[0] + y * ux[1])
        projections_v.append(x * uy[0] + y * uy[1])

    if not projections_u or not projections_v:
        raise ValueError("Impossibile calcolare l'orientamento della griglia.")

    u_min = min(projections_u)
    u_max = max(projections_u)
    v_min = min(projections_v)
    v_max = max(projections_v)

    u_values = _axis_breaks(u_min, u_max, distance_x)
    v_values = _axis_breaks(v_min, v_max, distance_y)
    candidate_cells = max(0, len(u_values) - 1) * max(0, len(v_values) - 1)
    if candidate_cells > max_cells:
        raise ValueError(
            f"Grid too dense: {candidate_cells} candidate cells exceed limit ({max_cells}). "
            "Increase X/Y length or reduce area size."
        )

    grid_layer = QgsVectorLayer(
        f"Polygon?crs={polygon_layer.crs().authid()}",
        f"{cell_prefix}_grid",
        "memory",
    )
    provider = grid_layer.dataProvider()
    provider.addAttributes(
        [
            QgsField("cell_id", QVariant.String),
            QgsField("area", QVariant.String),
            QgsField("row", QVariant.Int),
            QgsField("col", QVariant.Int),
        ]
    )
    grid_layer.updateFields()

    features = []
    row_idx = 0
    for j in range(len(v_values) - 1):
        v0 = v_values[j]
        v1 = v_values[j + 1]
        row_idx += 1
        col_idx = 0
        for i in range(len(u_values) - 1):
            u0 = u_values[i]
            u1 = u_values[i + 1]
            col_idx += 1

            cell_geom = _build_rotated_cell(u0, u1, v0, v1, ux, uy)
            clipped = cell_geom.intersection(union_geom)
            if clipped.isEmpty() or clipped.area() <= 0:
                continue

            feat = QgsFeature(grid_layer.fields())
            feat.setGeometry(clipped)
            feat.setAttributes([f"{cell_prefix}_{row_idx:03d}_{col_idx:03d}", area_name, row_idx, col_idx])
            features.append(feat)

    provider.addFeatures(features)
    grid_layer.updateExtents()
    QgsProject.instance().addMapLayer(grid_layer)
    return grid_layer
