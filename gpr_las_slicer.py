"""LAS/LAZ → GeoTIFF slice generator with IDW interpolation.

Supports:
  - single-band (intensity / any field)
  - 3-band RGB (field='rgb') → GeoTIFF + QML sidecar multibandcolor
"""

from __future__ import annotations

import json
import os
import textwrap

import numpy as np


# ---------------------------------------------------------------------------
# LAS field diagnosis  (fast: first n_sample points only)
# ---------------------------------------------------------------------------

_USEFUL_FIELDS = (
    "intensity", "red", "green", "blue",
    "amplitude", "reflectance", "deviation", "signal",
)
_SKIP_FIELDS = frozenset((
    "x", "y", "z", "X", "Y", "Z",
    "return_number", "number_of_returns",
    "scan_direction_flag", "edge_of_flight_line",
    "scan_angle_rank", "scan_angle",
    "user_data", "point_source_id",
    "gps_time",
))


def diagnose_las_fields(las_path: str, n_sample: int = 30000) -> dict:
    """
    Quick diagnosis of available value fields.
    Reads only first `n_sample` points.

    Returns:
        {
          "available":    [(name, min, max, has_data), ...],
          "suggested":    field_name,
          "point_format": int,
          "n_points":     int,
        }
    """
    import laspy  # type: ignore

    result = {
        "available":    [],
        "suggested":    "intensity",
        "point_format": -1,
        "n_points":     0,
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

            sample = None
            try:
                for chunk in reader.chunk_iterator(n_sample):
                    sample = chunk
                    break
            except Exception:
                pass

            for name in all_dims:
                if name in _SKIP_FIELDS:
                    continue
                try:
                    arr    = np.asarray(getattr(sample or reader, name), dtype=np.float64)
                    finite = arr[np.isfinite(arr)]
                    if finite.size == 0:
                        continue
                    mn, mx   = float(finite.min()), float(finite.max())
                    has_data = mx > mn or mx > 0
                    result["available"].append((name, mn, mx, has_data))
                except Exception:
                    pass

    except Exception:
        result["available"] = [("intensity", 0.0, 0.0, False)]
        return result

    # Sort: useful fields with data first
    def _sort_key(e):
        name, mn, mx, has_data = e
        return (0 if name in _USEFUL_FIELDS else 1, 0 if has_data else 1, name)

    result["available"].sort(key=_sort_key)

    # --- Check whether RGB is fully available with data ---
    field_map = {name: (mn, mx, hd) for name, mn, mx, hd in result["available"]}
    r_ok = field_map.get("red",   (0, 0, False))[2]
    g_ok = field_map.get("green", (0, 0, False))[2]
    b_ok = field_map.get("blue",  (0, 0, False))[2]

    if r_ok and g_ok and b_ok:
        # Aggregate range: min of mins, max of maxes
        mn_rgb = min(field_map["red"][0], field_map["green"][0], field_map["blue"][0])
        mx_rgb = max(field_map["red"][1], field_map["green"][1], field_map["blue"][1])
        # Insert at top of list
        result["available"].insert(0, ("rgb", mn_rgb, mx_rgb, True))
        result["suggested"] = "rgb"
    else:
        # Fall back to first field with data
        for name, mn, mx, has_data in result["available"]:
            if has_data:
                result["suggested"] = name
                break

    return result


# ---------------------------------------------------------------------------
# LAS reading
# ---------------------------------------------------------------------------

def _read_las_arrays(las_path: str, value_field: str = "intensity"):
    """
    Read X, Y, Z and a single value field.  Returns (x, y, z, values).
    """
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
        for alias in ("intensity", "Intensity", "red", "amplitude"):
            try:
                raw = np.asarray(getattr(las, alias), dtype=np.float32)
                if raw.size == len(x):
                    values = raw
                    break
            except Exception:
                pass

    if values is None:
        values = np.ones(len(x), dtype=np.float32)

    return x, y, z, values


def _read_las_rgb(las_path: str):
    """
    Read X, Y, Z, R, G, B.  Returns (x, y, z, r, g, b) all as float32 / float64.
    R/G/B are float32 (original uint16 scale preserved).
    """
    import laspy  # type: ignore

    las = laspy.read(las_path)
    x   = np.asarray(las.x,     dtype=np.float64)
    y   = np.asarray(las.y,     dtype=np.float64)
    z   = np.asarray(las.z,     dtype=np.float64)
    r   = np.asarray(las.red,   dtype=np.float32)
    g   = np.asarray(las.green, dtype=np.float32)
    b   = np.asarray(las.blue,  dtype=np.float32)
    return x, y, z, r, g, b


# ---------------------------------------------------------------------------
# IDW binning (vectorised)
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
    """
    Bin values onto a regular grid using IDW-2 within `radius` metres.
    Returns float32 grid shape (n_y, n_x); NaN where no data.
    """
    total = np.zeros((n_y, n_x), dtype=np.float64)
    wsum  = np.zeros((n_y, n_x), dtype=np.float64)
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
            np.add.at(total, (yi[in_b], xi[in_b]), w * i_pts[in_b].astype(np.float64))
            np.add.at(wsum,  (yi[in_b], xi[in_b]), w)

    grid = np.full((n_y, n_x), np.nan, dtype=np.float32)
    hd   = wsum > 0
    grid[hd] = (total[hd] / wsum[hd]).astype(np.float32)
    return grid


# ---------------------------------------------------------------------------
# GeoTIFF writers
# ---------------------------------------------------------------------------

def _write_tif_singleband(
    grid: np.ndarray,
    out_path: str,
    x_min: float,
    y_min: float,
    y_max: float,
    resolution: float,
    epsg: int | None,
) -> None:
    """Write a single-band float32 GeoTIFF."""
    from osgeo import gdal, osr  # type: ignore

    n_y, n_x = grid.shape
    NODATA   = -9999.0
    data     = np.where(np.isnan(grid), NODATA, grid).astype(np.float32)

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(
        out_path, n_x, n_y, 1, gdal.GDT_Float32,
        options=["COMPRESS=LZW", "TILED=YES"],
    )
    ds.SetGeoTransform(
        (x_min - resolution / 2.0, resolution, 0.0,
         y_max + resolution / 2.0, 0.0, -resolution)
    )
    if epsg:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(int(epsg))
        ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.WriteArray(np.flipud(data))
    band.SetNoDataValue(NODATA)
    band.FlushCache()
    ds.FlushCache()
    ds = None


def _write_tif_rgb(
    r_grid: np.ndarray,
    g_grid: np.ndarray,
    b_grid: np.ndarray,
    out_path: str,
    x_min: float,
    y_min: float,
    y_max: float,
    resolution: float,
    epsg: int | None,
) -> tuple:
    """
    Write a 3-band float32 GeoTIFF (band 1=R, 2=G, 3=B).
    Returns (min_r, max_r, min_g, max_g, min_b, max_b) for QML generation.
    """
    from osgeo import gdal, osr  # type: ignore

    NODATA = -9999.0
    n_y, n_x = r_grid.shape

    def _prep(arr):
        return np.where(np.isnan(arr), NODATA, arr).astype(np.float32)

    r_data = _prep(r_grid)
    g_data = _prep(g_grid)
    b_data = _prep(b_grid)

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(
        out_path, n_x, n_y, 3, gdal.GDT_Float32,
        options=["COMPRESS=LZW", "TILED=YES"],
    )
    ds.SetGeoTransform(
        (x_min - resolution / 2.0, resolution, 0.0,
         y_max + resolution / 2.0, 0.0, -resolution)
    )
    if epsg:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(int(epsg))
        ds.SetProjection(srs.ExportToWkt())

    for idx, arr in enumerate([r_data, g_data, b_data], start=1):
        bnd = ds.GetRasterBand(idx)
        bnd.WriteArray(np.flipud(arr))
        bnd.SetNoDataValue(NODATA)
        bnd.FlushCache()

    ds.FlushCache()
    ds = None

    def _safe_range(arr):
        valid = arr[arr != NODATA]
        if valid.size == 0:
            return 0.0, 1.0
        return float(valid.min()), float(valid.max())

    mn_r, mx_r = _safe_range(r_data)
    mn_g, mx_g = _safe_range(g_data)
    mn_b, mx_b = _safe_range(b_data)
    return mn_r, mx_r, mn_g, mx_g, mn_b, mx_b


# ---------------------------------------------------------------------------
# QML sidecar for multiband renderer
# ---------------------------------------------------------------------------

_QML_TEMPLATE = textwrap.dedent("""\
    <!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
    <qgis version="3.0" styleCategories="AllStyleCategories">
      <flags>
        <Identifiable>1</Identifiable>
        <Removable>1</Removable>
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
            <limits>MinMax</limits>
            <extent>WholeRaster</extent>
            <statAccuracy>Estimated</statAccuracy>
            <cumulativeCutLower>0.02</cumulativeCutLower>
            <cumulativeCutUpper>0.98</cumulativeCutUpper>
            <stdDevFactor>2</stdDevFactor>
          </minMaxOrigin>
          <redContrastEnhancement>
            <minValue>{min_r}</minValue>
            <maxValue>{max_r}</maxValue>
            <algorithm>StretchToMinimumMaximum</algorithm>
          </redContrastEnhancement>
          <greenContrastEnhancement>
            <minValue>{min_g}</minValue>
            <maxValue>{max_g}</maxValue>
            <algorithm>StretchToMinimumMaximum</algorithm>
          </greenContrastEnhancement>
          <blueContrastEnhancement>
            <minValue>{min_b}</minValue>
            <maxValue>{max_b}</maxValue>
            <algorithm>StretchToMinimumMaximum</algorithm>
          </blueContrastEnhancement>
        </rasterrenderer>
        <brightnesscontrast brightness="0" contrast="0" gamma="1"/>
        <huesaturation saturation="0" grayscaleMode="0"
                       colorizeOn="0" colorizeRed="255"
                       colorizeGreen="128" colorizeBlue="128"
                       colorizeStrength="100" invertColors="0"/>
        <rasterresampler maxOversampling="2"/>
        <resamplingStage>resamplingFilter</resamplingStage>
      </pipe>
      <blendMode>0</blendMode>
    </qgis>
""")


def _write_qml_multiband(
    tif_path: str,
    min_r: float, max_r: float,
    min_g: float, max_g: float,
    min_b: float, max_b: float,
) -> str:
    """
    Write a `.qml` sidecar next to the TIF so QGIS auto-applies
    'Multiband color' renderer (R=band1, G=band2, B=band3) on load.
    Returns the QML file path.
    """
    qml_content = _QML_TEMPLATE.format(
        min_r=min_r, max_r=max_r,
        min_g=min_g, max_g=max_g,
        min_b=min_b, max_b=max_b,
    )
    qml_path = os.path.splitext(tif_path)[0] + ".qml"
    with open(qml_path, "w", encoding="utf-8") as f:
        f.write(qml_content)
    return qml_path


# ---------------------------------------------------------------------------
# Sidecar params (for re-slice)
# ---------------------------------------------------------------------------

SIDECAR_FILENAME = ".las_slicer_params.json"


def save_slicer_params(output_dir: str, params: dict) -> str:
    path = os.path.join(output_dir, SIDECAR_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2, ensure_ascii=True)
    return path


def load_slicer_params(output_dir: str) -> dict | None:
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
    value_field: str = "intensity",
) -> list[dict]:
    """
    Slice a LAS/LAZ file into GeoTIFF rasters (one per Z interval).

    When value_field == 'rgb':
      - creates 3-band (R, G, B) GeoTIFF
      - writes a QML sidecar with multibandcolor renderer

    Otherwise creates single-band float32 GeoTIFF.

    Returns list of dicts: path, z_from, z_to, z_center, index, name.
    """
    if radius is None:
        radius = resolution * (2 ** 0.5)

    os.makedirs(output_dir, exist_ok=True)

    use_rgb = (value_field == "rgb")

    if use_rgb:
        x, y, z, r, g, b = _read_las_rgb(las_path)
    else:
        x, y, z, values = _read_las_arrays(las_path, value_field)

    if z_min is None:
        z_min = float(z.min())
    if z_max is None:
        z_max = float(z.max())

    x_min = float(x.min())
    y_min = float(y.min())
    x_max = float(x.max())
    y_max = float(y.max())

    x_centers = np.arange(x_min, x_max + resolution * 0.5, resolution)
    y_centers = np.arange(y_min, y_max + resolution * 0.5, resolution)
    n_x = len(x_centers)
    n_y = len(y_centers)

    z_levels = np.arange(z_min, z_max + z_step * 0.5, z_step)
    results  = []

    for iz, z_lev in enumerate(z_levels):
        z_from = float(z_lev - z_step / 2.0)
        z_to   = float(z_lev + z_step / 2.0)

        mask = (z >= z_from) & (z < z_to)
        if not np.any(mask):
            continue

        z_label  = f"{z_lev:.4f}".replace(".", "_").replace("-", "m")
        tif_name = f"slice_{iz:04d}_z{z_label}.tif"
        tif_path = os.path.join(output_dir, tif_name)

        kw = dict(
            x_min=x_min, y_min=y_min, n_x=n_x, n_y=n_y,
            resolution=resolution, radius=radius,
        )

        if use_rgb:
            r_grid = _bin_with_idw(x[mask], y[mask], r[mask], **kw)
            g_grid = _bin_with_idw(x[mask], y[mask], g[mask], **kw)
            b_grid = _bin_with_idw(x[mask], y[mask], b[mask], **kw)

            mn_r, mx_r, mn_g, mx_g, mn_b, mx_b = _write_tif_rgb(
                r_grid, g_grid, b_grid,
                tif_path, x_min, y_min, y_max, resolution, epsg,
            )
            _write_qml_multiband(
                tif_path,
                mn_r, mx_r, mn_g, mx_g, mn_b, mx_b,
            )
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
