"""Pipeline di processing basilare per radargram GPR.

Tutte le funzioni operano su una matrice (n_samples, n_slices) float32
e restituiscono una matrice della stessa forma.
"""

from __future__ import annotations

import numpy as np
from scipy import signal as sp_signal  # type: ignore


# ---------------------------------------------------------------------------
# Singole operazioni
# ---------------------------------------------------------------------------

def dewow(data: np.ndarray, window: int = 16) -> np.ndarray:
    """
    Rimuove la componente DC lenta (wow) con un filtro passa-alto.
    Per ogni traccia sottrae la media mobile di lunghezza `window`.
    """
    data = data.astype(np.float32)
    kernel = np.ones(window, dtype=np.float32) / window
    out = np.empty_like(data)
    for i in range(data.shape[1]):
        trend = np.convolve(data[:, i], kernel, mode="same")
        out[:, i] = data[:, i] - trend
    return out


def time_zero_correction(data: np.ndarray, method: str = "energy") -> np.ndarray:
    """
    Allinea le tracce portando al campione 0 il picco di energia.
    method: 'energy' (envelope RMS) | 'first_break'
    """
    data  = data.astype(np.float32)
    n_s, n_t = data.shape
    out   = np.zeros_like(data)

    for i in range(n_t):
        tr = data[:, i]
        if method == "energy":
            env    = np.abs(sp_signal.hilbert(tr))
            t0_idx = int(np.argmax(env[:n_s // 4]))
        else:
            t0_idx = int(np.argmax(np.abs(tr[:n_s // 4])))
        shifted = np.roll(tr, -t0_idx)
        out[:, i] = shifted
    return out


def background_removal(data: np.ndarray) -> np.ndarray:
    """
    Rimuove il clutter di sfondo sottraendo la media di tutte le tracce
    (background = segnale stazionario = pavimento dell'antenna).
    """
    bg = data.mean(axis=1, keepdims=True)
    return (data - bg).astype(np.float32)


def agc_gain(
    data: np.ndarray,
    window: int = 32,
    clip_percentile: float = 99.0,
) -> np.ndarray:
    """
    Automatic Gain Control: normalizza l'ampiezza per finestre di profondità.
    Compensa l'attenuazione geometrica e dielettrica.
    """
    data  = data.astype(np.float32)
    n_s, n_t = data.shape
    out   = np.empty_like(data)
    half  = window // 2

    for i in range(n_t):
        tr = data[:, i]
        for s in range(n_s):
            lo  = max(0, s - half)
            hi  = min(n_s, s + half)
            rms = np.sqrt(np.mean(tr[lo:hi] ** 2)) + 1e-10
            out[s, i] = tr[s] / rms

    # Clip per evitare valori esplosivi in zone silenziose
    clip = np.percentile(np.abs(out), clip_percentile)
    out  = np.clip(out, -clip, clip)
    return out


def bandpass_filter(
    data: np.ndarray,
    dt_ns: float,
    low_mhz: float,
    high_mhz: float,
    order: int = 4,
) -> np.ndarray:
    """
    Filtro Butterworth passa-banda (in frequenza).
    """
    fs_hz   = 1.0 / (dt_ns * 1e-9)   # campionamento in Hz
    nyq     = fs_hz / 2.0
    lo      = max(1e-3, low_mhz  * 1e6 / nyq)
    hi      = min(0.999, high_mhz * 1e6 / nyq)
    b, a    = sp_signal.butter(order, [lo, hi], btype="band")
    out     = sp_signal.filtfilt(b, a, data, axis=0).astype(np.float32)
    return out


def normalize_display(data: np.ndarray, clip_pct: float = 98.0) -> np.ndarray:
    """
    Normalizza in [-1, 1] con clip ai percentili per il display.
    """
    vmax = np.percentile(np.abs(data), clip_pct)
    if vmax < 1e-12:
        return np.zeros_like(data)
    return np.clip(data / vmax, -1.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------------------
# Pipeline completa
# ---------------------------------------------------------------------------

DEFAULT_PIPELINE = {
    "dewow":      True,
    "dewow_win":  16,
    "timezero":   False,
    "bg_removal": True,
    "agc":        True,
    "agc_win":    32,
    "bandpass":   False,
    "bp_low_mhz":  100.0,
    "bp_high_mhz": 1200.0,
    "clip_pct":   98.0,
}


def apply_pipeline(
    data: np.ndarray,
    params: dict,
    dt_ns: float = 0.117,
) -> np.ndarray:
    """
    Applica la pipeline completa secondo il dizionario `params`.
    Tutti i parametri hanno un default in DEFAULT_PIPELINE.
    """
    p = {**DEFAULT_PIPELINE, **params}
    out = data.copy()

    if p["dewow"]:
        out = dewow(out, window=int(p["dewow_win"]))
    if p["timezero"]:
        out = time_zero_correction(out)
    if p["bg_removal"]:
        out = background_removal(out)
    if p["agc"]:
        out = agc_gain(out, window=int(p["agc_win"]))
    if p["bandpass"]:
        out = bandpass_filter(out, dt_ns,
                              float(p["bp_low_mhz"]),
                              float(p["bp_high_mhz"]))
    out = normalize_display(out, clip_pct=float(p["clip_pct"]))
    return out
