# -*- coding: utf-8 -*-
"""
OGPR profiles -> GeoTIFF timeslice pipeline.

Per ogni profilo GPR gia' letto (OgprProfile) applica la pipeline di
processing e costruisce una nuvola di punti 2.5D (E, N, ampiezza).
Per ogni finestra di profondita' interpola la nuvola su griglia regolare
tramite IDW, poi scrive un GeoTIFF single-band float32 compatibile con
il catalogo del plugin (stesso formato output di gpr_las_slicer).

Nessuna dipendenza extra: riusa _bin_with_idw e _write_tif_singleband
gia' presenti in gpr_las_slicer.
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


def slice_ogpr_to_tifs(
    profiles: list,
    output_dir: str,
    channel: int = 0,
    resolution: float = 0.10,
    z_step: float = 0.05,
    z_min: float | None = None,
    z_max: float | None = None,
    radius: float | None = None,
    epsg: int | None = None,
    pipeline_params: dict | None = None,
) -> list[dict]:
    """
    Genera GeoTIFF timeslice da una lista di OgprProfile.

    Algoritmo:
      1. Applica processing pipeline ad ogni profilo (canale 'channel').
      2. Per ogni finestra di profondita' [z_from, z_to]:
         - estrae ampiezza media |segnale processato| nel range di campioni
         - raccoglie tutti i punti (easting, northing, ampiezza) dai profili
         - IDW su griglia regolare -> GeoTIFF float32

    Parametri
    ----------
    profiles     : lista di OgprProfile gia' letti da read_ogpr()
    output_dir   : cartella output (viene creata se non esiste)
    channel      : indice canale da usare (default 0; clampato a n_channels-1)
    resolution   : passo griglia XY in metri
    z_step       : spessore di ogni finestra di profondita' in metri
    z_min/z_max  : range di profondita' (None = auto dai profili)
    radius       : raggio IDW in metri (None = resolution * sqrt(2))
    epsg         : EPSG del CRS di output (None = non georef.)
    pipeline_params : override di DEFAULT_PIPELINE (None = default)

    Ritorna
    -------
    Lista di dict {path, z_from, z_to, z_center, index, name}
    come slice_las_to_tifs, compatibile con _register_las_slices_in_catalog.
    """
    from .gpr_processing  import apply_pipeline, DEFAULT_PIPELINE
    from .gpr_las_slicer  import _bin_with_idw, _write_tif_singleband

    if not profiles:
        return []

    if radius is None:
        radius = resolution * (2.0 ** 0.5)

    params = {**DEFAULT_PIPELINE, **(pipeline_params or {})}
    os.makedirs(output_dir, exist_ok=True)

    # ----------------------------------------------------------------
    # 1. Processa tutti i profili -> (ch, proc_data)
    # ----------------------------------------------------------------
    processed = []
    for prof in profiles:
        ch_idx = min(channel, prof.n_channels - 1)
        ch     = prof.channel(ch_idx)
        raw    = ch.data.copy()
        try:
            proc = apply_pipeline(raw, params, dt_ns=prof.dt_ns)
        except Exception as exc:
            print(f"[OGPR slicer] pipeline error on {prof.path}: {exc}")
            proc = np.abs(raw).astype(np.float32)
            mx   = proc.max()
            if mx > 1e-10:
                proc /= mx
        processed.append((prof, ch, proc))

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
    # 3. Range di profondita'
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
    # 4. Genera una slice per ogni livello di profondita'
    # ----------------------------------------------------------------
    results = []

    for iz, z_lev in enumerate(z_levels):
        z_from = float(z_lev - z_step / 2.0)
        z_to   = float(z_lev + z_step / 2.0)

        pts_e = []
        pts_n = []
        pts_a = []

        for prof, ch, proc in processed:
            n_s = proc.shape[0]
            s_lo, s_hi = _depth_to_sample_range(z_from, z_to,
                                                 prof.depth_max_m, n_s)
            if s_lo >= s_hi:
                continue
            # ampiezza media del segnale nel range di campioni per ogni traccia
            ampl = np.abs(proc[s_lo:s_hi, :]).mean(axis=0).astype(np.float32)
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

        z_label  = f"{z_lev:.4f}".replace(".", "_").replace("-", "m")
        tif_name = f"slice_{iz:04d}_z{z_label}.tif"
        tif_path = os.path.join(output_dir, tif_name)

        _write_tif_singleband(
            grid, tif_path,
            x_min, y_min, y_max, resolution, epsg,
        )

        results.append({
            "path":     tif_path,
            "z_from":   round(z_from, 6),
            "z_to":     round(z_to,   6),
            "z_center": round(float(z_lev), 6),
            "index":    iz,
            "name":     os.path.splitext(tif_name)[0],
        })

        print(f"[OGPR slicer] z={z_lev:.3f}m  pts={len(e_all)}  tif={tif_name}")

    return results
