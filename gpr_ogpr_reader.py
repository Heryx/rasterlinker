"""Parser per il formato OGPR (v1.0 int16 / v2.0 float32).

Struttura file:
  b'ogpr\r\n' | b'ogpr\n'  (magic, 5-6 byte)
  MD5 + EOL
  NNNNNNNN + EOL            (lunghezza JSON)
  { ... JSON ... }
  --- Radar Volume ---
  --- Sample Geolocations ---

  Layout per slice (auto-rilevato):
    [extra0..N]  n_channels x [east, north, alt, head, pitch, roll, sp, sp]
    oppure
    n_channels x [east, north, alt, head, pitch, roll, sp, sp]  [extra0..N]

  La posizione degli extra double (inizio o fine) è auto-rilevata
  verificando se l'easting risultante è plausibile (> 1000 m).
"""

from __future__ import annotations

import hashlib
import json
import mmap
import functools
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

GEO_DOUBLES_PER_CHANNEL = 8   # east, north, alt, heading, pitch, roll, spare x2
_UTM_MIN = 1_000.0            # soglia minima plausibile per coordinate proiettate (m)
_MAX_PLAUSIBILITY_SAMPLE = 250_000


# ---------------------------------------------------------------------------
# Helper: legge la prossima riga con EOL Unix o Windows
# ---------------------------------------------------------------------------

def _read_line(raw, pos: int) -> tuple[bytes, int]:
    end = pos
    while end < len(raw) and raw[end] not in (ord('\r'), ord('\n')):
        end += 1
    content = bytes(raw[pos:end])
    if end < len(raw) and raw[end] == ord('\r'):
        end += 1
    if end < len(raw) and raw[end] == ord('\n'):
        end += 1
    return content, end


def _md5_hexdigest_range(raw, start: int = 0, end: int | None = None, chunk_size: int = 4 * 1024 * 1024) -> str:
    """Calcola MD5 su [start:end) senza copiare grandi buffer in RAM."""
    total = len(raw)
    s = int(max(0, start))
    e = int(total if end is None else min(total, max(s, end)))
    h = hashlib.md5()
    while s < e:
        nxt = min(e, s + int(chunk_size))
        h.update(raw[s:nxt])
        s = nxt
    return h.hexdigest()


def _md5_hexdigest_multi_ranges(raw, ranges: list[tuple[int, int]], chunk_size: int = 4 * 1024 * 1024) -> str:
    """Calcola MD5 concatenando logicamente piu' range senza materializzare byte extra."""
    h = hashlib.md5()
    total = len(raw)
    for start, end in ranges:
        s = int(max(0, start))
        e = int(min(total, max(s, end)))
        while s < e:
            nxt = min(e, s + int(chunk_size))
            h.update(raw[s:nxt])
            s = nxt
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OgprChannel:
    channel_idx:  int
    data:         np.ndarray   # (n_samples, n_slices)  float32
    easting:      np.ndarray   # (n_slices,)  float64
    northing:     np.ndarray   # (n_slices,)  float64
    altitude:     np.ndarray   # (n_slices,)  float64
    heading:      np.ndarray   # (n_slices,)  float64
    distances:    np.ndarray   # (n_slices,)  distanza cumulativa (m)


@dataclass
class OgprProfile:
    path:              str
    version_major:     int
    version_minor:     int
    swath_name:        str
    swath_id:          str
    array_id:          int
    n_samples:         int
    n_channels:        int
    n_slices:          int
    sampling_step_m:   float
    sampling_time_ns:  float
    velocity_m_s:      float
    frequency_mhz:     float
    polarization:      str
    epsg:              int
    value_type:        str
    channels:          list
    header_raw:        dict
    value_type_raw:    str = ""
    raw_dtype:         str = ""
    byte_order:        str = "<"
    geo_raw_dtype:     str = ""
    geo_byte_order:    str = "<"
    md5_verified:      bool = False
    md5_scope:         str = ""
    parse_warnings:    list[str] = field(default_factory=list)

    @property
    def dt_ns(self) -> float:
        return self.sampling_time_ns

    @property
    def depth_max_m(self) -> float:
        v_m_ns = self.velocity_m_s / 1e9
        return self.n_samples * self.dt_ns * v_m_ns / 2.0

    @property
    def depth_axis(self) -> np.ndarray:
        v_m_ns = self.velocity_m_s / 1e9
        return np.arange(self.n_samples) * self.dt_ns * v_m_ns / 2.0

    def channel(self, idx: int) -> OgprChannel:
        return self.channels[idx]


