# -*- coding: utf-8 -*-
"""
OGPR profiles -> GeoTIFF timeslice pipeline.

Flusso:
  1. apply_pipeline() su ogni canale di ogni profilo
  2. _envelope(): inviluppo di Hilbert (scipy) -> ampiezza istantanea
     [motivo: np.abs(segnale GPR) oscilla attorno a zero; la media
      risultante e' quasi zero -> timeslice nere. L'inviluppo di Hilbert
      da' sempre valori >= 0 = ampiezza reale del segnale.]
  3. Per ogni finestra di profondita': raccolta punti (E, N, ampiezza)
     e interpolazione IDW su griglia regolare
  4. Scrittura GeoTIFF float32 + sidecar .qml (percentile stretch)

Riusa _bin_with_idw e _write_tif_singleband da gpr_las_slicer.
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
# Inviluppo di Hilbert
# ---------------------------------------------------------------------------

def _envelope(data: np.ndarray) -> np.ndarray:
    """
    Calcola l'inviluppo di Hilbert (ampiezza istantanea) lungo l'asse 0
    (campioni / profondita'). Fallback a |abs| se scipy non disponibile.

    data: (n_samples, n_traces) float32/64
    return: (n_samples, n_traces) float32, valori >= 0
    """
    try:
        from scipy.signal import hilbert  # type: ignore
        # hilbert opera lungo l'ultimo asse per default; usiamo axis=0
        analytic = hilbert(data.astype(np.float64), axis=0)
        return np.abs(analytic).astype(np.float32)
    except ImportError:
        # scipy non disponibile: fallback
        return np.abs(data).astype(np.float32)


# ---------------------------------------------------------------------------
# Helpers di profondita'
# ---------------------------------------------------------------------------

def _depth_to_sample_range(
    z_from: float,
    z_to: float,
    depth_max_m: float,
    n_samples: int,
) -> tuple[int, int]:
    """Converte finestra di profondita' (m) in indici campione."""
    if depth_max_m <= 0:
        return 0, n_samples
    s_lo = int(np.floor(max(z_from, 0.0) / depth_max_m * (n_samples - 1)))
    s_hi = int(np.ceil( min(z_to,   depth_max_m) / depth_max_m * (n_samples - 1))) + 1
    return max(0, s_lo), min(n_samples, s_hi)


# ---------------------------------------------------------------------------
# QML sidecar (single-band, percentile stretch)
# ---------------------------------------------------------------------------

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
    """Scrive sidecar .qml con stretch 2-98 percentile calcolato sulla grid."""
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
# Core: elaborazione profili -> grids (senza I/O su disco)
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
) -> tuple[list[dict], dict]:
    """
    Calcola le griglie IDW per ogni slice di profondita'.
    NON scrive nulla su disco: utile per anteprima.

    Ritorna
    -------
    grids : lista di dict
        { z_lev, z_from, z_to, index, grid (np.ndarray float32 con NaN) }
    meta  : dict
        { x_min, y_min, y_max, x_max, n_x, n_y, resolution }
    """
    from .gpr_processing import apply_pipeline, DEFAULT_PIPELINE
    from .gpr_las_slicer import _bin_with_idw

    if not profiles:
        return [], {}

    if radius is None:
        radius = resolution * (2.0 ** 0.5)

    params = {**DEFAULT_PIPELINE, **(pipeline_params or {})}

    # ----------------------------------------------------------------
    # 1. Processa + inviluppo di Hilbert
    # ----------------------------------------------------------------
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
                proc = apply_pipeline(raw, params, dt_ns=prof.dt_ns)
            except Exception as exc:
                print(f"[OGPR slicer] pipeline error ch{ci} in {prof.path}: {exc}")
                proc = raw.astype(np.float32)
            # inviluppo di Hilbert
            env = _envelope(proc)
            proc_channels.append(env)

        ampl_3d = np.stack(proc_channels, axis=2)  # (n_s, n_t, n_ch)
        processed.append((prof, ch_ref, ampl_3d))

    # ----------------------------------------------------------------
    # 2. Bounding box
    # ----------------------------------------------------------------
    all_e = np.concatenate([ch.easting  for _, ch, _ in processed])
    all_n = np.concatenate([ch.northing for _, ch, _ in processed])

    x_min = float(all_e.min())
    x_max = float(all_e.max())
    y_min = float(all_n.min())
    y_max = float(all_n.max())
    n_x   = max(2, int(np.round((x_max - x_min) / resolution)) + 1)
    n_y   = max(2, int(np.round((y_max - y_min) / resolution)) + 1)

    meta = dict(x_min=x_min, y_min=y_min, y_max=y_max,
                x_max=x_max, n_x=n_x, n_y=n_y, resolution=resolution)

    # ----------------------------------------------------------------
    # 3. Range profondita'
    # ----------------------------------------------------------------
    if z_min is None:
        z_min = 0.0
    if z_max is None:
        z_max = max(prof.depth_max_m for prof, _, _ in processed)

    z_levels = np.arange(float(z_min), float(z_max) + z_step * 0.5, float(z_step))

    # ----------------------------------------------------------------
    # 4. Grids per slice
    # ----------------------------------------------------------------
    grids = []
    for iz, z_lev in enumerate(z_levels):
        z_from = float(z_lev - z_step / 2.0)
        z_to   = float(z_lev + z_step / 2.0)

        pts_e, pts_n, pts_a = [], [], []
        for prof, ch, ampl_3d in processed:
            n_s = ampl_3d.shape[0]
            s_lo, s_hi = _depth_to_sample_range(z_from, z_to, prof.depth_max_m, n_s)
            if s_lo >= s_hi:
                continue
            window = ampl_3d[s_lo:s_hi, :, :]          # (win, n_t, n_ch)
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

        grid = _bin_with_idw(
            e_all, n_all, a_all,
            x_min, y_min, n_x, n_y, resolution, radius,
        )
        grids.append({
            "z_lev":  float(z_lev),
            "z_from": round(z_from, 6),
            "z_to":   round(z_to,   6),
            "index":  iz,
            "grid":   grid,
        })
        print(f"[OGPR slicer] z={z_lev:.3f}m  pts={len(e_all)}  "
              f"ch={'all' if channel < 0 else channel}")

    return grids, meta


