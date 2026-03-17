"""3D volume helpers for GPR timeslice grids.

This module is intentionally independent from QGIS APIs.
It consumes the output of ``compute_ogpr_slice_grids``:
  - ``grids``: list[dict] with per-slice ``grid`` (2D float array)
  - ``meta``: dict with ``x_min``, ``y_min``, ``resolution`` and shape info

Volume convention:
  - shape is ``(n_z, n_y, n_x)``
  - axis 0: depth/slice index
  - axis 1: y rows
  - axis 2: x cols
"""

from __future__ import annotations

from typing import Iterable

import numpy as np


def _grid_sort_key(item: dict) -> tuple[float, float]:
    """Sort slices by explicit index first, then by depth value."""
    idx = item.get("index")
    z_lev = item.get("z_lev")
    try:
        k_idx = float(idx)
    except Exception:
        k_idx = float("inf")
    try:
        k_z = float(z_lev)
    except Exception:
        k_z = float("inf")
    return k_idx, k_z


def _sorted_grids(grids: Iterable[dict]) -> list[dict]:
    out = [g for g in grids if isinstance(g, dict) and "grid" in g]
    return sorted(out, key=_grid_sort_key)


def _validate_grid_shapes(grids: list[dict]) -> tuple[int, int]:
    first = np.asarray(grids[0]["grid"], dtype=np.float32)
    if first.ndim != 2:
        raise ValueError("grids[0]['grid'] must be 2D")
    n_y, n_x = int(first.shape[0]), int(first.shape[1])
    for i, item in enumerate(grids[1:], start=1):
        arr = np.asarray(item["grid"])
        if arr.ndim != 2:
            raise ValueError(f"grids[{i}]['grid'] must be 2D")
        if arr.shape != (n_y, n_x):
            raise ValueError(
                f"Inconsistent grid shape at index {i}: {arr.shape} != {(n_y, n_x)}"
            )
    return n_y, n_x


def build_3d_volume(grids: list[dict], meta: dict | None = None) -> np.ndarray:
    """Stack timeslice grids into a volume shaped ``(n_z, n_y, n_x)``.

    Grids are sorted by ``index`` (fallback ``z_lev``) before stacking.
    """
    ordered = _sorted_grids(grids)
    if not ordered:
        raise ValueError("No valid grids to build 3D volume")
    n_z = len(ordered)
    n_y, n_x = _validate_grid_shapes(ordered)
    vol = np.full((n_z, n_y, n_x), np.nan, dtype=np.float32)
    for iz, item in enumerate(ordered):
        vol[iz] = np.asarray(item["grid"], dtype=np.float32)
    return vol


