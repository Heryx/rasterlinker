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
    N = int(data.shape[0])
    dt_s = float(dt_ns) * 1e-9
    if N <= 1 or (not np.isfinite(dt_s)) or dt_s <= 0.0:
        return np.asarray(data, dtype=np.float32, copy=True)

    freqs = np.fft.rfftfreq(N, d=dt_s)
    f_lo = max(0.0, float(low_mhz) * 1e6)
    f_hi = max(f_lo, float(high_mhz) * 1e6)
    if f_hi <= f_lo:
        return np.asarray(data, dtype=np.float32, copy=True)

    # Cosine-tapered passband to reduce Gibbs ringing vs. rectangular mask.
    bw = max(1e-9, f_hi - f_lo)
    margin = max(1e-9, bw * 0.05)
    lo1 = f_lo
    lo2 = f_lo + margin
    hi1 = f_hi - margin
    hi2 = f_hi

    mask = np.zeros(freqs.shape, dtype=np.float64)
    core = (freqs >= lo2) & (freqs <= hi1)
    mask[core] = 1.0

    rise = (freqs > lo1) & (freqs < lo2)
    if np.any(rise):
        x = (freqs[rise] - lo1) / margin
        mask[rise] = 0.5 * (1.0 - np.cos(np.pi * x))

    fall = (freqs > hi1) & (freqs < hi2)
    if np.any(fall):
        x = (hi2 - freqs[fall]) / margin
        mask[fall] = 0.5 * (1.0 - np.cos(np.pi * x))

    out = np.empty_like(data)
    for i in range(data.shape[1]):
        Xf = np.fft.rfft(data[:, i].astype(np.float64))
        out[:, i] = np.fft.irfft(Xf * mask, n=N).astype(np.float32)
    return out


# ---------------------------------------------------------------------------
# Singole operazioni di processing
# ---------------------------------------------------------------------------

def dewow(data: np.ndarray, window: int = 16) -> np.ndarray:
    """Rimuove componente DC lenta sottraendo la media mobile."""
    data = np.asarray(data, dtype=np.float32)
    n_s, n_t = data.shape
    try:
        win = int(window)
    except Exception:
        win = 16
    if win <= 1 or n_s <= 2:
        return data.copy()
    win = max(2, min(win, n_s))
    kernel = np.ones(win, dtype=np.float32) / float(win)
    half = win // 2
    out = np.empty_like(data)
    # Reflect padding avoids edge spikes at trace start/end.
    for i in range(n_t):
        tr = data[:, i].astype(np.float32, copy=False)
        pad_left = half
        pad_right = max(0, win - 1 - half)
        tr_pad = np.pad(tr, (pad_left, pad_right), mode="reflect")
        trend = np.convolve(tr_pad, kernel, mode="valid")
        out[:, i] = tr - trend[:n_s]
    return out


