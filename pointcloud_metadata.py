# -*- coding: utf-8 -*-
"""
Helpers to inspect LAS/LAZ point cloud metadata with QGIS API.
"""

import os

from qgis.core import QgsPointCloudLayer


def inspect_las_laz(file_path):
    """
    Return best-effort metadata for a LAS/LAZ file.
    """
    layer_name = os.path.basename(file_path)
    layer = QgsPointCloudLayer(file_path, layer_name, "pdal")
    if not layer.isValid():
        raise ValueError(f"Invalid point cloud layer: {file_path}")

    extent = layer.extent()
    crs_authid = layer.crs().authid() if layer.crs().isValid() else None

    point_count = None
    # pointCount API may vary by QGIS version/provider.
    if hasattr(layer, "pointCount"):
        try:
            point_count = int(layer.pointCount())
        except Exception:
            point_count = None

    return {
        "name": layer_name,
        "path": file_path,
        "crs": crs_authid,
        "extent": {
            "xmin": extent.xMinimum(),
            "xmax": extent.xMaximum(),
            "ymin": extent.yMinimum(),
            "ymax": extent.yMaximum(),
        },
        "point_count": point_count,
    }
