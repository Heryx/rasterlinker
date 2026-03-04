"""Parser per il formato OGPR (v1.0 int16 / v2.0 float32).

Struttura file:
  b'ogpr\r\n' | b'ogpr\n'  (magic, 5-6 byte, sia Unix che Windows EOL)
  MD5 + EOL                 (32 byte hex + EOL)
  NNNNNNNN + EOL            (lunghezza JSON, decimale + EOL)
  { ... JSON ... }          (NNNNNNNN byte)
  --- Radar Volume ---       (float32 o int16)
  --- Sample Geolocations -- (float64 x 8 per canale per slice)

Geolocations layout per slice:
  Per ogni canale (channelsCount volte):
    8 x float64 = 64 byte:
      [0] Easting   (m)
      [1] Northing  (m)
      [2] Altitude  (m)
      [3] Heading   (rad)
      [4] Pitch     (rad)
      [5] Roll      (rad)
      [6] spare
      [7] spare
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

GEO_DOUBLES_PER_CHANNEL = 8
GEO_BYTES_PER_CHANNEL   = GEO_DOUBLES_PER_CHANNEL * 8  # 64

_MAGIC_BASE = b"ogpr"


# ---------------------------------------------------------------------------
# Helper: legge la prossima riga terminata da \r\n o \n
# ---------------------------------------------------------------------------

def _read_line(raw: bytes, pos: int) -> tuple[bytes, int]:
    """
    Legge bytes da `pos` fino al prossimo \r\n o \n (incluso).
    Restituisce (contenuto_senza_eol, nuova_posizione).
    """
    end = pos
    while end < len(raw) and raw[end] not in (ord('\r'), ord('\n')):
        end += 1
    content = raw[pos:end]
    # consuma \r\n o solo \n
    if end < len(raw) and raw[end] == ord('\r'):
        end += 1  # salta \r
    if end < len(raw) and raw[end] == ord('\n'):
        end += 1  # salta \n
    return content, end


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OgprChannel:
    """Un singolo canale (antenna) con i propri dati e posizioni."""
    channel_idx:  int
    data:         np.ndarray   # shape (n_samples, n_slices)  float32
    easting:      np.ndarray   # shape (n_slices,)            float64
    northing:     np.ndarray   # shape (n_slices,)            float64
    altitude:     np.ndarray   # shape (n_slices,)            float64
    heading:      np.ndarray   # shape (n_slices,)            float64
    distances:    np.ndarray   # shape (n_slices,)  distanza cumulativa (m)


@dataclass
class OgprProfile:
    """Profilo GPR completo letto da un file .ogpr."""
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
    value_type:        str       # 'float' | 'int16'
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


# ---------------------------------------------------------------------------
# Eccezione
# ---------------------------------------------------------------------------

class OgprReadError(Exception):
    pass


# ---------------------------------------------------------------------------
# Reader principale
# ---------------------------------------------------------------------------

def read_ogpr(path: str, verify_md5: bool = False) -> OgprProfile:
    """
    Legge un file .ogpr (v1.0 o v2.0) e restituisce un OgprProfile.
    Accetta sia line ending Unix (\n) che Windows (\r\n).
    """
    p = Path(path)
    if not p.exists():
        raise OgprReadError(f"File non trovato: {path}")

    with open(p, "rb") as f:
        raw = f.read()

    # --- Magic: accetta ogpr\r\n o ogpr\n ---
    if raw[:6] == b"ogpr\r\n":
        pos = 6
    elif raw[:5] == b"ogpr\n":
        pos = 5
    else:
        got = raw[:8]
        raise OgprReadError(f"Magic non valido: {got!r}  (atteso b'ogpr\\n' o b'ogpr\\r\\n')")

    # --- MD5 ---
    md5_line, pos = _read_line(raw, pos)
    md5_stored = md5_line.decode("ascii").strip()

    # --- JSON length ---
    len_line, pos = _read_line(raw, pos)
    json_len = int(len_line.decode("ascii").strip())

    # --- JSON header ---
    json_bytes = raw[pos: pos + json_len]
    hdr = json.loads(json_bytes.decode("utf-8"))
    pos += json_len

    # --- MD5 opzionale ---
    if verify_md5:
        computed = hashlib.md5(raw[pos:]).hexdigest()
        if computed != md5_stored:
            raise OgprReadError(
                f"MD5 non valido (stored={md5_stored}, computed={computed})"
            )

    # --- Metadati header ---
    md         = hdr["mainDescriptor"]
    n_samples  = int(md["samplesCount"])
    n_channels = int(md["channelsCount"])
    n_slices   = int(md["slicesCount"])
    swath_name = md["metadata"].get("swathName", "")
    swath_id   = md["metadata"].get("swathId",   "")
    array_id   = int(md["metadata"].get("arrayId", 0))
    v_major    = int(hdr["version"]["major"])
    v_minor    = int(hdr["version"]["minor"])

    # --- Data block descriptors ---
    radar_desc = None
    geo_desc   = None
    for blk in hdr.get("dataBlockDescriptors", []):
        t = blk.get("type", "")
        if t == "Radar Volume":
            radar_desc = blk
        elif t == "Sample Geolocations":
            geo_desc = blk

    if radar_desc is None:
        raise OgprReadError("dataBlock 'Radar Volume' non trovato nell'header")
    if geo_desc is None:
        raise OgprReadError("dataBlock 'Sample Geolocations' non trovato nell'header")

    radar_info    = radar_desc["radar"]
    sampling_step = float(radar_info["samplingStep_m"])
    sampling_time = float(radar_info["samplingTime_ns"])
    velocity      = float(radar_info["propagationVelocity_mPerSec"])
    frequency     = float(radar_info.get("fequency_MHz",
                           radar_info.get("frequency_MHz", 600.0)))
    polarization  = str(radar_info.get("polarization", "horizontal"))
    value_type    = str(radar_desc.get("valueType", "int16"))
    epsg          = int(geo_desc.get("srs", {}).get("value", 32633))

    # --- Radar Volume ---
    r_offset   = int(radar_desc["byteOffset"])
    r_bytesize = int(radar_desc["byteSize"])
    radar_raw  = raw[r_offset: r_offset + r_bytesize]

    expected_floats = n_samples * n_channels * n_slices
    if value_type == "float":
        radar_flat = np.frombuffer(radar_raw, dtype="<f4")
    else:
        radar_flat = np.frombuffer(radar_raw, dtype="<i2").astype(np.float32)

    if radar_flat.size != expected_floats:
        raise OgprReadError(
            f"Dimensione dati radar inattesa: "
            f"attesi {expected_floats}, trovati {radar_flat.size}"
        )

    radar_3d = radar_flat.reshape((n_slices, n_channels, n_samples))

    # --- Sample Geolocations ---
    g_offset   = int(geo_desc["byteOffset"])
    g_bytesize = int(geo_desc["byteSize"])
    geo_raw    = raw[g_offset: g_offset + g_bytesize]

    expected_geo_bytes = n_slices * n_channels * GEO_BYTES_PER_CHANNEL
    if len(geo_raw) != expected_geo_bytes:
        raise OgprReadError(
            f"Dimensione geolocations inattesa: "
            f"attesi {expected_geo_bytes}, trovati {len(geo_raw)}"
        )

    geo_flat = np.frombuffer(geo_raw, dtype="<f8").reshape(
        (n_slices, n_channels, GEO_DOUBLES_PER_CHANNEL)
    )

    # --- Costruisci canali ---
    channels = []
    for ch_i in range(n_channels):
        data_ch  = radar_3d[:, ch_i, :].T.copy()   # (n_samples, n_slices)
        east_ch  = geo_flat[:, ch_i, 0].copy()
        north_ch = geo_flat[:, ch_i, 1].copy()
        alt_ch   = geo_flat[:, ch_i, 2].copy()
        head_ch  = geo_flat[:, ch_i, 3].copy()

        dx   = np.diff(east_ch,  prepend=east_ch[0])
        dy   = np.diff(north_ch, prepend=north_ch[0])
        dist = np.cumsum(np.sqrt(dx ** 2 + dy ** 2))

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
