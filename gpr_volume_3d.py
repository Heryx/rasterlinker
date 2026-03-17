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

import json
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


def _axes_from_meta(volume: np.ndarray, meta: dict | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build coordinate axes from volume shape + metadata only."""
    vol = np.asarray(volume, dtype=np.float32)
    if vol.ndim != 3:
        raise ValueError("volume must be 3D (n_z, n_y, n_x)")
    n_z, n_y, n_x = [int(v) for v in vol.shape]
    md = dict(meta or {})

    res = float(md.get("resolution", 1.0) or 1.0)
    if not np.isfinite(res) or res <= 0:
        res = 1.0
    x_min = float(md.get("x_min", 0.0) or 0.0)
    y_min = float(md.get("y_min", 0.0) or 0.0)
    z_step = float(md.get("z_step", md.get("z_resolution", res)) or res)
    if not np.isfinite(z_step) or z_step <= 0:
        z_step = res

    z_levels = md.get("z_levels")
    z_axis = None
    if isinstance(z_levels, (list, tuple, np.ndarray)):
        try:
            z_candidate = np.asarray(z_levels, dtype=np.float32)
            if z_candidate.size == n_z and np.isfinite(z_candidate).all():
                z_axis = z_candidate
        except Exception:
            z_axis = None
    if z_axis is None:
        z_min = float(md.get("z_min", 0.0) or 0.0)
        z_max_raw = md.get("z_max", None)
        if z_max_raw is not None:
            try:
                z_max = float(z_max_raw)
                if n_z > 1 and np.isfinite(z_max):
                    z_axis = np.linspace(z_min, z_max, n_z, dtype=np.float32)
            except Exception:
                z_axis = None
        if z_axis is None:
            z_axis = (z_min + np.arange(n_z, dtype=np.float32) * np.float32(z_step)).astype(np.float32)

    y_axis = (y_min + np.arange(n_y, dtype=np.float32) * np.float32(res)).astype(np.float32)
    x_axis = (x_min + np.arange(n_x, dtype=np.float32) * np.float32(res)).astype(np.float32)
    return z_axis.astype(np.float32), y_axis, x_axis


def _axes_for_volume(
    volume: np.ndarray,
    grids: list[dict] | None = None,
    meta: dict | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Resolve axes from grids when possible, otherwise from metadata."""
    vol = np.asarray(volume, dtype=np.float32)
    if vol.ndim != 3:
        raise ValueError("volume must be 3D (n_z, n_y, n_x)")

    if grids:
        try:
            z_axis, y_axis, x_axis = volume_axes(grids, dict(meta or {}))
            if (
                z_axis.size == vol.shape[0]
                and y_axis.size == vol.shape[1]
                and x_axis.size == vol.shape[2]
            ):
                return z_axis.astype(np.float32), y_axis.astype(np.float32), x_axis.astype(np.float32)
        except Exception:
            pass

    return _axes_from_meta(vol, meta)


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def export_volume_to_npz(
    volume: np.ndarray,
    grids: list[dict] | None,
    meta: dict | None,
    output_path: str,
) -> dict:
    """Save volume + axes + metadata to compressed .npz."""
    vol = np.asarray(volume, dtype=np.float32)
    if vol.ndim != 3:
        raise ValueError("volume must be 3D (n_z, n_y, n_x)")
    if not output_path:
        raise ValueError("output_path is required")
    if not str(output_path).lower().endswith(".npz"):
        output_path = f"{output_path}.npz"

    md = dict(meta or {})
    z_axis, y_axis, x_axis = _axes_for_volume(vol, grids, md)
    z_step = float(np.median(np.diff(z_axis))) if z_axis.size > 1 else float(md.get("z_step", md.get("resolution", 1.0)) or 1.0)
    if not np.isfinite(z_step) or z_step <= 0:
        z_step = float(md.get("resolution", 1.0) or 1.0)

    md_export = dict(md)
    md_export.update(
        {
            "n_z": int(vol.shape[0]),
            "n_y": int(vol.shape[1]),
            "n_x": int(vol.shape[2]),
            "x_min": float(x_axis[0]) if x_axis.size else float(md.get("x_min", 0.0) or 0.0),
            "y_min": float(y_axis[0]) if y_axis.size else float(md.get("y_min", 0.0) or 0.0),
            "z_min": float(np.min(z_axis)) if z_axis.size else float(md.get("z_min", 0.0) or 0.0),
            "z_max": float(np.max(z_axis)) if z_axis.size else float(md.get("z_max", 0.0) or 0.0),
            "resolution": float(md.get("resolution", 1.0) or 1.0),
            "z_step": float(abs(z_step)),
            "z_levels": [float(v) for v in z_axis.tolist()],
        }
    )

    meta_json = json.dumps(_json_safe(md_export), ensure_ascii=True)
    np.savez_compressed(
        output_path,
        volume=vol,
        z_axis=z_axis.astype(np.float32, copy=False),
        y_axis=y_axis.astype(np.float32, copy=False),
        x_axis=x_axis.astype(np.float32, copy=False),
        meta_json=np.array(meta_json),
    )
    return {
        "path": output_path,
        "shape": tuple(int(v) for v in vol.shape),
        "n_voxels": int(vol.size),
    }


def load_volume_from_npz(input_path: str) -> tuple[np.ndarray, dict]:
    """Load volume + metadata from a .npz file created by export_volume_to_npz."""
    if not input_path:
        raise ValueError("input_path is required")
    with np.load(input_path, allow_pickle=False) as data:
        if "volume" not in data:
            raise ValueError("Invalid NPZ: missing 'volume'")
        vol = np.asarray(data["volume"], dtype=np.float32)
        if vol.ndim != 3:
            raise ValueError("Invalid NPZ: 'volume' must be 3D")

        meta = {}
        if "meta_json" in data:
            try:
                raw = data["meta_json"]
                if isinstance(raw, np.ndarray) and raw.shape == ():
                    raw = raw.item()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                if isinstance(raw, str):
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        meta.update(parsed)
            except Exception:
                pass

        z_axis = np.asarray(data["z_axis"], dtype=np.float32) if "z_axis" in data else np.zeros(0, dtype=np.float32)
        y_axis = np.asarray(data["y_axis"], dtype=np.float32) if "y_axis" in data else np.zeros(0, dtype=np.float32)
        x_axis = np.asarray(data["x_axis"], dtype=np.float32) if "x_axis" in data else np.zeros(0, dtype=np.float32)

    n_z, n_y, n_x = [int(v) for v in vol.shape]
    if x_axis.size == n_x:
        meta["x_min"] = float(x_axis[0])
        if x_axis.size > 1:
            dx = float(np.median(np.diff(x_axis)))
            if np.isfinite(dx) and dx > 0:
                meta["resolution"] = dx
    if y_axis.size == n_y:
        meta["y_min"] = float(y_axis[0])
    if z_axis.size == n_z:
        meta["z_levels"] = [float(v) for v in z_axis.tolist()]
        meta["z_min"] = float(np.min(z_axis))
        meta["z_max"] = float(np.max(z_axis))
        if z_axis.size > 1:
            dz = float(np.median(np.diff(z_axis)))
            if np.isfinite(dz) and dz > 0:
                meta["z_step"] = dz

    meta["n_z"] = n_z
    meta["n_y"] = n_y
    meta["n_x"] = n_x
    return vol, meta


def export_volume_to_vti(
    volume: np.ndarray,
    grids: list[dict] | None,
    meta: dict | None,
    output_path: str,
    epsg: int | None = None,
) -> dict:
    """Save volume as VTK ImageData (.vti), directly readable by PyVista."""
    try:
        import pyvista as pv
    except Exception as exc:
        raise ImportError("pyvista is required to export .vti volumes") from exc

    vol = np.asarray(volume, dtype=np.float32)
    if vol.ndim != 3:
        raise ValueError("volume must be 3D (n_z, n_y, n_x)")
    if not output_path:
        raise ValueError("output_path is required")
    if not str(output_path).lower().endswith(".vti"):
        output_path = f"{output_path}.vti"

    md = dict(meta or {})
    z_axis, y_axis, x_axis = _axes_for_volume(vol, grids, md)
    n_z, n_y, n_x = [int(v) for v in vol.shape]

    dx = float(np.median(np.diff(x_axis))) if x_axis.size > 1 else float(md.get("resolution", 1.0) or 1.0)
    dy = float(np.median(np.diff(y_axis))) if y_axis.size > 1 else dx
    dz = float(np.median(np.diff(z_axis))) if z_axis.size > 1 else float(md.get("z_step", dx) or dx)
    if not np.isfinite(dx) or dx <= 0:
        dx = 1.0
    if not np.isfinite(dy) or dy <= 0:
        dy = dx
    if not np.isfinite(dz) or dz <= 0:
        dz = max(dx, dy)

    x_min = float(x_axis[0]) if x_axis.size else float(md.get("x_min", 0.0) or 0.0)
    y_min = float(y_axis[0]) if y_axis.size else float(md.get("y_min", 0.0) or 0.0)
    z_min = float(np.min(z_axis)) if z_axis.size else float(md.get("z_min", 0.0) or 0.0)
    z_max = float(np.max(z_axis)) if z_axis.size else float(md.get("z_max", z_min + dz * max(n_z - 1, 0)) or 0.0)

    image = pv.ImageData()
    image.dimensions = (n_x + 1, n_y + 1, n_z + 1)
    image.origin = (x_min, y_min, -z_max)
    image.spacing = (dx, dy, dz)
    image.cell_data["amplitude"] = np.transpose(vol[::-1, :, :], (2, 1, 0)).ravel(order="F")
    image.field_data["z_min"] = np.array([z_min], dtype=np.float64)
    image.field_data["z_max"] = np.array([z_max], dtype=np.float64)
    image.field_data["z_step"] = np.array([dz], dtype=np.float64)
    if epsg:
        image.field_data["epsg"] = np.array([int(epsg)], dtype=np.int32)
    image.save(output_path)
    return {
        "path": output_path,
        "shape": (n_z, n_y, n_x),
        "spacing": (float(dx), float(dy), float(dz)),
        "epsg": int(epsg) if epsg else None,
    }


def load_volume_from_vti(input_path: str) -> tuple[np.ndarray, dict]:
    """Load a volume from a .vti file and reconstruct plugin metadata."""
    try:
        import pyvista as pv
    except Exception as exc:
        raise ImportError("pyvista is required to load .vti volumes") from exc

    if not input_path:
        raise ValueError("input_path is required")
    mesh = pv.read(input_path)
    if not hasattr(mesh, "dimensions"):
        raise ValueError("Invalid VTI: missing dimensions")
    dims = tuple(int(v) for v in mesh.dimensions)
    if len(dims) != 3 or min(dims) < 2:
        raise ValueError(f"Invalid VTI dimensions: {dims}")
    n_x, n_y, n_z = dims[0] - 1, dims[1] - 1, dims[2] - 1
    if min(n_x, n_y, n_z) <= 0:
        raise ValueError(f"Invalid VTI cell shape: {(n_z, n_y, n_x)}")

    scalar_name = "amplitude"
    if scalar_name not in mesh.cell_data:
        keys = list(mesh.cell_data.keys())
        if not keys:
            raise ValueError("Invalid VTI: no cell scalars")
        scalar_name = str(keys[0])

    values = np.asarray(mesh.cell_data[scalar_name], dtype=np.float32)
    expected = int(n_x * n_y * n_z)
    if values.size != expected:
        raise ValueError(
            f"Invalid VTI scalar size for '{scalar_name}': {values.size} != {expected}"
        )

    xyz = values.reshape((n_x, n_y, n_z), order="F")
    vol = np.transpose(xyz, (2, 1, 0))[::-1, :, :].astype(np.float32, copy=False)

    origin = tuple(float(v) for v in mesh.origin)
    spacing = tuple(float(v) for v in mesh.spacing)
    z_max = -origin[2]
    z_step = float(spacing[2])
    z_min = z_max - z_step * float(max(n_z - 1, 0))

    meta = {
        "x_min": float(origin[0]),
        "y_min": float(origin[1]),
        "resolution": float(spacing[0]),
        "z_step": float(z_step),
        "z_min": float(z_min),
        "z_max": float(z_max),
        "n_x": int(n_x),
        "n_y": int(n_y),
        "n_z": int(n_z),
        "z_levels": [float(v) for v in (z_min + np.arange(n_z, dtype=np.float64) * z_step).tolist()],
    }

    field_data = getattr(mesh, "field_data", None)
    if field_data is not None:
        try:
            if "epsg" in field_data:
                epsg_arr = np.asarray(field_data["epsg"]).ravel()
                if epsg_arr.size:
                    meta["epsg"] = int(epsg_arr[0])
            if "z_min" in field_data:
                zmin_arr = np.asarray(field_data["z_min"]).ravel()
                if zmin_arr.size and np.isfinite(float(zmin_arr[0])):
                    meta["z_min"] = float(zmin_arr[0])
            if "z_max" in field_data:
                zmax_arr = np.asarray(field_data["z_max"]).ravel()
                if zmax_arr.size and np.isfinite(float(zmax_arr[0])):
                    meta["z_max"] = float(zmax_arr[0])
            if "z_step" in field_data:
                zstep_arr = np.asarray(field_data["z_step"]).ravel()
                if zstep_arr.size and np.isfinite(float(zstep_arr[0])) and float(zstep_arr[0]) > 0:
                    meta["z_step"] = float(zstep_arr[0])
            if int(meta["n_z"]) > 0:
                meta["z_levels"] = [
                    float(v)
                    for v in (
                        float(meta["z_min"])
                        + np.arange(int(meta["n_z"]), dtype=np.float64) * float(meta["z_step"])
                    ).tolist()
                ]
        except Exception:
            pass

    return vol, meta


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
    grids: list[dict] | None,
    meta: dict | None,
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

    z_axis, y_axis, x_axis = _axes_for_volume(vol, grids, dict(meta or {}))
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