def _detect_t0_single(
    trace: np.ndarray,
    method: str,
    threshold: float,
    backup_nsamp: int,
    search_end: int,
) -> int:
    tr  = np.asarray(trace, dtype=np.float64)
    win = tr[:search_end]
    pk  = float(np.max(np.abs(win)))
    if pk < 1e-12:
        return 0
    thr_abs    = threshold * pk
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
    sample_start: int = 0,
    sample_end: int = 0,
) -> np.ndarray:
    """
    Background removal (GPR-SLICE §Background Removal, pag. 166).

    Sottrae una traccia di riferimento (il "clutter" orizzontale costante)
    da ogni traccia del radargram.

    Modalita'
    ---------
    'line_by_line'
        window = 0  (AUTO): media globale sull'intero profilo.
        window > 0        : media scorrevole di `window` tracce.

    'grid_by_grid'
        Sottrae `reference_trace` pre-calcolata su tutti i radargram.
        Il chiamante deve fornirla via apply_pipeline(bg_reference_trace=...).
        Se None: fallback a line_by_line auto.

    Finestra temporale (sample_start / sample_end)
    -----------------------------------------------
    Permette di limitare la sottrazione a un range di campioni [s_lo, s_hi).
    I campioni FUORI dalla finestra vengono lasciati INVARIATI.

    Caso d'uso principale: escludere i primi N campioni (ground coupling /
    onda diretta) dalla rimozione del background.
    Effetto: preserva il segnale nei primi 50 cm, permettendo all'AGC di
    amplificarlo correttamente invece di trovare ~0 dopo la sottrazione.

        sample_start = 0, sample_end = 0  ->  intera traccia (default).
        sample_start = 20, sample_end = 0 ->  salta i primi 20 campioni
                                               (ground coupling), applica
                                               BG removal dal campione 20
                                               in poi.
        sample_start = 0, sample_end = 80 ->  applica solo ai primi 80
                                               campioni (zona superficiale).

    Parametri
    ----------
    data             : (n_samples, n_traces) float32
    mode             : 'line_by_line' | 'grid_by_grid'
    window           : int  lunghezza filtro in tracce (0 = auto)
    reference_trace  : (n_samples,) float64 | None  solo per grid_by_grid
    sample_start     : int  primo campione incluso nella finestra (0 = inizio)
    sample_end       : int  ultimo campione escluso (0 = auto = n_samples)
    """
    d64      = data.astype(np.float64)
    n_s, n_t = d64.shape

    # Diagnostico
    row_std = d64.std(axis=1)
    n_zero  = int(np.count_nonzero(row_std == 0.0))
    s_lo    = max(0, int(sample_start))
    s_hi    = min(n_s, int(sample_end)) if sample_end > 0 else n_s
    print(
        f"[GPR] bg_diag: mode={mode} win={window if window > 0 else 'auto'}  "
        f"samples=[{s_lo}:{s_hi}/{n_s}]  "
        f"mean_inter-trace_std={row_std.mean():.6g}  "
        f"rows_zero_std={n_zero}/{n_s}"
    )

    if s_lo >= s_hi:
        print("[GPR] bg_removal: finestra campioni vuota, nessuna modifica.")
        return d64.astype(np.float32)

    # Sub-matrice su cui operare
    sub = d64[s_lo:s_hi, :]   # (n_win, n_t)

    # ---------------------------------------------------------------
    # grid_by_grid
    # ---------------------------------------------------------------
    if mode == "grid_by_grid":
        if reference_trace is not None:
            ref = np.asarray(reference_trace, dtype=np.float64)
            # Adatta ref alla finestra campioni
            ref_sub = ref[s_lo:min(s_hi, ref.shape[0])]
            if ref_sub.shape[0] != sub.shape[0]:
                print(
                    f"[GPR] bg_removal grid_by_grid: ref len {ref_sub.shape[0]} "
                    f"!= window {sub.shape[0]}; fallback a line_by_line auto."
                )
                ref_sub = sub.mean(axis=1)
        else:
            print("[GPR] bg_removal: grid_by_grid senza reference_trace; fallback a line_by_line auto.")
            ref_sub = sub.mean(axis=1)
        out = d64.copy()
        out[s_lo:s_hi, :] = sub - ref_sub.reshape(-1, 1)
        return out.astype(np.float32)

    # ---------------------------------------------------------------
    # line_by_line — auto: media globale del sub-range
    # ---------------------------------------------------------------
    if window <= 0 or window >= n_t:
        mean_trace = sub.mean(axis=1, keepdims=True)
        print(f"[GPR] bg_removal: global mean subtracted  n_t={n_t}  s=[{s_lo}:{s_hi}]")
        out = d64.copy()
        out[s_lo:s_hi, :] = sub - mean_trace
        return out.astype(np.float32)

    # ---------------------------------------------------------------
    # line_by_line — finestra scorrevole lungo asse tracce
    # ---------------------------------------------------------------
    half   = window // 2
    n_sub  = sub.shape[1]
    idx    = np.arange(n_sub)
    hi_t   = np.minimum(n_sub - 1, idx + half)
    lo_t   = np.maximum(0,         idx - half)
    cs     = np.cumsum(sub, axis=1)
    sum_hi = cs[:, hi_t]
    lo_prev = np.maximum(0, lo_t - 1)
    sum_lo  = cs[:, lo_prev]
    sum_lo[:, lo_t == 0] = 0.0
    win_len    = (hi_t - lo_t + 1).reshape(1, -1)
    local_mean = (sum_hi - sum_lo) / win_len
    print(
        f"[GPR] bg_removal: sliding win={window}  s=[{s_lo}:{s_hi}]  "
        f"eff_win=[{(hi_t-lo_t+1).min()},{(hi_t-lo_t+1).max()}]"
    )
    out = d64.copy()
    out[s_lo:s_hi, :] = sub - local_mean
    return out.astype(np.float32)


