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

    Calcolo in float64 per evitare cancellazione catastrofica in float32
    (con dati int16 in float32, residui reali possono essere < ULP e azzerarsi).

    Diagnostico: stampa la deviazione standard inter-traccia prima della
    sottrazione per verificare se il dato ha variazione laterale reale.
    Se mean_std ~ 0 e rows_zero_std ~ n_samples, il dato non ha variazione
    laterale e l'azzeramento dopo bg_removal e' corretto (non un bug).
    """
    d64      = data.astype(np.float64)
    row_std  = d64.std(axis=1)           # std across n_slices per ogni sample
    n_zero   = int(np.count_nonzero(row_std == 0.0))
    print(
        f"[GPR] bg_diag: mean_inter-trace_std={row_std.mean():.6g}  "
        f"max={row_std.max():.6g}  "
        f"rows_with_zero_std={n_zero}/{len(row_std)}"
    )
    return (d64 - d64.mean(axis=1, keepdims=True)).astype(np.float32)


def agc_gain(
    data: np.ndarray,
    window: int = 128,
    clip_percentile: float = 99.0,
) -> np.ndarray:
    """
    Automatic Gain Control vettorizzato con cumsum.

    Per ogni campione s e ogni traccia, calcola il RMS locale su una finestra
    simmetrica di ampiezza `window` lungo l'asse profondita', poi divide.

    Implementazione:
      - sq = data^2  (n_s, n_t)
      - cs = cumsum(sq, axis=0)  -> somma prefissa per ogni traccia
      - rms[s] = sqrt( (cs[hi] - cs[lo-1]) / win_len ) + eps
      - out = data / rms

    Tutto vettorizzato: O(n_s * n_t) operazioni numpy, nessun loop Python.
    Speedup tipico vs implementazione a doppio loop: x50-100.

    Calcolo in float64 per evitare overflow nel cumsum di grandi valori^2.
    """
    n_s, n_t = data.shape
    half     = max(window, 8) // 2

    d64 = data.astype(np.float64)
    sq  = d64 ** 2                        # (n_s, n_t)
    cs  = np.cumsum(sq, axis=0)           # (n_s, n_t) cumsum lungo profondita'

    # Indici finestra per ogni campione (vettore 1D, shape n_s)
    s_idx  = np.arange(n_s)
    lo     = np.maximum(0,       s_idx - half)      # incluso
    hi     = np.minimum(n_s - 1, s_idx + half)      # incluso
    win_len = (hi - lo + 1).reshape(-1, 1)           # (n_s, 1)

    # Somma sq nel range [lo, hi] via differenza di cumsum
    sum_hi  = cs[hi, :]                              # (n_s, n_t)
    lo_prev = np.maximum(0, lo - 1)                  # clamp a 0
    sum_lo  = cs[lo_prev, :]                         # (n_s, n_t)
    # Se lo==0 non si sottrae nulla (cs[-1] non esiste -> delta=0 gia' incluso)
    mask_lo = (lo > 0).reshape(-1, 1)               # (n_s, 1)
    sum_lo  = np.where(mask_lo, sum_lo, 0.0)

    rms = np.sqrt((sum_hi - sum_lo) / win_len) + 1e-10  # (n_s, n_t)
    out = (d64 / rms).astype(np.float32)

    if out.size > 0:
        clip = float(np.percentile(np.abs(out), clip_percentile))
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
      3. Se ancora < 1e-12 -> dati effettivamente a zero -> ritorna zeros
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
    "timezero":    False,
    "bg_removal":  True,
    "agc":         True,
    "agc_win":     128,
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
