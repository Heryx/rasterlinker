"""LAS/LAZ → GeoTIFF slice generator with IDW interpolation.

Supports:
  - single-band (any field)
  - 3-band RGB (value_field='rgb') → GeoTIFF + QML sidecar multibandcolor
    with user-chosen R, G, B fields
"""

from __future__ import annotations

import json
import os
import textwrap

import numpy as np


# ---------------------------------------------------------------------------
# LAS field diagnosis: detailed preview of actual values
# ---------------------------------------------------------------------------

_SKIP_FIELDS = frozenset((
    "x", "y", "z", "X", "Y", "Z",
    "return_number", "number_of_returns",
    "scan_direction_flag", "edge_of_flight_line",
    "scan_angle_rank", "scan_angle",
    "gps_time",
))

_PRIORITY_FIELDS = ("intensity", "red", "green", "blue",
                    "amplitude", "reflectance", "deviation", "signal")


def diagnose_las_fields_detailed(
    las_path: str,
    n_preview: int = 6,
    n_sample: int = 30000,
) -> dict:
    """
    Read the first `n_sample` points and return a detailed field report.

    Returns:
    {
      "rows": [
          {
            "name":     str,
            "values":   [float, ...],   # first n_preview sample values
            "min":      float,
            "max":      float,
            "has_data": bool,           # max > 0 or max > min
          }, ...
      ],
      "all_field_names": [str, ...],    # all non-skip dimension names
      "suggested_r": str,
      "suggested_g": str,
      "suggested_b": str,
      "suggested_single": str,
      "point_format": int,
      "n_points": int,
    }
    """
    import laspy  # type: ignore

    result = {
        "rows":            [],
        "all_field_names": [],
        "suggested_r":     "red",
        "suggested_g":     "green",
        "suggested_b":     "blue",
        "suggested_single":"intensity",
        "point_format":    -1,
        "n_points":        0,
    }

    try:
        with laspy.LasReader(las_path) as reader:
            result["point_format"] = int(reader.header.point_format.id)
            result["n_points"]     = int(reader.header.point_count)

            fmt      = reader.header.point_format
            all_dims = list(fmt.dimension_names)
            try:
                for ed in fmt.extra_dims:
                    all_dims.append(ed.name)
            except Exception:
                pass

            # Filter out purely geometric/metadata fields
            value_dims = [d for d in all_dims if d not in _SKIP_FIELDS]
            result["all_field_names"] = value_dims

            # Read first chunk
            sample = None
            try:
                for chunk in reader.chunk_iterator(n_sample):
                    sample = chunk
                    break
            except Exception:
                pass

            if sample is None:
                return result

            n_pts = min(n_preview, len(np.asarray(sample.x)))

            for name in value_dims:
                try:
                    arr = np.asarray(getattr(sample, name), dtype=np.float64)
                    if arr.size == 0:
                        continue
                    finite = arr[np.isfinite(arr)]
                    if finite.size == 0:
                        continue
                    mn       = float(finite.min())
                    mx       = float(finite.max())
                    has_data = (mx > 0) or (mx > mn)
                    preview  = [round(float(arr[i]), 3)
                                for i in range(min(n_pts, arr.size))]
                    result["rows"].append({
                        "name":     name,
                        "values":   preview,
                        "min":      mn,
                        "max":      mx,
                        "has_data": has_data,
                    })
                except Exception:
                    pass

    except Exception:
        return result

    # Sort: priority fields with data first
    def _key(row):
        n = row["name"]
        p = _PRIORITY_FIELDS.index(n) if n in _PRIORITY_FIELDS else 99
        return (0 if row["has_data"] else 1, p, n)

    result["rows"].sort(key=_key)

    # Auto-suggest R, G, B, single
    has_data_fields = [r["name"] for r in result["rows"] if r["has_data"]]

    def _first(candidates):
        for c in candidates:
            if c in has_data_fields:
                return c
        return candidates[0] if candidates else "intensity"

    result["suggested_r"]      = _first(["red",   "intensity"])
    result["suggested_g"]      = _first(["green", "intensity"])
    result["suggested_b"]      = _first(["blue",  "intensity"])
    result["suggested_single"] = _first(["intensity", "red", "green", "blue"])

    return result


