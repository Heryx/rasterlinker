"""Pipeline di processing per radargram GPR — implementazione puro numpy.

Tutte le funzioni operano su matrice (n_samples, n_slices) float32.
Nessuna dipendenza da scipy: Hilbert e bandpass sono implementati
tramite FFT numpy.
"""

from __future__ import annotations

import numpy as np


# ---------------------------------------------------------------------------
# Primitivi FFT (senza scipy)
# ---------------------------------------------------------------------------

def _hilbert_numpy(x: np.ndarray) -> np.ndarray:
    N  = len(x)
    Xf = np.fft.fft(x)
    h  = np.zeros(N, dtype=np.float64)
    if N % 2 == 0:
        h[0] = h[N // 2] = 1.0
        h[1:N // 2] = 2.0
    else:
        h[0] = 1.0
        h[1:(N + 1) // 2] = 2.0
    return np.fft.ifft(Xf * h)


def _bandpass_fft(
    data: np.ndarray,
    dt_ns: float,
    low_mhz: float,
    high_mhz: float,
) -> np.ndarray:
    N     = data.shape[0]
    dt_s  = dt_ns * 1e-9
    freqs = np.fft.rfftfreq(N, d=dt_s)
    mask  = ((freqs >= low_mhz  * 1e6) &
              (freqs <= high_mhz * 1e6)).astype(np.float32)
    out   = np.empty_like(data)
    for i in range(data.shape[1]):
        Xf        = np.fft.rfft(data[:, i].astype(np.float64))
        out[:, i] = np.fft.irfft(Xf * mask, n=N).astype(np.float32)
    return out


# ---------------------------------------------------------------------------
# Singole operazioni di processing
# ---------------------------------------------------------------------------

def dewow(data: np.ndarray, window: int = 16) -> np.ndarray:
    """Rimuove componente DC lenta sottraendo la media mobile."""
    data   = data.astype(np.float32)
    kernel = np.ones(window, dtype=np.float32) / window
    out    = np.empty_like(data)
    for i in range(data.shape[1]):
        trend     = np.convolve(data[:, i], kernel, mode="same")
        out[:, i] = data[:, i] - trend
    return out


def time_zero_correction(data: np.ndarray, method: str = "energy") -> np.ndarray:
    data     = data.astype(np.float32)
    n_s, n_t = data.shape
    out      = np.zeros_like(data)
    for i in range(n_t):
        tr = data[:, i]
        if method == "energy":
            env    = np.abs(_hilbert_numpy(tr.astype(np.float64)))
            t0_idx = int(np.argmax(env[:n_s // 4]))
        else:
            t0_idx = int(np.argmax(np.abs(tr[:n_s // 4])))
        out[:, i] = np.roll(tr, -t0_idx)
    return out


def background_removal(data: np.ndarray) -> np.ndarray:
    """Sottrae la traccia media (clutter di sfondo orizzontale).

    Il calcolo avviene in float64 per evitare cancellazione catastrofica:
    con dati int16 (±32000) in float32, i residui dopo sottrazione della
    media possono essere inferiori alla precisione float32 (~0.004) e
    venire azzerati, lasciando meno del 2% di campioni nonzero.
    """
    d64 = data.astype(np.float64)
    return (d64 - d64.mean(axis=1, keepdims=True)).astype(np.float32)


def agc_gain(
    data: np.ndarray,
    window: int = 128,
    clip_percentile: float = 99.0,
) -> np.ndarray:
    """
    Automatic Gain Control: normalizza per finestre di profondità.
    window consigliato: 128 campioni (~30% di una traccia tipica da 50 ns
    a 0.117 ns/campione). Finestre piccole (<32) amplificano il rumore
    profondo producendo un'immagine grigia uniforme senza contrasto.
    """
    data     = data.astype(np.float32)
    n_s, n_t = data.shape
    out      = np.empty_like(data)
    half     = max(window, 8) // 2
    for i in range(n_t):
        tr = data[:, i]
        for s in range(n_s):
            lo        = max(0, s - half)
            hi        = min(n_s, s + half + 1)
            rms       = np.sqrt(np.mean(tr[lo:hi] ** 2)) + 1e-10
            out[s, i] = tr[s] / rms
    if out.size > 0:
        clip = np.percentile(np.abs(out), clip_percentile)
        if clip > 1e-12:
            return np.clip(out, -clip, clip)
    return out


def bandpass_filter(
    data: np.ndarray,
    dt_ns: float,
    low_mhz: float,
    high_mhz: float,
    order: int = 4,
) -> np.ndarray:
    return _bandpass_fft(data, dt_ns, low_mhz, high_mhz)


def hilbert_envelope(data: np.ndarray) -> np.ndarray:
    out = np.empty_like(data, dtype=np.float32)
    for i in range(data.shape[1]):
        out[:, i] = np.abs(
            _hilbert_numpy(data[:, i].astype(np.float64))
        ).astype(np.float32)
    return out


def normalize_display(data: np.ndarray, clip_pct: float = 98.0) -> np.ndarray:
    """
    Normalizza in [-1, 1] con clip ai percentili per il display.

    Strategia robusta:
      1. Usa il percentile clip_pct del valore assoluto
      2. Se < 1e-12 (dati quasi nulli), usa il massimo assoluto reale
      3. Se ancora < 1e-12 → dati effettivamente a zero → ritorna zeros
         e stampa avviso su console
    """
    vmax = float(np.percentile(np.abs(data), clip_pct))
    if vmax < 1e-12:
        vmax = float(np.max(np.abs(data)))
    if vmax < 1e-12:
        print(
            "[GPR] ATTENZIONE: dati nulli dopo processing. "
            f"Shape={data.shape}  dtype={data.dtype}  "
            f"min={float(data.min()):.4g}  max={float(data.max()):.4g}"
        )
        return np.zeros_like(data)
    return np.clip(data / vmax, -1.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------------------
# Pipeline completa
# ---------------------------------------------------------------------------

DEFAULT_PIPELINE = {
    "dewow":       True,
    "dewow_win":   16,
    "timezero":    False,      # controllato dall'UI viewer, off di default
    "bg_removal":  True,
    "agc":         True,
    "agc_win":     128,        # finestra ampia: preserva il decadimento in profondità
    "bandpass":    False,
    "bp_low_mhz":  100.0,
    "bp_high_mhz": 1200.0,
    "clip_pct":    98.0,
}


def apply_pipeline(
    data: np.ndarray,
    params: dict,
    dt_ns: float = 0.117,
) -> np.ndarray:
    """
    Applica la pipeline di processing completa.
    Stampa su console (QGIS Python Console) il range del dato
    in ogni stadio per facilitare il debug.
    """
    p   = {**DEFAULT_PIPELINE, **params}
    out = data.copy()

    def _log(tag: str, arr: np.ndarray) -> None:
        mn = float(arr.min())
        mx = float(arr.max())
        nz = int(np.count_nonzero(arr))
        print(f"[GPR] {tag:20s}  min={mn:.4g}  max={mx:.4g}  "
              f"nonzero={nz}/{arr.size}")

    _log("raw", out)

    if p["dewow"]:
        out = dewow(out, window=int(p["dewow_win"]))
        _log(f"dewow(win={p['dewow_win']})", out)

    if p["timezero"]:
        out = time_zero_correction(out)
        _log("timezero", out)

    if p["bg_removal"]:
        out = background_removal(out)
        _log("bg_removal", out)

    if p["agc"]:
        out = agc_gain(out, window=int(p["agc_win"]))
        _log(f"agc(win={p['agc_win']})", out)

    if p["bandpass"]:
        out = bandpass_filter(
            out, dt_ns,
            float(p["bp_low_mhz"]),
            float(p["bp_high_mhz"]),
        )
        _log(f"bandpass({p['bp_low_mhz']}-{p['bp_high_mhz']}MHz)", out)

    out = normalize_display(out, clip_pct=float(p["clip_pct"]))
    _log("normalize (finale)", out)
    return out
