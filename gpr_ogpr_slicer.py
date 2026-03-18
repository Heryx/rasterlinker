# -*- coding: utf-8 -*-
"""
OGPR profiles -> GeoTIFF timeslice pipeline.

Flow summary:
  - Default LAS-like extraction: abs(amplitude) per sample window
    without forcing dewow/bg/agc normalization.
  - Optional processing + Hilbert envelope mode when requested.
  - Aggregate points per depth window and interpolate via IDW
    (isotropic or anisotropic).
  - Support preview-in-RAM (compute grids without I/O) and
    separated write step to persist GeoTIFF + QML sidecar.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from time import perf_counter

import numpy as np

SIDECAR_FILENAME = ".ogpr_slicer_params.json"
_PREVIEW_CACHE: dict[str, object] = {
    "key": None,
    "processed": None,
    "gp": None,
    "z_max_depth": None,
    "topo_ref": None,
}


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


def _stable_json_key(obj) -> str:
    try:
        return json.dumps(obj, sort_keys=True, ensure_ascii=True, default=str)
    except Exception:
        return repr(obj)


def _profiles_cache_signature(profiles: list) -> tuple:
    sig = []
    for prof in list(profiles or []):
        pth = str(getattr(prof, "path", "") or "")
        n_samples = int(getattr(prof, "n_samples", 0) or 0)
        n_channels = int(getattr(prof, "n_channels", 0) or 0)
        n_slices = int(getattr(prof, "n_slices", 0) or 0)
        mtime_ns = 0
        fsize = 0
        if pth:
            try:
                st = Path(pth).stat()
                mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
                fsize = int(st.st_size)
            except Exception:
                pass
        sig.append((pth, mtime_ns, fsize, n_samples, n_channels, n_slices))
    return tuple(sig)


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
    if n_samples <= 0:
        return 0, 0
    if depth_max_m <= 0:
        return 0, n_samples

    z0 = float(np.clip(min(z_from, z_to), 0.0, depth_max_m))
    z1 = float(np.clip(max(z_from, z_to), 0.0, depth_max_m))
    scale = float(n_samples) / max(float(depth_max_m), 1e-12)
    s_lo = int(np.floor(z0 * scale))
    s_hi = int(np.ceil(z1 * scale))
    s_lo = int(np.clip(s_lo, 0, n_samples))
    s_hi = int(np.clip(s_hi, 0, n_samples))
    # Ensure at least one sample when a positive depth window collapses by rounding.
    if z1 > z0 and s_hi <= s_lo:
        s_hi = min(n_samples, s_lo + 1)
    return s_lo, s_hi


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


def _normalize_inter_profile_processed(processed: list[tuple]) -> list[tuple]:
    """Normalize amplitudes across profiles using robust global median scaling.

    Each profile cube (samples, traces, channels) is scaled so that its median
    absolute amplitude matches the global median across all profiles.
    """
    if not processed:
        return processed

    prof_medians = []
    for entry in processed:
        ampl_3d = entry[3]
        arr = np.asarray(ampl_3d, dtype=np.float64)
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            prof_medians.append(float("nan"))
            continue
        prof_medians.append(float(np.nanmedian(np.abs(finite))))

    valid = [m for m in prof_medians if np.isfinite(m) and m > 1e-12]
    if not valid:
        return processed
    global_med = float(np.nanmedian(np.asarray(valid, dtype=np.float64)))
    if not np.isfinite(global_med) or global_med <= 1e-12:
        return processed

    out = []
    for entry, prof_med in zip(processed, prof_medians):
        prof, x_ref, y_ref, ampl_3d = entry[:4]
        scale = 1.0
        if np.isfinite(prof_med) and prof_med > 1e-12:
            scale = global_med / prof_med
            if not np.isfinite(scale) or scale <= 0:
                scale = 1.0
        ampl_scaled = (np.asarray(ampl_3d, dtype=np.float64) * scale).astype(np.float32)
        if len(entry) >= 5:
            out.append((prof, x_ref, y_ref, ampl_scaled, entry[4]))
        else:
            out.append((prof, x_ref, y_ref, ampl_scaled))
    return out


def _iter_processed_entries(processed: list[tuple]):
    """Yield normalized processed tuples as (prof, x_ref, y_ref, ampl_3d, z_surf_ref)."""
    for entry in processed:
        if not isinstance(entry, (list, tuple)) or len(entry) < 4:
            continue
        prof, x_ref, y_ref, ampl_3d = entry[:4]
        n_traces = int(np.asarray(ampl_3d).shape[1]) if np.asarray(ampl_3d).ndim >= 2 else 0
        if len(entry) >= 5:
            z_surf_ref = _resample_vec_to_n(np.asarray(entry[4], dtype=np.float64), n_traces)
        else:
            z_surf_ref = np.full(n_traces, np.nan, dtype=np.float64)
        yield prof, np.asarray(x_ref, dtype=np.float64), np.asarray(y_ref, dtype=np.float64), np.asarray(ampl_3d), z_surf_ref


def _has_plausible_geo_xy(x: np.ndarray, y: np.ndarray) -> bool:
    if x.size == 0 or y.size == 0:
        return False
    finite = np.isfinite(x) & np.isfinite(y)
    if float(finite.mean()) < 0.95:
        return False
    xf = x[finite].astype(np.float64, copy=False)
    yf = y[finite].astype(np.float64, copy=False)
    if xf.size < 2:
        return False
    span = float(np.ptp(xf) + np.ptp(yf))
    if not np.isfinite(span) or span < 1e-6:
        return False
    return True


def _synthetic_profile_xy(ch, prof, profile_idx: int) -> tuple[np.ndarray, np.ndarray]:
    n = int(getattr(ch, "data", np.empty((0, 0))).shape[1] or 0)
    if n <= 0:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.float64)

    dist = np.asarray(getattr(ch, "distances", []), dtype=np.float64)
    if dist.size != n or not np.isfinite(dist).all():
        step = float(getattr(prof, "sampling_step_m", 0.1) or 0.1)
        if not np.isfinite(step) or step <= 0:
            step = 0.1
        dist = np.arange(n, dtype=np.float64) * step
    else:
        d0 = float(dist[0])
        if not np.isfinite(d0):
            d0 = 0.0
        dist = dist - d0
        if dist[-1] <= 0:
            step = float(np.median(np.diff(dist))) if dist.size > 1 else 0.1
            if not np.isfinite(step) or step <= 0:
                step = 0.1
            dist = np.arange(n, dtype=np.float64) * step

    dx = np.diff(dist)
    dx = dx[np.isfinite(dx) & (dx > 1e-6)]
    trace_step = float(np.median(dx)) if dx.size else 0.1
    line_spacing = max(0.5, trace_step * 10.0)

    x = dist.astype(np.float64, copy=False)
    y = np.full(n, float(profile_idx) * line_spacing, dtype=np.float64)
    return x, y


def _resample_vec_to_n(vec: np.ndarray, n: int) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float64)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    if arr.size == n:
        return arr.astype(np.float64, copy=False)
    if arr.size == 0:
        return np.zeros(n, dtype=np.float64)
    if arr.size == 1:
        return np.full(n, float(arr[0]), dtype=np.float64)
    src = np.linspace(0.0, 1.0, arr.size, dtype=np.float64)
    dst = np.linspace(0.0, 1.0, n, dtype=np.float64)
    return np.interp(dst, src, arr).astype(np.float64)


def _align_2d_arrays_for_stack(arrays: list[np.ndarray]) -> tuple[list[np.ndarray], tuple[int, int], bool]:
    """Normalize a list of 2D arrays so they can be stacked safely.

    Returns:
      - list of arrays cropped to common (min_samples, min_traces)
      - common shape tuple
      - boolean flag indicating whether any crop/resample was applied
    """
    mats = []
    for arr in arrays:
        a = np.asarray(arr, dtype=np.float32)
        if a.ndim != 2 or a.size <= 0:
            continue
        mats.append(a)
    if not mats:
        return [], (0, 0), False

    min_s = int(min(m.shape[0] for m in mats))
    min_t = int(min(m.shape[1] for m in mats))
    if min_s <= 0 or min_t <= 0:
        return [], (0, 0), False

    changed = False
    out = []
    for m in mats:
        if m.shape[0] != min_s or m.shape[1] != min_t:
            changed = True
            out.append(m[:min_s, :min_t].astype(np.float32, copy=False))
        else:
            out.append(m.astype(np.float32, copy=False))
    return out, (min_s, min_t), changed


def _normalize_flip_mode(mode: str | None) -> str:
    m = str(mode or "none").strip().lower()
    if m in {"all", "odd", "even"}:
        return m
    return "none"


def _should_flip_profile(profile_idx: int, mode: str | None) -> bool:
    """Return True if traces for the given profile index should be reversed.

    `odd`/`even` are evaluated in 1-based profile order:
      - odd  => profiles 1, 3, 5, ...
      - even => profiles 2, 4, 6, ...
    """
    m = _normalize_flip_mode(mode)
    if m == "all":
        return True
    if m == "odd":
        return ((int(profile_idx) + 1) % 2) == 1
    if m == "even":
        return ((int(profile_idx) + 1) % 2) == 0
    return False


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


def _amplitude_diagnostics(values: np.ndarray, hist_bins: int = 10) -> dict:
    arr = np.asarray(values, dtype=np.float64)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return {"count": 0}

    mn = float(np.nanmin(finite))
    mx = float(np.nanmax(finite))
    mean = float(np.nanmean(finite))
    std = float(np.nanstd(finite))
    p01, p05, p50, p95, p99 = [float(v) for v in np.nanpercentile(finite, [1, 5, 50, 95, 99])]

    try:
        bins = int(hist_bins)
    except Exception:
        bins = 10
    bins = max(4, min(64, bins))

    lo, hi = p01, p99
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = mn, mx
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = -1.0, 1.0

    counts, edges = np.histogram(finite, bins=bins, range=(lo, hi))
    return {
        "count": int(finite.size),
        "min": mn,
        "max": mx,
        "mean": mean,
        "std": std,
        "p01": p01,
        "p05": p05,
        "p50": p50,
        "p95": p95,
        "p99": p99,
        "hist_counts": [int(v) for v in counts.tolist()],
        "hist_edges": [float(v) for v in edges.tolist()],
    }


def _stack_traces(
    data: np.ndarray,
    stack_n: int = 1,
    kernel: str = "boxcar",
) -> np.ndarray:
    """Smooth along trace axis by stacking neighboring traces."""
    try:
        n = int(stack_n)
    except Exception:
        n = 1
    if n <= 1:
        return np.asarray(data, dtype=np.float32)
    n = max(1, min(255, n))
    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] <= 1:
        return np.asarray(data, dtype=np.float32)

    if str(kernel or "boxcar").strip().lower() in {"tri", "triangle", "triangular"}:
        # Triangular weights: 1..k..1
        half = max(1, n // 2)
        if n % 2 == 0:
            w = np.concatenate([np.arange(1, half + 1), np.arange(half, 0, -1)])
        else:
            w = np.concatenate([np.arange(1, half + 1), np.arange(half + 1, 0, -1)])
        w = w.astype(np.float64)
    else:
        w = np.ones(n, dtype=np.float64)
    w /= max(float(w.sum()), 1e-12)

    pad = len(w) // 2
    padded = np.pad(arr, ((0, 0), (pad, pad)), mode="edge")
    out = np.empty_like(arr)
    for r in range(arr.shape[0]):
        out[r, :] = np.convolve(padded[r, :], w, mode="valid")[: arr.shape[1]]
    return out.astype(np.float32)


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


def _depth_adaptive_radius(
    base_radius: float,
    z_center: float,
    z_max: float,
    depth_factor: float = 0.6,
) -> float:
    radius = float(base_radius)
    if not np.isfinite(radius) or radius <= 0.0:
        return 1e-6
    try:
        df = float(depth_factor)
    except Exception:
        df = 0.0
    if not np.isfinite(df) or df <= 0.0:
        return radius
    try:
        zmax = float(z_max)
    except Exception:
        zmax = 0.0
    if not np.isfinite(zmax) or zmax <= 0.0:
        return radius
    try:
        zc = float(z_center)
    except Exception:
        zc = 0.0
    if not np.isfinite(zc):
        zc = 0.0
    zc = min(max(zc, 0.0), zmax)
    scale = 1.0 + df * (zc / max(zmax, 1e-9))
    if not np.isfinite(scale) or scale <= 0.0:
        scale = 1.0
    return radius * scale


def _fill_nodata_grid(grid: np.ndarray, max_distance: int = 5) -> np.ndarray:
    from scipy.ndimage import distance_transform_edt
    if grid is None or grid.ndim != 2:
        return grid
    try:
        max_d = int(max_distance)
    except Exception:
        max_d = 0
    if max_d <= 0:
        return grid
    nan_mask = np.isnan(grid)
    if not nan_mask.any():
        return grid
    # Safety guard: EDT can allocate large temporary buffers on huge grids.
    MAX_SAFE_CELLS = 5_000_000
    if int(nan_mask.size) > MAX_SAFE_CELLS:
        return grid
    mask_u8 = nan_mask.astype(np.uint8, copy=False)
    try:
        dist, (row_idx, col_idx) = distance_transform_edt(
            mask_u8, return_distances=True, return_indices=True,
        )
    except MemoryError:
        return grid
    except Exception:
        return grid
    filled = grid.copy()
    fw = nan_mask & np.isfinite(dist) & (dist <= float(max_d))
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


def _blanking_mask_from_distance(
    e_pts: np.ndarray,
    n_pts: np.ndarray,
    x_min: float,
    y_min: float,
    n_x: int,
    n_y: int,
    resolution: float,
    blanking_distance: float,
) -> np.ndarray | None:
    """Return mask for cells farther than blanking_distance from real samples."""
    try:
        dmax = float(blanking_distance)
    except Exception:
        dmax = 0.0
    if (not np.isfinite(dmax)) or dmax <= 0.0:
        return None
    if len(e_pts) <= 0 or len(n_pts) <= 0:
        return None
    try:
        from scipy.spatial import cKDTree
    except Exception:
        return None
    try:
        gx = float(x_min) + np.arange(int(n_x), dtype=np.float64) * float(resolution)
        gy = float(y_min) + np.arange(int(n_y), dtype=np.float64) * float(resolution)
        gxx, gyy = np.meshgrid(gx, gy)
        qpts = np.column_stack([gxx.ravel(), gyy.ravel()])
        tree = cKDTree(np.column_stack([e_pts.astype(np.float64), n_pts.astype(np.float64)]))
        dist, _ = tree.query(qpts, k=1, workers=-1)
        return np.asarray(dist, dtype=np.float64).reshape(int(n_y), int(n_x)) > dmax
    except Exception:
        return None


def _estimate_idw_knn_k(
    x_pts: np.ndarray,
    y_pts: np.ndarray,
    radius: float,
    min_points: int,
    max_k: int = 512,
) -> int:
    n_pts = int(len(x_pts))
    if n_pts <= 0:
        return 1
    r = float(radius)
    if not np.isfinite(r) or r <= 0.0:
        r = 1e-6

    try:
        area = float(np.ptp(x_pts)) * float(np.ptp(y_pts))
    except Exception:
        area = 0.0
    if not np.isfinite(area) or area <= 1e-12:
        area = max(float(n_pts), 1.0)

    density = float(n_pts) / area
    expected = np.pi * (r ** 2) * density
    try:
        k = int(np.ceil(expected * 1.8)) + 8
    except Exception:
        k = 32
    k = max(int(min_points), k, 8)
    k = min(k, int(max_k), n_pts)
    return max(1, int(k))


def _idw_chunk_size_from_k(k: int, target_elements: int = 500_000) -> int:
    kk = max(1, int(k))
    cs = int(target_elements // kk)
    return max(1_000, cs)


def _ckdtree_query_knn(tree, qpts: np.ndarray, k: int, distance_upper_bound: float):
    try:
        return tree.query(
            qpts,
            k=int(k),
            distance_upper_bound=float(distance_upper_bound),
            workers=-1,
        )
    except TypeError:
        return tree.query(
            qpts,
            k=int(k),
            distance_upper_bound=float(distance_upper_bound),
        )


def _normalize_idw_mode(mode: str | None) -> str:
    txt = str(mode or "fast").strip().lower()
    if txt in {"quality", "accurate", "radius", "ball"}:
        return "quality"
    return "fast"


def _bin_with_idw_ball(
    x_pts, y_pts, i_pts, x_min, y_min, n_x, n_y, resolution, radius, power=2.0, min_points=1
):
    from scipy.spatial import cKDTree

    gx = x_min + np.arange(n_x) * resolution
    gy = y_min + np.arange(n_y) * resolution
    gxx, gyy = np.meshgrid(gx, gy)
    gpts = np.column_stack([gxx.ravel(), gyy.ravel()])
    tree = cKDTree(np.column_stack([x_pts, y_pts]))
    try:
        results = tree.query_ball_point(gpts, r=radius, workers=-1)
    except TypeError:
        results = tree.query_ball_point(gpts, r=radius)
    i64 = np.asarray(i_pts, dtype=np.float64)
    x_arr = np.asarray(x_pts, dtype=np.float64)
    y_arr = np.asarray(y_pts, dtype=np.float64)
    pwr = float(power) if np.isfinite(power) and float(power) > 0 else 2.0
    min_pts = max(1, int(min_points))

    grid = np.full(n_x * n_y, np.nan, dtype=np.float32)
    for k, idx_list in enumerate(results):
        if len(idx_list) < min_pts:
            continue
        idx = np.asarray(idx_list, dtype=np.int64)
        cx, cy = gpts[k]
        d = np.sqrt((x_arr[idx] - cx) ** 2 + (y_arr[idx] - cy) ** 2)
        w = 1.0 / (d ** pwr + 1e-9)
        ws = w.sum()
        if ws > 0:
            grid[k] = float(np.dot(w, i64[idx]) / ws)
    return grid.reshape(n_y, n_x)


def _bin_with_idw_anisotropic_ball(
    x_pts, y_pts, i_pts, x_min, y_min, n_x, n_y, resolution, radius, power=2.0, min_points=1,
    anisotropy_ratio=1.0, anisotropy_angle=0.0, max_points_per_cell=4096,
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
    try:
        results = tree.query_ball_point(gpts, r=radius * max(1.0, anisotropy_ratio), workers=-1)
    except TypeError:
        results = tree.query_ball_point(gpts, r=radius * max(1.0, anisotropy_ratio))

    i64 = np.asarray(i_pts, dtype=np.float64)
    x_arr = np.asarray(x_pts, dtype=np.float64)
    y_arr = np.asarray(y_pts, dtype=np.float64)
    pwr = float(power) if np.isfinite(power) and float(power) > 0 else 2.0
    min_pts = max(1, int(min_points))
    cap_pts = int(max_points_per_cell or 0)

    grid = np.full(n_x * n_y, np.nan, dtype=np.float32)
    for k, idx_list in enumerate(results):
        if not idx_list:
            continue
        idx = np.asarray(idx_list, dtype=np.int64)
        cx, cy = gpts[k]
        if cap_pts and idx.size > cap_pts:
            d2 = (x_arr[idx] - cx) ** 2 + (y_arr[idx] - cy) ** 2
            keep = np.argpartition(d2, cap_pts - 1)[:cap_pts]
            idx = idx[keep]
        d = _ad(x_arr[idx], y_arr[idx], cx, cy)
        in_r = d <= radius
        if int(np.count_nonzero(in_r)) < min_pts:
            continue
        d_in = d[in_r]
        w = 1.0 / (d_in ** pwr + 1e-9)
        ws = w.sum()
        if ws > 0:
            grid[k] = float(np.dot(w, i64[idx[in_r]]) / ws)
    return grid.reshape(n_y, n_x)


def _bin_with_idw(
    x_pts, y_pts, i_pts, x_min, y_min, n_x, n_y, resolution, radius, power=2.0, min_points=1
):
    from scipy.spatial import cKDTree
    gx = x_min + np.arange(n_x) * resolution
    gy = y_min + np.arange(n_y) * resolution
    gxx, gyy = np.meshgrid(gx, gy)
    gpts = np.column_stack([gxx.ravel(), gyy.ravel()])
    n_cells = int(gpts.shape[0])
    n_pts = int(np.asarray(x_pts).size)
    if n_cells <= 0 or n_pts <= 0:
        return np.full((n_y, n_x), np.nan, dtype=np.float32)
    MAX_CELLS = 10_000_000
    if n_cells > MAX_CELLS:
        print(
            f"[OGPR slicer][ERROR] IDW abortito: griglia {n_y}x{n_x}={n_cells:,} celle "
            f"supera il limite di sicurezza ({MAX_CELLS:,}). "
            "Aumenta la risoluzione o riduci il raggio IDW."
        )
        return np.full((n_y, n_x), np.nan, dtype=np.float32)

    r = float(radius)
    if not np.isfinite(r) or r <= 0.0:
        r = 1e-6
    pwr = float(power) if np.isfinite(power) and float(power) > 0.0 else 2.0
    min_pts = max(1, int(min_points))

    x_arr = np.asarray(x_pts, dtype=np.float64)
    y_arr = np.asarray(y_pts, dtype=np.float64)
    i64 = np.asarray(i_pts, dtype=np.float64)

    tree = cKDTree(np.column_stack([x_arr, y_arr]))
    k = _estimate_idw_knn_k(x_arr, y_arr, r, min_pts, max_k=32)
    chunk_size = _idw_chunk_size_from_k(k)

    grid = np.full(n_cells, np.nan, dtype=np.float32)
    for start in range(0, n_cells, chunk_size):
        end = min(n_cells, start + chunk_size)
        q = gpts[start:end]
        dists, idxs = _ckdtree_query_knn(tree, q, k=k, distance_upper_bound=r)
        dists = np.asarray(dists, dtype=np.float64)
        idxs = np.asarray(idxs, dtype=np.int64)
        if dists.ndim == 1:
            dists = dists[:, np.newaxis]
            idxs = idxs[:, np.newaxis]

        valid = np.isfinite(dists) & (idxs >= 0) & (idxs < n_pts)
        if not np.any(valid):
            continue

        idx_clip = np.clip(idxs, 0, max(0, n_pts - 1))
        vals = i64[idx_clip]
        d_safe = np.where(valid, dists, 1.0)
        w = np.where(valid, 1.0 / (np.power(d_safe, pwr) + 1e-9), 0.0)
        ws = np.sum(w, axis=1)
        cnt = np.sum(valid, axis=1)
        ok = (cnt >= min_pts) & (ws > 1e-12)
        if not np.any(ok):
            continue
        num = np.sum(w * vals, axis=1)
        out = np.full(q.shape[0], np.nan, dtype=np.float32)
        out[ok] = (num[ok] / ws[ok]).astype(np.float32)
        grid[start:end] = out

    return grid.reshape(n_y, n_x)


def _bin_with_idw_anisotropic(
    x_pts, y_pts, i_pts, x_min, y_min, n_x, n_y, resolution, radius, power=2.0, min_points=1,
    anisotropy_ratio=1.0, anisotropy_angle=0.0, max_points_per_cell=4096,
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
    n_cells = int(gpts.shape[0])
    n_pts = int(np.asarray(x_pts).size)
    if n_cells <= 0 or n_pts <= 0:
        return np.full((n_y, n_x), np.nan, dtype=np.float32)
    MAX_CELLS = 10_000_000
    if n_cells > MAX_CELLS:
        print(
            f"[OGPR slicer][ERROR] IDW anisotropo abortito: griglia {n_y}x{n_x}={n_cells:,} celle "
            f"supera il limite di sicurezza ({MAX_CELLS:,}). "
            "Aumenta la risoluzione o riduci il raggio IDW."
        )
        return np.full((n_y, n_x), np.nan, dtype=np.float32)

    r = float(radius)
    if not np.isfinite(r) or r <= 0.0:
        r = 1e-6
    r_search = r * max(1.0, float(anisotropy_ratio))
    pwr = float(power) if np.isfinite(power) and float(power) > 0.0 else 2.0
    min_pts = max(1, int(min_points))
    max_k = int(max_points_per_cell) if max_points_per_cell else 32
    max_k = max(min_pts, min(32, max(8, max_k)))

    x_arr = np.asarray(x_pts, dtype=np.float64)
    y_arr = np.asarray(y_pts, dtype=np.float64)
    i64 = np.asarray(i_pts, dtype=np.float64)

    tree = cKDTree(np.column_stack([x_arr, y_arr]))
    k = _estimate_idw_knn_k(x_arr, y_arr, r_search, min_pts, max_k=max_k)
    chunk_size = _idw_chunk_size_from_k(k)

    grid = np.full(n_cells, np.nan, dtype=np.float32)
    for start in range(0, n_cells, chunk_size):
        end = min(n_cells, start + chunk_size)
        q = gpts[start:end]
        dists, idxs = _ckdtree_query_knn(tree, q, k=k, distance_upper_bound=r_search)
        dists = np.asarray(dists, dtype=np.float64)
        idxs = np.asarray(idxs, dtype=np.int64)
        if dists.ndim == 1:
            dists = dists[:, np.newaxis]
            idxs = idxs[:, np.newaxis]

        valid0 = np.isfinite(dists) & (idxs >= 0) & (idxs < n_pts)
        if not np.any(valid0):
            continue

        idx_clip = np.clip(idxs, 0, max(0, n_pts - 1))
        px = x_arr[idx_clip]
        py = y_arr[idx_clip]
        cx = q[:, 0][:, np.newaxis]
        cy = q[:, 1][:, np.newaxis]

        d_aniso = _ad(px, py, cx, cy)
        valid = valid0 & np.isfinite(d_aniso) & (d_aniso <= r)
        if not np.any(valid):
            continue

        vals = i64[idx_clip]
        d_safe = np.where(valid, d_aniso, 1.0)
        w = np.where(valid, 1.0 / (np.power(d_safe, pwr) + 1e-9), 0.0)
        ws = np.sum(w, axis=1)
        cnt = np.sum(valid, axis=1)
        ok = (cnt >= min_pts) & (ws > 1e-12)
        if not np.any(ok):
            continue
        num = np.sum(w * vals, axis=1)
        out = np.full(q.shape[0], np.nan, dtype=np.float32)
        out[ok] = (num[ok] / ws[ok]).astype(np.float32)
        grid[start:end] = out

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
    extraction_mode: str = "las_like",
    use_hilbert: bool = True,
    use_processing: bool = False,
    balance_profiles: bool = True,
    pre_slice_bg_removal: bool = False,
    pre_slice_bg_mode: str = "line_by_line",
    pre_slice_bg_window: int = 0,
    pre_slice_bg_sample_start: int = 0,
    pre_slice_bg_sample_end: int = 0,
    stack_n: int = 1,
    stack_kernel: str = "boxcar",
    flip_traces_mode: str = "none",
    return_diagnostics: bool = False,
    parallel_profiles: bool = False,
    profile_workers: int = 0,
) -> list[tuple] | tuple[list[tuple], dict]:
    """Process profiles and return list of (prof, x_ref, y_ref, ampl_3d).

    Applies optional processing per channel and extracts amplitudes according to
    extraction_mode + use_hilbert:
      - signed: keep signed processed trace
      - envelope/hilbert: Hilbert envelope
      - las_like: Hilbert envelope when use_hilbert=True, else abs(amplitude)
    Optionally applies robust inter-profile balancing using global median.
    Optionally parallelizes per-profile preprocessing with a thread pool.
    """
    from .gpr_processing import apply_pipeline, apply_pre_bg_pipeline, background_removal

    mode = str(extraction_mode or "las_like").strip().lower()
    hilbert_on = bool(use_hilbert)
    use_proc = bool(use_processing)

    if not profiles:
        return []

    # Coordinate validity per profilo: se nessun profilo e' georiferito, usa
    # coordinate sintetiche profilo/traccia per mantenere una griglia coerente.
    valid_geo_flags = []
    for prof in profiles:
        n_ch = int(getattr(prof, "n_channels", 0) or 0)
        coord_idx = 0 if channel < 0 else min(channel, max(0, n_ch - 1))
        try:
            ch_geo = prof.channel(coord_idx)
            ok_geo = _has_plausible_geo_xy(
                np.asarray(ch_geo.easting, dtype=np.float64),
                np.asarray(ch_geo.northing, dtype=np.float64),
            )
        except Exception:
            ok_geo = False
        valid_geo_flags.append(ok_geo)
    use_synthetic_coords = not any(valid_geo_flags)
    proc_diag = {
        "profiles_total": int(len(profiles)),
        "profiles_geo_valid": int(sum(1 for v in valid_geo_flags if bool(v))),
        "profiles_geo_skipped": 0,
        "using_synthetic_coords": bool(use_synthetic_coords),
        "parallel_profiles": False,
        "profile_workers_used": 1,
    }
    if use_synthetic_coords:
        print(
            "[OGPR slicer][WARN] nessun profilo con geolocalizzazione plausibile; "
            "uso coordinate sintetiche locali."
        )

    bg_reference = None
    if use_proc and params.get("bg_removal", True) and params.get("bg_mode") == "grid_by_grid":
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

    def _compute_proc_channels(job: tuple[int, object, list[int]]):
        p_idx, prof_local, ch_list_local = job
        path_local = str(getattr(prof_local, "path", "") or "")
        msgs: list[str] = []
        proc_channels_local = []
        try:
            for ci in ch_list_local:
                ch = prof_local.channel(ci)
                raw = ch.data.astype(np.float32, copy=False)
                if use_proc:
                    try:
                        proc = apply_pipeline(
                            raw.copy(),
                            params,
                            dt_ns=prof_local.dt_ns,
                            bg_reference_trace=bg_reference,
                            normalize_output=False,
                        )
                    except Exception as exc:
                        msgs.append(f"[OGPR slicer] pipeline error ch{ci} in {path_local}: {exc}")
                        proc = raw
                else:
                    proc = raw

                try:
                    need_pre_bg = bool(pre_slice_bg_removal) and (
                        (not use_proc) or (not bool(params.get("bg_removal", False)))
                    )
                    if need_pre_bg:
                        proc = background_removal(
                            proc,
                            mode=str(pre_slice_bg_mode or "line_by_line"),
                            window=int(pre_slice_bg_window or 0),
                            sample_start=int(pre_slice_bg_sample_start or 0),
                            sample_end=int(pre_slice_bg_sample_end or 0),
                        )
                except Exception as exc:
                    msgs.append(f"[OGPR slicer] pre-slice bg_removal error ch{ci}: {exc}")

                try:
                    proc = _stack_traces(
                        proc,
                        stack_n=int(stack_n or 1),
                        kernel=str(stack_kernel or "boxcar"),
                    )
                except Exception as exc:
                    msgs.append(f"[OGPR slicer] trace stacking error ch{ci}: {exc}")

                if mode in {"signed", "signed_amp"}:
                    ampl = proc.astype(np.float32, copy=False)
                elif mode in {"envelope", "hilbert"} or (hilbert_on and mode not in {"signed", "signed_amp"}):
                    ampl = _envelope(proc)
                else:
                    # LAS-like: usa ampiezza assoluta direttamente dai campioni.
                    ampl = np.abs(proc).astype(np.float32, copy=False)
                if ampl.ndim != 2 or ampl.size <= 0:
                    msgs.append(f"[OGPR slicer] skip invalid channel matrix ch{ci} in {path_local}")
                    continue
                proc_channels_local.append(ampl)
        except Exception as exc:
            msgs.append(f"[OGPR slicer] profile processing error in {path_local}: {exc}")
            return p_idx, [], msgs
        return p_idx, proc_channels_local, msgs

    use_parallel = bool(parallel_profiles) and len(profiles) > 1
    workers_used = 1
    precomputed_channels: dict[int, list[np.ndarray]] = {}
    precomputed_msgs: dict[int, list[str]] = {}
    if use_parallel:
        try:
            requested_workers = int(profile_workers or 0)
        except Exception:
            requested_workers = 0
        auto_workers = max(1, min(len(profiles), max(1, int(os.cpu_count() or 2) - 1)))
        workers_used = requested_workers if requested_workers > 0 else auto_workers
        workers_used = max(1, min(workers_used, len(profiles)))
        use_parallel = workers_used > 1

    if use_parallel:
        jobs = []
        for p_idx, prof in enumerate(profiles):
            n_ch = int(getattr(prof, "n_channels", 0) or 0)
            ch_list = list(range(n_ch)) if channel < 0 else [min(channel, max(0, n_ch - 1))]
            jobs.append((p_idx, prof, ch_list))
        try:
            with ThreadPoolExecutor(max_workers=workers_used, thread_name_prefix="ogpr-prof") as ex:
                for p_idx, proc_channels_local, msgs in ex.map(_compute_proc_channels, jobs):
                    precomputed_channels[int(p_idx)] = list(proc_channels_local or [])
                    precomputed_msgs[int(p_idx)] = list(msgs or [])
            proc_diag["parallel_profiles"] = True
            proc_diag["profile_workers_used"] = int(workers_used)
        except Exception as exc:
            print(f"[OGPR slicer][WARN] parallel profile processing failed, fallback to sequential: {exc}")
            precomputed_channels.clear()
            precomputed_msgs.clear()
            use_parallel = False
            workers_used = 1

    if not use_parallel:
        proc_diag["parallel_profiles"] = False
        proc_diag["profile_workers_used"] = int(workers_used)

    processed = []
    for p_idx, prof in enumerate(profiles):
        n_ch = prof.n_channels
        if int(n_ch or 0) <= 0:
            print(f"[OGPR slicer] no channels for profile: {getattr(prof, 'path', '')}")
            continue
        coord_idx = 0 if channel < 0 else min(channel, n_ch - 1)
        ch_ref = prof.channel(coord_idx)
        ch_list = list(range(n_ch)) if channel < 0 else [min(channel, n_ch - 1)]
        if use_parallel:
            for msg in precomputed_msgs.get(int(p_idx), []):
                if msg:
                    print(msg)
            proc_channels = list(precomputed_channels.get(int(p_idx), []) or [])
        else:
            _, proc_channels, msgs = _compute_proc_channels((int(p_idx), prof, ch_list))
            for msg in msgs:
                if msg:
                    print(msg)

        if not proc_channels:
            print(f"[OGPR slicer] no valid channels for profile: {getattr(prof, 'path', '')}")
            continue

        proc_channels, common_shape, cropped = _align_2d_arrays_for_stack(proc_channels)
        if not proc_channels:
            print(f"[OGPR slicer] no stackable channels for profile: {getattr(prof, 'path', '')}")
            continue
        if cropped:
            print(
                f"[OGPR slicer][WARN] channel shapes differ in {getattr(prof, 'path', '')}; "
                f"cropped to common shape {common_shape[0]}x{common_shape[1]}"
            )

        ampl_3d = np.stack(proc_channels, axis=2)
        n_traces = int(ampl_3d.shape[1])

        profile_geo_valid = bool(valid_geo_flags[p_idx]) if p_idx < len(valid_geo_flags) else False
        if use_synthetic_coords:
            x_ref, y_ref = _synthetic_profile_xy(ch_ref, prof, p_idx)
        else:
            if not profile_geo_valid:
                proc_diag["profiles_geo_skipped"] += 1
                print(
                    f"[OGPR slicer][WARN] skip profile senza geolocalizzazione valida: "
                    f"{getattr(prof, 'path', '')}"
                )
                continue
            x_ref = np.asarray(ch_ref.easting, dtype=np.float64)
            y_ref = np.asarray(ch_ref.northing, dtype=np.float64)
            finite = np.isfinite(x_ref) & np.isfinite(y_ref)
            if finite.any() and not np.all(finite):
                idx = np.arange(x_ref.size, dtype=np.float64)
                idx_ok = np.where(finite)[0].astype(np.float64)
                x_ref = np.interp(idx, idx_ok, x_ref[finite]).astype(np.float64)
                y_ref = np.interp(idx, idx_ok, y_ref[finite]).astype(np.float64)
            elif not finite.any():
                proc_diag["profiles_geo_skipped"] += 1
                print(
                    f"[OGPR slicer][WARN] skip profile senza coordinate finite: "
                    f"{getattr(prof, 'path', '')}"
                )
                continue

        x_ref = _resample_vec_to_n(x_ref, n_traces)
        y_ref = _resample_vec_to_n(y_ref, n_traces)
        if (not use_synthetic_coords) and (not _has_plausible_geo_xy(x_ref, y_ref)):
            proc_diag["profiles_geo_skipped"] += 1
            print(
                f"[OGPR slicer][WARN] skip profile con geolocalizzazione non plausibile dopo resample: "
                f"{getattr(prof, 'path', '')}"
            )
            continue
        z_surf_ref = _resample_vec_to_n(
            np.asarray(getattr(ch_ref, "altitude", []), dtype=np.float64),
            n_traces,
        )
        if z_surf_ref.size != n_traces:
            z_surf_ref = np.full(n_traces, np.nan, dtype=np.float64)
        if not np.isfinite(z_surf_ref).any():
            z_surf_ref = np.full(n_traces, np.nan, dtype=np.float64)

        if _should_flip_profile(p_idx, flip_traces_mode):
            ampl_3d = ampl_3d[:, ::-1, :]
            x_ref = x_ref[::-1].copy()
            y_ref = y_ref[::-1].copy()
            z_surf_ref = z_surf_ref[::-1].copy()

        if normalize_channels and ampl_3d.shape[2] > 1:
            ampl_3d = _normalize_channels(ampl_3d)
        processed.append((prof, x_ref, y_ref, ampl_3d, z_surf_ref))
    if balance_profiles:
        processed = _normalize_inter_profile_processed(processed)
    if return_diagnostics:
        return processed, proc_diag
    return processed


def _build_grid_params(
    processed: list,
    resolution: float,
    radius: float | None,
    auto_radius: bool,
    use_anisotropic_idw: bool,
    anisotropy_ratio: float | None,
    anisotropy_angle: float | None,
    bounds_margin: float = 0.0,
) -> dict:
    radius_user_provided = radius is not None
    all_e = np.concatenate([x for _, x, _, _, _ in _iter_processed_entries(processed)])
    all_n = np.concatenate([y for _, _, y, _, _ in _iter_processed_entries(processed)])
    finite = np.isfinite(all_e) & np.isfinite(all_n)
    if finite.any():
        all_e = all_e[finite]
        all_n = all_n[finite]
    else:
        all_e = np.array([0.0, 1.0], dtype=np.float64)
        all_n = np.array([0.0, 1.0], dtype=np.float64)
    x_min_data = float(all_e.min())
    x_max_data = float(all_e.max())
    y_min_data = float(all_n.min())
    y_max_data = float(all_n.max())

    try:
        margin = float(bounds_margin)
    except Exception:
        margin = 0.0
    if not np.isfinite(margin) or margin < 0.0:
        margin = 0.0

    x_min = float(x_min_data - margin)
    x_max = float(x_max_data + margin)
    y_min = float(y_min_data - margin)
    y_max = float(y_max_data + margin)

    try:
        res = float(resolution)
    except Exception:
        res = 1.0
    if not np.isfinite(res) or res <= 0.0:
        res = 1.0
    n_x = max(2, int(np.ceil(max(0.0, (x_max - x_min)) / res)) + 1)
    n_y = max(2, int(np.ceil(max(0.0, (y_max - y_min)) / res)) + 1)
    _aniso_info = None
    need_spacing_estimate = bool(
        radius is None
        or auto_radius
        or (use_anisotropic_idw and (anisotropy_ratio is None or anisotropy_angle is None))
    )
    if need_spacing_estimate:
        try:
            _aniso_info = _estimate_interline_radius(all_e, all_n)
        except Exception:
            _aniso_info = None

    if radius is None:
        try:
            radius_est = float((_aniso_info or {}).get("radius", np.nan))
        except Exception:
            radius_est = float("nan")
        if np.isfinite(radius_est) and radius_est > 0.0:
            radius = radius_est
        else:
            radius = resolution * (2.0 ** 0.5)
    # Auto-radius must never override an explicit manual radius.
    if auto_radius and (not radius_user_provided) and _aniso_info is not None:
        radius = _aniso_info["radius"]
    eff_ratio = anisotropy_ratio
    eff_angle = anisotropy_angle
    if use_anisotropic_idw:
        if eff_ratio is None:
            eff_ratio = (_aniso_info or {}).get("anisotropy_ratio", 1.0)
        if eff_angle is None:
            eff_angle = (_aniso_info or {}).get("acquisition_angle", 0.0)
        try:
            eff_ratio = float(eff_ratio)
        except Exception:
            eff_ratio = 1.0
        if (not np.isfinite(eff_ratio)) or eff_ratio <= 0.0:
            eff_ratio = 1.0
        # Keep anisotropy bounded to avoid extreme grid dilation/instability.
        eff_ratio = min(float(eff_ratio), 10.0)
    return {
        "x_min": x_min, "y_min": y_min,
        "x_max": x_max, "y_max": y_max,
        "n_x": n_x, "n_y": n_y,
        "radius": radius,
        "eff_ratio": eff_ratio,
        "eff_angle": eff_angle,
        "bounds_margin_m": margin,
        "x_min_data": x_min_data,
        "x_max_data": x_max_data,
        "y_min_data": y_min_data,
        "y_max_data": y_max_data,
    }


def _compute_topo_reference(
    processed: list,
    mode: str = "median",
    custom_elevation: float | None = None,
) -> float | None:
    if custom_elevation is not None and np.isfinite(float(custom_elevation)):
        return float(custom_elevation)
    vals = []
    for _, _, _, _, z_surf_ref in _iter_processed_entries(processed):
        zz = np.asarray(z_surf_ref, dtype=np.float64)
        zz = zz[np.isfinite(zz)]
        if zz.size > 0:
            vals.append(zz)
    if not vals:
        return None
    all_z = np.concatenate(vals).astype(np.float64, copy=False)
    if all_z.size <= 0:
        return None
    md = str(mode or "median").strip().lower()
    if md == "mean":
        return float(np.nanmean(all_z))
    if md == "min":
        return float(np.nanmin(all_z))
    if md == "max":
        return float(np.nanmax(all_z))
    return float(np.nanmedian(all_z))


def _interpolate_z_level(
    processed: list,
    z_from: float, z_to: float,
    combine_method: str,
    gp: dict,
    resolution: float,
    amplitude_sigma: float | None,
    use_anisotropic_idw: bool,
    idw_mode: str,
    idw_power: float, min_points: int,
    fill_nodata: bool, fill_nodata_max_distance: float,
    blanking_distance: float,
    smooth_sigma: float,
    balance_profiles: bool = True,
    per_slice_balance: bool = False,
    amplitude_hist_bins: int = 10,
    topographic_correction: bool = False,
    topo_reference_elevation: float | None = None,
) -> tuple[np.ndarray | None, int, dict]:
    profile_rows = []
    topo_on = bool(topographic_correction) and topo_reference_elevation is not None and np.isfinite(float(topo_reference_elevation))
    topo_ref = float(topo_reference_elevation) if topo_on else float("nan")

    for prof, x_ref, y_ref, ampl_3d, z_surf_ref in _iter_processed_entries(processed):
        n_s = ampl_3d.shape[0]
        n_t = ampl_3d.shape[1]

        if topo_on and z_surf_ref.size == n_t and np.isfinite(z_surf_ref).any():
            # Shift depth window per trace: d_local = d_ref + (z_surf - z_ref)
            delta = np.asarray(z_surf_ref, dtype=np.float64) - topo_ref
            per_ch = np.full((n_t, ampl_3d.shape[2]), np.nan, dtype=np.float32)
            for it in range(n_t):
                d = float(delta[it]) if np.isfinite(delta[it]) else float("nan")
                if not np.isfinite(d):
                    continue
                s_lo, s_hi = _depth_to_sample_range(
                    float(z_from + d),
                    float(z_to + d),
                    prof.depth_max_m,
                    n_s,
                )
                if s_lo >= s_hi:
                    continue
                win = np.abs(ampl_3d[s_lo:s_hi, it, :]).astype(np.float64, copy=False)
                if win.size == 0:
                    continue
                per_ch[it, :] = np.sqrt(np.mean(win ** 2, axis=0)).astype(np.float32)
        else:
            s_lo, s_hi = _depth_to_sample_range(z_from, z_to, prof.depth_max_m, n_s)
            if s_lo >= s_hi:
                continue
            window = np.abs(ampl_3d[s_lo:s_hi, :, :]).astype(np.float32, copy=False)
            if window.size == 0:
                continue
            # RMS integra la finestra verticale in modo piu' stabile rispetto alla media.
            per_ch = np.sqrt(np.mean(window.astype(np.float64) ** 2, axis=0)).astype(np.float32)

        ampl = (per_ch.mean(axis=1) if (per_ch.shape[1] == 1 or combine_method == "mean")
                else per_ch.max(axis=1)).astype(np.float32)
        valid = np.isfinite(ampl) & np.isfinite(x_ref) & np.isfinite(y_ref)
        if not valid.any():
            continue
        x_use = x_ref[valid]
        y_use = y_ref[valid]
        ampl_use = ampl[valid]
        finite_ampl = ampl_use[np.isfinite(ampl_use)]
        prof_mean = float(np.nanmean(np.abs(finite_ampl))) if finite_ampl.size else float("nan")
        profile_rows.append((x_use, y_use, ampl_use, prof_mean))
    if not profile_rows:
        return None, 0, {"amp_pre": {"count": 0}, "amp_post": {"count": 0}}

    # Residual per-slice balancing. Disable when global inter-profile balancing
    # is already active to avoid double scaling.
    do_per_slice_balance = bool(per_slice_balance) and (not bool(balance_profiles))
    target_mean = float("nan")
    if do_per_slice_balance:
        valid_means = [m for _, _, _, m in profile_rows if np.isfinite(m) and m > 1e-12]
        if valid_means:
            target_mean = float(np.nanmedian(np.asarray(valid_means, dtype=np.float64)))

    pts_e, pts_n, pts_a = [], [], []
    for x_ref, y_ref, ampl, prof_mean in profile_rows:
        ampl_out = ampl
        if (
            do_per_slice_balance
            and np.isfinite(target_mean)
            and target_mean > 1e-12
            and np.isfinite(prof_mean)
            and prof_mean > 1e-12
        ):
            scale = target_mean / prof_mean
            if np.isfinite(scale) and scale > 0.0:
                ampl_out = (ampl.astype(np.float64) * scale).astype(np.float32)
        pts_e.append(x_ref)
        pts_n.append(y_ref)
        pts_a.append(ampl_out)

    e_all = np.concatenate(pts_e)
    n_all = np.concatenate(pts_n)
    a_all = np.concatenate(pts_a)
    e_raw = e_all
    n_raw = n_all
    a_raw = a_all
    amp_pre = _amplitude_diagnostics(a_all, hist_bins=amplitude_hist_bins)
    n_before = int(a_all.size)
    sigma_filter_relaxed = False
    if amplitude_sigma is not None:
        e_f, n_f, a_f = _remove_amplitude_outliers(e_all, n_all, a_all, n_sigma=amplitude_sigma)
        n_after_filter = int(a_f.size)
        min_keep = max(int(min_points) * 8, int(round(0.35 * max(1, n_before))))
        if n_after_filter >= min_keep:
            e_all, n_all, a_all = e_f, n_f, a_f
            sigma_filter_relaxed = False
        else:
            # Keep original points when sigma filter is too aggressive on sparse data.
            e_all, n_all, a_all = e_raw, n_raw, a_raw
            sigma_filter_relaxed = True
    amp_post = _amplitude_diagnostics(a_all, hist_bins=amplitude_hist_bins)
    n_pts = len(e_all)
    diag = {
        "amp_pre": amp_pre,
        "amp_post": amp_post,
        "n_before_filter": n_before,
        "n_after_filter": int(n_pts),
        "sigma_filter": (float(amplitude_sigma) if amplitude_sigma is not None else None),
        "sigma_filter_relaxed": bool(sigma_filter_relaxed),
    }
    if n_pts <= 0:
        return None, 0, diag

    mode_norm = _normalize_idw_mode(idw_mode)
    if use_anisotropic_idw:
        if mode_norm == "quality":
            grid = _bin_with_idw_anisotropic_ball(
                e_all, n_all, a_all,
                gp["x_min"], gp["y_min"], gp["n_x"], gp["n_y"],
                resolution, gp["radius"],
                power=idw_power, min_points=min_points,
                anisotropy_ratio=gp["eff_ratio"] or 1.0,
                anisotropy_angle=gp["eff_angle"] or 0.0,
            )
        else:
            grid = _bin_with_idw_anisotropic(
                e_all, n_all, a_all,
                gp["x_min"], gp["y_min"], gp["n_x"], gp["n_y"],
                resolution, gp["radius"],
                power=idw_power, min_points=min_points,
                anisotropy_ratio=gp["eff_ratio"] or 1.0,
                anisotropy_angle=gp["eff_angle"] or 0.0,
            )
    else:
        if mode_norm == "quality":
            grid = _bin_with_idw_ball(
                e_all, n_all, a_all,
                gp["x_min"], gp["y_min"], gp["n_x"], gp["n_y"],
                resolution, gp["radius"],
                power=idw_power, min_points=min_points,
            )
        else:
            grid = _bin_with_idw(
                e_all, n_all, a_all,
                gp["x_min"], gp["y_min"], gp["n_x"], gp["n_y"],
                resolution, gp["radius"],
                power=idw_power, min_points=min_points,
            )
    blank_mask = _blanking_mask_from_distance(
        e_all,
        n_all,
        gp["x_min"],
        gp["y_min"],
        gp["n_x"],
        gp["n_y"],
        resolution,
        blanking_distance,
    )
    if blank_mask is not None:
        try:
            diag["blanking_distance_m"] = float(blanking_distance)
        except Exception:
            diag["blanking_distance_m"] = 0.0
        diag["blanked_cells"] = int(np.count_nonzero(blank_mask))
    else:
        diag["blanking_distance_m"] = float(0.0)
        diag["blanked_cells"] = int(0)

    if fill_nodata:
        try:
            fill_m = float(fill_nodata_max_distance)
        except Exception:
            fill_m = 0.0
        # Auto fill distance tied to active IDW radius to reduce ring artifacts.
        if (not np.isfinite(fill_m)) or fill_m <= 0.0:
            try:
                radius_m = float(gp.get("radius", resolution) or resolution)
            except Exception:
                radius_m = float(resolution)
            if (not np.isfinite(radius_m)) or radius_m <= 0.0:
                radius_m = float(resolution)
            fill_m = max(float(resolution), radius_m * 1.5)
        if np.isfinite(fill_m) and fill_m > 0.0 and resolution > 0.0:
            fill_px = max(1, int(round(fill_m / float(resolution))))
            grid = _fill_nodata_grid(grid, max_distance=fill_px)
    if smooth_sigma > 0:
        grid = _smooth_grid_gaussian(grid, sigma=smooth_sigma)
    # Enforce blanking at the end so fill/smooth never reintroduce ghost values.
    if blank_mask is not None:
        grid = grid.astype(np.float32, copy=False)
        grid[blank_mask] = np.nan
    diag["idw_mode"] = mode_norm
    return grid, n_pts, diag


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
    normalize_channels: bool = False,
    extraction_mode: str = "las_like",
    use_hilbert: bool = True,
    use_processing: bool = False,
    amplitude_sigma: float | None = None,
    use_anisotropic_idw: bool = False,
    auto_radius: bool = True,
    anisotropy_ratio: float | None = None,
    anisotropy_angle: float | None = None,
    idw_mode: str = "quality",
    idw_power: float = 1.5,
    min_points: int = 1,
    fill_nodata: bool = True,
    fill_nodata_max_distance: float = 0.0,
    blanking_distance: float = 0.0,
    smooth_sigma: float = 0.8,
    depth_radius_factor: float = 0.6,
    balance_profiles: bool = True,
    per_slice_balance: bool = False,
    amplitude_hist_bins: int = 10,
    pre_slice_bg_removal: bool = False,
    pre_slice_bg_mode: str = "line_by_line",
    pre_slice_bg_window: int = 0,
    pre_slice_bg_sample_start: int = 0,
    pre_slice_bg_sample_end: int = 0,
    stack_n: int = 1,
    stack_kernel: str = "boxcar",
    flip_traces_mode: str = "none",
    topographic_correction: bool = False,
    topo_reference_mode: str = "median",
    topo_reference_elevation: float | None = None,
) -> dict | None:
    """Compute a single timeslice without writing to disk; used for preview dialog."""
    from .gpr_processing import DEFAULT_PIPELINE
    if not profiles:
        return None
    params = {**DEFAULT_PIPELINE, **(pipeline_params or {})} if use_processing else {}
    per_slice_balance_eff = bool(per_slice_balance) and (not bool(balance_profiles))
    preview_key = (
        _profiles_cache_signature(profiles),
        int(channel),
        str(combine_method or "mean"),
        float(resolution),
        (None if radius is None else float(radius)),
        bool(normalize_channels),
        str(extraction_mode or "las_like"),
        bool(use_hilbert),
        bool(use_processing),
        bool(balance_profiles),
        bool(pre_slice_bg_removal),
        str(pre_slice_bg_mode or "line_by_line"),
        int(pre_slice_bg_window or 0),
        int(pre_slice_bg_sample_start or 0),
        int(pre_slice_bg_sample_end or 0),
        int(stack_n or 1),
        str(stack_kernel or "boxcar"),
        str(flip_traces_mode or "none"),
        bool(use_anisotropic_idw),
        _normalize_idw_mode(idw_mode),
        float(idw_power),
        int(min_points),
        bool(fill_nodata),
        float(fill_nodata_max_distance or 0.0),
        float(blanking_distance or 0.0),
        float(smooth_sigma or 0.0),
        float(depth_radius_factor or 0.0),
        bool(auto_radius),
        (None if anisotropy_ratio is None else float(anisotropy_ratio)),
        (None if anisotropy_angle is None else float(anisotropy_angle)),
        bool(topographic_correction),
        str(topo_reference_mode or "median"),
        (None if topo_reference_elevation is None else float(topo_reference_elevation)),
        _stable_json_key(params),
    )

    cached = _PREVIEW_CACHE.get("key") == preview_key
    if cached:
        processed = _PREVIEW_CACHE.get("processed") or []
        gp = dict(_PREVIEW_CACHE.get("gp") or {})
        z_max_depth = float(_PREVIEW_CACHE.get("z_max_depth") or 0.0)
        topo_ref = _PREVIEW_CACHE.get("topo_ref")
        if not processed:
            return None
    else:
        processed = _process_profiles(
            profiles,
            channel,
            combine_method,
            params,
            normalize_channels,
            extraction_mode=extraction_mode,
            use_hilbert=use_hilbert,
            use_processing=use_processing,
            balance_profiles=balance_profiles,
            pre_slice_bg_removal=pre_slice_bg_removal,
            pre_slice_bg_mode=pre_slice_bg_mode,
            pre_slice_bg_window=pre_slice_bg_window,
            pre_slice_bg_sample_start=pre_slice_bg_sample_start,
            pre_slice_bg_sample_end=pre_slice_bg_sample_end,
            stack_n=stack_n,
            stack_kernel=stack_kernel,
            flip_traces_mode=flip_traces_mode,
        )
        if not processed:
            _PREVIEW_CACHE["key"] = preview_key
            _PREVIEW_CACHE["processed"] = []
            _PREVIEW_CACHE["gp"] = {}
            _PREVIEW_CACHE["z_max_depth"] = 0.0
            _PREVIEW_CACHE["topo_ref"] = None
            return None
        gp = _build_grid_params(
            processed, resolution, radius,
            auto_radius, use_anisotropic_idw,
            anisotropy_ratio, anisotropy_angle,
            bounds_margin=0.0,
        )
        z_max_depth = max(
            float(getattr(prof, "depth_max_m", 0.0) or 0.0)
            for prof, _, _, _, _ in _iter_processed_entries(processed)
        )
        topo_ref = None
        if topographic_correction:
            topo_ref = _compute_topo_reference(
                processed,
                mode=topo_reference_mode,
                custom_elevation=topo_reference_elevation,
            )
        _PREVIEW_CACHE["key"] = preview_key
        _PREVIEW_CACHE["processed"] = processed
        _PREVIEW_CACHE["gp"] = dict(gp)
        _PREVIEW_CACHE["z_max_depth"] = float(z_max_depth)
        _PREVIEW_CACHE["topo_ref"] = topo_ref

    z_from = z_center - z_step / 2.0
    z_to = z_center + z_step / 2.0
    ratio_for_margin = 1.0
    if use_anisotropic_idw:
        try:
            ratio_for_margin = float(gp.get("eff_ratio", 1.0) or 1.0)
        except Exception:
            ratio_for_margin = 1.0
        if not np.isfinite(ratio_for_margin) or ratio_for_margin <= 0.0:
            ratio_for_margin = 1.0
        ratio_for_margin = max(1.0, ratio_for_margin)
    radius_margin = _depth_adaptive_radius(
        float(gp.get("radius", radius if radius is not None else resolution)),
        float(z_center),
        float(z_max_depth),
        depth_radius_factor,
    )
    bounds_margin = float(radius_margin) * float(ratio_for_margin)
    try:
        max_margin = float(gp.get("radius", radius if radius is not None else resolution) or resolution) * 2.0
    except Exception:
        max_margin = 0.0
    if np.isfinite(max_margin) and max_margin > 0.0:
        bounds_margin = min(float(bounds_margin), float(max_margin))
    if np.isfinite(bounds_margin) and bounds_margin > 0.0:
        gp = _build_grid_params(
            processed, resolution, radius,
            auto_radius, use_anisotropic_idw,
            anisotropy_ratio, anisotropy_angle,
            bounds_margin=float(bounds_margin),
        )
    if not topographic_correction:
        topo_ref = None
    radius_z = _depth_adaptive_radius(gp["radius"], float(z_center), float(z_max_depth), depth_radius_factor)
    gp_slice = dict(gp)
    gp_slice["radius"] = radius_z
    grid, n_pts, amp_diag = _interpolate_z_level(
        processed, z_from, z_to, combine_method, gp_slice, resolution,
        amplitude_sigma, use_anisotropic_idw,
        idw_mode,
        idw_power, min_points,
        fill_nodata, fill_nodata_max_distance,
        blanking_distance=blanking_distance,
        smooth_sigma=smooth_sigma,
        balance_profiles=balance_profiles,
        per_slice_balance=per_slice_balance_eff,
        amplitude_hist_bins=amplitude_hist_bins,
        topographic_correction=bool(topographic_correction),
        topo_reference_elevation=topo_ref,
    )
    if grid is None or n_pts == 0:
        return None
    n_valid = int(np.count_nonzero(~np.isnan(grid)))
    n_total = gp_slice["n_x"] * gp_slice["n_y"]
    ratio_for_radius = 1.0
    if use_anisotropic_idw:
        try:
            ratio_for_radius = float(gp_slice.get("eff_ratio", 1.0) or 1.0)
        except Exception:
            ratio_for_radius = 1.0
        if not np.isfinite(ratio_for_radius) or ratio_for_radius <= 0:
            ratio_for_radius = 1.0
    effective_radius = float(radius_z) * (max(1.0, ratio_for_radius) if use_anisotropic_idw else 1.0)
    return {
        "grid": grid,
        "x_min": gp_slice["x_min"],
        "y_min": gp_slice["y_min"],
        "resolution": resolution,
        "z_center": float(z_center),
        "z_from": round(z_from, 6),
        "z_to": round(z_to, 6),
        "n_pts": n_pts,
        "n_valid_cells": n_valid,
        "n_total_cells": n_total,
        "radius": float(radius_z),
        "effective_radius": float(effective_radius),
        "depth_radius_factor": float(depth_radius_factor),
        "fill_pct": 100.0 * n_valid / max(n_total, 1),
        "amplitude_diag": amp_diag,
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
    normalize_channels: bool = False,
    extraction_mode: str = "las_like",
    use_hilbert: bool = True,
    use_processing: bool = False,
    amplitude_sigma: float | None = None,
    use_anisotropic_idw: bool = False,
    auto_radius: bool = True,
    anisotropy_ratio: float | None = None,
    anisotropy_angle: float | None = None,
    idw_mode: str = "quality",
    idw_power: float = 1.5,
    min_points: int = 1,
    fill_nodata: bool = True,
    fill_nodata_max_distance: float = 0.0,
    overlap_fraction: float = 0.5,
    blanking_distance: float = 0.0,
    smooth_sigma: float = 0.8,
    emit_diagnostics: bool = True,
    depth_radius_factor: float = 0.6,
    balance_profiles: bool = True,
    per_slice_balance: bool = False,
    amplitude_hist_bins: int = 10,
    pre_slice_bg_removal: bool = False,
    pre_slice_bg_mode: str = "line_by_line",
    pre_slice_bg_window: int = 0,
    pre_slice_bg_sample_start: int = 0,
    pre_slice_bg_sample_end: int = 0,
    stack_n: int = 1,
    stack_kernel: str = "boxcar",
    flip_traces_mode: str = "none",
    topographic_correction: bool = False,
    topo_reference_mode: str = "median",
    topo_reference_elevation: float | None = None,
    parallel_profiles: bool = False,
    profile_workers: int = 0,
) -> tuple[list[dict], dict]:
    """Compute IDW grids for each slice and return (grids, meta)."""
    from .gpr_processing import DEFAULT_PIPELINE

    t_total_start = perf_counter()
    t_preprocess_s = 0.0
    t_grid_setup_s = 0.0
    t_interp_s = 0.0
    t_slice_loop_s = 0.0

    if not profiles:
        return [], {}

    params = {**DEFAULT_PIPELINE, **(pipeline_params or {})} if use_processing else {}
    idw_mode_norm = _normalize_idw_mode(idw_mode)

    t0 = perf_counter()
    processed_result = _process_profiles(
        profiles,
        channel,
        combine_method,
        params,
        normalize_channels,
        extraction_mode=extraction_mode,
        use_hilbert=use_hilbert,
        use_processing=use_processing,
        balance_profiles=balance_profiles,
        pre_slice_bg_removal=pre_slice_bg_removal,
        pre_slice_bg_mode=pre_slice_bg_mode,
        pre_slice_bg_window=pre_slice_bg_window,
        pre_slice_bg_sample_start=pre_slice_bg_sample_start,
        pre_slice_bg_sample_end=pre_slice_bg_sample_end,
        stack_n=stack_n,
        stack_kernel=stack_kernel,
        flip_traces_mode=flip_traces_mode,
        return_diagnostics=True,
        parallel_profiles=parallel_profiles,
        profile_workers=profile_workers,
    )
    t_preprocess_s = max(0.0, perf_counter() - t0)
    if isinstance(processed_result, tuple):
        processed, proc_diag = processed_result
    else:
        processed = processed_result
        proc_diag = {}
    if not processed:
        t_total_s = max(0.0, perf_counter() - t_total_start)
        return [], {
            "profiles_total": int(proc_diag.get("profiles_total", len(profiles))),
            "profiles_geo_valid": int(proc_diag.get("profiles_geo_valid", 0)),
            "profiles_geo_skipped": int(proc_diag.get("profiles_geo_skipped", 0)),
            "using_synthetic_coords": bool(proc_diag.get("using_synthetic_coords", False)),
            "timing_s": {
                "total": float(round(t_total_s, 6)),
                "preprocess": float(round(t_preprocess_s, 6)),
                "grid_setup": 0.0,
                "slice_loop": 0.0,
                "interpolation": 0.0,
                "interpolation_avg_per_slice": 0.0,
            },
        }
    per_slice_balance_eff = bool(per_slice_balance) and (not bool(balance_profiles))
    if bool(per_slice_balance) and bool(balance_profiles) and emit_diagnostics:
        print(
            "[OGPR slicer][INFO] per-slice balancing disattivato: "
            "global balance_profiles e' gia' attivo."
        )

    t0 = perf_counter()
    gp0 = _build_grid_params(
        processed,
        resolution,
        radius,
        auto_radius,
        use_anisotropic_idw,
        anisotropy_ratio,
        anisotropy_angle,
        bounds_margin=0.0,
    )
    radius = float(gp0["radius"])
    eff_ratio = gp0.get("eff_ratio")
    eff_angle = gp0.get("eff_angle")

    if z_min is None:
        z_min = 0.0
    if z_max is None:
        z_max = max(float(getattr(prof, "depth_max_m", 0.0) or 0.0) for prof, _, _, _, _ in _iter_processed_entries(processed))

    ratio_for_margin = 1.0
    if use_anisotropic_idw:
        try:
            ratio_for_margin = float(eff_ratio if eff_ratio is not None else 1.0)
        except Exception:
            ratio_for_margin = 1.0
        if not np.isfinite(ratio_for_margin) or ratio_for_margin <= 0.0:
            ratio_for_margin = 1.0
        ratio_for_margin = max(1.0, ratio_for_margin)
    radius_margin_max = _depth_adaptive_radius(
        float(radius),
        float(z_max),
        float(z_max),
        depth_radius_factor,
    )
    bounds_margin = float(radius_margin_max) * (
        ratio_for_margin if use_anisotropic_idw else 1.0
    )
    # Keep margins bounded so sparse surveys do not explode grid extents.
    max_margin = float(radius) * 2.0
    if np.isfinite(max_margin) and max_margin > 0.0:
        bounds_margin = min(float(bounds_margin), float(max_margin))
    if np.isfinite(bounds_margin) and bounds_margin > 0.0:
        gp = _build_grid_params(
            processed,
            resolution,
            radius,
            auto_radius,
            use_anisotropic_idw,
            anisotropy_ratio,
            anisotropy_angle,
            bounds_margin=float(bounds_margin),
        )
    else:
        gp = gp0

    x_min = float(gp["x_min"])
    x_max = float(gp["x_max"])
    y_min = float(gp["y_min"])
    y_max = float(gp["y_max"])
    radius_eff = float(gp.get("radius", radius))
    n_x = int(gp["n_x"])
    n_y = int(gp["n_y"])
    n_cells = int(n_x) * int(n_y)
    MAX_CELLS_WARN = 5_000_000
    MAX_CELLS_ABORT = 50_000_000
    if emit_diagnostics:
        print(
            f"[OGPR slicer][INFO] griglia: {n_x}x{n_y} = {n_cells:,} celle "
            f"(risoluzione={float(resolution):.4f}m, raggio={radius_eff:.4f}m)"
        )
    if n_cells > MAX_CELLS_WARN:
        print(
            f"[OGPR slicer][WARN] griglia molto grande ({n_cells:,} celle). "
            "Aumenta la risoluzione o riduci il raggio IDW."
        )
    if n_cells > MAX_CELLS_ABORT:
        raise MemoryError(
            f"Griglia troppo grande ({n_cells:,} celle, shape {n_y}x{n_x}). "
            f"Aumenta la risoluzione da {float(resolution):.4f}m "
            f"a {float(resolution) * 3.0:.2f}m o superiore."
        )
    radius = radius_eff
    eff_ratio = gp.get("eff_ratio")
    eff_angle = gp.get("eff_angle")

    topo_ref = None
    if topographic_correction:
        topo_ref = _compute_topo_reference(
            processed,
            mode=topo_reference_mode,
            custom_elevation=topo_reference_elevation,
        )
    t_grid_setup_s = max(0.0, perf_counter() - t0)

    try:
        overlap_f = float(overlap_fraction)
    except Exception:
        overlap_f = 0.5
    if not np.isfinite(overlap_f):
        overlap_f = 0.5
    overlap_f = float(np.clip(overlap_f, 0.0, 0.9))
    slice_step = float(z_step) * float(1.0 - overlap_f)
    if (not np.isfinite(slice_step)) or slice_step <= 0.0:
        slice_step = float(z_step)
    z_levels = np.arange(float(z_min), float(z_max) + slice_step * 0.5, slice_step)

    grids = []
    ratio_for_radius = 1.0
    if use_anisotropic_idw:
        try:
            ratio_for_radius = float(eff_ratio if eff_ratio is not None else 1.0)
        except Exception:
            ratio_for_radius = 1.0
        if not np.isfinite(ratio_for_radius) or ratio_for_radius <= 0:
            ratio_for_radius = 1.0
    base_radius = float(radius)
    effective_radius_base = base_radius * (max(1.0, ratio_for_radius) if use_anisotropic_idw else 1.0)
    meta = dict(
        x_min=x_min,
        y_min=y_min,
        y_max=y_max,
        x_max=x_max,
        n_x=n_x,
        n_y=n_y,
        resolution=resolution,
        z_min=float(z_min),
        z_max=float(z_max),
        z_step=float(z_step),
        channel=int(channel),
        combine_method=str(combine_method or "mean"),
        extraction_mode=str(extraction_mode or "las_like"),
        use_hilbert=bool(use_hilbert),
        use_processing=bool(use_processing),
        pipeline_params=dict(params or {}),
        radius=float(base_radius),
        effective_radius=float(effective_radius_base),
        depth_radius_factor=float(depth_radius_factor),
        overlap_fraction=float(overlap_f),
        slice_step=float(slice_step),
        idw_power=float(idw_power),
        use_anisotropic_idw=bool(use_anisotropic_idw),
        idw_mode=idw_mode_norm,
        min_points=int(min_points),
        balance_profiles=bool(balance_profiles),
        per_slice_balance=bool(per_slice_balance_eff),
        fill_nodata_max_distance_m=float(fill_nodata_max_distance),
        blanking_distance_m=float(blanking_distance),
        pre_slice_bg_removal=bool(pre_slice_bg_removal),
        pre_slice_bg_mode=str(pre_slice_bg_mode or "line_by_line"),
        pre_slice_bg_window=int(pre_slice_bg_window or 0),
        stack_n=int(stack_n or 1),
        stack_kernel=str(stack_kernel or "boxcar"),
        flip_traces_mode=_normalize_flip_mode(flip_traces_mode),
        topographic_correction=bool(topographic_correction),
        topo_reference_mode=str(topo_reference_mode or "median"),
        topo_reference_elevation=(float(topo_ref) if topo_ref is not None and np.isfinite(topo_ref) else None),
        profiles_total=int(proc_diag.get("profiles_total", len(profiles))),
        profiles_geo_valid=int(proc_diag.get("profiles_geo_valid", 0)),
        profiles_geo_skipped=int(proc_diag.get("profiles_geo_skipped", 0)),
        using_synthetic_coords=bool(proc_diag.get("using_synthetic_coords", False)),
        parallel_profiles=bool(proc_diag.get("parallel_profiles", False)),
        profile_workers_used=int(proc_diag.get("profile_workers_used", 1)),
        bounds_margin_m=float(gp.get("bounds_margin_m", 0.0) or 0.0),
    )

    t_loop_start = perf_counter()
    for iz, z_lev in enumerate(z_levels):
        z_from = float(z_lev - z_step / 2.0)
        z_to = float(z_lev + z_step / 2.0)
        radius_z = _depth_adaptive_radius(base_radius, float(z_lev), float(z_max), depth_radius_factor)
        effective_radius = radius_z * (max(1.0, ratio_for_radius) if use_anisotropic_idw else 1.0)
        t_i0 = perf_counter()
        grid, n_pts, amp_diag = _interpolate_z_level(
            processed, z_from, z_to, combine_method,
            {
                "x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max,
                "n_x": n_x, "n_y": n_y, "radius": radius_z,
                "eff_ratio": eff_ratio, "eff_angle": eff_angle,
            },
            resolution, amplitude_sigma, use_anisotropic_idw,
            idw_mode=idw_mode_norm,
            idw_power=idw_power, min_points=min_points,
            fill_nodata=fill_nodata,
            fill_nodata_max_distance=fill_nodata_max_distance,
            blanking_distance=blanking_distance,
            smooth_sigma=smooth_sigma,
            balance_profiles=balance_profiles,
            per_slice_balance=per_slice_balance_eff,
            amplitude_hist_bins=amplitude_hist_bins,
            topographic_correction=bool(topographic_correction),
            topo_reference_elevation=topo_ref,
        )
        t_interp_s += max(0.0, perf_counter() - t_i0)
        if grid is None:
            if emit_diagnostics:
                print(
                    f"[OGPR slicer] slice {iz:04d} z={z_lev:.4f}m skipped: "
                    f"no points in window [{z_from:.4f}, {z_to:.4f}]"
                )
            continue
        n_valid = int(np.count_nonzero(~np.isnan(grid)))
        n_total = int(grid.size)
        fill_pct = 100.0 * n_valid / max(n_total, 1)
        if emit_diagnostics:
            low_fill = fill_pct < 60.0
            level = "WARN" if low_fill else "INFO"
            print(
                f"[OGPR slicer][{level}] slice {iz:04d} z={z_lev:.4f}m "
                f"fill_pct={fill_pct:.1f}% valid={n_valid}/{n_total} "
                f"n_pts={n_pts} radius={float(radius_z):.4f}m "
                f"effective_radius={effective_radius:.4f}m"
                + (" -> radius likely too small" if low_fill else "")
            )
            amp_post = (amp_diag or {}).get("amp_post", {}) if isinstance(amp_diag, dict) else {}
            if int(amp_post.get("count", 0) or 0) > 0:
                print(
                    f"[OGPR slicer][INFO] slice {iz:04d} amp "
                    f"p05={float(amp_post.get('p05', 0.0)):.4g} "
                    f"p95={float(amp_post.get('p95', 0.0)):.4g} "
                    f"mean={float(amp_post.get('mean', 0.0)):.4g} "
                    f"std={float(amp_post.get('std', 0.0)):.4g} "
                    f"hist={amp_post.get('hist_counts', [])}"
                )
        grids.append({
            "z_lev": float(z_lev),
            "z_from": round(z_from, 6),
            "z_to": round(z_to, 6),
            "index": iz,
            "grid": grid,
            "n_pts": int(n_pts),
            "n_valid_cells": n_valid,
            "n_total_cells": n_total,
            "fill_pct": float(fill_pct),
            "radius": float(radius_z),
            "effective_radius": float(effective_radius),
            "amplitude_diag": amp_diag,
        })

    t_slice_loop_s = max(0.0, perf_counter() - t_loop_start)
    t_total_s = max(0.0, perf_counter() - t_total_start)
    n_slice_total = int(len(z_levels))
    n_slice_done = int(len(grids))
    interp_avg = float(t_interp_s / max(1, n_slice_done))

    if grids:
        fill_vals = np.asarray([g.get("fill_pct", 0.0) for g in grids], dtype=np.float64)
        low_fill_count = int(np.count_nonzero(fill_vals < 60.0))
        meta["n_z"] = int(len(grids))
        try:
            z_vals = np.asarray([float(g.get("z_lev")) for g in grids], dtype=np.float64)
            if z_vals.size > 0 and np.isfinite(z_vals).any():
                z_finite = z_vals[np.isfinite(z_vals)]
                meta["z_first"] = float(z_finite.min())
                meta["z_last"] = float(z_finite.max())
                meta["z_levels"] = [float(v) for v in z_finite.tolist()]
        except Exception:
            pass
        meta["fill_pct_mean"] = float(np.nanmean(fill_vals))
        meta["fill_pct_min"] = float(np.nanmin(fill_vals))
        meta["fill_pct_low_count"] = low_fill_count
        meta["fill_pct_low_threshold"] = 60.0
        if emit_diagnostics and low_fill_count > 0:
            print(
                f"[OGPR slicer][WARN] low fill slices: {low_fill_count}/{len(grids)} "
                f"(threshold < 60%)"
            )

    meta["timing_s"] = {
        "total": float(round(t_total_s, 6)),
        "preprocess": float(round(t_preprocess_s, 6)),
        "grid_setup": float(round(t_grid_setup_s, 6)),
        "slice_loop": float(round(t_slice_loop_s, 6)),
        "interpolation": float(round(t_interp_s, 6)),
        "interpolation_avg_per_slice": float(round(interp_avg, 6)),
    }
    meta["timing_counts"] = {
        "slices_requested": int(n_slice_total),
        "slices_computed": int(n_slice_done),
    }
    if emit_diagnostics:
        print(
            "[OGPR slicer][TIMING] "
            f"total={t_total_s:.3f}s preprocess={t_preprocess_s:.3f}s "
            f"grid_setup={t_grid_setup_s:.3f}s slice_loop={t_slice_loop_s:.3f}s "
            f"idw={t_interp_s:.3f}s idw_avg={interp_avg:.3f}s/slice "
            f"slices={n_slice_done}/{n_slice_total}"
        )

    return grids, meta


# ---------------------------------------------------------------------------
# Write grids to GeoTIFF + sidecar
# ---------------------------------------------------------------------------


def _validate_grid_georef_meta(meta: dict, grid: np.ndarray | None = None) -> None:
    """Validate georeferencing metadata before writing GeoTIFF outputs."""
    if not isinstance(meta, dict):
        raise ValueError("Invalid georef metadata: expected dict.")

    required = ("x_min", "y_min", "y_max", "resolution")
    missing = [k for k in required if k not in meta]
    if missing:
        raise ValueError(f"Invalid georef metadata: missing keys {missing}.")

    try:
        x_min = float(meta.get("x_min"))
        y_min = float(meta.get("y_min"))
        y_max = float(meta.get("y_max"))
        res = float(meta.get("resolution"))
    except Exception as exc:
        raise ValueError(f"Invalid georef metadata numeric fields: {exc}") from exc

    if not np.isfinite(x_min):
        raise ValueError("Invalid georef metadata: x_min is not finite.")
    if not np.isfinite(y_min) or not np.isfinite(y_max):
        raise ValueError("Invalid georef metadata: y_min/y_max must be finite.")
    if not np.isfinite(res) or res <= 0.0:
        raise ValueError("Invalid georef metadata: resolution must be > 0.")
    if y_max <= y_min:
        raise ValueError("Invalid georef metadata: y_max must be greater than y_min.")

    n_x_meta = meta.get("n_x")
    n_y_meta = meta.get("n_y")
    if n_x_meta is not None:
        try:
            n_x_meta = int(n_x_meta)
        except Exception as exc:
            raise ValueError(f"Invalid georef metadata: n_x is not an integer ({exc}).") from exc
        if n_x_meta <= 0:
            raise ValueError("Invalid georef metadata: n_x must be > 0.")
    if n_y_meta is not None:
        try:
            n_y_meta = int(n_y_meta)
        except Exception as exc:
            raise ValueError(f"Invalid georef metadata: n_y is not an integer ({exc}).") from exc
        if n_y_meta <= 0:
            raise ValueError("Invalid georef metadata: n_y must be > 0.")

    x_max = meta.get("x_max")
    if x_max is not None:
        try:
            x_max = float(x_max)
        except Exception as exc:
            raise ValueError(f"Invalid georef metadata: x_max is not numeric ({exc}).") from exc
        if not np.isfinite(x_max):
            raise ValueError("Invalid georef metadata: x_max is not finite.")
        if x_max <= x_min:
            raise ValueError("Invalid georef metadata: x_max must be greater than x_min.")
        if n_x_meta is not None:
            expected_x = x_min + max(0, n_x_meta - 1) * res
            tol = max(res * 1.5, 1e-6)
            if abs(expected_x - x_max) > tol:
                raise ValueError(
                    "Invalid georef metadata: x_max is not coherent with x_min, n_x and resolution."
                )

    if grid is not None:
        arr = np.asarray(grid)
        if arr.ndim != 2 or arr.size <= 0:
            raise ValueError("Invalid grid: expected a non-empty 2D array.")
        if n_x_meta is not None and arr.shape[1] != n_x_meta:
            raise ValueError(
                f"Invalid grid width: grid n_x={arr.shape[1]} differs from metadata n_x={n_x_meta}."
            )
        if n_y_meta is not None and arr.shape[0] != n_y_meta:
            raise ValueError(
                f"Invalid grid height: grid n_y={arr.shape[0]} differs from metadata n_y={n_y_meta}."
            )


def write_grids_to_tifs(
    grids: list[dict],
    meta: dict,
    output_dir: str,
    epsg: int | None = None,
) -> list[dict]:
    """Write precomputed grids to disk as GeoTIFF + QML sidecar."""
    from .gpr_las_slicer import _write_tif_singleband

    _validate_grid_georef_meta(meta)
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
        _validate_grid_georef_meta(meta, grid=np.asarray(grid))
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
    normalize_channels: bool = False,
    extraction_mode: str = "las_like",
    use_processing: bool = False,
    idw_mode: str = "quality",
    min_points: int = 1,
    depth_radius_factor: float = 0.6,
    balance_profiles: bool = True,
    flip_traces_mode: str = "none",
    parallel_profiles: bool = False,
    profile_workers: int = 0,
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
        normalize_channels=normalize_channels,
        extraction_mode=extraction_mode,
        use_processing=use_processing,
        idw_mode=idw_mode,
        min_points=min_points,
        depth_radius_factor=depth_radius_factor,
        balance_profiles=balance_profiles,
        flip_traces_mode=flip_traces_mode,
        parallel_profiles=parallel_profiles,
        profile_workers=profile_workers,
    )
    if not grids:
        return []
    return write_grids_to_tifs(grids, meta, output_dir, epsg)