# ---------------------------------------------------------------------------
# LAS reading
# ---------------------------------------------------------------------------

def _read_las_single(las_path: str, value_field: str):
    """Read X, Y, Z + one value field. Returns (x, y, z, values)."""
    import laspy  # type: ignore

    las    = laspy.read(las_path)
    x      = np.asarray(las.x, dtype=np.float64)
    y      = np.asarray(las.y, dtype=np.float64)
    z      = np.asarray(las.z, dtype=np.float64)
    values = None
    try:
        raw = np.asarray(getattr(las, value_field), dtype=np.float32)
        if raw.size == len(x):
            values = raw
    except Exception:
        pass
    if values is None:
        values = np.zeros(len(x), dtype=np.float32)
    return x, y, z, values


def _read_las_rgb(
    las_path: str,
    r_field: str = "red",
    g_field: str = "green",
    b_field: str = "blue",
):
    """Read X, Y, Z + three value fields. Returns (x, y, z, r, g, b)."""
    import laspy  # type: ignore

    las = laspy.read(las_path)
    x   = np.asarray(las.x, dtype=np.float64)
    y   = np.asarray(las.y, dtype=np.float64)
    z   = np.asarray(las.z, dtype=np.float64)

    def _get(field):
        try:
            arr = np.asarray(getattr(las, field), dtype=np.float32)
            if arr.size == len(x):
                return arr
        except Exception:
            pass
        return np.zeros(len(x), dtype=np.float32)

    return x, y, z, _get(r_field), _get(g_field), _get(b_field)


# ---------------------------------------------------------------------------
# IDW binning
# ---------------------------------------------------------------------------

def _bin_with_idw(
    x_pts: np.ndarray,
    y_pts: np.ndarray,
    i_pts: np.ndarray,
    x_min: float,
    y_min: float,
    n_x: int,
    n_y: int,
    resolution: float,
    radius: float,
) -> np.ndarray:
    """IDW-2 binning onto regular grid. Returns float32 (NaN = no data)."""
    total   = np.zeros((n_y, n_x), dtype=np.float64)
    wsum    = np.zeros((n_y, n_x), dtype=np.float64)
    r_cells = max(0, int(np.ceil(radius / resolution)))

    for diy in range(-r_cells, r_cells + 1):
        for dix in range(-r_cells, r_cells + 1):
            xi = np.round((x_pts - x_min) / resolution).astype(np.int64) + dix
            yi = np.round((y_pts - y_min) / resolution).astype(np.int64) + diy

            x_cell = x_min + xi * resolution
            y_cell = y_min + yi * resolution
            dist   = np.sqrt((x_pts - x_cell) ** 2 + (y_pts - y_cell) ** 2)

            in_b = (
                (xi >= 0) & (xi < n_x) &
                (yi >= 0) & (yi < n_y) &
                (dist <= radius)
            )
            if not np.any(in_b):
                continue

            w = 1.0 / (dist[in_b] ** 2 + 1e-9)
            np.add.at(total, (yi[in_b], xi[in_b]),
                      w * i_pts[in_b].astype(np.float64))
            np.add.at(wsum, (yi[in_b], xi[in_b]), w)

    grid = np.full((n_y, n_x), np.nan, dtype=np.float32)
    hd   = wsum > 0
    grid[hd] = (total[hd] / wsum[hd]).astype(np.float32)
    return grid


# ---------------------------------------------------------------------------
# GeoTIFF writers
# ---------------------------------------------------------------------------

def _make_geotransform(x_min, y_max, resolution):
    return (x_min - resolution / 2.0, resolution, 0.0,
            y_max + resolution / 2.0, 0.0, -resolution)


def _set_projection(ds, epsg):
    from osgeo import osr  # type: ignore
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(int(epsg))
    ds.SetProjection(srs.ExportToWkt())


def _write_tif_singleband(grid, out_path, x_min, y_min, y_max, resolution, epsg):
    from osgeo import gdal  # type: ignore
    NODATA = -9999.0
    n_y, n_x = grid.shape
    data = np.where(np.isnan(grid), NODATA, grid).astype(np.float32)
    ds = gdal.GetDriverByName("GTiff").Create(
        out_path, n_x, n_y, 1, gdal.GDT_Float32,
        options=["COMPRESS=LZW", "TILED=YES"],
    )
    ds.SetGeoTransform(_make_geotransform(x_min, y_max, resolution))
    if epsg:
        _set_projection(ds, epsg)
    bnd = ds.GetRasterBand(1)
    bnd.WriteArray(np.flipud(data))
    bnd.SetNoDataValue(NODATA)
    bnd.FlushCache()
    ds.FlushCache()
    ds = None