class OgprReadError(Exception):
    pass


# ---------------------------------------------------------------------------
# Helper: value type and radar decoding
# ---------------------------------------------------------------------------

def _normalize_value_type(value_type_raw: str) -> tuple[str, np.dtype, list[str]]:
    txt = str(value_type_raw or "").strip().lower()
    key = txt.replace(" ", "").replace("_", "")
    warnings = []

    if key in {"float", "float32", "single", "f4"}:
        return "float32", np.dtype("f4"), warnings
    if key in {"double", "float64", "f8"}:
        return "float64", np.dtype("f8"), warnings
    if key in {"int16", "short", "signedshort", "i2"}:
        return "int16", np.dtype("i2"), warnings
    if key in {"int32", "integer32", "i4"}:
        return "int32", np.dtype("i4"), warnings

    warnings.append(
        f"valueType non riconosciuto '{value_type_raw}', uso fallback int16."
    )
    return "int16", np.dtype("i2"), warnings


def _radar_data_is_suspicious(arr: np.ndarray, kind: str) -> bool:
    if arr.size == 0:
        return True

    sample = arr
    if sample.size > _MAX_PLAUSIBILITY_SAMPLE:
        step = max(1, sample.size // _MAX_PLAUSIBILITY_SAMPLE)
        sample = sample[::step]

    finite = np.isfinite(sample)
    finite_ratio = float(finite.mean()) if finite.size else 0.0
    if finite_ratio < 0.999:
        return True
    sample = sample[finite]
    if sample.size == 0:
        return True

    if np.count_nonzero(sample) == 0:
        return True

    abs_sample = np.abs(sample.astype(np.float64))
    p99 = float(np.percentile(abs_sample, 99))
    if not np.isfinite(p99):
        return True
    if kind == "f" and p99 > 1e12:
        return True

    # Heuristic: dati int16 endian-swapped possono risultare quasi tutti multipli di 256.
    if kind in {"i", "u"} and sample.size >= 1024:
        as_int = sample.astype(np.int64, copy=False)
        mult256_ratio = float(np.mean(np.mod(as_int, 256) == 0))
        if mult256_ratio > 0.97:
            return True

    return False


def _decode_radar_volume(
    radar_raw: bytes,
    exp_count: int,
    value_type_raw: str,
) -> tuple[np.ndarray, str, str, str, list[str]]:
    value_type_norm, base_dtype, warnings = _normalize_value_type(value_type_raw)

    # Dichiara fallback più comuni, senza forzare override se il tipo dichiarato è valido.
    fallback_bases = [np.dtype("i2"), np.dtype("f4"), np.dtype("i4"), np.dtype("f8")]
    base_candidates = [base_dtype]
    for fb in fallback_bases:
        if fb != base_dtype:
            base_candidates.append(fb)

    candidates = []
    for base in base_candidates:
        for byte_order in ("<", ">"):
            dtype = base.newbyteorder(byte_order)
            arr = np.frombuffer(radar_raw, dtype=dtype)
            if arr.size != exp_count:
                continue
            arr_f32 = arr.astype(np.float32, copy=False)
            suspicious = _radar_data_is_suspicious(arr_f32, base.kind)
            candidates.append({
                "arr": arr_f32,
                "raw_dtype": dtype.str,
                "byte_order": byte_order,
                "base": base,
                "suspicious": suspicious,
            })

    if not candidates:
        raise OgprReadError(
            f"Radar: attesi {exp_count} valori, byteSize={len(radar_raw)} "
            f"(valueType='{value_type_raw}')."
        )

    # 1) preferisci il tipo dichiarato little-endian se plausibile
    for cand in candidates:
        if cand["base"] == base_dtype and cand["byte_order"] == "<" and not cand["suspicious"]:
            return cand["arr"], value_type_norm, cand["raw_dtype"], cand["byte_order"], warnings

    # 2) altrimenti prova tipo dichiarato big-endian se è l'unico plausibile
    little_declared = None
    big_declared = None
    for cand in candidates:
        if cand["base"] != base_dtype:
            continue
        if cand["byte_order"] == "<":
            little_declared = cand
        elif cand["byte_order"] == ">":
            big_declared = cand
    if little_declared and big_declared and little_declared["suspicious"] and not big_declared["suspicious"]:
        warnings.append(
            f"Endian fallback applicato per Radar Volume: valueType='{value_type_raw}' letto come big-endian."
        )
        return (
            big_declared["arr"],
            value_type_norm,
            big_declared["raw_dtype"],
            big_declared["byte_order"],
            warnings,
        )

    # 3) usa il primo candidato non sospetto (fallback su tipo alternativo)
    for cand in candidates:
        if not cand["suspicious"]:
            if cand["base"] != base_dtype:
                warnings.append(
                    f"valueType='{value_type_raw}' non coerente; usato fallback dtype {cand['raw_dtype']}."
                )
            return cand["arr"], value_type_norm, cand["raw_dtype"], cand["byte_order"], warnings

    # 4) ultimo fallback: tipo dichiarato little-endian (comportamento storico)
    if little_declared is not None:
        # Se little e big sono entrambi "sospetti", il check e' inconclusivo:
        # evita warning allarmistici e usa il tipo dichiarato.
        if not (big_declared is not None and bool(big_declared["suspicious"])):
            warnings.append(
                f"Controllo plausibilita' radar inconclusivo; mantenuta lettura dichiarata {little_declared['raw_dtype']}."
            )
        return (
            little_declared["arr"],
            value_type_norm,
            little_declared["raw_dtype"],
            little_declared["byte_order"],
            warnings,
        )

    first = candidates[0]
    warnings.append(
        f"Dati radar plausibilita' bassa; usato fallback dtype {first['raw_dtype']}."
    )
    return first["arr"], value_type_norm, first["raw_dtype"], first["byte_order"], warnings


def _is_valid_md5_hex(txt: str) -> bool:
    if len(txt) != 32:
        return False
    hexdigits = set("0123456789abcdefABCDEF")
    return all(ch in hexdigits for ch in txt)


def _geo_data_score(geo_ch: np.ndarray) -> float:
    if geo_ch.size == 0:
        return -1e9
    east = geo_ch[:, 0, 0].astype(np.float64, copy=False)
    north = geo_ch[:, 0, 1].astype(np.float64, copy=False)
    finite_mask = np.isfinite(east) & np.isfinite(north)
    if not finite_mask.any():
        return -1e9

    east = east[finite_mask]
    north = north[finite_mask]
    if east.size == 0:
        return -1e9

    mean_abs_e = float(np.mean(np.abs(east)))
    mean_abs_n = float(np.mean(np.abs(north)))
    std_e = float(np.std(east))
    std_n = float(np.std(north))

    score = 8.0 * float(finite_mask.mean())
    if mean_abs_e >= _UTM_MIN:
        score += 2.5
    if mean_abs_n >= _UTM_MIN:
        score += 2.5
    if std_e + std_n > 1e-6:
        score += 1.0
    if mean_abs_e < 1.0 and mean_abs_n < 1.0:
        score -= 2.0
    if mean_abs_e > 1e9 or mean_abs_n > 1e9:
        score -= 3.0

    if east.size > 2:
        de = np.diff(east)
        dn = np.diff(north)
        step95 = float(np.percentile(np.sqrt(de ** 2 + dn ** 2), 95))
        if step95 > 1e6:
            score -= 2.0
    return score


def _decode_geo_volume(
    geo_raw: bytes,
    n_slices: int,
    n_channels: int,
    extra_doubles: int,
    dps: int,
) -> tuple[np.ndarray, str, str, list[str]]:
    expected_count = n_slices * dps
    warnings_list = []
    candidates = []
    for byte_order in ("<", ">"):
        dtype = np.dtype("f8").newbyteorder(byte_order)
        arr = np.frombuffer(geo_raw, dtype=dtype)
        if arr.size != expected_count:
            continue
        geo_array = arr.reshape((n_slices, dps))
        geo_ch = _extract_geo_channels(geo_array, n_slices, n_channels, extra_doubles)
        score = _geo_data_score(geo_ch)
        candidates.append({
            "geo_ch": geo_ch,
            "raw_dtype": dtype.str,
            "byte_order": byte_order,
            "score": score,
            "suspicious": score < 8.0,
        })

    if not candidates:
        raise OgprReadError(
            f"Sample Geolocations: attesi {expected_count} double, trovati {len(geo_raw) // 8}."
        )

    little = next((c for c in candidates if c["byte_order"] == "<"), None)
    big = next((c for c in candidates if c["byte_order"] == ">"), None)

    if little and not little["suspicious"]:
        return little["geo_ch"], little["raw_dtype"], little["byte_order"], warnings_list

    if little and big and little["suspicious"] and not big["suspicious"]:
        warnings_list.append(
            "Endian fallback applicato per Sample Geolocations: letto come big-endian."
        )
        return big["geo_ch"], big["raw_dtype"], big["byte_order"], warnings_list

    best = max(candidates, key=lambda c: c["score"])
    if best["byte_order"] == ">" and (little is None or best["score"] > little["score"] + 0.5):
        warnings_list.append(
            "Sample Geolocations decodificato come big-endian (plausibilita' coordinate superiore)."
        )

    if best["suspicious"]:
        warnings_list.append(
            f"Sample Geolocations con plausibilita' bassa (score={best['score']:.2f}); "
            f"usato dtype {best['raw_dtype']}."
        )

    return best["geo_ch"], best["raw_dtype"], best["byte_order"], warnings_list


# ---------------------------------------------------------------------------
# Helper: layout geolocations
# ---------------------------------------------------------------------------

def _parse_geo_layout(g_bytesize: int, n_slices: int, n_channels: int) -> int:
    """Ritorna numero di double extra per slice (0 = formato standard)."""
    if n_slices == 0:
        return 0
    if g_bytesize % n_slices != 0:
        raise OgprReadError(
            f"Geo byteSize={g_bytesize} non divisibile per n_slices={n_slices}"
        )
    bps = g_bytesize // n_slices
    if bps % 8 != 0:
        raise OgprReadError(f"Geo bytes/slice={bps} non multiplo di 8")
    extra = bps // 8 - n_channels * GEO_DOUBLES_PER_CHANNEL
    if extra < 0:
        raise OgprReadError(
            f"Geo block troppo piccolo: servono "
            f"{n_channels * GEO_DOUBLES_PER_CHANNEL} double/slice, trovati {bps // 8}"
        )
    return extra


def _extract_geo_channels(
    geo_array: np.ndarray,
    n_slices: int,
    n_channels: int,
    extra_doubles: int,
) -> np.ndarray:
    """
    Estrae la matrice canali (n_slices, n_channels, 8) dal blocco geo.

    Strategia:
      1. Prova extra all'INIZIO  (offset = extra_doubles)
      2. Prova extra alla FINE   (offset = 0)
      Sceglie la versione con score di plausibilita' piu' alto.
    """
    ch_len = n_channels * GEO_DOUBLES_PER_CHANNEL

    def _try(offset: int) -> np.ndarray:
        return geo_array[:, offset:offset + ch_len].reshape(
            (n_slices, n_channels, GEO_DOUBLES_PER_CHANNEL)
        )

    if extra_doubles == 0:
        return _try(0)

    # Testa entrambe le disposizioni e usa il layout più plausibile.
    geo_extra_start = _try(extra_doubles)
    geo_extra_end = _try(0)
    score_start = _geo_data_score(geo_extra_start)
    score_end = _geo_data_score(geo_extra_end)
    if score_start >= score_end:
        return geo_extra_start
    return geo_extra_end


# ---------------------------------------------------------------------------
# Reader principale
# ---------------------------------------------------------------------------

def read_ogpr(path: str, verify_md5: bool = False) -> OgprProfile:
    """
    Legge un file .ogpr (v1.0 o v2.0).
    Gestisce Unix/Windows EOL e extra-doubles nel blocco geolocations.
    """
    p = Path(path)
    if not p.exists():
        raise OgprReadError(f"File non trovato: {path}")

    file_size = int(p.stat().st_size)
    if file_size <= 0:
        raise OgprReadError(f"File vuoto o non leggibile: {path}")

    parse_warnings: list[str] = []

    with open(p, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as raw_mm:
        # Usa mmap direttamente (senza memoryview) per evitare BufferError in chiusura:
        # "cannot close exported pointers exist" quando restano riferimenti da np.frombuffer.
        raw = raw_mm
        try:
            # Magic
            if raw[:6] == b"ogpr\r\n":
                pos = 6
            elif raw[:5] == b"ogpr\n":
                pos = 5
            else:
                raise OgprReadError(f"Magic non valido: {bytes(raw[:8])!r}")
            pos_after_magic = pos

            md5_line, pos = _read_line(raw, pos)
            pos_after_md5_line = pos
            try:
                md5_stored = md5_line.decode("ascii").strip()
            except Exception as exc:
                raise OgprReadError(f"Riga MD5 non ASCII o corrotta: {exc}") from exc
            if not _is_valid_md5_hex(md5_stored):
                msg = (
                    f"MD5 header non valido ('{md5_stored}'). "
                    "Possibile file OGPR corrotto/troncato."
                )
                if verify_md5:
                    raise OgprReadError(msg)
                parse_warnings.append(msg)

            len_line, pos = _read_line(raw, pos)
            pos_after_len_line = pos
            try:
                json_len = int(len_line.decode("ascii").strip())
            except Exception as exc:
                raise OgprReadError(f"Lunghezza JSON non valida: {exc}") from exc
            if json_len <= 0:
                raise OgprReadError(f"Lunghezza JSON non valida: {json_len}")
            if pos + json_len > file_size:
                raise OgprReadError(
                    f"Header JSON troncato: attesi {json_len} byte, disponibili {file_size - pos}."
                )

            json_bytes = bytes(raw[pos: pos + json_len])
            try:
                hdr = json.loads(json_bytes.decode("utf-8"))
            except Exception as exc:
                raise OgprReadError(f"Header JSON non parseabile: {exc}") from exc
            pos += json_len

            # Metadati
            md         = hdr["mainDescriptor"]
            md_meta    = md.get("metadata") if isinstance(md.get("metadata"), dict) else {}
            n_samples  = int(md["samplesCount"])
            n_channels = int(md["channelsCount"])
            n_slices   = int(md["slicesCount"])
            swath_name = md_meta.get("swathName", "")
            swath_id   = md_meta.get("swathId",   "")
            array_id   = int(md_meta.get("arrayId", 0))
            v_major    = int(hdr["version"]["major"])
            v_minor    = int(hdr["version"]["minor"])

            radar_desc = None
            geo_desc   = None
            for blk in hdr.get("dataBlockDescriptors", []):
                t = blk.get("type", "")
                if t == "Radar Volume":
                    radar_desc = blk
                elif t == "Sample Geolocations":
                    geo_desc = blk

            if radar_desc is None:
                raise OgprReadError("'Radar Volume' non trovato")
            if geo_desc is None:
                raise OgprReadError("'Sample Geolocations' non trovato")

            radar_info    = radar_desc["radar"]
            sampling_step = float(radar_info["samplingStep_m"])
            sampling_time = float(radar_info["samplingTime_ns"])
            velocity      = float(radar_info["propagationVelocity_mPerSec"])
            frequency     = float(radar_info.get("fequency_MHz",
                                   radar_info.get("frequency_MHz", 600.0)))
            polarization  = str(radar_info.get("polarization", "horizontal"))
            value_type_raw = str(radar_desc.get("valueType", "int16"))
            epsg          = int(geo_desc.get("srs", {}).get("value", 32633))

            # Radar Volume
            r_offset = int(radar_desc["byteOffset"])
            r_bytesize = int(radar_desc["byteSize"])
            if r_offset < 0 or r_bytesize <= 0 or (r_offset + r_bytesize) > file_size:
                raise OgprReadError(
                    f"Radar Volume fuori range: offset={r_offset}, size={r_bytesize}, file={file_size}."
                )
            radar_raw = raw[r_offset: r_offset + r_bytesize]

            exp = n_samples * n_channels * n_slices
            radar_flat, value_type, raw_dtype, byte_order, radar_warnings = _decode_radar_volume(
                radar_raw=radar_raw,
                exp_count=exp,
                value_type_raw=value_type_raw,
            )
            parse_warnings.extend(radar_warnings)
            radar_3d = radar_flat.reshape((n_slices, n_channels, n_samples))

            # Sample Geolocations
            g_offset = int(geo_desc["byteOffset"])
            g_bytesize = int(geo_desc["byteSize"])
            if g_offset < 0 or g_bytesize <= 0 or (g_offset + g_bytesize) > file_size:
                raise OgprReadError(
                    f"Sample Geolocations fuori range: offset={g_offset}, size={g_bytesize}, file={file_size}."
                )
            geo_raw = raw[g_offset: g_offset + g_bytesize]

            md5_verified = False
            md5_scope = ""
            if _is_valid_md5_hex(md5_stored) and verify_md5:
                md5_candidates: dict[str, str] = {}

                ordered_checks = [
                    ("payload_after_json", lambda: _md5_hexdigest_range(raw, start=pos, end=file_size)),
                    ("full_file", lambda: _md5_hexdigest_range(raw, start=0, end=file_size)),
                    ("after_magic", lambda: _md5_hexdigest_range(raw, start=pos_after_magic, end=file_size)),
                    ("after_md5_line", lambda: _md5_hexdigest_range(raw, start=pos_after_md5_line, end=file_size)),
                    ("after_len_line", lambda: _md5_hexdigest_range(raw, start=pos_after_len_line, end=file_size)),
                    ("radar_volume", lambda: _md5_hexdigest_range(raw, start=r_offset, end=r_offset + r_bytesize)),
                    ("sample_geolocations", lambda: _md5_hexdigest_range(raw, start=g_offset, end=g_offset + g_bytesize)),
                    (
                        "radar_plus_geo",
                        lambda: _md5_hexdigest_multi_ranges(
                            raw,
                            [
                                (r_offset, r_offset + r_bytesize),
                                (g_offset, g_offset + g_bytesize),
                            ],
                        ),
                    ),
                    (
                        "geo_plus_radar",
                        lambda: _md5_hexdigest_multi_ranges(
                            raw,
                            [
                                (g_offset, g_offset + g_bytesize),
                                (r_offset, r_offset + r_bytesize),
                            ],
                        ),
                    ),
                ]

                for scope, fn in ordered_checks:
                    try:
                        digest = fn()
                    except Exception:
                        continue
                    md5_candidates[scope] = digest
                    if digest == md5_stored:
                        md5_verified = True
                        md5_scope = scope
                        break

                if not md5_verified:
                    block_spans = []
                    for blk in hdr.get("dataBlockDescriptors", []):
                        try:
                            bo = int(blk.get("byteOffset"))
                            bs = int(blk.get("byteSize"))
                        except Exception:
                            continue
                        if bo < 0 or bs <= 0 or (bo + bs) > file_size:
                            continue
                        block_spans.append((bo, bo + bs))
                    if block_spans:
                        block_spans.sort(key=lambda t: t[0])
                        digest_blocks = _md5_hexdigest_multi_ranges(raw, block_spans)
                        md5_candidates["all_blocks_sorted"] = digest_blocks
                        if digest_blocks == md5_stored:
                            md5_verified = True
                            md5_scope = "all_blocks_sorted"

                if md5_verified:
                    if md5_scope != "payload_after_json":
                        parse_warnings.append(
                            f"MD5 verificato con scope '{md5_scope}' (non payload_after_json)."
                        )
                else:
                    short = ", ".join(
                        f"{k}={v}" for k, v in md5_candidates.items() if k in {
                            "payload_after_json",
                            "full_file",
                            "after_md5_line",
                            "radar_volume",
                            "sample_geolocations",
                        }
                    )
                    msg = (
                        f"MD5 mismatch: stored={md5_stored}. "
                        f"Candidati principali: {short}."
                    )
                    raise OgprReadError(
                        msg + " Il file potrebbe essere corrotto/troncato oppure usare uno schema MD5 non standard."
                    )

            extra_doubles = _parse_geo_layout(g_bytesize, n_slices, n_channels)
            dps = n_channels * GEO_DOUBLES_PER_CHANNEL + extra_doubles

            geo_ch, geo_raw_dtype, geo_byte_order, geo_warnings = _decode_geo_volume(
                geo_raw=geo_raw,
                n_slices=n_slices,
                n_channels=n_channels,
                extra_doubles=extra_doubles,
                dps=dps,
            )
            parse_warnings.extend(geo_warnings)

            # Costruisci canali
            channels = []
            for ch_i in range(n_channels):
                data_ch = radar_3d[:, ch_i, :].T.copy()   # (n_samples, n_slices)
                east_ch = geo_ch[:, ch_i, 0].copy()
                north_ch = geo_ch[:, ch_i, 1].copy()
                alt_ch = geo_ch[:, ch_i, 2].copy()
                head_ch = geo_ch[:, ch_i, 3].copy()

                # Sanitize coordinate arrays to avoid NaN/Inf propagation in distance axis.
                finite_xy = np.isfinite(east_ch) & np.isfinite(north_ch)
                if not np.all(finite_xy):
                    if finite_xy.any():
                        idx = np.arange(n_slices, dtype=np.float64)
                        idx_ok = np.where(finite_xy)[0].astype(np.float64)
                        east_ch = np.interp(idx, idx_ok, east_ch[finite_xy]).astype(np.float64)
                        north_ch = np.interp(idx, idx_ok, north_ch[finite_xy]).astype(np.float64)
                    else:
                        east_ch = np.zeros(n_slices, dtype=np.float64)
                        north_ch = np.zeros(n_slices, dtype=np.float64)
                    parse_warnings.append(
                        f"Coordinate non finite nel canale {ch_i}: applicato fallback/interpolazione per asse distanza."
                    )

                dx = np.diff(east_ch, prepend=east_ch[0])
                dy = np.diff(north_ch, prepend=north_ch[0])
                dist = np.cumsum(np.sqrt(dx ** 2 + dy ** 2))

                # Fallback: se le coordinate sono zero o costanti usa sampling_step_m
                if (not np.isfinite(dist).all()) or (dist[-1] < 1e-3):
                    dist = np.arange(n_slices, dtype=np.float64) * sampling_step

                channels.append(OgprChannel(
                    channel_idx = ch_i,
                    data        = data_ch,
                    easting     = east_ch,
                    northing    = north_ch,
                    altitude    = alt_ch,
                    heading     = head_ch,
                    distances   = dist,
                ))

            # Rilascia esplicitamente i riferimenti voluminosi prima di uscire dal contesto mmap.
            del radar_3d, radar_flat, radar_raw, geo_raw, geo_ch
        finally:
            try:
                rel = getattr(raw, "release", None)
                if callable(rel):
                    rel()
            except Exception:
                pass

    for msg in parse_warnings:
        warnings.warn(f"[OGPR] {p.name}: {msg}", RuntimeWarning)

    return OgprProfile(
        path             = str(path),
        version_major    = v_major,
        version_minor    = v_minor,
        swath_name       = swath_name,
        swath_id         = swath_id,
        array_id         = array_id,
        n_samples        = n_samples,
        n_channels       = n_channels,
        n_slices         = n_slices,
        sampling_step_m  = sampling_step,
        sampling_time_ns = sampling_time,
        velocity_m_s     = velocity,
        frequency_mhz    = frequency,
        polarization     = polarization,
        epsg             = epsg,
        value_type       = value_type,
        channels         = channels,
        header_raw       = hdr,
        value_type_raw   = value_type_raw,
        raw_dtype        = raw_dtype,
        byte_order       = byte_order,
        geo_raw_dtype    = geo_raw_dtype,
        geo_byte_order   = geo_byte_order,
        md5_verified     = md5_verified,
        md5_scope        = md5_scope,
        parse_warnings   = parse_warnings,
    )


@functools.lru_cache(maxsize=32)
def _read_ogpr_cached_impl(path: str, mtime_ns: int, size: int, verify_md5: bool) -> OgprProfile:
    _ = (mtime_ns, size)  # parte della chiave cache per invalidazione su file modificato
    return read_ogpr(path, verify_md5=verify_md5)


def read_ogpr_cached(path: str, verify_md5: bool = False) -> OgprProfile:
    p = Path(path).expanduser().resolve()
    st = p.stat()
    mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
    size = int(st.st_size)
    return _read_ogpr_cached_impl(str(p), mtime_ns, size, bool(verify_md5))


def clear_ogpr_cache() -> None:
    _read_ogpr_cached_impl.cache_clear()
