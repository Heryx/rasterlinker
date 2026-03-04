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
from dataclasses import dataclass
from pathlib import Path

import numpy as np

GEO_DOUBLES_PER_CHANNEL = 8   # east, north, alt, heading, pitch, roll, spare x2
_UTM_MIN = 1_000.0            # soglia minima plausibile per coordinate proiettate (m)


# ---------------------------------------------------------------------------
# Helper: legge la prossima riga con EOL Unix o Windows
# ---------------------------------------------------------------------------

def _read_line(raw: bytes, pos: int) -> tuple[bytes, int]:
    end = pos
    while end < len(raw) and raw[end] not in (ord('\r'), ord('\n')):
        end += 1
    content = raw[pos:end]
    if end < len(raw) and raw[end] == ord('\r'):
        end += 1
    if end < len(raw) and raw[end] == ord('\n'):
        end += 1
    return content, end


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
      Sceglie la versione in cui l'easting del canale 0 è plausibile (> _UTM_MIN).
      Se nessuna delle due è plausibile usa offset=0 come fallback silenzioso.
    """
    ch_len = n_channels * GEO_DOUBLES_PER_CHANNEL

    def _try(offset: int) -> np.ndarray:
        return geo_array[:, offset:offset + ch_len].reshape(
            (n_slices, n_channels, GEO_DOUBLES_PER_CHANNEL)
        )

    if extra_doubles == 0:
        return _try(0)

    # Testa offset=extra (extra all'inizio)
    geo_extra_start = _try(extra_doubles)
    east_start = np.abs(geo_extra_start[:, 0, 0]).mean()

    # Testa offset=0 (extra alla fine)
    geo_extra_end = _try(0)
    east_end = np.abs(geo_extra_end[:, 0, 0]).mean()

    # Scegli la versione con easting più plausibile
    if east_start >= _UTM_MIN and east_start > east_end:
        return geo_extra_start
    if east_end >= _UTM_MIN:
        return geo_extra_end
    # Fallback: extra all'inizio (il più comune dai file testati)
    return geo_extra_start


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

    with open(p, "rb") as f:
        raw = f.read()

    # Magic
    if raw[:6] == b"ogpr\r\n":
        pos = 6
    elif raw[:5] == b"ogpr\n":
        pos = 5
    else:
        raise OgprReadError(f"Magic non valido: {raw[:8]!r}")

    md5_line, pos  = _read_line(raw, pos)
    md5_stored     = md5_line.decode("ascii").strip()
    len_line, pos  = _read_line(raw, pos)
    json_len       = int(len_line.decode("ascii").strip())
    json_bytes     = raw[pos: pos + json_len]
    hdr            = json.loads(json_bytes.decode("utf-8"))
    pos           += json_len

    if verify_md5:
        computed = hashlib.md5(raw[pos:]).hexdigest()
        if computed != md5_stored:
            raise OgprReadError(f"MD5 non valido (stored={md5_stored}, computed={computed})")

    # Metadati
    md         = hdr["mainDescriptor"]
    n_samples  = int(md["samplesCount"])
    n_channels = int(md["channelsCount"])
    n_slices   = int(md["slicesCount"])
    swath_name = md["metadata"].get("swathName", "")
    swath_id   = md["metadata"].get("swathId",   "")
    array_id   = int(md["metadata"].get("arrayId", 0))
    v_major    = int(hdr["version"]["major"])
    v_minor    = int(hdr["version"]["minor"])

    radar_desc = None
    geo_desc   = None
    for blk in hdr.get("dataBlockDescriptors", []):
        t = blk.get("type", "")
        if t == "Radar Volume":         radar_desc = blk
        elif t == "Sample Geolocations": geo_desc   = blk

    if radar_desc is None: raise OgprReadError("'Radar Volume' non trovato")
    if geo_desc   is None: raise OgprReadError("'Sample Geolocations' non trovato")

    radar_info    = radar_desc["radar"]
    sampling_step = float(radar_info["samplingStep_m"])
    sampling_time = float(radar_info["samplingTime_ns"])
    velocity      = float(radar_info["propagationVelocity_mPerSec"])
    frequency     = float(radar_info.get("fequency_MHz",
                           radar_info.get("frequency_MHz", 600.0)))
    polarization  = str(radar_info.get("polarization", "horizontal"))
    value_type    = str(radar_desc.get("valueType", "int16"))
    epsg          = int(geo_desc.get("srs", {}).get("value", 32633))

    # Radar Volume
    r_offset   = int(radar_desc["byteOffset"])
    r_bytesize = int(radar_desc["byteSize"])
    radar_raw  = raw[r_offset: r_offset + r_bytesize]

    exp = n_samples * n_channels * n_slices
    if value_type == "float":
        radar_flat = np.frombuffer(radar_raw, dtype="<f4")
    else:
        radar_flat = np.frombuffer(radar_raw, dtype="<i2").astype(np.float32)

    if radar_flat.size != exp:
        raise OgprReadError(
            f"Radar: attesi {exp} valori, trovati {radar_flat.size}"
        )
    radar_3d = radar_flat.reshape((n_slices, n_channels, n_samples))

    # Sample Geolocations
    g_offset      = int(geo_desc["byteOffset"])
    g_bytesize    = int(geo_desc["byteSize"])
    geo_raw       = raw[g_offset: g_offset + g_bytesize]
    extra_doubles = _parse_geo_layout(g_bytesize, n_slices, n_channels)
    dps           = n_channels * GEO_DOUBLES_PER_CHANNEL + extra_doubles

    geo_array = np.frombuffer(geo_raw, dtype="<f8").reshape((n_slices, dps))
    geo_ch    = _extract_geo_channels(geo_array, n_slices, n_channels, extra_doubles)

    # Costruisci canali
    channels = []
    for ch_i in range(n_channels):
        data_ch  = radar_3d[:, ch_i, :].T.copy()   # (n_samples, n_slices)
        east_ch  = geo_ch[:, ch_i, 0].copy()
        north_ch = geo_ch[:, ch_i, 1].copy()
        alt_ch   = geo_ch[:, ch_i, 2].copy()
        head_ch  = geo_ch[:, ch_i, 3].copy()

        dx   = np.diff(east_ch,  prepend=east_ch[0])
        dy   = np.diff(north_ch, prepend=north_ch[0])
        dist = np.cumsum(np.sqrt(dx ** 2 + dy ** 2))

        # Fallback: se le coordinate sono zero o costanti usa sampling_step_m
        if dist[-1] < 1e-3:
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
    )
