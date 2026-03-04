"""LAS/LAZ → GeoTIFF slice generator with IDW and Kriging interpolation.

Supports:
  - single-band (any field)
  - 3-band RGB (value_field='rgb') → GeoTIFF + QML sidecar multibandcolor
    with user-chosen R, G, B fields

Interpolation methods:
  - 'idw'    : Inverse Distance Weighting with configurable power exponent
               (gather approach via scipy.spatial.cKDTree)
  - 'kriging': Ordinary Kriging via pykrige (requires: pip install pykrige)
               Variogram models: exponential, spherical, gaussian, linear

GPR-specific processing pipeline:
  1. Amplitude outlier removal  (_remove_amplitude_outliers)
  2. Interpolation              (IDW kdtree or Kriging)
  3. Fill NoData gaps           (_fill_nodata_grid)        [optional]
  4. Gaussian smoothing         (_smooth_grid_gaussian)    [optional]
"""

from __future__ import annotations

import json
import os
import textwrap

import numpy as np

try:
    from pykrige.ok import OrdinaryKriging as _OrdinaryKriging
    _HAS_PYKRIGE = True
except ImportError:
    _OrdinaryKriging = None
    _HAS_PYKRIGE = False


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

            value_dims = [d for d in all_dims if d not in _SKIP_FIELDS]
            result["all_field_names"] = value_dims

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

    def _key(row):
        n = row["name"]
        p = _PRIORITY_FIELDS.index(n) if n in _PRIORITY_FIELDS else 99
        return (0 if row["has_data"] else 1, p, n)

    result["rows"].sort(key=_key)

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
# GPR-specific: amplitude outlier removal
# ---------------------------------------------------------------------------