def agc_gain(
    data: np.ndarray,
    window: int = 128,
    clip_percentile: float = 99.0,
) -> np.ndarray:
    """
    Automatic Gain Control vettorizzato con cumsum.
    O(n_s * n_t) senza loop Python.
    """
    n_s, n_t = data.shape
    half = max(window, 8) // 2
    d64 = data.astype(np.float64, copy=False)
    sq = d64 ** 2
    # Exclusive cumsum: cs[k] = sum(sq[:k]); avoids off-by-one at window start.
    cs = np.vstack([np.zeros((1, n_t), dtype=np.float64), np.cumsum(sq, axis=0)])
    s_idx = np.arange(n_s, dtype=np.int64)
    lo = np.maximum(0, s_idx - half)
    hi = np.minimum(n_s - 1, s_idx + half)
    win_len = (hi - lo + 1).reshape(-1, 1).astype(np.float64)
    sum_win = cs[hi + 1, :] - cs[lo, :]
    rms = np.sqrt(np.maximum(sum_win / win_len, 0.0)) + 1e-10
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
# Pipeline
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
    "bg_mode":          "line_by_line",
    "bg_window":        0,
    "bg_sample_start":  0,    # 0 = dall'inizio
    "bg_sample_end":    0,    # 0 = auto (intera traccia)
    "agc":              True,
    "agc_win":          128,
    # Optional depth-dependent gain applied BEFORE AGC.
    # Useful to attenuate direct-wave/surface burst before local AGC normalization.
    "pre_agc_gain":     False,
    "pre_agc_surface_gain": 1.0,
    "pre_agc_deep_gain":    1.0,
    "pre_agc_curve":        "power",  # linear|power|exp|breakpoints
    "pre_agc_power":        1.8,
    "pre_agc_breakpoints":  None,     # [[x(0..1), gain], ...]
    "bandpass":         False,
    "bp_low_mhz":       100.0,
    "bp_high_mhz":      1200.0,
    "envelope":         False,
    "chain_order":      None,
    "clip_pct":         98.0,
}


FILTER_REGISTRY = {
    "timezero": {
        "label": "Tempo zero",
        "param_key": "timezero",
    },
    "dewow": {
        "label": "Wobble removal (dewow)",
        "param_key": "dewow",
    },
    "bandpass": {
        "label": "Filtro passa-banda",
        "param_key": "bandpass",
    },
    "bg_removal": {
        "label": "Rimozione rumore di fondo",
        "param_key": "bg_removal",
    },
    "pre_agc_gain": {
        "label": "Range gain (pre-AGC)",
        "param_key": "pre_agc_gain",
    },
    "agc": {
        "label": "Guadagno automatico (AGC)",
        "param_key": "agc",
    },
    "envelope": {
        "label": "Inviluppo (Hilbert)",
        "param_key": "envelope",
    },
}

DEFAULT_CHAIN_ORDER = [
    "timezero",
    "dewow",
    "bg_removal",
    "bandpass",
    "agc",
    "envelope",
]


def _resolve_chain_order(chain_order, params: dict) -> list[str]:
    """Resolve pipeline order from explicit arg or params['chain_order']."""
    raw = chain_order if chain_order is not None else params.get("chain_order")
    if raw is None:
        return list(DEFAULT_CHAIN_ORDER)
    if isinstance(raw, (list, tuple)):
        seen = set()
        out = []
        for step in raw:
            sid = str(step or "").strip()
            if sid in FILTER_REGISTRY and sid not in seen:
                out.append(sid)
                seen.add(sid)
        for sid in DEFAULT_CHAIN_ORDER:
            if sid not in seen:
                out.append(sid)
        return out
    return list(DEFAULT_CHAIN_ORDER)


