"""Utilities for loading LAS point clouds and applying Z filters in QGIS."""

from __future__ import annotations

import json
import os
import subprocess

try:
    import laspy
    import numpy as np
    _HAS_LASPY = True
except ImportError:
    laspy = None
    np = None
    _HAS_LASPY = False
from qgis.core import QgsPointCloudLayer


def _header_z_range(reader):
    """Best-effort Z range from LAS header metadata."""
    try:
        mins = getattr(reader.header, "mins", None)
        maxs = getattr(reader.header, "maxs", None)
        if mins is None or maxs is None:
            return None
        z_min = float(mins[2])
        z_max = float(maxs[2])
        if np.isfinite(z_min) and np.isfinite(z_max) and z_min <= z_max:
            return z_min, z_max
    except Exception:
        return None
    return None


def _pdal_stats_z_range(las_path: str):
    """Fallback Z range extraction via PDAL CLI stats output."""
    try:
        result = subprocess.run(
            ["pdal", "info", las_path, "--stats"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout or "{}")
        statistics = ((payload.get("stats") or {}).get("statistic") or [])
        for stat in statistics:
            if str(stat.get("name") or "").strip().upper() != "Z":
                continue
            z_min = stat.get("minimum")
            z_max = stat.get("maximum")
            if z_min is None or z_max is None:
                continue
            z_min = float(z_min)
            z_max = float(z_max)
            if np.isfinite(z_min) and np.isfinite(z_max) and z_min <= z_max:
                return z_min, z_max
    except Exception:
        return None
    return None


def _validate_las_header_file_size(las_path: str) -> None:
    """Detect truncated/corrupted uncompressed LAS files using header values."""
    if not las_path.lower().endswith(".las"):
        return
    if not _HAS_LASPY:
        return

    try:
        with laspy.open(las_path) as reader:
            header = reader.header
            point_count = int(getattr(header, "point_count", 0) or 0)
            point_format = getattr(header, "point_format", None)
            point_size = int(getattr(point_format, "size", 0) or 0)
            offset = int(getattr(header, "offset_to_point_data", 0) or 0)
    except Exception as e:
        raise ValueError(f"Header LAS non leggibile: {e}")

    if point_count <= 0 or point_size <= 0:
        return

    file_size = int(os.path.getsize(las_path))
    minimum_size = offset + (point_count * point_size)
    if minimum_size > file_size:
        raise ValueError(
            "Header LAS incoerente: "
            f"point_count={point_count}, point_size={point_size}, "
            f"offset={offset}, file_size={file_size}. "
            "File probabilmente troncato o corrotto."
        )


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
    _validate_las_header_file_size(las_path)

    z_min = np.inf
    z_max = -np.inf
    last_error = None
    header_range = None

    # --- Method 1: chunked streaming (laspy 2.x path-based API) ----------
    try:
        with laspy.open(las_path) as reader:
            header_range = _header_z_range(reader)
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

    except Exception as e:
        last_error = e
        # Any error (buffer alignment, unsupported format, etc.)
        # → fall through to full read
        pass

    # --- Method 2: full in-memory read (slower but always works) ---------
    try:
        las = laspy.read(las_path)
        try:
            z_values = np.asarray(las.z, dtype=np.float64)
        except Exception:
            z_values = np.asarray(las.z).astype(np.float64)
        if z_values.size > 0:
            return float(z_values.min()), float(z_values.max())
    except Exception as e:
        last_error = e

    # --- Method 3: header metadata fallback -------------------------------
    if header_range is not None:
        return float(header_range[0]), float(header_range[1])

    # --- Method 4: PDAL stats fallback ------------------------------------
    pdal_range = _pdal_stats_z_range(las_path)
    if pdal_range is not None:
        return float(pdal_range[0]), float(pdal_range[1])

    if last_error is not None:
        raise ValueError(f"Impossibile leggere range Z: {last_error}")
    raise ValueError("File LAS vuoto o privo di coordinate Z")


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