def _remove_amplitude_outliers(
    x: np.ndarray,
    y: np.ndarray,
    values: np.ndarray,
    n_sigma: float = 3.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Remove GPR amplitude outliers using a simple sigma-clipping filter.

    GPR-specific outliers (antenna ringing, metallic reflections, surface
    coupling spikes) appear as amplitude values far from the mean. This
    filter removes points whose amplitude deviates more than `n_sigma`
    standard deviations from the slice mean.

    A higher `n_sigma` is more permissive (keeps more points);
    a lower value is more aggressive. Typical range: 2.5 – 4.0.

    Parameters
    ----------
    x, y    : point coordinates
    values  : radar amplitude/intensity values
    n_sigma : clipping threshold in standard deviations (default 3.0)

    Returns
    -------
    Filtered (x, y, values) arrays.
    """
    if values.size == 0:
        return x, y, values
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return x, y, values
    mean = float(finite.mean())
    std  = float(finite.std())
    if std == 0.0:
        return x, y, values
    mask = np.abs(values - mean) <= n_sigma * std
    return x[mask], y[mask], values[mask]


# ---------------------------------------------------------------------------
# GPR-specific: adaptive radius from inter-line spacing
# ---------------------------------------------------------------------------

def _estimate_interline_radius(
    x: np.ndarray,
    y: np.ndarray,
    percentile: float = 95.0,
    multiplier: float = 1.5,
) -> float:
    """Estimate search radius from the inter-line spacing of GPR data.

    GPR datasets consist of parallel acquisition lines. The optimal IDW
    search radius should cover at least the gap between adjacent lines.
    This function estimates that gap by computing the distance to the
    nearest neighbour for each point and taking the given percentile
    (capturing the widest gaps, i.e. the inter-line spacing) then
    multiplying by `multiplier` to ensure full coverage.

    Parameters
    ----------
    x, y        : point coordinates
    percentile  : percentile of nearest-neighbour distances to use (default 95)
    multiplier  : safety factor applied to the estimated spacing (default 1.5)

    Returns
    -------
    Estimated search radius in CRS units.
    """
    from scipy.spatial import cKDTree  # type: ignore

    if x.size < 2:
        return 1.0
    tree   = cKDTree(np.column_stack([x, y]))
    dists, _ = tree.query(np.column_stack([x, y]), k=2)
    nn_dists = dists[:, 1]  # distance to nearest neighbour
    radius   = float(np.percentile(nn_dists, percentile)) * multiplier
    return max(radius, 1e-6)


# ---------------------------------------------------------------------------
# GPR-specific: fill NoData gaps between acquisition lines
# ---------------------------------------------------------------------------

def _fill_nodata_grid(grid: np.ndarray, max_distance: int = 5) -> np.ndarray:
    """Fill NaN cells in the interpolated grid using nearest-neighbour diffusion.

    GPR acquisition lines leave systematic NaN bands between lines after
    interpolation. This function fills those gaps by propagating valid
    values outward up to `max_distance` cells, using
    scipy.ndimage.distance_transform_edt for efficiency.

    Parameters
    ----------
    grid         : 2-D float32 array with NaN where no data
    max_distance : maximum fill distance in grid cells (default 5)

    Returns
    -------
    Filled float32 grid (remaining NaN only where fill distance exceeded).
    """
    from scipy.ndimage import distance_transform_edt  # type: ignore

    nan_mask  = np.isnan(grid)
    if not nan_mask.any():
        return grid

    valid_mask = ~nan_mask
    # For each NaN cell, find the index of the nearest valid cell
    dist, (row_idx, col_idx) = distance_transform_edt(
        nan_mask,
        return_distances=True,
        return_indices=True,
    )
    filled = grid.copy()
    # Fill only within max_distance cells
    fill_where = nan_mask & (dist <= max_distance)
    filled[fill_where] = grid[row_idx[fill_where], col_idx[fill_where]]
    return filled


# ---------------------------------------------------------------------------
# GPR-specific: post-interpolation Gaussian smoothing
# ---------------------------------------------------------------------------

def _smooth_grid_gaussian(grid: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    """Apply Gaussian smoothing to the interpolated grid.

    Reduces inter-line interpolation artefacts and high-frequency noise
    in GPR time-slice maps. NaN cells are handled by normalised convolution
    (ignoring NaN in both numerator and denominator) so that valid data
    near NaN boundaries is not contaminated.

    Parameters
    ----------
    grid  : 2-D float32 array (NaN = no data)
    sigma : Gaussian standard deviation in grid cells (default 1.0)
            Typical values: 0.5 (light) – 2.0 (heavy)

    Returns
    -------
    Smoothed float32 grid (NaN preserved where no valid neighbours exist).
    """
    from scipy.ndimage import gaussian_filter  # type: ignore

    if sigma <= 0:
        return grid

    nan_mask   = np.isnan(grid)
    filled     = np.where(nan_mask, 0.0, grid.astype(np.float64))
    weight     = np.where(nan_mask, 0.0, 1.0)

    smooth_num = gaussian_filter(filled, sigma=sigma)
    smooth_den = gaussian_filter(weight, sigma=sigma)

    result = np.full_like(grid, np.nan, dtype=np.float32)
    valid  = smooth_den > 1e-9
    result[valid] = (smooth_num[valid] / smooth_den[valid]).astype(np.float32)
    return result


# ---------------------------------------------------------------------------
# IDW binning — original scatter approach (kept for compatibility)
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
    power: float = 2.0,
) -> np.ndarray:
    """IDW binning onto regular grid using scatter approach (legacy).

    Kept for backward compatibility. For new code prefer _bin_with_idw_kdtree.

    weight:  w = 1 / (dist^power + epsilon)
    """
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

            w = 1.0 / (dist[in_b] ** power + 1e-9)
            np.add.at(total, (yi[in_b], xi[in_b]),
                      w * i_pts[in_b].astype(np.float64))
            np.add.at(wsum, (yi[in_b], xi[in_b]), w)

    grid = np.full((n_y, n_x), np.nan, dtype=np.float32)
    hd   = wsum > 0
    grid[hd] = (total[hd] / wsum[hd]).astype(np.float32)
    return grid


# ---------------------------------------------------------------------------
# IDW binning — gather approach with scipy cKDTree (recommended)
# ---------------------------------------------------------------------------

def _bin_with_idw_kdtree(
    x_pts: np.ndarray,
    y_pts: np.ndarray,
    i_pts: np.ndarray,
    x_min: float,
    y_min: float,
    n_x: int,
    n_y: int,
    resolution: float,
    radius: float,
    power: float = 2.0,
    min_points: int = 3,
) -> np.ndarray:
    """IDW binning using a gather approach via scipy.spatial.cKDTree.

    For each grid cell, queries all source points within `radius` and
    computes the inverse-distance-weighted average:

        estimated = sum(wi * zi) / sum(wi)   where  wi = 1 / (hi^power + eps)

    GPR note: `min_points` prevents unstable estimates in the gaps between
    acquisition lines, where only 1-2 isolated points from adjacent lines
    might fall within the search radius.

    Parameters
    ----------
    min_points : minimum number of source points required to estimate a cell.
                 Cells with fewer neighbours are left as NaN. Default 3.
    """
    from scipy.spatial import cKDTree  # type: ignore

    gx = x_min + np.arange(n_x) * resolution
    gy = y_min + np.arange(n_y) * resolution
    gxx, gyy  = np.meshgrid(gx, gy)
    grid_pts  = np.column_stack([gxx.ravel(), gyy.ravel()])

    tree    = cKDTree(np.column_stack([x_pts, y_pts]))
    results = tree.query_ball_point(grid_pts, r=radius, workers=-1)

    i_pts_f64 = i_pts.astype(np.float64)
    grid      = np.full(n_x * n_y, np.nan, dtype=np.float32)

    for k, idx_list in enumerate(results):
        if len(idx_list) < min_points:
            continue
        idx = np.asarray(idx_list, dtype=np.int64)
        cx  = grid_pts[k, 0]
        cy  = grid_pts[k, 1]
        d   = np.sqrt((x_pts[idx] - cx) ** 2 + (y_pts[idx] - cy) ** 2)
        w   = 1.0 / (d ** power + 1e-9)
        ws  = w.sum()
        if ws > 0:
            grid[k] = float(np.dot(w, i_pts_f64[idx]) / ws)

    return grid.reshape(n_y, n_x)


# ---------------------------------------------------------------------------
# Kriging interpolation (Ordinary Kriging via pykrige)
# ---------------------------------------------------------------------------

def _bin_with_kriging(
    x_pts: np.ndarray,
    y_pts: np.ndarray,
    i_pts: np.ndarray,
    x_min: float,
    y_min: float,
    n_x: int,
    n_y: int,
    resolution: float,
    variogram_model: str = "exponential",
    nugget: float = 0.0,
) -> np.ndarray:
    """Ordinary Kriging interpolation onto a regular grid.

    Uses the GPRSlice covariance model:

        cij = c0 + c1          if h == 0
        cij = c1 * exp(-3h/a)  if h >  0   (exponential variogram)

    Variogram models: 'exponential' (default), 'spherical', 'gaussian', 'linear'.
    Requires: pip install pykrige
    """
    if not _HAS_PYKRIGE:
        raise ImportError(
            "pykrige non e' installato. "
            "Installare con: pip install pykrige"
        )

    gx = x_min + np.arange(n_x) * resolution
    gy = y_min + np.arange(n_y) * resolution

    ok = _OrdinaryKriging(
        x_pts,
        y_pts,
        i_pts.astype(np.float64),
        variogram_model=variogram_model,
        variogram_parameters={"nugget": nugget},
        verbose=False,
        enable_plotting=False,
    )
    z_grid, _variance = ok.execute("grid", gx, gy)
    return np.asarray(z_grid, dtype=np.float32)


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
    """Persist slicing parameters to a JSON sidecar file.

    Includes all interpolation and GPR-specific post-processing settings
    so that the exact configuration can be reloaded and reapplied later.
    """
    path = os.path.join(output_dir, SIDECAR_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2, ensure_ascii=True)
    return path


def load_slicer_params(output_dir):
    """Load slicing parameters from the JSON sidecar file, or return None."""
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
    r_field: str = "red",
    g_field: str = "green",
    b_field: str = "blue",
    # --- interpolation ---
    interpolation_method: str = "idw",
    idw_power: float = 2.0,
    min_points: int = 3,
    kriging_model: str = "exponential",
    kriging_nugget: float = 0.0,
    # --- GPR-specific filters ---
    auto_radius: bool = False,
    amplitude_sigma: float | None = None,
    fill_nodata: bool = False,
    fill_nodata_max_distance: int = 5,
    smooth_sigma: float = 0.0,
) -> list[dict]:
    """
    Slice LAS/LAZ GPR data into GeoTIFF rasters (one per Z interval).

    Processing pipeline per slice
    -----------------------------
    1. [optional] Amplitude outlier removal  (amplitude_sigma)
    2. Interpolation onto regular grid       (IDW kdtree or Kriging)
    3. [optional] Fill NaN inter-line gaps   (fill_nodata)
    4. [optional] Gaussian smoothing         (smooth_sigma > 0)

    Parameters
    ----------
    las_path             : path to input LAS/LAZ file
    output_dir           : output directory for GeoTIFFs
    resolution           : grid cell size in CRS units
    z_step               : Z slice thickness
    z_min, z_max         : optional Z range override
    radius               : IDW search radius (default: resolution * sqrt(2)).
                           Overridden by auto_radius if True.
    epsg                 : output EPSG code (None = no CRS set)
    value_field          : LAS field to interpolate, or 'rgb' for 3-band
    r_field, g_field, b_field : LAS fields for RGB mode

    interpolation_method : 'idw' (default) or 'kriging'
    idw_power            : IDW smoothing exponent a (GPRSlice notation).
                           Default 2.0.
    min_points           : minimum neighbours required to estimate a cell.
                           Cells with fewer points left as NaN. Default 3.
    kriging_model        : variogram model ('exponential', 'spherical',
                           'gaussian', 'linear'). Default 'exponential'.
    kriging_nugget       : nugget effect c0. Default 0.0.

    auto_radius          : if True, estimate search radius automatically from
                           the inter-line spacing of the dataset. Default False.
    amplitude_sigma      : if set, remove GPR amplitude outliers beyond
                           this many standard deviations before interpolating.
                           Recommended: 3.0. Default None (disabled).
    fill_nodata          : if True, fill NaN gaps between acquisition lines
                           using nearest-neighbour diffusion. Default False.
    fill_nodata_max_distance : maximum fill distance in grid cells. Default 5.
    smooth_sigma         : Gaussian smoothing sigma in grid cells applied
                           after interpolation. 0 = disabled (default).
                           Typical values: 0.5 (light) to 2.0 (heavy).

    Returns
    -------
    List of dicts: path, z_from, z_to, z_center, index, name.
    """
    if radius is None:
        radius = resolution * (2 ** 0.5)

    os.makedirs(output_dir, exist_ok=True)
    use_rgb     = (value_field == "rgb")
    use_kriging = (interpolation_method == "kriging")

    if use_kriging and not _HAS_PYKRIGE:
        raise ImportError(
            "pykrige non e' installato. "
            "Installare con: pip install pykrige"
        )

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

    # Auto-estimate search radius from inter-line spacing
    if auto_radius:
        radius = _estimate_interline_radius(x, y)

    _kw_base    = dict(x_min=x_min, y_min=y_min, n_x=n_x, n_y=n_y,
                       resolution=resolution)
    _kw_idw     = dict(**_kw_base, radius=radius,
                       power=idw_power, min_points=min_points)
    _kw_kriging = dict(**_kw_base, variogram_model=kriging_model,
                       nugget=kriging_nugget)

    def _interp(xm, ym, vm):
        if use_kriging:
            return _bin_with_kriging(xm, ym, vm, **_kw_kriging)
        return _bin_with_idw_kdtree(xm, ym, vm, **_kw_idw)

    def _postprocess(grid):
        """Apply GPR-specific post-processing steps to a single grid."""
        if fill_nodata:
            grid = _fill_nodata_grid(grid, max_distance=fill_nodata_max_distance)
        if smooth_sigma > 0:
            grid = _smooth_grid_gaussian(grid, sigma=smooth_sigma)
        return grid

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

        if use_rgb:
            def _interp_channel(ch):
                xm, ym, vm = x[mask], y[mask], ch[mask]
                if amplitude_sigma is not None:
                    xm, ym, vm = _remove_amplitude_outliers(
                        xm, ym, vm, n_sigma=amplitude_sigma)
                return _postprocess(_interp(xm, ym, vm))

            r_grid = _interp_channel(r)
            g_grid = _interp_channel(g)
            b_grid = _interp_channel(b)
            ranges = _write_tif_rgb(
                r_grid, g_grid, b_grid,
                tif_path, x_min, y_min, y_max, resolution, epsg,
            )
            _write_qml_multiband(tif_path, *ranges)
        else:
            xm, ym, vm = x[mask], y[mask], values[mask]
            if amplitude_sigma is not None:
                xm, ym, vm = _remove_amplitude_outliers(
                    xm, ym, vm, n_sigma=amplitude_sigma)
            grid = _postprocess(_interp(xm, ym, vm))
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
