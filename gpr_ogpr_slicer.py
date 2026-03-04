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
# Conversione profondità → indici campione
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

def _normalize_channels(
    ampl_3d: np.ndarray,
) -> np.ndarray:
    """Bilancia l'ampiezza di ciascun canale rispetto alla media globale.

    ampl_3d: (n_samples, n_traces, n_channels)
    Ogni canale di un array GPR ha una risposta in ampiezza leggermente
    diversa. Questa funzione normalizza ciascun canale in modo che la
    sua ampiezza media corrisponda alla media globale di tutti i canali,
    eliminando le striature verticali nelle time-slice.
    """
    out = ampl_3d.copy()
    n_ch = ampl_3d.shape[2]
    flat = ampl_3d.reshape(-1, n_ch)
    global_mean = float(np.nanmean(np.abs(flat)))
    if global_mean < 1e-12:
        return out
    for ci in range(n_ch):
        ch_vals = flat[:, ci]
        ch_mean = float(np.nanmean(np.abs(ch_vals)))
        if ch_mean > 1e-12:
            out[:, :, ci] = ampl_3d[:, :, ci] * (global_mean / ch_mean)
    return out


# ---------------------------------------------------------------------------
# Rimozione outlier ampiezza
# ---------------------------------------------------------------------------

def _remove_amplitude_outliers(
    x: np.ndarray,
    y: np.ndarray,
    values: np.ndarray,
    n_sigma: float = 3.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rimuove spike GPR tramite sigma-clipping."""
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
    """Stima la direzione principale delle linee GPR via PCA.

    Ritorna l'angolo in gradi (0-180) della direzione along-line,
    misurato in senso antiorario dall'asse X positivo.
    """
    if x.size < 3:
        return 0.0
    pts  = np.column_stack([x - x.mean(), y - y.mean()])
    cov  = np.cov(pts.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    principal = eigvecs[:, np.argmax(eigvals)]
    return float(np.degrees(np.arctan2(principal[1], principal[0]))) % 180.0


# ---------------------------------------------------------------------------
# Stima raggio adattivo e anisotropia
# ---------------------------------------------------------------------------

def _estimate_interline_radius(
    x: np.ndarray,
    y: np.ndarray,
    percentile: float = 95.0,
    multiplier: float = 1.5,
) -> dict:
    """Stima raggio di ricerca e anisotropia dalla spaziatura inter-linea."""
    from scipy.spatial import cKDTree

    if x.size < 4:
        return {"radius": 1.0, "anisotropy_ratio": 1.0, "acquisition_angle": 0.0}

    tree = cKDTree(np.column_stack([x, y]))
    dists, _ = tree.query(np.column_stack([x, y]), k=min(6, x.size))

    nn_dists   = dists[:, 1]
    along_line = float(np.percentile(nn_dists, 10))
    cross_line = float(np.percentile(nn_dists, percentile))

    radius           = cross_line * multiplier
    anisotropy_ratio = (cross_line / along_line) if along_line > 1e-9 else 1.0
    acquisition_angle = _estimate_acquisition_direction(x, y)

    return {
        "radius":            max(radius, 1e-6),
        "anisotropy_ratio":  anisotropy_ratio,
        "acquisition_angle": acquisition_angle,
    }


# ---------------------------------------------------------------------------
# Fill NoData
# ---------------------------------------------------------------------------

def _fill_nodata_grid(grid: np.ndarray, max_distance: int = 5) -> np.ndarray:
    """Riempie celle NaN tramite diffusione nearest-neighbour."""
    from scipy.ndimage import distance_transform_edt

    nan_mask = np.isnan(grid)
    if not nan_mask.any():
        return grid
    dist, (row_idx, col_idx) = distance_transform_edt(
        nan_mask, return_distances=True, return_indices=True,
    )
    filled = grid.copy()
    fill_where = nan_mask & (dist <= max_distance)
    filled[fill_where] = grid[row_idx[fill_where], col_idx[fill_where]]
    return filled


# ---------------------------------------------------------------------------
# Smoothing gaussiano
# ---------------------------------------------------------------------------

def _smooth_grid_gaussian(grid: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    """Smoothing gaussiano con gestione corretta dei NaN."""
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
# IDW isotropo (gather cKDTree)
# ---------------------------------------------------------------------------

def _bin_with_idw(
    x_pts, y_pts, i_pts,
    x_min, y_min, n_x, n_y,
    resolution, radius,
    power=2.0, min_points=3,
):
    from scipy.spatial import cKDTree

    gx = x_min + np.arange(n_x) * resolution
    gy = y_min + np.arange(n_y) * resolution
    gxx, gyy  = np.meshgrid(gx, gy)
    grid_pts  = np.column_stack([gxx.ravel(), gyy.ravel()])

    tree    = cKDTree(np.column_stack([x_pts, y_pts]))
    results = tree.query_ball_point(grid_pts, r=radius, workers=-1)

    i_pts_f64 = i_pts.astype(np.float64)
    grid      = np.full(n_x * n_y, np.nan, dtype=np.float32)

    for k, idx_list in enumerate(results):
        if len(idx_list) < min_points:
            continue
        idx = np.asarray(idx_list, dtype=np.int64)
        cx  = grid_pts[k, 0]
        cy  = grid_pts[k, 1]
        d   = np.sqrt((x_pts[idx] - cx) ** 2 + (y_pts[idx] - cy) ** 2)
        w   = 1.0 / (d ** power + 1e-9)
        ws  = w.sum()
        if ws > 0:
            grid[k] = float(np.dot(w, i_pts_f64[idx]) / ws)

    return grid.reshape(n_y, n_x)


# ---------------------------------------------------------------------------
# IDW anisotropo (metrica ellittica per linee parallele)
# ---------------------------------------------------------------------------

def _bin_with_idw_anisotropic(
    x_pts, y_pts, i_pts,
    x_min, y_min, n_x, n_y,
    resolution, radius,
    power=2.0, min_points=3,
    anisotropy_ratio=1.0,
    anisotropy_angle=0.0,
):
    """IDW con distanza ellittica nel sistema di riferimento della survey.

    d_aniso = sqrt( dx_rot^2 + (dy_rot / anisotropy_ratio)^2 )

    dove dx_rot, dy_rot sono le coordinate ruotate nell'asse along-line.
    """
    from scipy.spatial import cKDTree

    if anisotropy_ratio <= 0:
        anisotropy_ratio = 1.0

    angle_rad = np.radians(anisotropy_angle)
    cos_a = np.cos(angle_rad)
    sin_a = np.sin(angle_rad)

    def _aniso_dist(px, py, cx, cy):
        dx = px - cx
        dy = py - cy
        dx_rot =  dx * cos_a + dy * sin_a
        dy_rot = -dx * sin_a + dy * cos_a
        return np.sqrt(dx_rot ** 2 + (dy_rot / anisotropy_ratio) ** 2)

    gx = x_min + np.arange(n_x) * resolution
    gy = y_min + np.arange(n_y) * resolution
    gxx, gyy  = np.meshgrid(gx, gy)
    grid_pts  = np.column_stack([gxx.ravel(), gyy.ravel()])

    search_r = radius * max(1.0, anisotropy_ratio)
    tree     = cKDTree(np.column_stack([x_pts, y_pts]))
    results  = tree.query_ball_point(grid_pts, r=search_r, workers=-1)

    i_pts_f64 = i_pts.astype(np.float64)
    grid      = np.full(n_x * n_y, np.nan, dtype=np.float32)

    for k, idx_list in enumerate(results):
        if not idx_list:
            continue
        idx = np.asarray(idx_list, dtype=np.int64)
        cx  = grid_pts[k, 0]
        cy  = grid_pts[k, 1]
        d   = _aniso_dist(x_pts[idx], y_pts[idx], cx, cy)
        in_r = d <= radius
        if in_r.sum() < min_points:
            continue
        d_in = d[in_r]
        w    = 1.0 / (d_in ** power + 1e-9)
        ws   = w.sum()
        if ws > 0:
            grid[k] = float(np.dot(w, i_pts_f64[idx[in_r]]) / ws)

    return grid.reshape(n_y, n_x)


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
    # --- normalizzazione inter-canale ---
    normalize_channels: bool = True,
    # --- filtro ampiezza ---
    amplitude_sigma: float | None = None,
    # --- IDW anisotropo ---
    use_anisotropic_idw: bool = False,
    auto_radius: bool = False,
    anisotropy_ratio: float | None = None,
    anisotropy_angle: float | None = None,
    idw_power: float = 2.0,
    min_points: int = 3,
    # --- post-processing ---
    fill_nodata: bool = False,
    fill_nodata_max_distance: int = 5,
    smooth_sigma: float = 0.0,
) -> list[dict]:
    """
    Genera GeoTIFF timeslice da una lista di OgprProfile.

    Parametri
    ----------
    profiles            : lista di OgprProfile da read_ogpr()
    output_dir          : cartella output
    channel             : -1 = tutti i canali combinati; 0..N = canale singolo
    combine_method      : 'mean' o 'max' (usato se channel == -1)
    resolution          : passo griglia XY in metri
    z_step              : spessore finestra di profondita' in metri
    z_min / z_max       : range profondita' (None = auto)
    radius              : raggio IDW in metri (None = resolution * sqrt(2))
    epsg                : EPSG del CRS (None = dal file)
    pipeline_params     : override DEFAULT_PIPELINE
    normalize_channels  : bilancia ampiezza inter-canale (elimina striature)
    amplitude_sigma     : soglia sigma-clipping outlier (None = disabilitato)
    use_anisotropic_idw : usa IDW con metrica ellittica per linee parallele
    auto_radius         : stima raggio dalla spaziatura inter-linea
    anisotropy_ratio    : rapporto cross/along-line (None = auto)
    anisotropy_angle    : angolo acquisizione in gradi (None = auto via PCA)
    idw_power           : esponente IDW
    min_points          : punti minimi per stimare una cella
    fill_nodata         : riempie gap NaN tra le linee
    fill_nodata_max_distance : distanza massima fill in celle
    smooth_sigma        : sigma smoothing gaussiano (0 = disabilitato)

    Ritorna
    -------
    Lista di dict {path, z_from, z_to, z_center, index, name}
    """
    from .gpr_processing import apply_pipeline, DEFAULT_PIPELINE
    from .gpr_las_slicer import _write_tif_singleband

    if not profiles:
        return []

    if radius is None:
        radius = resolution * (2.0 ** 0.5)

    params = {**DEFAULT_PIPELINE, **(pipeline_params or {})}
    os.makedirs(output_dir, exist_ok=True)

    # ----------------------------------------------------------------
    # 1. Processa tutti i profili e i canali richiesti
    # ----------------------------------------------------------------
    processed = []  # (prof, ch_ref, ampl_3d)

    for prof in profiles:
        n_ch   = prof.n_channels
        ch_ref = prof.channel(0)

        ch_list = list(range(n_ch)) if channel < 0 else [min(channel, n_ch - 1)]

        proc_channels = []
        for ci in ch_list:
            ch  = prof.channel(ci)
            raw = ch.data.copy()
            try:
                proc = apply_pipeline(raw, params, dt_ns=prof.dt_ns)
            except Exception as exc:
                print(f"[OGPR slicer] pipeline error ch{ci} in {prof.path}: {exc}")
                proc = np.abs(raw).astype(np.float32)
                mx   = proc.max()
                if mx > 1e-10:
                    proc /= mx
            proc_channels.append(proc)  # (n_s, n_t)

        # stack -> (n_s, n_t, n_ch)
        ampl_3d = np.stack(proc_channels, axis=2)

        # Normalizzazione inter-canale: elimina striature da risposta differenziale
        if normalize_channels and ampl_3d.shape[2] > 1:
            ampl_3d = _normalize_channels(ampl_3d)

        processed.append((prof, ch_ref, ampl_3d))

    # ----------------------------------------------------------------
    # 2. Bounding box globale
    # ----------------------------------------------------------------
    all_e = np.concatenate([ch.easting  for _, ch, _ in processed])
    all_n = np.concatenate([ch.northing for _, ch, _ in processed])

    x_min = float(all_e.min())
    x_max = float(all_e.max())
    y_min = float(all_n.min())
    y_max = float(all_n.max())

    n_x = max(2, int(np.round((x_max - x_min) / resolution)) + 1)
    n_y = max(2, int(np.round((y_max - y_min) / resolution)) + 1)

    # ----------------------------------------------------------------
    # 3. Stima raggio adattivo e anisotropia (su tutti i punti)
    # ----------------------------------------------------------------
    _aniso_info = None
    if auto_radius or (use_anisotropic_idw and
                       (anisotropy_ratio is None or anisotropy_angle is None)):
        _aniso_info = _estimate_interline_radius(all_e, all_n)

    if auto_radius and _aniso_info is not None:
        radius = _aniso_info["radius"]

    _eff_ratio = anisotropy_ratio
    _eff_angle = anisotropy_angle
    if use_anisotropic_idw:
        if _eff_ratio is None:
            _eff_ratio = (_aniso_info or {}).get("anisotropy_ratio", 1.0)
        if _eff_angle is None:
            _eff_angle = (_aniso_info or {}).get("acquisition_angle", 0.0)

    # ----------------------------------------------------------------
    # 4. Range di profondita'
    # ----------------------------------------------------------------
    if z_min is None:
        z_min = 0.0
    if z_max is None:
        z_max = max(prof.depth_max_m for prof, _, _ in processed)

    z_levels = np.arange(
        float(z_min),
        float(z_max) + z_step * 0.5,
        float(z_step),
    )

    # ----------------------------------------------------------------
    # 5. Genera una slice per ogni livello
    # ----------------------------------------------------------------
    results = []

    # EPSG: usa quello del primo profilo se non specificato
    if epsg is None:
        epsg = processed[0][0].epsg if processed else None

    for iz, z_lev in enumerate(z_levels):
        z_from = float(z_lev - z_step / 2.0)
        z_to   = float(z_lev + z_step / 2.0)

        pts_e = []
        pts_n = []
        pts_a = []

        for prof, ch, ampl_3d in processed:
            n_s = ampl_3d.shape[0]
            s_lo, s_hi = _depth_to_sample_range(
                z_from, z_to, prof.depth_max_m, n_s
            )
            if s_lo >= s_hi:
                continue

            window = np.abs(ampl_3d[s_lo:s_hi, :, :])  # (win, n_t, n_ch)
            per_ch = window.mean(axis=0)                # (n_t, n_ch)

            if per_ch.shape[1] == 1 or combine_method == "mean":
                ampl = per_ch.mean(axis=1).astype(np.float32)
            else:
                ampl = per_ch.max(axis=1).astype(np.float32)

            pts_e.append(ch.easting)
            pts_n.append(ch.northing)
            pts_a.append(ampl)

        if not pts_e:
            continue

        e_all = np.concatenate(pts_e)
        n_all = np.concatenate(pts_n)
        a_all = np.concatenate(pts_a)

        # Filtro ampiezza
        if amplitude_sigma is not None:
            e_all, n_all, a_all = _remove_amplitude_outliers(
                e_all, n_all, a_all, n_sigma=amplitude_sigma
            )

        # Interpolazione
        if use_anisotropic_idw:
            grid = _bin_with_idw_anisotropic(
                e_all, n_all, a_all,
                x_min, y_min, n_x, n_y, resolution, radius,
                power=idw_power, min_points=min_points,
                anisotropy_ratio=_eff_ratio or 1.0,
                anisotropy_angle=_eff_angle or 0.0,
            )
        else:
            grid = _bin_with_idw(
                e_all, n_all, a_all,
                x_min, y_min, n_x, n_y, resolution, radius,
                power=idw_power, min_points=min_points,
            )

        # Post-processing
        if fill_nodata:
            grid = _fill_nodata_grid(grid, max_distance=fill_nodata_max_distance)
        if smooth_sigma > 0:
            grid = _smooth_grid_gaussian(grid, sigma=smooth_sigma)

        z_label  = f"{z_lev:.4f}".replace(".", "_").replace("-", "m")
        tif_name = f"slice_{iz:04d}_z{z_label}.tif"
        tif_path = os.path.join(output_dir, tif_name)

        _write_tif_singleband(
            grid, tif_path,
            x_min, y_min, y_max, resolution, epsg,
        )

        n_ch_used = ampl_3d.shape[2]
        print(
            f"[OGPR slicer] z={z_lev:.3f}m  pts={len(e_all)}  "
            f"ch={'all' if channel < 0 else channel}({n_ch_used})  "
            f"tif={tif_name}"
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
