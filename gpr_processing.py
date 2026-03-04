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


def _detect_t0_single(
    trace: np.ndarray,
    method: str,
    threshold: float,
    backup_nsamp: int,
    search_end: int,
) -> int:
    """
    Rileva l'indice t=0 su una singola traccia GPR.

    Implementa i 3 metodi di GPR-SLICE:

    'threshold'
        Primo campione in cui |traccia| supera `threshold` * max(|traccia[:search_end]|).
        Equivale al metodo 1 del manuale GPR-SLICE.

    'peak'
        Primo picco (positivo o negativo) dopo la soglia.
        Equivale al metodo 2: detection of first peak response past threshold.

    'zero_crossing'
        Primo attraversamento dello zero dopo il picco.
        Equivale al metodo 3: first zero crossing after threshold breach.

    In tutti i casi si arretra di `backup_nsamp` campioni dal punto trovato
    (comportamento identico al parametro 'Backup Nsamp' di GPR-SLICE).

    Ritorna l'indice t=0 clampato a [0, search_end - 1].
    """
    tr  = np.asarray(trace, dtype=np.float64)
    win = tr[:search_end]
    pk  = float(np.max(np.abs(win)))
    if pk < 1e-12:
        return 0

    thr_abs = threshold * pk

    # Indice della prima breach
    breach_idx = 0
    for s in range(search_end):
        if abs(tr[s]) >= thr_abs:
            breach_idx = s
            break

    if method == "threshold":
        t0 = breach_idx

    elif method == "peak":
        # Cerca il primo picco locale dopo breach_idx
        t0 = breach_idx
        for s in range(breach_idx + 1, search_end - 1):
            if abs(tr[s]) > abs(tr[s - 1]) and abs(tr[s]) > abs(tr[s + 1]):
                t0 = s
                break

    elif method == "zero_crossing":
        # Prima picco dopo breach, poi primo zero-crossing
        peak_idx = breach_idx
        for s in range(breach_idx + 1, search_end - 1):
            if abs(tr[s]) > abs(tr[s - 1]) and abs(tr[s]) > abs(tr[s + 1]):
                peak_idx = s
                break
        t0 = peak_idx
        for s in range(peak_idx + 1, search_end - 1):
            if tr[s] * tr[s + 1] <= 0.0:   # cambio di segno
                t0 = s
                break
    else:
        t0 = breach_idx

    return max(0, t0 - backup_nsamp)


