"""Utilities for loading LAS point clouds and applying Z filters in QGIS."""

from __future__ import annotations

import laspy
import numpy as np
from qgis.core import QgsLayerTreeLayer, QgsPointCloudLayer, QgsProject


def get_z_range_chunked(las_path: str, chunk_size: int = 200_000) -> tuple[float, float]:
    """Return (z_min, z_max) by streaming LAS chunks without full in-memory load."""
    z_min = np.inf
    z_max = -np.inf

    with open(las_path, "rb") as las_stream:
        with laspy.LasReader(las_stream) as reader:
            for chunk in reader.chunk_iterator(chunk_size):
                z_values = np.asarray(chunk.z)
                if z_values.size == 0:
                    continue
                chunk_min = float(np.min(z_values))
                chunk_max = float(np.max(z_values))
                if chunk_min < z_min:
                    z_min = chunk_min
                if chunk_max > z_max:
                    z_max = chunk_max

    if z_min == np.inf:
        raise ValueError("File LAS vuoto o privo di coordinate Z")

    return float(z_min), float(z_max)


def load_gpr_pointcloud(las_path: str, layer_name: str = "GPR_Volume") -> QgsPointCloudLayer:
    """Create and validate a QGIS point cloud layer backed by PDAL."""
    layer = QgsPointCloudLayer(las_path, layer_name, "pdal")
    if not layer.isValid():
        raise ValueError("Impossibile caricare il LAS: richiede QGIS >= 3.18 con provider PDAL attivo.")
    return layer


def set_z_slice(layer: QgsPointCloudLayer, z_low: float, z_high: float) -> bool:
    """Apply a Z slice; return True only when subset-string filtering succeeds."""
    try:
        expr = f'"Z" >= {z_low:.6f} AND "Z" < {z_high:.6f}'
        if layer.setSubsetString(expr):
            layer.triggerRepaint()
            return True
    except Exception:
        pass

    try:
        elev = layer.elevationProperties()
        if hasattr(elev, "setZMin") and hasattr(elev, "setZMax"):
            elev.setZMin(z_low)
            elev.setZMax(z_high)
            layer.triggerRepaint()
            return False
    except Exception:
        pass

    return False

