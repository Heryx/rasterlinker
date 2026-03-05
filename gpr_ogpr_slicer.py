# -*- coding: utf-8 -*-
"""
OGPR profiles -> GeoTIFF timeslice pipeline.

Pipeline per slice:
  1. Normalizzazione inter-canale   (normalize_channels=True)
  2. Filtro ampiezza outlier        (amplitude_sigma)
  3. Interpolazione IDW isotropo/anisotropo
  4. Fill NoData gap inter-linea    (fill_nodata=True)
  5. Smoothing gaussiano            (smooth_sigma > 0)
"""

from __future__ import annotations

import json
import os

import numpy as np

SIDECAR_FILENAME = ".ogpr_slicer_params.json"


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
# Conversione profondita' -> indici campione
# ---------------------------------------------------------------------------

def _depth_to_sample_range(
    z_from: float,
    z_to: float,
    depth_max_m: float,
    n_samples: int,
) -> tuple[int, int]:
    if depth_max_m <= 0:
        return 0, n_samples
    s_lo = int(np.floor(max(z_from, 0.0) / depth_max_m * (n_samples - 1)))
    s_hi = int(np.ceil( min(z_to,   depth_max_m) / depth_max_m * (n_samples - 1))) + 1
    return max(0, s_lo), min(n_samples, s_hi)


# ---------------------------------------------------------------------------
# Normalizzazione inter-canale
# ---------------------------------------------------------------------------

def _normalize_channels(ampl_3d: np.ndarray) -> np.ndarray:
    out  = ampl_3d.copy()
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


# ---------------------------------------------------------------------------
# Rimozione outlier ampiezza
# ---------------------------------------------------------------------------

