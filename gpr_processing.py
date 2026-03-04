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

    breach_idx = 0
    for s in range(search_end):
        if abs(tr[s]) >= thr_abs:
            breach_idx = s
            break

    if method == "threshold":
        t0 = breach_idx

    elif method == "peak":
        t0 = breach_idx
        for s in range(breach_idx + 1, search_end - 1):
            if abs(tr[s]) > abs(tr[s - 1]) and abs(tr[s]) > abs(tr[s + 1]):
                t0 = s
                break

    elif method == "zero_crossing":
        peak_idx = breach_idx
        for s in range(breach_idx + 1, search_end - 1):
            if abs(tr[s]) > abs(tr[s - 1]) and abs(tr[s]) > abs(tr[s + 1]):
                peak_idx = s
                break
        t0 = peak_idx
        for s in range(peak_idx + 1, search_end - 1):
            if tr[s] * tr[s + 1] <= 0.0:
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
        - 'line_by_line' : usa la mediana di t0 sull'intera linea.
    threshold : float
        Frazione del picco massimo per la breach (default 0.2 = 20%).
    backup_nsamp : int
        Campioni da arretrare rispetto al punto rilevato (default 4).
    search_fraction : float
        Frazione di n_samples entro cui cercare il t=0 (default 0.25).

    Ritorna
    -------
    ndarray (n_samples - t0_max, n_traces) float32
    """
    data     = data.astype(np.float32)
    n_s, n_t = data.shape
    search_end = max(4, int(n_s * search_fraction))

    t0_indices = np.zeros(n_t, dtype=np.int64)
    for i in range(n_t):
        t0_indices[i] = _detect_t0_single(
            data[:, i], method, threshold, backup_nsamp, search_end
        )

    if mode == "line_by_line":
        t0_median     = int(np.median(t0_indices))
        t0_indices[:] = t0_median

    t0_max = int(t0_indices.max())

    print(
        f"[GPR] time_zero: method={method} mode={mode}  "
        f"t0_min={t0_indices.min()}  t0_max={t0_max}  "
        f"n_samples_out={n_s - t0_max}"
    )

    if t0_max <= 0:
        return data

    n_out = n_s - t0_max
    out   = np.zeros((n_out, n_t), dtype=np.float32)

    for i in range(n_t):
        t0_i   = int(t0_indices[i])
        src    = data[t0_i:, i]
        n_copy = min(len(src), n_out)
        out[:n_copy, i] = src[:n_copy]

    return out


def background_removal(
    data: np.ndarray,
    mode: str = "line_by_line",
    window: int = 0,
    reference_trace: np.ndarray | None = None,
) -> np.ndarray:
    """
    Background removal (GPR-SLICE §Background Removal, pag. 166).

    Sottrae una traccia di riferimento (il "clutter" orizzontale costante)
    da ogni traccia del radargram.

    Modalita'
    ---------
    'line_by_line'
        La traccia media viene calcolata all'interno del singolo radargram
        e sottratta da ogni scan.

        window = 0  (AUTO, consigliato)
            Media globale sull'intero profilo.  Equivale al checkbox
            'Auto Set' di GPR-SLICE (filter_length = 99000).
            Garantisce che la stessa traccia media venga sottratta da ogni
            scan, rimuovendo il banding costante.

        window > 0  (media locale scorrevole)
            Per ogni traccia i, sottrae la media delle `window` tracce
            centrate in i.  Utile in mapping 2D per rimuovere background
            locale preservando strutture laterali.
            ATTENZIONE (nota manuale GPR-SLICE): con window piccolo
            si rischia di rimuovere anche riflessi reali lineari
            paralleli al profilo (es. tubazioni parallele all'antenna).

    'grid_by_grid'
        Sottrae `reference_trace` (traccia media pre-calcolata sull'intero
        grid, cioe' su tutti i radargram del progetto).  Il chiamante deve
        fornirla; se None, viene calcolata dalla media del dato corrente
        (equivalente a line_by_line auto).

    Diagnostico
    -----------
    Stampa su console la deviazione standard inter-traccia per verificare
    che il dato abbia variazione laterale reale prima della sottrazione.

    Parametri
    ----------
    data             : (n_samples, n_traces) float32
    mode             : 'line_by_line' | 'grid_by_grid'
    window           : int — lunghezza filtro in tracce (0 = auto)
    reference_trace  : (n_samples,) float64 | None — solo per grid_by_grid
    """
    d64      = data.astype(np.float64)
    n_s, n_t = d64.shape

    # Diagnostico inter-traccia
    row_std = d64.std(axis=1)
    n_zero  = int(np.count_nonzero(row_std == 0.0))
    print(
        f"[GPR] bg_diag: mode={mode} window={window if window > 0 else 'auto'}  "
        f"mean_inter-trace_std={row_std.mean():.6g}  "
        f"max={row_std.max():.6g}  "
        f"rows_with_zero_std={n_zero}/{len(row_std)}"
    )

    # ---------------------------------------------------------------
    # grid_by_grid: traccia di riferimento esterna
    # ---------------------------------------------------------------
    if mode == "grid_by_grid":
        if reference_trace is not None:
            ref = np.asarray(reference_trace, dtype=np.float64)
            if ref.shape[0] != n_s:
                print(
                    f"[GPR] bg_removal: reference_trace shape {ref.shape} != n_samples {n_s}; "
                    "fallback a line_by_line auto."
                )
                ref = d64.mean(axis=1)
        else:
            print("[GPR] bg_removal: grid_by_grid ma reference_trace=None; fallback a line_by_line auto.")
            ref = d64.mean(axis=1)
        return (d64 - ref.reshape(-1, 1)).astype(np.float32)

    # ---------------------------------------------------------------
    # line_by_line — auto (window = 0): media globale
    # ---------------------------------------------------------------
    if window <= 0 or window >= n_t:
        mean_trace = d64.mean(axis=1, keepdims=True)   # (n_s, 1)
        print(f"[GPR] bg_removal: global mean subtracted  n_t={n_t}")
        return (d64 - mean_trace).astype(np.float32)

    # ---------------------------------------------------------------
    # line_by_line — finestra scorrevole lungo l'asse tracce
    # ---------------------------------------------------------------
    half = window // 2
    idx  = np.arange(n_t)
    hi   = np.minimum(n_t - 1, idx + half)    # (n_t,)
    lo   = np.maximum(0,       idx - half)    # (n_t,)

    cs      = np.cumsum(d64, axis=1)           # (n_s, n_t)
    sum_hi  = cs[:, hi]                        # (n_s, n_t)
    lo_prev = np.maximum(0, lo - 1)
    sum_lo  = cs[:, lo_prev]
    # annulla contributo quando lo == 0 (non c'e' prefisso da sottrarre)
    sum_lo[:, lo == 0] = 0.0

    win_len    = (hi - lo + 1).reshape(1, -1)  # (1, n_t)
    local_mean = (sum_hi - sum_lo) / win_len   # (n_s, n_t)

    print(
        f"[GPR] bg_removal: sliding window={window} (half={half})  "
        f"effective_window range=[{(hi-lo+1).min()}, {(hi-lo+1).max()}]"
    )
    return (d64 - local_mean).astype(np.float32)


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
    "bg_mode":          "line_by_line",   # 'line_by_line' | 'grid_by_grid'
    "bg_window":        0,                # 0 = auto (media globale, equiv. GPR-SLICE Auto Set)
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
    bg_reference_trace: np.ndarray | None = None,
) -> np.ndarray:
    """
    Applica la pipeline di processing completa.
    Stampa su console (QGIS Python Console) il range del dato
    in ogni stadio per facilitare il debug.

    Parametri
    ----------
    data                : (n_samples, n_traces) float32
    params              : dict con chiavi da DEFAULT_PIPELINE
    dt_ns               : intervallo di campionamento in nanosecondi
    bg_reference_trace  : (n_samples,) traccia media dell'intero grid
                          (richiesta solo se bg_mode='grid_by_grid')
    """
    p   = {**DEFAULT_PIPELINE, **params}
    out = data.copy()

    def _log(tag: str, arr: np.ndarray) -> None:
        mn = float(arr.min())
        mx = float(arr.max())
        nz = int(np.count_nonzero(arr))
        print(f"[GPR] {tag:35s}  min={mn:.4g}  max={mx:.4g}  "
              f"nonzero={nz}/{arr.size}  shape={arr.shape}")

    _log("raw", out)

    if p["dewow"]:
        out = dewow(out, window=int(p["dewow_win"]))
        _log(f"dewow(win={p['dewow_win']})", out)

    if p["timezero"]:
        out = time_zero_correction(
            out,
            method       = str(p.get("tz_method",       "peak")),
            mode         = str(p.get("tz_mode",         "line_by_line")),
            threshold    = float(p.get("tz_threshold",   0.2)),
            backup_nsamp = int(p.get("tz_backup_nsamp",  4)),
        )
        _log("timezero", out)

    if p["bg_removal"]:
        bg_mode   = str(p.get("bg_mode",   "line_by_line"))
        bg_window = int(p.get("bg_window",  0))
        out = background_removal(
            out,
            mode            = bg_mode,
            window          = bg_window,
            reference_trace = bg_reference_trace,
        )
        win_label = "auto" if bg_window <= 0 else str(bg_window)
        _log(f"bg_removal(mode={bg_mode} win={win_label})", out)

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
