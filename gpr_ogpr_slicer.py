# -*- coding: utf-8 -*-
"""
OGPR profiles -> GeoTIFF timeslice pipeline.

Flow summary:
  - Apply pipeline per channel, compute Hilbert envelope for amplitude
    extraction (fallback to abs if SciPy missing).
  - Aggregate points per depth window and interpolate via IDW
    (isotropic or anisotropic).
  - Support preview-in-RAM (compute grids without I/O) and
    separated write step to persist GeoTIFF + QML sidecar.
"""

from __future__ import annotations

import json
import os

import numpy as np

SIDECAR_FILENAME = ".ogpr_slicer_params.json"


# ---------------------------------------------------------------------------
# Sidecar params
# ---------------------------------------------------------------------------


def save_ogpr_slicer_params(output_dir: str, params: dict) -> str:
    path = os.path.join(output_dir, SIDECAR_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2, ensure_ascii=True)
    return path


def load_ogpr_slicer_params(output_dir: str) -> dict | None:
    path = os.path.join(output_dir, SIDECAR_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _envelope(data: np.ndarray) -> np.ndarray:
    """Calculate Hilbert envelope along axis 0 (samples/depth).

    Falls back to absolute value if SciPy is not available.
    """
    try:
        from scipy.signal import hilbert  # type: ignore
        analytic = hilbert(data.astype(np.float64), axis=0)
        return np.abs(analytic).astype(np.float32)
    except Exception:
        return np.abs(data).astype(np.float32)


def _depth_to_sample_range(
    z_from: float,
    z_to: float,
    depth_max_m: float,
    n_samples: int,
) -> tuple[int, int]:
    if depth_max_m <= 0:
        return 0, n_samples
    s_lo = int(np.floor(max(z_from, 0.0) / depth_max_m * (n_samples - 1)))
    s_hi = int(np.ceil(min(z_to, depth_max_m) / depth_max_m * (n_samples - 1))) + 1
    return max(0, s_lo), min(n_samples, s_hi)


def _normalize_channels(ampl_3d: np.ndarray) -> np.ndarray:
    out = ampl_3d.copy()
    n_ch = ampl_3d.shape[2]
    flat = ampl_3d.reshape(-1, n_ch)
    global_mean = float(np.nanmean(np.abs(flat)))
    if global_mean < 1e-12:
        return out
    for ci in range(n_ch):
        ch_mean = float(np.nanmean(np.abs(flat[:, ci])))
        if ch_mean > 1e-12:
            out[:, :, ci] = ampl_3d[:, :, ci] * (global_mean / ch_mean)
    return out


def _remove_amplitude_outliers(
    x: np.ndarray, y: np.ndarray, values: np.ndarray, n_sigma: float = 3.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if values.size == 0:
        return x, y, values
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return x, y, values
    mean = float(finite.mean())
    std = float(finite.std())
    if std == 0.0:
        return x, y, values
    mask = np.abs(values - mean) <= n_sigma * std
    return x[mask], y[mask], values[mask]


def _estimate_acquisition_direction(x: np.ndarray, y: np.ndarray) -> float:
    if x.size < 3:
        return 0.0
    pts = np.column_stack([x - x.mean(), y - y.mean()])
    cov = np.cov(pts.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    principal = eigvecs[:, np.argmax(eigvals)]
    return float(np.degrees(np.arctan2(principal[1], principal[0]))) % 180.0


def _estimate_interline_radius(
    x: np.ndarray, y: np.ndarray, percentile: float = 95.0, multiplier: float = 1.5,
) -> dict:
    from scipy.spatial import cKDTree

    if x.size < 4:
        return {"radius": 1.0, "anisotropy_ratio": 1.0, "acquisition_angle": 0.0}
    tree = cKDTree(np.column_stack([x, y]))
    dists, _ = tree.query(np.column_stack([x, y]), k=min(6, x.size))
    along_line = float(np.percentile(dists[:, 1], 10))
    cross_line = float(np.percentile(dists[:, 1], percentile))
    radius = cross_line * multiplier
    aniso = (cross_line / along_line) if along_line > 1e-9 else 1.0
    return {
        "radius": max(radius, 1e-6),
        "anisotropy_ratio": aniso,
        "acquisition_angle": _estimate_acquisition_direction(x, y),
    }


def _fill_nodata_grid(grid: np.ndarray, max_distance: int = 5) -> np.ndarray:
    from scipy.ndimage import distance_transform_edt
    nan_mask = np.isnan(grid)
    if not nan_mask.any():
        return grid
    dist, (row_idx, col_idx) = distance_transform_edt(
        nan_mask, return_distances=True, return_indices=True,
    )
    filled = grid.copy()
    fw = nan_mask & (dist <= max_distance)
    filled[fw] = grid[row_idx[fw], col_idx[fw]]
    return filled


def _smooth_grid_gaussian(grid: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    from scipy.ndimage import gaussian_filter
    if sigma <= 0:
        return grid
    nan_mask = np.isnan(grid)
    filled = np.where(nan_mask, 0.0, grid.astype(np.float64))
    weight = np.where(nan_mask, 0.0, 1.0)
    smooth_num = gaussian_filter(filled, sigma=sigma)
    smooth_den = gaussian_filter(weight, sigma=sigma)
    result = np.full_like(grid, np.nan, dtype=np.float32)
    valid = smooth_den > 1e-9
    result[valid] = (smooth_num[valid] / smooth_den[valid]).astype(np.float32)
    return result


def _bin_with_idw(
    x_pts, y_pts, i_pts, x_min, y_min, n_x, n_y, resolution, radius, power=2.0, min_points=3
):
    from scipy.spatial import cKDTree
    gx = x_min + np.arange(n_x) * resolution
    gy = y_min + np.arange(n_y) * resolution
    gxx, gyy = np.meshgrid(gx, gy)
    gpts = np.column_stack([gxx.ravel(), gyy.ravel()])
    tree = cKDTree(np.column_stack([x_pts, y_pts]))
    results = tree.query_ball_point(gpts, r=radius, workers=-1)
    i64 = i_pts.astype(np.float64)
    grid = np.full(n_x * n_y, np.nan, dtype=np.float32)
    for k, idx_list in enumerate(results):
        if len(idx_list) < min_points:
            continue
        idx = np.asarray(idx_list, dtype=np.int64)
        cx, cy = gpts[k]
        d = np.sqrt((x_pts[idx] - cx) ** 2 + (y_pts[idx] - cy) ** 2)
        w = 1.0 / (d ** power + 1e-9)
        ws = w.sum()
        if ws > 0:
            grid[k] = float(np.dot(w, i64[idx]) / ws)
    return grid.reshape(n_y, n_x)


def _bin_with_idw_anisotropic(
    x_pts, y_pts, i_pts, x_min, y_min, n_x, n_y, resolution, radius, power=2.0, min_points=3,
    anisotropy_ratio=1.0, anisotropy_angle=0.0,
):
    from scipy.spatial import cKDTree
    if anisotropy_ratio <= 0:
        anisotropy_ratio = 1.0
    ar = np.radians(anisotropy_angle)
    ca, sa = np.cos(ar), np.sin(ar)

    def _ad(px, py, cx, cy):
        dx, dy = px - cx, py - cy
        return np.sqrt((dx * ca + dy * sa) ** 2 + ((-dx * sa + dy * ca) / anisotropy_ratio) ** 2)

    gx = x_min + np.arange(n_x) * resolution
    gy = y_min + np.arange(n_y) * resolution
    gxx, gyy = np.meshgrid(gx, gy)
    gpts = np.column_stack([gxx.ravel(), gyy.ravel()])
    tree = cKDTree(np.column_stack([x_pts, y_pts]))
    results = tree.query_ball_point(gpts, r=radius * max(1.0, anisotropy_ratio), workers=-1)
    i64 = i_pts.astype(np.float64)
    grid = np.full(n_x * n_y, np.nan, dtype=np.float32)
    for k, idx_list in enumerate(results):
        if not idx_list:
            continue
        idx = np.asarray(idx_list, dtype=np.int64)
        cx, cy = gpts[k]
        d = _ad(x_pts[idx], y_pts[idx], cx, cy)
        in_r = d <= radius
        if in_r.sum() < min_points:
            continue
        d_in = d[in_r]
        w = 1.0 / (d_in ** power + 1e-9)
        ws = w.sum()
        if ws > 0:
            grid[k] = float(np.dot(w, i64[idx[in_r]]) / ws)
    return grid.reshape(n_y, n_x)


# QML sidecar (single-band, percentile stretch)
_QML_SINGLEBAND = """\
<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.0" styleCategories="AllStyleCategories">
  <pipe>
    <provider><resampling enabled="false" maxOversampling="2"
      zoomedInResamplingMethod="nearestNeighbour"
      zoomedOutResamplingMethod="nearestNeighbour"/></provider>
    <rasterrenderer type="singlebandgray" opacity="1" alphaBand="-1"
                    grayBand="1" gradient="BlackToWhite">
      <rasterTransparency/>
      <minMaxOrigin>
        <limits>CumulativeCut</limits>
        <extent>WholeRaster</extent>
        <statAccuracy>Estimated</statAccuracy>
        <cumulativeCutLower>0.02</cumulativeCutLower>
        <cumulativeCutUpper>0.98</cumulativeCutUpper>
      </minMaxOrigin>
      <contrastEnhancement>
        <minValue>{vmin}</minValue>
        <maxValue>{vmax}</maxValue>
        <algorithm>StretchToMinimumMaximum</algorithm>
      </contrastEnhancement>
    </rasterrenderer>
    <brightnesscontrast brightness="0" contrast="0" gamma="1"/>
    <huesaturation saturation="0" grayscaleMode="0" colorizeOn="0"/>
    <rasterresampler maxOversampling="2"/>
  </pipe>
  <blendMode>0</blendMode>
</qgis>
"""


def _write_qml_singleband(tif_path: str, grid: np.ndarray) -> None:
    """Write .qml sidecar using 2-98 percentile stretch of the grid."""
    finite = grid[np.isfinite(grid)]
    if finite.size == 0:
        vmin, vmax = 0.0, 1.0
    else:
        vmin = float(np.percentile(finite, 2))
        vmax = float(np.percentile(finite, 98))
        if vmax <= vmin:
            vmax = vmin + 1e-6
    qml = _QML_SINGLEBAND.format(vmin=vmin, vmax=vmax)
    qml_path = os.path.splitext(tif_path)[0] + ".qml"
    with open(qml_path, "w", encoding="utf-8") as f:
        f.write(qml)


# ---------------------------------------------------------------------------
# Shared helpers: processing pipeline, grid param building and interpolation
# ---------------------------------------------------------------------------


def _process_profiles(
    profiles: list,
    channel: int,
    combine_method: str,
    params: dict,
    normalize_channels: bool,
) -> list[tuple]:
    """Process profiles and return list of (prof, ch_ref, ampl_3d).

    Applies the pipeline per channel, computes Hilbert envelope (fallback to abs)
    and optionally normalizes channels.
    """
    from .gpr_processing import apply_pipeline, apply_pre_bg_pipeline

    bg_reference = None
    if params.get("bg_removal", True) and params.get("bg_mode") == "grid_by_grid":
        _pre_traces = []
        for prof in profiles:
            n_ch = prof.n_channels
            ch_list = list(range(n_ch)) if channel < 0 else [min(channel, n_ch - 1)]
            for ci in ch_list:
                ch = prof.channel(ci)
                pre = apply_pre_bg_pipeline(ch.data.copy(), params, dt_ns=prof.dt_ns)
                _pre_traces.append(pre)
        if _pre_traces:
            min_ns = min(t.shape[0] for t in _pre_traces)
            stacked = np.concatenate([t[:min_ns, :] for t in _pre_traces], axis=1)
            bg_reference = stacked.mean(axis=1).astype(np.float64)

    processed = []
    for prof in profiles:
        n_ch = prof.n_channels
        ch_ref = prof.channel(0)
        ch_list = list(range(n_ch)) if channel < 0 else [min(channel, n_ch - 1)]
        proc_channels = []
        for ci in ch_list:
            ch = prof.channel(ci)
            raw = ch.data.copy()
            try:
                proc = apply_pipeline(
                    raw, params, dt_ns=prof.dt_ns, bg_reference_trace=bg_reference
                )
            except Exception as exc:
                print(f"[OGPR slicer] pipeline error ch{ci} in {getattr(prof, 'path', '')}: {exc}")
                proc = np.abs(raw).astype(np.float32)
            # compute envelope (hilbert) for amplitude extraction
            env = _envelope(proc)
            proc_channels.append(env)

        ampl_3d = np.stack(proc_channels, axis=2)
        if normalize_channels and ampl_3d.shape[2] > 1:
            ampl_3d = _normalize_channels(ampl_3d)
        processed.append((prof, ch_ref, ampl_3d))
    return processed


def _build_grid_params(
    processed: list,
    resolution: float,
    radius: float | None,
    auto_radius: bool,
    use_anisotropic_idw: bool,
    anisotropy_ratio: float | None,
    anisotropy_angle: float | None,
) -> dict:
    all_e = np.concatenate([ch.easting for _, ch, _ in processed])
    all_n = np.concatenate([ch.northing for _, ch, _ in processed])
    x_min = float(all_e.min())
    x_max = float(all_e.max())
    y_min = float(all_n.min())
    y_max = float(all_n.max())
    n_x = max(2, int(np.round((x_max - x_min) / resolution)) + 1)
    n_y = max(2, int(np.round((y_max - y_min) / resolution)) + 1)
    if radius is None:
        radius = resolution * (2.0 ** 0.5)
    _aniso_info = None
    if auto_radius or (use_anisotropic_idw and (anisotropy_ratio is None or anisotropy_angle is None)):
        _aniso_info = _estimate_interline_radius(all_e, all_n)
    if auto_radius and _aniso_info is not None:
        radius = _aniso_info["radius"]
    eff_ratio = anisotropy_ratio
    eff_angle = anisotropy_angle
    if use_anisotropic_idw:
        if eff_ratio is None:
            eff_ratio = (_aniso_info or {}).get("anisotropy_ratio", 1.0)
        if eff_angle is None:
            eff_angle = (_aniso_info or {}).get("acquisition_angle", 0.0)
    return {
        "x_min": x_min, "y_min": y_min,
        "x_max": x_max, "y_max": y_max,
        "n_x": n_x, "n_y": n_y,
        "radius": radius,
        "eff_ratio": eff_ratio,
        "eff_angle": eff_angle,
    }


def _interpolate_z_level(
    processed: list,
    z_from: float, z_to: float,
    combine_method: str,
    gp: dict,
    resolution: float,
    amplitude_sigma: float | None,
    use_anisotropic_idw: bool,
    idw_power: float, min_points: int,
    fill_nodata: bool, fill_nodata_max_distance: int,
    smooth_sigma: float,
) -> tuple[np.ndarray | None, int]:
    pts_e, pts_n, pts_a = [], [], []
    for prof, ch, ampl_3d in processed:
        n_s = ampl_3d.shape[0]
        s_lo, s_hi = _depth_to_sample_range(z_from, z_to, prof.depth_max_m, n_s)
        if s_lo >= s_hi:
            continue
        window = np.abs(ampl_3d[s_lo:s_hi, :, :])
        per_ch = window.mean(axis=0)
        ampl = (per_ch.mean(axis=1) if (per_ch.shape[1] == 1 or combine_method == "mean")
                else per_ch.max(axis=1)).astype(np.float32)
        pts_e.append(ch.easting)
        pts_n.append(ch.northing)
        pts_a.append(ampl)
    if not pts_e:
        return None, 0
    e_all = np.concatenate(pts_e)
    n_all = np.concatenate(pts_n)
    a_all = np.concatenate(pts_a)
    if amplitude_sigma is not None:
        e_all, n_all, a_all = _remove_amplitude_outliers(e_all, n_all, a_all, n_sigma=amplitude_sigma)
    n_pts = len(e_all)
    if use_anisotropic_idw:
        grid = _bin_with_idw_anisotropic(
            e_all, n_all, a_all,
            gp["x_min"], gp["y_min"], gp["n_x"], gp["n_y"],
            resolution, gp["radius"],
            power=idw_power, min_points=min_points,
            anisotropy_ratio=gp["eff_ratio"] or 1.0,
            anisotropy_angle=gp["eff_angle"] or 0.0,
        )
    else:
        grid = _bin_with_idw(
            e_all, n_all, a_all,
            gp["x_min"], gp["y_min"], gp["n_x"], gp["n_y"],
            resolution, gp["radius"],
            power=idw_power, min_points=min_points,
        )
    if fill_nodata:
        grid = _fill_nodata_grid(grid, max_distance=fill_nodata_max_distance)
    if smooth_sigma > 0:
        grid = _smooth_grid_gaussian(grid, sigma=smooth_sigma)
    return grid, n_pts


# ---------------------------------------------------------------------------
# Preview: single slice in RAM
# ---------------------------------------------------------------------------


def compute_preview_slice(
    profiles: list,
    z_center: float,
    channel: int = -1,
    combine_method: str = "mean",
    resolution: float = 0.10,
    z_step: float = 0.10,
    radius: float | None = None,
    pipeline_params: dict | None = None,
    normalize_channels: bool = True,
    amplitude_sigma: float | None = None,
    use_anisotropic_idw: bool = False,
    auto_radius: bool = False,
    anisotropy_ratio: float | None = None,
    anisotropy_angle: float | None = None,
    idw_power: float = 2.0,
    min_points: int = 3,
    fill_nodata: bool = False,
    fill_nodata_max_distance: int = 5,
    smooth_sigma: float = 0.0,
) -> dict | None:
    """Compute a single timeslice without writing to disk; used for preview dialog."""
    from .gpr_processing import DEFAULT_PIPELINE
    if not profiles:
        return None
    params = {**DEFAULT_PIPELINE, **(pipeline_params or {})}
    processed = _process_profiles(profiles, channel, combine_method, params, normalize_channels)
    if not processed:
        return None
    gp = _build_grid_params(
        processed, resolution, radius,
        auto_radius, use_anisotropic_idw,
        anisotropy_ratio, anisotropy_angle,
    )
    z_from = z_center - z_step / 2.0
    z_to = z_center + z_step / 2.0
    grid, n_pts = _interpolate_z_level(
        processed, z_from, z_to, combine_method, gp, resolution,
        amplitude_sigma, use_anisotropic_idw,
        idw_power, min_points,
        fill_nodata, fill_nodata_max_distance, smooth_sigma,
    )
    if grid is None or n_pts == 0:
        return None
    n_valid = int(np.count_nonzero(~np.isnan(grid)))
    n_total = gp["n_x"] * gp["n_y"]
    return {
        "grid": grid,
        "x_min": gp["x_min"],
        "y_min": gp["y_min"],
        "resolution": resolution,
        "z_center": float(z_center),
        "z_from": round(z_from, 6),
        "z_to": round(z_to, 6),
        "n_pts": n_pts,
        "n_valid_cells": n_valid,
        "n_total_cells": n_total,
        "radius": gp["radius"],
        "fill_pct": 100.0 * n_valid / max(n_total, 1),
    }


# ---------------------------------------------------------------------------
# Compute grids for multiple slices (no I/O)
# ---------------------------------------------------------------------------


def compute_ogpr_slice_grids(
    profiles: list,
    channel: int = -1,
    combine_method: str = "mean",
    resolution: float = 0.10,
    z_step: float = 0.05,
    z_min: float | None = None,
    z_max: float | None = None,
    radius: float | None = None,
    pipeline_params: dict | None = None,
    normalize_channels: bool = True,
    amplitude_sigma: float | None = None,
    use_anisotropic_idw: bool = False,
    auto_radius: bool = False,
    anisotropy_ratio: float | None = None,
    anisotropy_angle: float | None = None,
    idw_power: float = 2.0,
    min_points: int = 3,
    fill_nodata: bool = False,
    fill_nodata_max_distance: int = 5,
    smooth_sigma: float = 0.0,
) -> tuple[list[dict], dict]:
    """Compute IDW grids for each slice and return (grids, meta)."""
    from .gpr_processing import DEFAULT_PIPELINE

    if not profiles:
        return [], {}

    params = {**DEFAULT_PIPELINE, **(pipeline_params or {})}

    processed = _process_profiles(profiles, channel, combine_method, params, normalize_channels)
    if not processed:
        return [], {}

    # bounding box and grid params
    all_e = np.concatenate([ch.easting for _, ch, _ in processed])
    all_n = np.concatenate([ch.northing for _, ch, _ in processed])
    x_min = float(all_e.min())
    x_max = float(all_e.max())
    y_min = float(all_n.min())
    y_max = float(all_n.max())
    n_x = max(2, int(np.round((x_max - x_min) / resolution)) + 1)
    n_y = max(2, int(np.round((y_max - y_min) / resolution)) + 1)
    if radius is None:
        radius = resolution * (2.0 ** 0.5)
    _aniso_info = None
    if auto_radius or (use_anisotropic_idw and (anisotropy_ratio is None or anisotropy_angle is None)):
        _aniso_info = _estimate_interline_radius(all_e, all_n)
    if auto_radius and _aniso_info is not None:
        radius = _aniso_info["radius"]
    eff_ratio = anisotropy_ratio
    eff_angle = anisotropy_angle
    if use_anisotropic_idw:
        if eff_ratio is None:
            eff_ratio = (_aniso_info or {}).get("anisotropy_ratio", 1.0)
        if eff_angle is None:
            eff_angle = (_aniso_info or {}).get("acquisition_angle", 0.0)

    meta = dict(x_min=x_min, y_min=y_min, y_max=y_max, x_max=x_max, n_x=n_x, n_y=n_y, resolution=resolution)

    if z_min is None:
        z_min = 0.0
    if z_max is None:
        z_max = max(prof.depth_max_m for prof, _, _ in processed)

    z_levels = np.arange(float(z_min), float(z_max) + z_step * 0.5, float(z_step))

    grids = []
    for iz, z_lev in enumerate(z_levels):
        z_from = float(z_lev - z_step / 2.0)
        z_to = float(z_lev + z_step / 2.0)
        grid, n_pts = _interpolate_z_level(
            processed, z_from, z_to, combine_method,
            {
                "x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max,
                "n_x": n_x, "n_y": n_y, "radius": radius,
                "eff_ratio": eff_ratio, "eff_angle": eff_angle,
            },
            resolution, amplitude_sigma, use_anisotropic_idw,
            idw_power, min_points, fill_nodata, fill_nodata_max_distance, smooth_sigma,
        )
        if grid is None:
            continue
        grids.append({
            "z_lev": float(z_lev),
            "z_from": round(z_from, 6),
            "z_to": round(z_to, 6),
            "index": iz,
            "grid": grid,
        })

    return grids, meta


# ---------------------------------------------------------------------------
# Write grids to GeoTIFF + sidecar
# ---------------------------------------------------------------------------


def write_grids_to_tifs(
    grids: list[dict],
    meta: dict,
    output_dir: str,
    epsg: int | None = None,
) -> list[dict]:
    """Write precomputed grids to disk as GeoTIFF + QML sidecar."""
    from .gpr_las_slicer import _write_tif_singleband

    os.makedirs(output_dir, exist_ok=True)
    results = []
    x_min = meta["x_min"]
    y_min = meta["y_min"]
    y_max = meta["y_max"]
    res = meta["resolution"]

    for item in grids:
        z_lev = item["z_lev"]
        iz = item["index"]
        grid = item["grid"]
        z_label = f"{z_lev:.4f}".replace(".", "_").replace("-", "m")
        tif_name = f"slice_{iz:04d}_z{z_label}.tif"
        tif_path = os.path.join(output_dir, tif_name)

        _write_tif_singleband(grid, tif_path, x_min, y_min, y_max, res, epsg)
        _write_qml_singleband(tif_path, grid)

        results.append({
            "path": tif_path,
            "z_from": item["z_from"],
            "z_to": item["z_to"],
            "z_center": round(z_lev, 6),
            "index": iz,
            "name": os.path.splitext(tif_name)[0],
        })
    return results


# ---------------------------------------------------------------------------
# Legacy entrypoint: compute grids and write to disk
# ---------------------------------------------------------------------------


def slice_ogpr_to_tifs(
    profiles: list,
    output_dir: str,
    channel: int = -1,
    combine_method: str = "mean",
    resolution: float = 0.10,
    z_step: float = 0.05,
    z_min: float | None = None,
    z_max: float | None = None,
    radius: float | None = None,
    epsg: int | None = None,
    pipeline_params: dict | None = None,
) -> list[dict]:
    """Legacy entrypoint: compute grids and write to disk."""
    grids, meta = compute_ogpr_slice_grids(
        profiles=profiles,
        channel=channel,
        combine_method=combine_method,
        resolution=resolution,
        z_step=z_step,
        z_min=z_min,
        z_max=z_max,
        radius=radius,
        pipeline_params=pipeline_params,
    )
    if not grids:
        return []
    return write_grids_to_tifs(grids, meta, output_dir, epsg)