def _sanitize_gain_breakpoints(points) -> np.ndarray:
    rows = []
    if isinstance(points, np.ndarray):
        pts_iter = points.tolist()
    elif isinstance(points, (list, tuple)):
        pts_iter = points
    else:
        pts_iter = []
    for it in pts_iter:
        try:
            x = float(it[0])
            y = float(it[1])
        except Exception:
            continue
        if not (np.isfinite(x) and np.isfinite(y)):
            continue
        rows.append((float(np.clip(x, 0.0, 1.0)), max(float(y), 1e-6)))
    if len(rows) < 2:
        rows = [(0.0, 1.0), (1.0, 1.0)]
    arr = np.asarray(rows, dtype=np.float64)
    arr = arr[np.argsort(arr[:, 0], kind="mergesort")]
    x = arr[:, 0]
    keep = np.concatenate(([True], np.diff(x) > 1e-9))
    arr = arr[keep]
    if arr[0, 0] > 0.0:
        arr = np.vstack([[0.0, arr[0, 1]], arr])
    else:
        arr[0, 0] = 0.0
    if arr[-1, 0] < 1.0:
        arr = np.vstack([arr, [1.0, arr[-1, 1]]])
    else:
        arr[-1, 0] = 1.0
    return arr


def _build_pre_agc_gain(n_samples: int, p: dict) -> np.ndarray:
    n = max(1, int(n_samples))
    g0 = max(float(p.get("pre_agc_surface_gain", 1.0) or 1.0), 1e-6)
    g1 = max(float(p.get("pre_agc_deep_gain", 1.0) or 1.0), 1e-6)
    mode = str(p.get("pre_agc_curve", "power") or "power").strip().lower()
    t = np.linspace(0.0, 1.0, n, dtype=np.float64)
    if mode == "breakpoints":
        pts = _sanitize_gain_breakpoints(p.get("pre_agc_breakpoints"))
        g = np.interp(t, pts[:, 0], pts[:, 1])
    else:
        power = float(np.clip(float(p.get("pre_agc_power", 1.8) or 1.8), 0.2, 8.0))
        if mode == "linear":
            g = g0 + (g1 - g0) * t
        elif mode == "exp":
            g = np.exp(np.log(g0) + (np.log(g1) - np.log(g0)) * t)
        else:
            g = g0 + (g1 - g0) * (t ** power)
    g = np.clip(g, 1e-6, 1e6).astype(np.float32, copy=False)
    return g.reshape(-1, 1)


def apply_pre_bg_pipeline(
    data: np.ndarray,
    params: dict,
    dt_ns: float = 0.117,
) -> np.ndarray:
    """
    Applica solo le fasi che precedono il BG removal (dewow + bandpass + time-zero).

    Usata per il calcolo della traccia di riferimento nel modo grid_by_grid
    a due passate:
      1a passata: apply_pre_bg_pipeline su tutti i profili -> media globale
      2a passata: apply_pipeline con bg_reference_trace = media globale

    Questo garantisce che la traccia sottratta sia la media reale del
    ground coupling comune a TUTTI i radargram del grid, non solo del
    singolo profilo.
    """
    p   = {**DEFAULT_PIPELINE, **params}
    out = data.copy()
    if p["dewow"]:
        out = dewow(out, window=int(p["dewow_win"]))
    if p["bandpass"]:
        out = bandpass_filter(
            out,
            dt_ns,
            float(p["bp_low_mhz"]),
            float(p["bp_high_mhz"]),
        )
    if p["timezero"]:
        out = time_zero_correction(
            out,
            method       = str(p.get("tz_method",      "peak")),
            mode         = str(p.get("tz_mode",        "line_by_line")),
            threshold    = float(p.get("tz_threshold",  0.2)),
            backup_nsamp = int(p.get("tz_backup_nsamp", 4)),
        )
    return out