def _write_tif_rgb(r_grid, g_grid, b_grid, out_path, x_min, y_min, y_max,
                   resolution, epsg):
    """Write 3-band float32 GeoTIFF. Returns (mn_r,mx_r, mn_g,mx_g, mn_b,mx_b)."""
    from osgeo import gdal  # type: ignore
    NODATA = -9999.0
    n_y, n_x = r_grid.shape

    def _prep(arr):
        return np.where(np.isnan(arr), NODATA, arr).astype(np.float32)

    bands_data = [_prep(r_grid), _prep(g_grid), _prep(b_grid)]

    ds = gdal.GetDriverByName("GTiff").Create(
        out_path, n_x, n_y, 3, gdal.GDT_Float32,
        options=["COMPRESS=LZW", "TILED=YES"],
    )
    ds.SetGeoTransform(_make_geotransform(x_min, y_max, resolution))
    if epsg:
        _set_projection(ds, epsg)
    for idx, data in enumerate(bands_data, start=1):
        bnd = ds.GetRasterBand(idx)
        bnd.WriteArray(np.flipud(data))
        bnd.SetNoDataValue(NODATA)
        bnd.FlushCache()
    ds.FlushCache()
    ds = None

    def _range(arr):
        v = arr[arr != NODATA]
        return (float(v.min()), float(v.max())) if v.size else (0.0, 1.0)

    mn_r, mx_r = _range(bands_data[0])
    mn_g, mx_g = _range(bands_data[1])
    mn_b, mx_b = _range(bands_data[2])
    return mn_r, mx_r, mn_g, mx_g, mn_b, mx_b


# ---------------------------------------------------------------------------
# QML sidecar (multibandcolor renderer)
# ---------------------------------------------------------------------------

_QML_TEMPLATE = textwrap.dedent("""\
    <!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
    <qgis version="3.0" styleCategories="AllStyleCategories">
      <flags>
        <Identifiable>1</Identifiable><Removable>1</Removable>
        <Searchable>0</Searchable>
      </flags>
      <pipe>
        <provider>
          <resampling enabled="false" maxOversampling="2"
                      zoomedInResamplingMethod="nearestNeighbour"
                      zoomedOutResamplingMethod="nearestNeighbour"/>
        </provider>
        <rasterrenderer type="multibandcolor" opacity="1" alphaBand="-1"
                        redBand="1" greenBand="2" blueBand="3" nodataColor="">
          <rasterTransparency/>
          <minMaxOrigin>
            <limits>MinMax</limits><extent>WholeRaster</extent>
            <statAccuracy>Estimated</statAccuracy>
            <cumulativeCutLower>0.02</cumulativeCutLower>
            <cumulativeCutUpper>0.98</cumulativeCutUpper>
            <stdDevFactor>2</stdDevFactor>
          </minMaxOrigin>
          <redContrastEnhancement>
            <minValue>{min_r}</minValue><maxValue>{max_r}</maxValue>
            <algorithm>StretchToMinimumMaximum</algorithm>
          </redContrastEnhancement>
          <greenContrastEnhancement>
            <minValue>{min_g}</minValue><maxValue>{max_g}</maxValue>
            <algorithm>StretchToMinimumMaximum</algorithm>
          </greenContrastEnhancement>
          <blueContrastEnhancement>
            <minValue>{min_b}</minValue><maxValue>{max_b}</maxValue>
            <algorithm>StretchToMinimumMaximum</algorithm>
          </blueContrastEnhancement>
        </rasterrenderer>
        <brightnesscontrast brightness="0" contrast="0" gamma="1"/>
        <huesaturation saturation="0" grayscaleMode="0" colorizeOn="0"
                       colorizeRed="255" colorizeGreen="128" colorizeBlue="128"
                       colorizeStrength="100" invertColors="0"/>
        <rasterresampler maxOversampling="2"/>
        <resamplingStage>resamplingFilter</resamplingStage>
      </pipe>
      <blendMode>0</blendMode>
    </qgis>
""")