def volume_axes(grids: list[dict], meta: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return coordinate axes (z_axis, y_axis, x_axis) for a stacked volume."""
    ordered = _sorted_grids(grids)
    if not ordered:
        raise ValueError("No valid grids to build axes")

    n_y, n_x = _validate_grid_shapes(ordered)
    res = float(meta.get("resolution", 1.0))
    x_min = float(meta.get("x_min", 0.0))
    y_min = float(meta.get("y_min", 0.0))

    z_vals = []
    for iz, item in enumerate(ordered):
        if item.get("z_lev") is not None:
            z_vals.append(float(item["z_lev"]))
            continue
        z_from = item.get("z_from")
        z_to = item.get("z_to")
        if z_from is not None and z_to is not None:
            z_vals.append((float(z_from) + float(z_to)) * 0.5)
        else:
            z_vals.append(float(iz))
    z_axis = np.asarray(z_vals, dtype=np.float32)
    y_axis = (y_min + np.arange(n_y, dtype=np.float32) * res).astype(np.float32)
    x_axis = (x_min + np.arange(n_x, dtype=np.float32) * res).astype(np.float32)
    return z_axis, y_axis, x_axis


def extract_c_scan(volume: np.ndarray, iz: int) -> np.ndarray:
    """Horizontal slice (timeslice): ``volume[iz, :, :]``."""
    vol = np.asarray(volume)
    if vol.ndim != 3:
        raise ValueError("volume must be 3D (n_z, n_y, n_x)")
    if iz < 0 or iz >= vol.shape[0]:
        raise IndexError(f"iz out of range: {iz}")
    return vol[iz, :, :]


def extract_b_scan_inline(volume: np.ndarray, iy: int) -> np.ndarray:
    """Vertical inline section: ``volume[:, iy, :]``."""
    vol = np.asarray(volume)
    if vol.ndim != 3:
        raise ValueError("volume must be 3D (n_z, n_y, n_x)")
    if iy < 0 or iy >= vol.shape[1]:
        raise IndexError(f"iy out of range: {iy}")
    return vol[:, iy, :]


def extract_b_scan_crossline(volume: np.ndarray, ix: int) -> np.ndarray:
    """Vertical crossline section: ``volume[:, :, ix]``."""
    vol = np.asarray(volume)
    if vol.ndim != 3:
        raise ValueError("volume must be 3D (n_z, n_y, n_x)")
    if ix < 0 or ix >= vol.shape[2]:
        raise IndexError(f"ix out of range: {ix}")
    return vol[:, :, ix]


def _surface_mask_from_threshold(mask: np.ndarray) -> np.ndarray:
    """Return boundary voxels of a binary 3D mask using 6-neighborhood."""
    inner = np.zeros_like(mask, dtype=bool)
    if mask.shape[0] < 3 or mask.shape[1] < 3 or mask.shape[2] < 3:
        return mask.copy()
    core = (
        mask[1:-1, 1:-1, 1:-1]
        & mask[:-2, 1:-1, 1:-1]
        & mask[2:, 1:-1, 1:-1]
        & mask[1:-1, :-2, 1:-1]
        & mask[1:-1, 2:, 1:-1]
        & mask[1:-1, 1:-1, :-2]
        & mask[1:-1, 1:-1, 2:]
    )
    inner[1:-1, 1:-1, 1:-1] = core
    return mask & (~inner)


def extract_isosurface_points(
    volume: np.ndarray,
    grids: list[dict],
    meta: dict,
    threshold: float,
    mode: str = "above",
    max_points: int | None = None,
    seed: int = 42,
) -> np.ndarray:
    """Extract isosurface-like voxel points from a thresholded volume.

    Returns array ``(N, 4)`` with columns: ``x, y, z, value``.
    """
    vol = np.asarray(volume, dtype=np.float32)
    if vol.ndim != 3:
        raise ValueError("volume must be 3D (n_z, n_y, n_x)")

    thr = float(threshold)
    md = str(mode or "above").strip().lower()
    finite = np.isfinite(vol)
    if md == "below":
        mask = finite & (vol <= thr)
    elif md == "abs":
        mask = finite & (np.abs(vol) >= abs(thr))
    else:
        mask = finite & (vol >= thr)

    surface = _surface_mask_from_threshold(mask)
    idx = np.argwhere(surface)
    if idx.size == 0:
        return np.zeros((0, 4), dtype=np.float32)

    if max_points is not None and max_points > 0 and idx.shape[0] > int(max_points):
        rng = np.random.default_rng(int(seed))
        sel = rng.choice(idx.shape[0], size=int(max_points), replace=False)
        idx = idx[sel]

    z_axis, y_axis, x_axis = volume_axes(grids, meta)
    iz = idx[:, 0].astype(np.int64)
    iy = idx[:, 1].astype(np.int64)
    ix = idx[:, 2].astype(np.int64)

    x = x_axis[ix]
    y = y_axis[iy]
    z = z_axis[iz]
    v = vol[iz, iy, ix]
    return np.column_stack([x, y, z, v]).astype(np.float32)


def export_points_to_las(
    output_path: str,
    points_xyzv: np.ndarray,
    epsg: int | None = None,
) -> dict:
    """Write ``(N,3)`` or ``(N,4)`` points to LAS using laspy.

    If the 4th column exists it is mapped to intensity [0..65535].
    """
    try:
        import laspy
    except Exception as exc:
        raise ImportError("laspy is required to export LAS points") from exc

    pts = np.asarray(points_xyzv, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] not in (3, 4):
        raise ValueError("points_xyzv must have shape (N,3) or (N,4)")
    if pts.shape[0] == 0:
        raise ValueError("No points to export")

    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = [0.001, 0.001, 0.001]
    las = laspy.LasData(header)
    las.x = pts[:, 0]
    las.y = pts[:, 1]
    las.z = pts[:, 2]

    if pts.shape[1] == 4:
        vals = pts[:, 3]
        finite = np.isfinite(vals)
        if finite.any():
            vmin = float(np.nanmin(vals[finite]))
            vmax = float(np.nanmax(vals[finite]))
            if vmax > vmin:
                scaled = (np.clip(vals, vmin, vmax) - vmin) / (vmax - vmin)
                intensity = np.clip(np.round(scaled * 65535.0), 0, 65535).astype(np.uint16)
            else:
                intensity = np.zeros(vals.shape[0], dtype=np.uint16)
        else:
            intensity = np.zeros(vals.shape[0], dtype=np.uint16)
        las.intensity = intensity

    if epsg:
        try:
            from pyproj import CRS

            las.header.add_crs(CRS.from_epsg(int(epsg)))
        except Exception:
            pass

    las.write(output_path)
    return {"path": output_path, "n_points": int(pts.shape[0]), "epsg": int(epsg) if epsg else None}
