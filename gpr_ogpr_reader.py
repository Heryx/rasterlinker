"""Parser per il formato OGPR (v1.0 int16 / v2.0 float32).

Struttura file:
  b'ogpr\r\n'           (magic, 6 byte)
  MD5 + b'\r\n'         (34 byte)
  NNNNNNNN + b'\r\n'    (lunghezza JSON, 10 byte decimali)
  { ... JSON ... }      (NNNNNNNN byte)
  --- Radar Volume ---   (float32 o int16)
  --- Sample Geolocations ---  (float64 x 8 per canale per slice)

Geolocations layout per slice:
  Per ogni canale (channelsCount volte):
    8 x float64 = 64 byte:
      [0] Easting   (m, EPSG come da header)
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
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

MAGIC_V1 = b"ogpr\r\n"
MAGIC_V2 = b"ogpr\r\n"
GEO_DOUBLES_PER_CHANNEL = 8
GEO_BYTES_PER_CHANNEL   = GEO_DOUBLES_PER_CHANNEL * 8  # 64


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
    sampling_step_m:   float     # passo spaziale (m)
    sampling_time_ns:  float     # passo temporale (ns)
    velocity_m_s:      float     # velocità EM (m/s)
    frequency_mhz:     float
    polarization:      str
    epsg:              int
    value_type:        str       # 'float' | 'int16'
    channels:          list      # List[OgprChannel]
    header_raw:        dict      # JSON originale

    @property
    def dt_ns(self) -> float:
        return self.sampling_time_ns

    @property
    def depth_max_m(self) -> float:
        """Profondità massima in metri (one-way * 0.5)."""
        v_m_ns = self.velocity_m_s / 1e9
        return self.n_samples * self.dt_ns * v_m_ns / 2.0

    @property
    def depth_axis(self) -> np.ndarray:
        """Array di profondità (m) per ogni campione."""
        v_m_ns = self.velocity_m_s / 1e9
        return np.arange(self.n_samples) * self.dt_ns * v_m_ns / 2.0

    def channel(self, idx: int) -> OgprChannel:
        return self.channels[idx]


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

class OgprReadError(Exception):
    pass


def read_ogpr(path: str, verify_md5: bool = False) -> OgprProfile:
    """
    Legge un file .ogpr (v1.0 o v2.0) e restituisce un OgprProfile.

    Parameters
    ----------
    path       : percorso al file .ogpr
    verify_md5 : se True controlla l'integrità MD5 (lento su file grandi)
    """
    p = Path(path)
    if not p.exists():
        raise OgprReadError(f"File non trovato: {path}")

    with open(p, "rb") as f:
        raw = f.read()

    # --- magic ---
    magic = raw[:6]
    if magic != b"ogpr\r\n":
        raise OgprReadError(f"Magic non valido: {magic!r}")

    pos = 6

    # --- MD5 ---
    eol = raw.index(b"\r\n", pos)
    md5_stored = raw[pos:eol].decode("ascii").strip()
    pos = eol + 2

    # --- JSON length ---
    eol2 = raw.index(b"\r\n", pos)
    json_len_str = raw[pos:eol2].decode("ascii").strip()
    json_len = int(json_len_str)
    pos = eol2 + 2

    # --- JSON header ---
    json_bytes = raw[pos: pos + json_len]
    hdr = json.loads(json_bytes.decode("utf-8"))
    pos += json_len  # pos ora = byteOffset del primo data block

    # --- Verifica MD5 opzionale ---
    if verify_md5:
        payload_start = pos  # MD5 copre tutto tranne magic+MD5 line
        computed = hashlib.md5(raw[payload_start:]).hexdigest()
        if computed != md5_stored:
            raise OgprReadError(
                f"MD5 non valido (stored={md5_stored}, computed={computed})"
            )

    # --- Estrai metadati dall'header ---
    md   = hdr["mainDescriptor"]
    n_samples  = int(md["samplesCount"])
    n_channels = int(md["channelsCount"])
    n_slices   = int(md["slicesCount"])
    swath_name = md["metadata"].get("swathName", "")
    swath_id   = md["metadata"].get("swathId",   "")
    array_id   = int(md["metadata"].get("arrayId", 0))
    v_major    = int(hdr["version"]["major"])
    v_minor    = int(hdr["version"]["minor"])

    # --- Trova i due data block descriptors ---
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

    radar_info     = radar_desc["radar"]
    sampling_step  = float(radar_info["samplingStep_m"])
    sampling_time  = float(radar_info["samplingTime_ns"])
    velocity       = float(radar_info["propagationVelocity_mPerSec"])
    frequency      = float(radar_info.get("fequency_MHz",  # typo nel formato
                            radar_info.get("frequency_MHz", 600.0)))
    polarization   = str(radar_info.get("polarization", "horizontal"))
    value_type     = str(radar_desc.get("valueType", "int16"))  # assente in v1.0

    epsg = int(geo_desc.get("srs", {}).get("value", 32633))

    # --- Leggi Radar Volume ---
    r_offset   = int(radar_desc["byteOffset"])
    r_bytesize = int(radar_desc["byteSize"])
    radar_raw  = raw[r_offset: r_offset + r_bytesize]

    expected_floats = n_samples * n_channels * n_slices
    if value_type == "float":
        radar_flat = np.frombuffer(radar_raw, dtype="<f4")  # float32 LE
    else:
        radar_flat = np.frombuffer(radar_raw, dtype="<i2").astype(np.float32)  # int16 LE

    if radar_flat.size != expected_floats:
        raise OgprReadError(
            f"Dimensione dati radar inattesa: "
            f"attesi {expected_floats}, trovati {radar_flat.size}"
        )

    # Layout: (n_slices, n_channels, n_samples)  →  riorganizza in (n_samples, n_slices) per canale
    radar_3d = radar_flat.reshape((n_slices, n_channels, n_samples))
    # radar_3d[slice_i, ch_i, sample_i]

    # --- Leggi Geolocations ---
    g_offset   = int(geo_desc["byteOffset"])
    g_bytesize = int(geo_desc["byteSize"])
    geo_raw    = raw[g_offset: g_offset + g_bytesize]

    expected_geo_bytes = n_slices * n_channels * GEO_BYTES_PER_CHANNEL
    if len(geo_raw) != expected_geo_bytes:
        raise OgprReadError(
            f"Dimensione geolocations inattesa: "
            f"attesi {expected_geo_bytes}, trovati {len(geo_raw)}"
        )

    # geo_flat shape: (n_slices, n_channels, 8)  float64
    geo_flat = np.frombuffer(geo_raw, dtype="<f8").reshape(
        (n_slices, n_channels, GEO_DOUBLES_PER_CHANNEL)
    )

    # --- Costruisci canali ---
    channels = []
    for ch_i in range(n_channels):
        data_ch = radar_3d[:, ch_i, :].T.copy()   # → (n_samples, n_slices)
        east_ch = geo_flat[:, ch_i, 0].copy()
        north_ch = geo_flat[:, ch_i, 1].copy()
        alt_ch   = geo_flat[:, ch_i, 2].copy()
        head_ch  = geo_flat[:, ch_i, 3].copy()

        # distanza cumulativa lungo il profilo
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
