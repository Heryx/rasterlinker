"""Pipeline di processing per radargram GPR — implementazione puro numpy.

Tutte le funzioni operano su matrice (n_samples, n_slices) float32.
Nessuna dipendenza da scipy: Hilbert e bandpass sono implementati
tramite FFT numpy, identico all'implementazione scipy internamente.
"""

from __future__ import annotations

import numpy as np


# ---------------------------------------------------------------------------
# Primitivi FFT (senza scipy)
# ---------------------------------------------------------------------------

def _hilbert_numpy(x: np.ndarray) -> np.ndarray:
    """
    Trasformata di Hilbert analitica via FFT (equivalente a scipy.signal.hilbert).
    Restituisce il segnale analitico complesso; usa np.abs() per l'envelope.
    """
    N = len(x)
    Xf = np.fft.fft(x)
    h = np.zeros(N, dtype=np.float64)
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
    """
    Filtro passa-banda brick-wall in frequenza via FFT (puro numpy).
    Più stabile del Butterworth per segnali GPR brevi.

    Parameters
    ----------
    data     : (n_samples, n_slices) float32
    dt_ns    : passo temporale in ns
    low_mhz  : frequenza di taglio inferiore (MHz)
    high_mhz : frequenza di taglio superiore (MHz)
    """
    N     = data.shape[0]
    dt_s  = dt_ns * 1e-9
    freqs = np.fft.rfftfreq(N, d=dt_s)           # Hz, metà positiva
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
    """
    Rimuove la componente DC lenta (wow) sottraendo la media mobile.
    Elimina la deriva a bassa frequenza dell'accoppiamento antenna-suolo.
    """
    data   = data.astype(np.float32)
    kernel = np.ones(window, dtype=np.float32) / window
    out    = np.empty_like(data)
    for i in range(data.shape[1]):
        trend     = np.convolve(data[:, i], kernel, mode="same")
        out[:, i] = data[:, i] - trend
    return out


def time_zero_correction(data: np.ndarray, method: str = "energy") -> np.ndarray:
    """
    Allinea le tracce portando al campione 0 il picco di energia.

    method:
      'energy'      - usa l'envelope Hilbert (più robusto, implementato FFT)
      'first_break' - usa il picco di ampiezza (più veloce)
    """
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
    """
    Rimuove il clutter di sfondo sottraendo la media di tutte le tracce.
    Elimina il segnale stazionario (riflessione dall'antenna, suolo piatto).
    """
    return (data - data.mean(axis=1, keepdims=True)).astype(np.float32)


def agc_gain(
    data: np.ndarray,
    window: int = 32,
    clip_percentile: float = 99.0,
) -> np.ndarray:
    """
    Automatic Gain Control: normalizza l'ampiezza per finestre di profondità.
    Compensa l'attenuazione geometrica e dielettrica del mezzo.
    """
    data     = data.astype(np.float32)
    n_s, n_t = data.shape
    out      = np.empty_like(data)
    half     = window // 2
    for i in range(n_t):
        tr = data[:, i]
        for s in range(n_s):
            lo        = max(0, s - half)
            hi        = min(n_s, s + half)
            rms       = np.sqrt(np.mean(tr[lo:hi] ** 2)) + 1e-10
            out[s, i] = tr[s] / rms
    clip = np.percentile(np.abs(out), clip_percentile)
    return np.clip(out, -clip, clip)


def bandpass_filter(
    data: np.ndarray,
    dt_ns: float,
    low_mhz: float,
    high_mhz: float,
    order: int = 4,    # mantenuto per compatibilità API, non usato
) -> np.ndarray:
    """
    Filtro passa-banda GPR via FFT brick-wall (puro numpy).
    Equivalente al Butterworth scipy ma senza dipendenze esterne.
    """
    return _bandpass_fft(data, dt_ns, low_mhz, high_mhz)


def hilbert_envelope(data: np.ndarray) -> np.ndarray:
    """
    Calcola l'envelope Hilbert di ogni traccia (ampiezza istantanea).
    Utile per visualizzare la riflettività del segnale GPR.
    Restituisce matrice float32 con stessa forma dell'input.
    """
    out = np.empty_like(data, dtype=np.float32)
    for i in range(data.shape[1]):
        out[:, i] = np.abs(
            _hilbert_numpy(data[:, i].astype(np.float64))
        ).astype(np.float32)
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
    "dewow":       True,
    "dewow_win":   16,
    "timezero":    False,
    "bg_removal":  True,
    "agc":         True,
    "agc_win":     32,
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
    Tutti i parametri hanno un default in DEFAULT_PIPELINE.
    """
    p   = {**DEFAULT_PIPELINE, **params}
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
        out = bandpass_filter(
            out, dt_ns,
            float(p["bp_low_mhz"]),
            float(p["bp_high_mhz"]),
        )
    out = normalize_display(out, clip_pct=float(p["clip_pct"]))
    return out