def apply_pipeline(
    data: np.ndarray,
    params: dict,
    dt_ns: float = 0.117,
    bg_reference_trace: np.ndarray | None = None,
    normalize_output: bool = True,
    chain_order: list[str] | None = None,
) -> np.ndarray:
    """
    Applica la pipeline di processing completa.

    Parametri
    ----------
    data                : (n_samples, n_traces) float32
    params              : dict con chiavi da DEFAULT_PIPELINE
    dt_ns               : intervallo di campionamento in nanosecondi
    bg_reference_trace  : (n_samples,) traccia media dell'intero grid.
                          Fornita da _process_profiles() per grid_by_grid
                          a due passate; None per line_by_line.
    """
    p = {**DEFAULT_PIPELINE, **params}
    out = data.copy()
    order = _resolve_chain_order(chain_order, p)

    def _log(tag: str, arr: np.ndarray) -> None:
        mn = float(arr.min())
        mx = float(arr.max())
        nz = int(np.count_nonzero(arr))
        print(f"[GPR] {tag:40s}  min={mn:.4g}  max={mx:.4g}  "
              f"nonzero={nz}/{arr.size}  shape={arr.shape}")

    _log("raw", out)

    for step in order:
        if step == "dewow":
            if p["dewow"]:
                out = dewow(out, window=int(p["dewow_win"]))
                _log(f"dewow(win={p['dewow_win']})", out)
            continue

        if step == "timezero":
            if p["timezero"]:
                out = time_zero_correction(
                    out,
                    method=str(p.get("tz_method", "peak")),
                    mode=str(p.get("tz_mode", "line_by_line")),
                    threshold=float(p.get("tz_threshold", 0.2)),
                    backup_nsamp=int(p.get("tz_backup_nsamp", 4)),
                )
                _log("timezero", out)
            continue

        if step == "bg_removal":
            if p["bg_removal"]:
                bg_mode = str(p.get("bg_mode", "line_by_line"))
                bg_window = int(p.get("bg_window", 0))
                bg_sample_start = int(p.get("bg_sample_start", 0))
                bg_sample_end = int(p.get("bg_sample_end", 0))
                out = background_removal(
                    out,
                    mode=bg_mode,
                    window=bg_window,
                    reference_trace=bg_reference_trace,
                    sample_start=bg_sample_start,
                    sample_end=bg_sample_end,
                )
                win_lbl = "auto" if bg_window <= 0 else str(bg_window)
                s_lbl = f"{bg_sample_start}:{bg_sample_end if bg_sample_end > 0 else 'end'}"
                _log(f"bg_removal(mode={bg_mode} win={win_lbl} s=[{s_lbl}])", out)
            continue

        if step == "pre_agc_gain":
            if bool(p.get("pre_agc_gain", False)):
                gain = _build_pre_agc_gain(int(out.shape[0]), p)
                out = (out.astype(np.float32, copy=False) * gain).astype(np.float32, copy=False)
                _log("pre_agc_gain", out)
            continue

        if step == "agc":
            if p["agc"]:
                out = agc_gain(out, window=int(p["agc_win"]))
                _log(f"agc(win={p['agc_win']})", out)
            continue

        if step == "bandpass":
            if p["bandpass"]:
                out = bandpass_filter(
                    out,
                    dt_ns,
                    float(p["bp_low_mhz"]),
                    float(p["bp_high_mhz"]),
                )
                _log(f"bandpass({p['bp_low_mhz']}-{p['bp_high_mhz']}MHz)", out)
            continue

        if step == "envelope":
            if bool(p.get("envelope", False)):
                out = hilbert_envelope(out)
                _log("envelope(hilbert)", out)
            continue

    if normalize_output:
        out = normalize_display(out, clip_pct=float(p["clip_pct"]))
        _log("normalize (finale)", out)
    else:
        out = out.astype(np.float32, copy=False)
        _log("finale (no normalize)", out)
    return out
