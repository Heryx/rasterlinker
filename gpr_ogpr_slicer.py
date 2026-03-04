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


def _amplitude_from_window(
    proc: np.ndarray,
    s_lo: int,
    s_hi: int,
) -> np.ndarray:
    """Ampiezza media |segnale| nel range [s_lo, s_hi) per ogni traccia."""
    return np.abs(proc[s_lo:s_hi, :]).mean(axis=0).astype(np.float32)


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
    """
    Genera GeoTIFF timeslice da una lista di OgprProfile.

    Parametri
    ----------
    profiles        : lista di OgprProfile gia' letti da read_ogpr()
    output_dir      : cartella output
    channel         : -1 = tutti i canali combinati (default)
                       0, 1, ... = canale singolo
    combine_method  : 'mean' (default) o 'max' -- usato solo se channel == -1
    resolution      : passo griglia XY in metri
    z_step          : spessore finestra di profondita' in metri
    z_min / z_max   : range profondita' (None = auto)
    radius          : raggio IDW in metri (None = resolution * sqrt(2))
    epsg            : EPSG del CRS (None = non georiferito)
    pipeline_params : override DEFAULT_PIPELINE

    Ritorna
    -------
    Lista di dict {path, z_from, z_to, z_center, index, name}
    compatibile con _register_las_slices_in_catalog.
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
    # 1. Processa tutti i canali di tutti i profili
    #    processed: lista di (ch, [proc_ch0, proc_ch1, ...])
    #    - channel == -1: processa tutti i canali, li combina per traccia
    #    - channel >= 0:  processa solo il canale richiesto
    # ----------------------------------------------------------------
    processed = []   # (ch_ref, easting, northing, ampl_3d)  con ampl_3d (n_s, n_t, n_ch)

    for prof in profiles:
        n_ch   = prof.n_channels
        ch_ref = prof.channel(0)   # usiamo ch0 per easting/northing (tutti uguale)

        if channel < 0:
            # tutti i canali
            ch_list = list(range(n_ch))
        else:
            ch_list = [min(channel, n_ch - 1)]

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
            proc_channels.append(proc)  # ogni elem (n_s, n_t)

        # stack -> (n_s, n_t, n_ch)
        ampl_3d = np.stack(proc_channels, axis=2)
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

        for prof, ch, ampl_3d in processed:
            n_s = ampl_3d.shape[0]
            s_lo, s_hi = _depth_to_sample_range(
                z_from, z_to, prof.depth_max_m, n_s
            )
            if s_lo >= s_hi:
                continue

            # ampl_3d: (n_s, n_t, n_ch)
            window = np.abs(ampl_3d[s_lo:s_hi, :, :])  # (win, n_t, n_ch)

            # 1) media lungo l'asse dei campioni (depth window) -> (n_t, n_ch)
            per_ch = window.mean(axis=0)

            # 2) combina i canali -> (n_t,)
            if per_ch.shape[1] == 1 or combine_method == "mean":
                ampl = per_ch.mean(axis=1).astype(np.float32)
            else:  # 'max'
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
