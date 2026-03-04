"""LAS/LAZ → GeoTIFF slice generator with IDW and Kriging interpolation.

Supports:
  - single-band (any field)
  - 3-band RGB (value_field='rgb') → GeoTIFF + QML sidecar multibandcolor
    with user-chosen R, G, B fields

Interpolation methods:
  - 'idw'    : Inverse Distance Weighting with configurable power exponent
               Standard (isotropic) or anisotropic variant for GPR line data.
               Gather approach via scipy.spatial.cKDTree.
  - 'kriging': Ordinary Kriging via pykrige (requires: pip install pykrige)
               Variogram models: exponential, spherical, gaussian, linear.
               Supports automatic variogram parameter estimation.

GPR-specific processing pipeline:
  1. Amplitude outlier removal  (_remove_amplitude_outliers)  [optional]
  2. Interpolation              (IDW isotropic/anisotropic or Kriging)
  3. Fill NoData gaps           (_fill_nodata_grid)            [optional]
  4. Gaussian smoothing         (_smooth_grid_gaussian)        [optional]
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
# LAS field diagnosis
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

    Returns a dict with keys: rows, all_field_names, suggested_r/g/b/single,
    point_format, n_points.
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
    """Remove GPR amplitude outliers using sigma-clipping.

    GPR-specific outliers (antenna ringing, metallic reflections, surface
    coupling spikes) appear as amplitude values far from the slice mean.
    Removes points whose amplitude deviates more than `n_sigma` standard
    deviations. Typical range: 2.5 – 4.0.
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
# GPR-specific: acquisition direction estimation via PCA
# ---------------------------------------------------------------------------