def _remove_amplitude_outliers(
    x: np.ndarray, y: np.ndarray, values: np.ndarray, n_sigma: float = 3.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if values.size == 0:
        return x, y, values
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return x, y, values
    mean = float(finite.mean())
    std  = float(finite.std())
    if std == 0.0:
        return x, y, values
    mask = np.abs(values - mean) <= n_sigma * std
    return x[mask], y[mask], values[mask]


# ---------------------------------------------------------------------------
# Stima direzione acquisizione via PCA
# ---------------------------------------------------------------------------

def _estimate_acquisition_direction(x: np.ndarray, y: np.ndarray) -> float:
    if x.size < 3:
        return 0.0
    pts  = np.column_stack([x - x.mean(), y - y.mean()])
    cov  = np.cov(pts.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    principal = eigvecs[:, np.argmax(eigvals)]
    return float(np.degrees(np.arctan2(principal[1], principal[0]))) % 180.0


# ---------------------------------------------------------------------------
# Stima raggio adattivo
# ---------------------------------------------------------------------------

def _estimate_interline_radius(
    x: np.ndarray, y: np.ndarray,
    percentile: float = 95.0, multiplier: float = 1.5,
) -> dict:
    from scipy.spatial import cKDTree
    if x.size < 4:
        return {"radius": 1.0, "anisotropy_ratio": 1.0, "acquisition_angle": 0.0}
    tree = cKDTree(np.column_stack([x, y]))
    dists, _ = tree.query(np.column_stack([x, y]), k=min(6, x.size))
    along_line = float(np.percentile(dists[:, 1], 10))
    cross_line = float(np.percentile(dists[:, 1], percentile))
    radius     = cross_line * multiplier
    aniso      = (cross_line / along_line) if along_line > 1e-9 else 1.0
    return {
        "radius":            max(radius, 1e-6),
        "anisotropy_ratio":  aniso,
        "acquisition_angle": _estimate_acquisition_direction(x, y),
    }


# ---------------------------------------------------------------------------
# Fill NoData / Smoothing
# ---------------------------------------------------------------------------

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
    nan_mask   = np.isnan(grid)
    filled     = np.where(nan_mask, 0.0, grid.astype(np.float64))
    weight     = np.where(nan_mask, 0.0, 1.0)
    smooth_num = gaussian_filter(filled, sigma=sigma)
    smooth_den = gaussian_filter(weight, sigma=sigma)
    result     = np.full_like(grid, np.nan, dtype=np.float32)
    valid      = smooth_den > 1e-9
    result[valid] = (smooth_num[valid] / smooth_den[valid]).astype(np.float32)
    return result


# ---------------------------------------------------------------------------
# IDW isotropo
# ---------------------------------------------------------------------------

def _bin_with_idw(
    x_pts, y_pts, i_pts, x_min, y_min, n_x, n_y,
    resolution, radius, power=2.0, min_points=3,
):
    from scipy.spatial import cKDTree
    gx  = x_min + np.arange(n_x) * resolution
    gy  = y_min + np.arange(n_y) * resolution
    gxx, gyy = np.meshgrid(gx, gy)
    gpts     = np.column_stack([gxx.ravel(), gyy.ravel()])
    tree     = cKDTree(np.column_stack([x_pts, y_pts]))
    results  = tree.query_ball_point(gpts, r=radius, workers=-1)
    i64      = i_pts.astype(np.float64)
    grid     = np.full(n_x * n_y, np.nan, dtype=np.float32)
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


# ---------------------------------------------------------------------------
# IDW anisotropo
# ---------------------------------------------------------------------------

def _bin_with_idw_anisotropic(
    x_pts, y_pts, i_pts, x_min, y_min, n_x, n_y,
    resolution, radius, power=2.0, min_points=3,
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

    gx  = x_min + np.arange(n_x) * resolution
    gy  = y_min + np.arange(n_y) * resolution
    gxx, gyy = np.meshgrid(gx, gy)
    gpts     = np.column_stack([gxx.ravel(), gyy.ravel()])
    tree     = cKDTree(np.column_stack([x_pts, y_pts]))
    results  = tree.query_ball_point(gpts, r=radius * max(1.0, anisotropy_ratio), workers=-1)
    i64      = i_pts.astype(np.float64)
    grid     = np.full(n_x * n_y, np.nan, dtype=np.float32)
    for k, idx_list in enumerate(results):
        if not idx_list:
            continue
        idx = np.asarray(idx_list, dtype=np.int64)
        cx, cy = gpts[k]
        d    = _ad(x_pts[idx], y_pts[idx], cx, cy)
        in_r = d <= radius
        if in_r.sum() < min_points:
            continue
        d_in = d[in_r]
        w    = 1.0 / (d_in ** power + 1e-9)
        ws   = w.sum()
        if ws > 0:
            grid[k] = float(np.dot(w, i64[idx[in_r]]) / ws)
    return grid.reshape(n_y, n_x)


# ---------------------------------------------------------------------------
# Helper condivisi tra slice_ogpr_to_tifs e compute_preview_slice
# ---------------------------------------------------------------------------

def _process_profiles(
    profiles: list,
    channel: int,
    combine_method: str,
    params: dict,
    normalize_channels: bool,
) -> list[tuple]:
    """
    Processa tutti i profili e ritorna lista di (prof, ch_ref, ampl_3d).

    True grid_by_grid a due passate
    --------------------------------
    Se params['bg_mode'] == 'grid_by_grid':
      1a passata: apply_pre_bg_pipeline su tutti i profili
                  -> traccia media globale (ground coupling comune)
      2a passata: apply_pipeline con bg_reference_trace = media globale

    Questo risolve il problema per cui grid_by_grid faceva fallback
    a line_by_line: ora la traccia di riferimento e' calcolata
    su TUTTI i radargram prima di sottrarre.
    """
    from .gpr_processing import apply_pipeline, apply_pre_bg_pipeline

    # ---------------------------------------------------------------
    # 1a passata: calcola traccia di riferimento per grid_by_grid
    # ---------------------------------------------------------------
    bg_reference = None
    if params.get("bg_removal", True) and params.get("bg_mode") == "grid_by_grid":
        _pre_traces = []
        for prof in profiles:
            n_ch    = prof.n_channels
            ch_list = list(range(n_ch)) if channel < 0 else [min(channel, n_ch - 1)]
            for ci in ch_list:
                ch  = prof.channel(ci)
                pre = apply_pre_bg_pipeline(ch.data.copy(), params, dt_ns=prof.dt_ns)
                _pre_traces.append(pre)   # (n_s, n_t)
        if _pre_traces:
            min_ns  = min(t.shape[0] for t in _pre_traces)
            stacked = np.concatenate(
                [t[:min_ns, :] for t in _pre_traces], axis=1
            )   # (min_ns, tot_tracce)
            bg_reference = stacked.mean(axis=1).astype(np.float64)
            print(
                f"[OGPR grid_by_grid] reference: {min_ns} campioni  "
                f"{stacked.shape[1]} tracce totali  "
                f"{len(profiles)} profili"
            )

    # ---------------------------------------------------------------
    # 2a passata (o unica passata per line_by_line)
    # ---------------------------------------------------------------
    processed = []
    for prof in profiles:
        n_ch   = prof.n_channels
        ch_ref = prof.channel(0)
        ch_list = list(range(n_ch)) if channel < 0 else [min(channel, n_ch - 1)]
        proc_channels = []
        for ci in ch_list:
            ch  = prof.channel(ci)
            raw = ch.data.copy()
            try:
                proc = apply_pipeline(
                    raw, params, dt_ns=prof.dt_ns,
                    bg_reference_trace=bg_reference,
                )
            except Exception as exc:
                print(f"[OGPR slicer] pipeline error ch{ci} in {prof.path}: {exc}")
                proc = np.abs(raw).astype(np.float32)
                mx   = proc.max()
                if mx > 1e-10:
                    proc /= mx
            proc_channels.append(proc)
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
    all_e = np.concatenate([ch.easting  for _, ch, _ in processed])
    all_n = np.concatenate([ch.northing for _, ch, _ in processed])
    x_min = float(all_e.min())
    x_max = float(all_e.max())
    y_min = float(all_n.min())
    y_max = float(all_n.max())
    n_x   = max(2, int(np.round((x_max - x_min) / resolution)) + 1)
    n_y   = max(2, int(np.round((y_max - y_min) / resolution)) + 1)
    if radius is None:
        radius = resolution * (2.0 ** 0.5)
    _aniso_info = None
    if auto_radius or (use_anisotropic_idw and
                       (anisotropy_ratio is None or anisotropy_angle is None)):
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
        ampl   = (per_ch.mean(axis=1) if (per_ch.shape[1] == 1 or combine_method == "mean")
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
# Anteprima: singola slice in RAM
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
    """
    Calcola una singola timeslice senza scrivere file su disco.
    Usata per l'anteprima nel dialog OGPR -> Timeslice.
    Ritorna dict con grid, metadata e fill_pct, oppure None.
    """
    from .gpr_processing import DEFAULT_PIPELINE
    if not profiles:
        return None
    params    = {**DEFAULT_PIPELINE, **(pipeline_params or {})}
    processed = _process_profiles(profiles, channel, combine_method, params, normalize_channels)
    if not processed:
        return None
    gp = _build_grid_params(
        processed, resolution, radius,
        auto_radius, use_anisotropic_idw,
        anisotropy_ratio, anisotropy_angle,
    )
    z_from = z_center - z_step / 2.0
    z_to   = z_center + z_step / 2.0
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
        "grid":          grid,
        "x_min":         gp["x_min"],
        "y_min":         gp["y_min"],
        "resolution":    resolution,
        "z_center":      float(z_center),
        "z_from":        round(z_from, 6),
        "z_to":          round(z_to,   6),
        "n_pts":         n_pts,
        "n_valid_cells": n_valid,
        "n_total_cells": n_total,
        "radius":        gp["radius"],
        "fill_pct":      100.0 * n_valid / max(n_total, 1),
    }


# ---------------------------------------------------------------------------
# Entry point principale
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
) -> list[dict]:
    from .gpr_processing import DEFAULT_PIPELINE
    from .gpr_las_slicer import _write_tif_singleband

    if not profiles:
        return []

    params    = {**DEFAULT_PIPELINE, **(pipeline_params or {})}
    os.makedirs(output_dir, exist_ok=True)

    processed = _process_profiles(
        profiles, channel, combine_method, params, normalize_channels
    )
    gp = _build_grid_params(
        processed, resolution, radius,
        auto_radius, use_anisotropic_idw,
        anisotropy_ratio, anisotropy_angle,
    )

    if z_min is None:
        z_min = 0.0
    if z_max is None:
        z_max = max(prof.depth_max_m for prof, _, _ in processed)

    z_levels = np.arange(
        float(z_min), float(z_max) + z_step * 0.5, float(z_step),
    )
    if epsg is None:
        epsg = processed[0][0].epsg if processed else None

    results = []
    for iz, z_lev in enumerate(z_levels):
        z_from = float(z_lev - z_step / 2.0)
        z_to   = float(z_lev + z_step / 2.0)
        grid, n_pts = _interpolate_z_level(
            processed, z_from, z_to, combine_method, gp, resolution,
            amplitude_sigma, use_anisotropic_idw,
            idw_power, min_points,
            fill_nodata, fill_nodata_max_distance, smooth_sigma,
        )
        if grid is None:
            continue
        z_label  = f"{z_lev:.4f}".replace(".", "_").replace("-", "m")
        tif_name = f"slice_{iz:04d}_z{z_label}.tif"
        tif_path = os.path.join(output_dir, tif_name)
        _write_tif_singleband(
            grid, tif_path,
            gp["x_min"], gp["y_min"], gp["y_max"], resolution, epsg,
        )
        n_valid  = int(np.count_nonzero(~np.isnan(grid)))
        fill_pct = 100.0 * n_valid / max(gp["n_x"] * gp["n_y"], 1)
        print(
            f"[OGPR slicer] z={z_lev:.3f}m  pts={n_pts}  "
            f"fill={fill_pct:.0f}%  tif={tif_name}"
        )
        results.append({
            "path":     tif_path,
            "z_from":   round(z_from, 6),
            "z_to":     round(z_to,   6),
            "z_center": round(float(z_lev), 6),
            "index":    iz,
            "name":     os.path.splitext(tif_name)[0],
        })
    return results