# ---------------------------------------------------------------------------
# Scrittura GeoTIFF da grids precalcolate
# ---------------------------------------------------------------------------

def write_grids_to_tifs(
    grids: list[dict],
    meta: dict,
    output_dir: str,
    epsg: int | None = None,
) -> list[dict]:
    """Scrive le grids precalcolate su disco come GeoTIFF + QML sidecar."""
    from .gpr_las_slicer import _write_tif_singleband

    os.makedirs(output_dir, exist_ok=True)
    results = []
    x_min = meta["x_min"]
    y_min = meta["y_min"]
    y_max = meta["y_max"]
    res   = meta["resolution"]

    for item in grids:
        z_lev    = item["z_lev"]
        iz       = item["index"]
        grid     = item["grid"]
        z_label  = f"{z_lev:.4f}".replace(".", "_").replace("-", "m")
        tif_name = f"slice_{iz:04d}_z{z_label}.tif"
        tif_path = os.path.join(output_dir, tif_name)

        _write_tif_singleband(grid, tif_path, x_min, y_min, y_max, res, epsg)
        _write_qml_singleband(tif_path, grid)

        results.append({
            "path":     tif_path,
            "z_from":   item["z_from"],
            "z_to":     item["z_to"],
            "z_center": round(z_lev, 6),
            "index":    iz,
            "name":     os.path.splitext(tif_name)[0],
        })
    return results


# ---------------------------------------------------------------------------
# Entry point legacy (compatibilita')
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
    """Compatibilita' con chiamate dirette: calcola grids e scrive su disco."""
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
