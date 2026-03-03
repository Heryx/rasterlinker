# -*- coding: utf-8 -*-
"""
Helpers to inspect LAS/LAZ point cloud metadata with QGIS API.
"""

import os
import struct

from qgis.core import QgsPointCloudLayer


def _check_las_signature(file_path: str) -> None:
    """
    Minimal sanity check: verify the LASF signature and that the file
    is large enough to contain a basic header.

    Deliberately does NOT check point_count vs file_size because:
    - Some tools (CloudCompare, LAStools, custom subsamplers) write the
      original point_count in the header after subsampling, making the
      body smaller than the header claims.
    - Compressed LAS variants stored with a .las extension appear smaller
      than point_count * point_size.
    - PDAL (via QgsPointCloudLayer) is the authoritative validity check
      and handles all these cases correctly.
    """
    if os.path.splitext(file_path)[1].lower() != ".las":
        return  # LAZ / COPC: skip, PDAL handles them

    with open(file_path, "rb") as fh:
        header = fh.read(128)

    if len(header) < 4:
        raise ValueError("File LAS troppo corto per essere valido.")
    if header[0:4] != b"LASF":
        raise ValueError(
            "File LAS non valido: firma LASF mancante. "
            "Il file potrebbe essere corrotto o non essere un LAS."
        )


def inspect_las_laz(file_path: str) -> dict:
    """
    Return best-effort metadata for a LAS/LAZ file.

    Raises ValueError if the file is missing, empty, lacks the LASF
    signature, or cannot be loaded by the PDAL provider.
    """
    if not os.path.isfile(file_path):
        raise ValueError(f"Point cloud file not found: {file_path}")
    if os.path.getsize(file_path) <= 0:
        raise ValueError(f"Point cloud file is empty: {file_path}")

    _check_las_signature(file_path)

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
        raise ValueError(f"PDAL non riesce ad aprire il file: {file_path}{suffix}")

    extent = layer.extent()
    crs_authid = layer.crs().authid() if layer.crs().isValid() else None

    point_count = None
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
