"""Utilities for loading LAS point clouds and applying Z filters in QGIS."""

from __future__ import annotations

try:
    import laspy
    import numpy as np
    _HAS_LASPY = True
except ImportError:
    laspy = None
    np = None
    _HAS_LASPY = False
from qgis.core import QgsPointCloudLayer


def get_z_range_chunked(las_path: str, chunk_size: int = 200_000) -> tuple[float, float]:
    """
    Return (z_min, z_max) scanning the LAS/LAZ file.

    Strategy:
      1. laspy.open(path) + chunk_iterator  – memory-efficient, handles
         most LAS 1.2/1.4 / COPC / LAZ files including extra-bytes VLRs.
      2. laspy.read(path)  – full in-memory fallback for non-standard
         point-record sizes that cause NumPy buffer alignment errors.
    """
    if not _HAS_LASPY:
        raise ImportError("laspy non installato")

    z_min = np.inf
    z_max = -np.inf

    # --- Method 1: chunked streaming (laspy 2.x path-based API) ----------
    try:
        with laspy.open(las_path) as reader:
            for chunk in reader.chunk_iterator(chunk_size):
                try:
                    z_values = np.asarray(chunk.z, dtype=np.float64)
                except Exception:
                    # extra-bytes or exotic dtype: let numpy pick
                    z_values = np.asarray(chunk.z).astype(np.float64)
                if z_values.size == 0:
                    continue
                chunk_min = float(z_values.min())
                chunk_max = float(z_values.max())
                if chunk_min < z_min:
                    z_min = chunk_min
                if chunk_max > z_max:
                    z_max = chunk_max

        if z_min != np.inf:
            return float(z_min), float(z_max)
        # Chunked read returned no data – fall through to method 2

    except Exception:
        # Any error (buffer alignment, unsupported format, etc.)
        # → fall through to full read
        pass

    # --- Method 2: full in-memory read (slower but always works) ---------
    las = laspy.read(las_path)
    try:
        z_values = np.asarray(las.z, dtype=np.float64)
    except Exception:
        z_values = np.asarray(las.z).astype(np.float64)

    if z_values.size == 0:
        raise ValueError("File LAS vuoto o privo di coordinate Z")

    return float(z_values.min()), float(z_values.max())


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