def _estimate_acquisition_direction(
    x: np.ndarray,
    y: np.ndarray,
) -> float:
    """Estimate the principal acquisition direction of GPR lines via PCA.

    GPR surveys consist of parallel lines. PCA on the XY point cloud
    identifies the direction of maximum variance, which corresponds to
    the along-line direction. The perpendicular is the cross-line direction
    (where the inter-line gaps occur).

    Returns
    -------
    angle_deg : float
        Angle in degrees (0-180) of the principal acquisition direction
        measured counter-clockwise from the positive X axis.
    """
    if x.size < 3:
        return 0.0
    pts  = np.column_stack([x - x.mean(), y - y.mean()])
    cov  = np.cov(pts.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    # Principal direction = eigenvector with largest eigenvalue
    principal = eigvecs[:, np.argmax(eigvals)]
    angle_deg = float(np.degrees(np.arctan2(principal[1], principal[0]))) % 180.0
    return angle_deg


# ---------------------------------------------------------------------------
# GPR-specific: adaptive radius + anisotropy from inter-line spacing
# ---------------------------------------------------------------------------

def _estimate_interline_radius(
    x: np.ndarray,
    y: np.ndarray,
    percentile: float = 95.0,
    multiplier: float = 1.5,
) -> dict:
    """Estimate search radius and anisotropy from GPR inter-line spacing.

    Computes nearest-neighbour distances for all points. The high-percentile
    distances correspond to inter-line gaps; the low-percentile distances
    correspond to along-line spacing.

    Returns
    -------
    dict with keys:
        radius          : recommended isotropic search radius
        anisotropy_ratio: ratio cross-line / along-line spacing (>1 means
                          points are closer along the line than across)
        acquisition_angle: principal acquisition direction in degrees
    """
    from scipy.spatial import cKDTree  # type: ignore

    if x.size < 4:
        return {"radius": 1.0, "anisotropy_ratio": 1.0, "acquisition_angle": 0.0}

    tree = cKDTree(np.column_stack([x, y]))
    # k=2 gives nearest neighbour; k=6 gives a richer neighbourhood sample
    dists, _ = tree.query(np.column_stack([x, y]), k=min(6, x.size))

    nn_dists    = dists[:, 1]                              # nearest neighbour
    along_line  = float(np.percentile(nn_dists, 10))      # dense direction
    cross_line  = float(np.percentile(nn_dists, percentile))  # sparse direction

    radius           = cross_line * multiplier
    anisotropy_ratio = (cross_line / along_line) if along_line > 1e-9 else 1.0
    acquisition_angle = _estimate_acquisition_direction(x, y)

    return {
        "radius":           max(radius, 1e-6),
        "anisotropy_ratio": anisotropy_ratio,
        "acquisition_angle": acquisition_angle,
    }


# ---------------------------------------------------------------------------
# GPR-specific: fill NoData gaps between acquisition lines
# ---------------------------------------------------------------------------

def _fill_nodata_grid(grid: np.ndarray, max_distance: int = 5) -> np.ndarray:
    """Fill NaN cells using nearest-neighbour diffusion.

    GPR acquisition lines leave systematic NaN bands between lines after
    interpolation. Fills those gaps by propagating valid values outward
    up to `max_distance` cells via scipy.ndimage.distance_transform_edt.
    """
    from scipy.ndimage import distance_transform_edt  # type: ignore

    nan_mask = np.isnan(grid)
    if not nan_mask.any():
        return grid

    dist, (row_idx, col_idx) = distance_transform_edt(
        nan_mask,
        return_distances=True,
        return_indices=True,
    )
    filled = grid.copy()
    fill_where = nan_mask & (dist <= max_distance)
    filled[fill_where] = grid[row_idx[fill_where], col_idx[fill_where]]
    return filled


# ---------------------------------------------------------------------------
# GPR-specific: post-interpolation Gaussian smoothing
# ---------------------------------------------------------------------------

def _smooth_grid_gaussian(grid: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    """Apply Gaussian smoothing with correct NaN handling.

    Reduces inter-line interpolation artefacts. Uses normalised convolution
    so that valid data near NaN boundaries is not contaminated.
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
    """IDW scatter approach (legacy). Prefer _bin_with_idw_kdtree for new code."""
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
# IDW binning — isotropic gather with cKDTree
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
    """Isotropic IDW gather via scipy.spatial.cKDTree.

    For each grid cell, queries all source points within `radius` and
    computes the inverse-distance-weighted average:

        estimated = sum(wi * zi) / sum(wi)   where  wi = 1 / (hi^power + eps)

    `min_points` prevents unstable estimates in GPR inter-line gaps.
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
# IDW binning — anisotropic gather (GPR parallel lines)
# ---------------------------------------------------------------------------

def _bin_with_idw_anisotropic(
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
    anisotropy_ratio: float = 1.0,
    anisotropy_angle: float = 0.0,
) -> np.ndarray:
    """Anisotropic IDW gather for GPR data with parallel acquisition lines.

    GPR surveys produce a point density that is much higher along the
    acquisition lines than across them. Standard isotropic IDW tends to
    over-smooth across lines and under-weight along-line data.

    This function applies an elliptical distance metric that stretches
    the search neighbourhood along the cross-line direction:

        d_aniso = sqrt( (dx_rot / 1)^2 + (dy_rot / anisotropy_ratio)^2 )

    where dx_rot, dy_rot are the point offsets rotated into the acquisition
    coordinate system (along-line, cross-line).

    Parameters
    ----------
    anisotropy_ratio  : ratio of cross-line to along-line scale factor (>1).
                        Estimated automatically by _estimate_interline_radius.
                        A value of 3 means the cross-line direction is weighted
                        3x more loosely than the along-line direction.
    anisotropy_angle  : principal acquisition direction in degrees CCW from X.
                        Estimated by _estimate_acquisition_direction.
    """
    from scipy.spatial import cKDTree  # type: ignore

    if anisotropy_ratio <= 0:
        anisotropy_ratio = 1.0

    angle_rad = np.radians(anisotropy_angle)
    cos_a = np.cos(angle_rad)
    sin_a = np.sin(angle_rad)

    def _aniso_dist(px, py, cx, cy):
        """Elliptical distance in acquisition coordinate system."""
        dx = px - cx
        dy = py - cy
        # Rotate into acquisition frame (along-line = X', cross-line = Y')
        dx_rot =  dx * cos_a + dy * sin_a
        dy_rot = -dx * sin_a + dy * cos_a
        return np.sqrt(dx_rot ** 2 + (dy_rot / anisotropy_ratio) ** 2)

    gx = x_min + np.arange(n_x) * resolution
    gy = y_min + np.arange(n_y) * resolution
    gxx, gyy  = np.meshgrid(gx, gy)
    grid_pts  = np.column_stack([gxx.ravel(), gyy.ravel()])

    # Use a slightly enlarged isotropic radius for the KD-tree query
    # (the anisotropic filter is applied afterwards)
    search_r = radius * max(1.0, anisotropy_ratio)
    tree     = cKDTree(np.column_stack([x_pts, y_pts]))
    results  = tree.query_ball_point(grid_pts, r=search_r, workers=-1)

    i_pts_f64 = i_pts.astype(np.float64)
    grid      = np.full(n_x * n_y, np.nan, dtype=np.float32)

    for k, idx_list in enumerate(results):
        if not idx_list:
            continue
        idx = np.asarray(idx_list, dtype=np.int64)
        cx  = grid_pts[k, 0]
        cy  = grid_pts[k, 1]
        d   = _aniso_dist(x_pts[idx], y_pts[idx], cx, cy)
        # Apply the actual anisotropic radius filter
        in_r = d <= radius
        if in_r.sum() < min_points:
            continue
        d_in = d[in_r]
        w    = 1.0 / (d_in ** power + 1e-9)
        ws   = w.sum()
        if ws > 0:
            grid[k] = float(np.dot(w, i_pts_f64[idx[in_r]]) / ws)

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
    auto_variogram: bool = False,
) -> np.ndarray:
    """Ordinary Kriging interpolation onto a regular grid.

    GPRSlice covariance model (exponential variogram):

        cij = c0 + c1          if h == 0
        cij = c1 * exp(-3h/a)  if h >  0

    where c0 = nugget, c1 = partial sill, a = range.

    Parameters
    ----------
    variogram_model : 'exponential' (default, matches GPRSlice formula),
                      'spherical', 'gaussian', 'linear'
    nugget          : nugget effect c0 (used when auto_variogram=False)
    auto_variogram  : if True, pykrige estimates nugget, sill and range
                      automatically from the data of each slice via
                      weighted least-squares fit of the experimental
                      variogram. Recommended for heterogeneous GPR datasets.
                      When True, the `nugget` parameter is ignored.

    Requires: pip install pykrige
    """
    if not _HAS_PYKRIGE:
        raise ImportError(
            "pykrige non e' installato. "
            "Installare con: pip install pykrige"
        )

    gx = x_min + np.arange(n_x) * resolution
    gy = y_min + np.arange(n_y) * resolution

    if auto_variogram:
        # Let pykrige fit nugget, sill and range from the data
        ok = _OrdinaryKriging(
            x_pts,
            y_pts,
            i_pts.astype(np.float64),
            variogram_model=variogram_model,
            nlags=6,
            weight=True,        # weight experimental variogram by pair count
            verbose=False,
            enable_plotting=False,
        )
    else:
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
    """Persist all slicing and interpolation parameters to a JSON sidecar."""
    path = os.path.join(output_dir, SIDECAR_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2, ensure_ascii=True)
    return path


def load_slicer_params(output_dir):
    """Load slicing parameters from the JSON sidecar, or return None."""
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
    kriging_auto_variogram: bool = False,
    # --- GPR anisotropy ---
    use_anisotropic_idw: bool = False,
    anisotropy_ratio: float | None = None,
    anisotropy_angle: float | None = None,
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
    1. [optional] Amplitude outlier removal   (amplitude_sigma)
    2. Interpolation onto regular grid        (IDW isotropic/anisotropic or Kriging)
    3. [optional] Fill NaN inter-line gaps    (fill_nodata)
    4. [optional] Gaussian smoothing          (smooth_sigma > 0)

    Parameters
    ----------
    las_path, output_dir, resolution, z_step, z_min, z_max, radius, epsg,
    value_field, r_field, g_field, b_field : as before.

    interpolation_method  : 'idw' (default) or 'kriging'
    idw_power             : IDW smoothing exponent a (GPRSlice: wi = 1/h^a).
                            Default 2.0.
    min_points            : minimum neighbours to estimate a cell. Default 3.
    kriging_model         : variogram model for Kriging.
                            'exponential' (default) matches GPRSlice formula.
    kriging_nugget        : nugget c0 (used when kriging_auto_variogram=False).
    kriging_auto_variogram: if True, pykrige estimates nugget, sill and range
                            automatically from each slice's data via WLS fit
                            of the experimental variogram. Recommended for
                            heterogeneous GPR datasets. Default False.

    use_anisotropic_idw   : if True, use elliptical IDW distance metric
                            suited for GPR parallel-line acquisitions.
                            Default False.
    anisotropy_ratio      : cross-line / along-line scale factor (>1).
                            If None and use_anisotropic_idw=True, estimated
                            automatically from the data.
    anisotropy_angle      : principal acquisition direction in degrees.
                            If None and use_anisotropic_idw=True, estimated
                            automatically via PCA.

    auto_radius           : estimate search radius from inter-line spacing.
                            Default False.
    amplitude_sigma       : sigma-clipping threshold for GPR amplitude
                            outlier removal. None = disabled. Default None.
    fill_nodata           : fill NaN gaps between lines. Default False.
    fill_nodata_max_distance : max fill distance in grid cells. Default 5.
    smooth_sigma          : Gaussian smoothing sigma (grid cells).
                            0 = disabled. Default 0.0.

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

    # --- Auto-estimate radius and anisotropy from inter-line spacing ---
    _aniso_info = None
    if auto_radius or (use_anisotropic_idw and
                       (anisotropy_ratio is None or anisotropy_angle is None)):
        _aniso_info = _estimate_interline_radius(x, y)

    if auto_radius and _aniso_info is not None:
        radius = _aniso_info["radius"]

    _eff_ratio = anisotropy_ratio
    _eff_angle = anisotropy_angle
    if use_anisotropic_idw:
        if _eff_ratio is None:
            _eff_ratio = (_aniso_info or {}).get("anisotropy_ratio", 1.0)
        if _eff_angle is None:
            _eff_angle = (_aniso_info or {}).get("acquisition_angle", 0.0)

    # --- Build interpolation keyword args ---
    _kw_base = dict(x_min=x_min, y_min=y_min, n_x=n_x, n_y=n_y,
                    resolution=resolution)

    _kw_idw = dict(**_kw_base, radius=radius,
                   power=idw_power, min_points=min_points)

    _kw_idw_aniso = dict(**_kw_idw,
                         anisotropy_ratio=_eff_ratio or 1.0,
                         anisotropy_angle=_eff_angle or 0.0)

    _kw_kriging = dict(**_kw_base,
                       variogram_model=kriging_model,
                       nugget=kriging_nugget,
                       auto_variogram=kriging_auto_variogram)

    def _interp(xm, ym, vm):
        if use_kriging:
            return _bin_with_kriging(xm, ym, vm, **_kw_kriging)
        if use_anisotropic_idw:
            return _bin_with_idw_anisotropic(xm, ym, vm, **_kw_idw_aniso)
        return _bin_with_idw_kdtree(xm, ym, vm, **_kw_idw)

    def _postprocess(grid):
        if fill_nodata:
            grid = _fill_nodata_grid(grid,
                                     max_distance=fill_nodata_max_distance)
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
