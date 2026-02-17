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
    if not os.path.isfile(file_path):
        raise ValueError(f"Point cloud file not found: {file_path}")
    if os.path.getsize(file_path) <= 0:
        raise ValueError(f"Point cloud file is empty: {file_path}")

    layer_name = os.path.basename(file_path)
    layer = QgsPointCloudLayer(file_path, layer_name, "pdal")
    if not layer.isValid():
        err_obj = getattr(layer, "error", None)
        err_txt = ""
        try:
            if callable(err_obj):
                err = err_obj()
                if err is not None and hasattr(err, "summary"):
                    err_txt = err.summary() or ""
        except Exception:
            err_txt = ""
        suffix = f" | provider: {err_txt}" if err_txt else ""
        raise ValueError(f"Invalid point cloud layer: {file_path}{suffix}")

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
