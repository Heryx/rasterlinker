# -*- coding: utf-8 -*-
"""
grid_creator.py
Create an oriented survey grid from a picked origin/orientation and explicit dimensions.
"""

import math

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)


def create_oriented_grid(
    x0,
    y0,
    x1,
    y1,
    distance_x,
    distance_y,
    raster_crs,
    grid_length_x=None,
    grid_length_y=None,
    y_axis_point=None,
):
    """
    Create an oriented line grid.

    Args:
        x0, y0: Origin point picked on canvas.
        x1, y1: Second picked point defining X-axis orientation.
        distance_x: Cell size along X axis.
        distance_y: Cell size along Y axis.
        raster_crs: CRS used for output layer.
        grid_length_x: Total grid size along X (from UI x0/x1).
        grid_length_y: Total grid size along Y (from UI y0/y1).
        y_axis_point: Optional third picked point defining Y-axis direction.
    """
    vx = x1 - x0
    vy = y1 - y0
    norm_v = math.hypot(vx, vy)
    if norm_v <= 0:
        raise ValueError("Invalid orientation: first and second picking points are coincident.")

    ux = vx / norm_v
    uy = vy / norm_v

    if y_axis_point is not None:
        yx, yy = y_axis_point
        wx = yx - x0
        wy = yy - y0
        norm_w = math.hypot(wx, wy)
        if norm_w > 0:
            vx2 = wx / norm_w
            vy2 = wy / norm_w
        else:
            vx2 = -uy
            vy2 = ux
    else:
        vx2 = -uy
        vy2 = ux

    if grid_length_x is None:
        grid_length_x = norm_v
    if grid_length_y is None:
        grid_length_y = norm_v

    grid_length_x = float(grid_length_x)
    grid_length_y = float(grid_length_y)
    distance_x = float(distance_x)
    distance_y = float(distance_y)

    if grid_length_x <= 0 or grid_length_y <= 0:
        raise ValueError("Grid dimensions must be positive.")
    if distance_x <= 0 or distance_y <= 0:
        raise ValueError("Grid cell size must be positive.")

    grid_layer = QgsVectorLayer(f"LineString?crs={raster_crs.authid()}", "Griglia Orientata", "memory")
    pr = grid_layer.dataProvider()
    pr.addAttributes([QgsField("ID", QVariant.String), QgsField("Type", QVariant.String)])
    grid_layer.updateFields()

    def point_at(base_x, base_y, ax_dx, ax_dy, ax_scale, ay_dx, ay_dy, ay_scale):
        return QgsPointXY(
            base_x + ax_dx * ax_scale + ay_dx * ay_scale,
            base_y + ax_dy * ax_scale + ay_dy * ay_scale,
        )

    def build_offsets(total_length, step):
        offsets = []
        value = 0.0
        while value < total_length:
            offsets.append(value)
            value += step
        if not offsets or abs(offsets[-1] - total_length) > 1e-9:
            offsets.append(total_length)
        return offsets

    offsets_x = build_offsets(grid_length_x, distance_x)
    offsets_y = build_offsets(grid_length_y, distance_y)

    features = []

    # TID: lines parallel to X axis, stepped along Y axis.
    for i, off_y in enumerate(offsets_y):
        p1 = point_at(x0, y0, vx2, vy2, off_y, ux, uy, 0.0)
        p2 = point_at(x0, y0, vx2, vy2, off_y, ux, uy, grid_length_x)
        f = QgsFeature()
        f.setGeometry(QgsGeometry.fromPolylineXY([p1, p2]))
        f.setAttributes([f"TID_{i}", "TID"])
        features.append(f)

    # LID: lines parallel to Y axis, stepped along X axis.
    for i, off_x in enumerate(offsets_x):
        p1 = point_at(x0, y0, ux, uy, off_x, vx2, vy2, 0.0)
        p2 = point_at(x0, y0, ux, uy, off_x, vx2, vy2, grid_length_y)
        f = QgsFeature()
        f.setGeometry(QgsGeometry.fromPolylineXY([p1, p2]))
        f.setAttributes([f"LID_{i}", "LID"])
        features.append(f)

    pr.addFeatures(features)
    grid_layer.updateExtents()
    QgsProject.instance().addMapLayer(grid_layer)
    return grid_layer
