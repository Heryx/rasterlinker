# -*- coding: utf-8 -*-
"""
Helpers to inspect LAS/LAZ point cloud metadata with QGIS API.
"""

import os
import struct

from qgis.core import QgsPointCloudLayer


def _validate_uncompressed_las_header(file_path):
    """Validate uncompressed LAS point-count vs file-size consistency."""
    if os.path.splitext(file_path)[1].lower() != ".las":
        return

    with open(file_path, "rb") as fh:
        header = fh.read(375)

    if len(header) < 111:
        raise ValueError("Header LAS incompleto (file troppo corto).")
    if header[0:4] != b"LASF":
        raise ValueError("Header LAS non valido (firma LASF mancante).")

    version_major = header[24]
    version_minor = header[25]
    point_offset = struct.unpack_from("<I", header, 96)[0]
    point_size = struct.unpack_from("<H", header, 105)[0]
    legacy_count = struct.unpack_from("<I", header, 107)[0]
    has_extended_count = (version_major > 1) or (
        version_major == 1 and version_minor >= 4
    )
    if has_extended_count and len(header) < 255:
        raise ValueError("Header LAS 1.4 incompleto (campo point_count esteso mancante).")

    extended_count = (
        struct.unpack_from("<Q", header, 247)[0] if has_extended_count else 0
    )
    point_count = extended_count if extended_count > 0 else legacy_count

    if point_count <= 0 or point_size <= 0:
        return

    file_size = os.path.getsize(file_path)
    minimum_size = int(point_offset) + int(point_count) * int(point_size)
    if minimum_size > file_size:
        raise ValueError(
            "Header LAS incoerente: "
            f"point_count={point_count}, point_size={point_size}, "
            f"offset={point_offset}, file_size={file_size}. "
            "File troncato/corrotto o esportazione LAS non valida."
        )


def inspect_las_laz(file_path):
    """
    Return best-effort metadata for a LAS/LAZ file.
    """
    if not os.path.isfile(file_path):
        raise ValueError(f"Point cloud file not found: {file_path}")
    if os.path.getsize(file_path) <= 0:
        raise ValueError(f"Point cloud file is empty: {file_path}")
    _validate_uncompressed_las_header(file_path)

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