def _write_qml_multiband(tif_path, mn_r, mx_r, mn_g, mx_g, mn_b, mx_b):
    qml = _QML_TEMPLATE.format(
        min_r=mn_r, max_r=mx_r,
        min_g=mn_g, max_g=mx_g,
        min_b=mn_b, max_b=mx_b,
    )
    qml_path = os.path.splitext(tif_path)[0] + ".qml"
    with open(qml_path, "w", encoding="utf-8") as f:
        f.write(qml)
    return qml_path


# ---------------------------------------------------------------------------
# Sidecar params
# ---------------------------------------------------------------------------

SIDECAR_FILENAME = ".las_slicer_params.json"


def save_slicer_params(output_dir, params):
    path = os.path.join(output_dir, SIDECAR_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2, ensure_ascii=True)
    return path


def load_slicer_params(output_dir):
    path = os.path.join(output_dir, SIDECAR_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def slice_las_to_tifs(
    las_path: str,
    output_dir: str,
    resolution: float = 0.10,
    z_step: float = 0.05,
    z_min: float | None = None,
    z_max: float | None = None,
    radius: float | None = None,
    epsg: int | None = None,
    value_field: str = "intensity",  # or 'rgb' for 3-band
    r_field: str = "red",
    g_field: str = "green",
    b_field: str = "blue",
) -> list[dict]:
    """
    Slice LAS/LAZ into GeoTIFF rasters (one per Z interval).

    When value_field == 'rgb':
      - Reads r_field, g_field, b_field from LAS
      - Creates 3-band GeoTIFF + QML sidecar (multibandcolor)
    Otherwise:
      - Reads value_field from LAS
      - Creates single-band float32 GeoTIFF
    """
    if radius is None:
        radius = resolution * (2 ** 0.5)

    os.makedirs(output_dir, exist_ok=True)
    use_rgb = (value_field == "rgb")

    if use_rgb:
        x, y, z, r, g, b = _read_las_rgb(las_path, r_field, g_field, b_field)
    else:
        x, y, z, values = _read_las_single(las_path, value_field)

    if z_min is None:
        z_min = float(z.min())
    if z_max is None:
        z_max = float(z.max())

    x_min = float(x.min())
    y_min = float(y.min())
    y_max = float(y.max())
    x_max = float(x.max())

    n_x = len(np.arange(x_min, x_max + resolution * 0.5, resolution))
    n_y = len(np.arange(y_min, y_max + resolution * 0.5, resolution))

    z_levels = np.arange(z_min, z_max + z_step * 0.5, z_step)
    results  = []

    for iz, z_lev in enumerate(z_levels):
        z_from = float(z_lev - z_step / 2.0)
        z_to   = float(z_lev + z_step / 2.0)
        mask   = (z >= z_from) & (z < z_to)
        if not np.any(mask):
            continue

        z_label  = f"{z_lev:.4f}".replace(".", "_").replace("-", "m")
        tif_name = f"slice_{iz:04d}_z{z_label}.tif"
        tif_path = os.path.join(output_dir, tif_name)

        kw = dict(x_min=x_min, y_min=y_min, n_x=n_x, n_y=n_y,
                  resolution=resolution, radius=radius)

        if use_rgb:
            r_grid = _bin_with_idw(x[mask], y[mask], r[mask], **kw)
            g_grid = _bin_with_idw(x[mask], y[mask], g[mask], **kw)
            b_grid = _bin_with_idw(x[mask], y[mask], b[mask], **kw)
            ranges = _write_tif_rgb(
                r_grid, g_grid, b_grid,
                tif_path, x_min, y_min, y_max, resolution, epsg,
            )
            _write_qml_multiband(tif_path, *ranges)
        else:
            grid = _bin_with_idw(x[mask], y[mask], values[mask], **kw)
            _write_tif_singleband(
                grid, tif_path, x_min, y_min, y_max, resolution, epsg,
            )

        results.append({
            "path":     tif_path,
            "z_from":   round(z_from, 6),
            "z_to":     round(z_to,   6),
            "z_center": round(float(z_lev), 6),
            "index":    iz,
            "name":     os.path.splitext(tif_name)[0],
        })

    return results