def time_zero_correction(
    data: np.ndarray,
    method: str = "peak",
    mode: str = "scan_by_scan",
    threshold: float = 0.2,
    backup_nsamp: int = 4,
    search_fraction: float = 0.25,
) -> np.ndarray:
    """
    Correzione time-zero con truncation reale, secondo il metodo GPR-SLICE.

    La funzione NON usa np.roll (che causa wrapping circolare e taglia
    artificialmente il segnale in profondita'). Invece tronca fisicamente
    i campioni pre-t=0, riducendo n_samples di t0_max campioni.

    Parametri
    ----------
    data : (n_samples, n_traces)  float32
    method : str
        Metodo di rilevamento t=0:
        - 'threshold'     : prima breach della soglia (GPR-SLICE metodo 1)
        - 'peak'          : primo picco dopo soglia   (GPR-SLICE metodo 2) [default]
        - 'zero_crossing' : primo zero-crossing dopo picco (GPR-SLICE metodo 3)
    mode : str
        Modalita' operativa:
        - 'scan_by_scan' : rileva t=0 per ogni traccia individualmente.
                           Corregge il drift elettronico traccia per traccia.
        - 'line_by_line' : usa la mediana di t0 sull'intera linea.
                           Piu' robusto su dati rumorosi (consigliato dal manuale
                           GPR-SLICE quando t0 non varia significativamente
                           all'interno del profilo).
    threshold : float
        Frazione del picco massimo per la breach (default 0.2 = 20%).
    backup_nsamp : int
        Campioni da arretrare rispetto al punto rilevato (default 4).
    search_fraction : float
        Frazione di n_samples entro cui cercare il t=0 (default 0.25 = primo 25%).

    Ritorna
    -------
    ndarray (n_samples - t0_max, n_traces) float32
        Radargram troncato: i campioni pre-t=0 sono rimossi fisicamente.
        n_samples_out = n_samples - max(t0_idx per tutte le tracce)
        (comportamento identico a GPR-SLICE 'Scan-by-Scan + Truncate')
    """
    data     = data.astype(np.float32)
    n_s, n_t = data.shape
    search_end = max(4, int(n_s * search_fraction))

    # --- Rilevamento t0 per ogni traccia ---
    t0_indices = np.zeros(n_t, dtype=np.int64)
    for i in range(n_t):
        t0_indices[i] = _detect_t0_single(
            data[:, i], method, threshold, backup_nsamp, search_end
        )

    # --- Mode: line_by_line usa mediana ---
    if mode == "line_by_line":
        t0_median   = int(np.median(t0_indices))
        t0_indices[:] = t0_median

    t0_max = int(t0_indices.max())

    print(
        f"[GPR] time_zero: method={method} mode={mode}  "
        f"t0_min={t0_indices.min()}  t0_max={t0_max}  "
        f"n_samples_out={n_s - t0_max}"
    )

    if t0_max <= 0:
        return data

    # --- Truncation reale ---
    # Ogni traccia viene allineata al proprio t0 e troncata a n_s - t0_max campioni.
    # Le tracce con t0 < t0_max vengono scalate di (t0_max - t0_i) campioni in piu':
    # la parte extra iniziale viene zero-paddata per mantenere la matrice rettangolare
    # (identico a GPR-SLICE che allinea tutte le tracce alla stessa lunghezza finale).
    n_out = n_s - t0_max
    out   = np.zeros((n_out, n_t), dtype=np.float32)

    for i in range(n_t):
        t0_i    = int(t0_indices[i])
        src     = data[t0_i:, i]       # campioni validi da t0 in poi
        n_copy  = min(len(src), n_out)
        out[:n_copy, i] = src[:n_copy]

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
    row_std  = d64.std(axis=1)
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
    sq  = d64 ** 2
    cs  = np.cumsum(sq, axis=0)

    s_idx   = np.arange(n_s)
    lo      = np.maximum(0,       s_idx - half)
    hi      = np.minimum(n_s - 1, s_idx + half)
    win_len = (hi - lo + 1).reshape(-1, 1)

    sum_hi  = cs[hi, :]
    lo_prev = np.maximum(0, lo - 1)
    sum_lo  = cs[lo_prev, :]
    mask_lo = (lo > 0).reshape(-1, 1)
    sum_lo  = np.where(mask_lo, sum_lo, 0.0)

    rms = np.sqrt((sum_hi - sum_lo) / win_len) + 1e-10
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
    "dewow":            True,
    "dewow_win":        16,
    "timezero":         False,
    "tz_method":        "peak",
    "tz_mode":          "line_by_line",
    "tz_threshold":     0.2,
    "tz_backup_nsamp":  4,
    "bg_removal":       True,
    "agc":              True,
    "agc_win":          128,
    "bandpass":         False,
    "bp_low_mhz":       100.0,
    "bp_high_mhz":      1200.0,
    "clip_pct":         98.0,
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
        print(f"[GPR] {tag:30s}  min={mn:.4g}  max={mx:.4g}  "
              f"nonzero={nz}/{arr.size}  shape={arr.shape}")

    _log("raw", out)

    if p["dewow"]:
        out = dewow(out, window=int(p["dewow_win"]))
        _log(f"dewow(win={p['dewow_win']})", out)

    if p["timezero"]:
        out = time_zero_correction(
            out,
            method          = str(p.get("tz_method",       "peak")),
            mode            = str(p.get("tz_mode",         "line_by_line")),
            threshold       = float(p.get("tz_threshold",   0.2)),
            backup_nsamp    = int(p.get("tz_backup_nsamp",  4)),
        )
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
