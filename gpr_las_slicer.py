"""LAS/LAZ → GeoTIFF slice generator with IDW interpolation.

Field diagnosis reads only the first N points (fast) so the selection
dialog can be shown before loading the full file.
"""

from __future__ import annotations

import json
import os

import numpy as np


# ---------------------------------------------------------------------------
# LAS field diagnosis  (fast: reads first n_sample points only)
# ---------------------------------------------------------------------------

# Fields that carry useful raster values for GPR/geophysics
_USEFUL_FIELDS = (
    "intensity", "red", "green", "blue",
    "amplitude", "reflectance", "deviation", "signal",
)
# Fields that are always present but never useful as raster values
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
    Quick diagnosis of available value fields in a LAS/LAZ file.
    Reads only the first `n_sample` points.

    Returns a dict:
        {
          "available": [(name, min, max, has_data: bool), ...],
          "suggested": field_name,
          "point_format": int,
          "n_points": int,
        }
    """
    import laspy  # type: ignore

    result: dict = {
        "available":    [],
        "suggested":    "intensity",
        "point_format": -1,
        "n_points":     0,
    }

    try:
        with laspy.LasReader(las_path) as reader:
            result["point_format"] = int(reader.header.point_format.id)
            result["n_points"] = int(reader.header.point_count)

            fmt = reader.header.point_format
            all_dims = list(fmt.dimension_names)

            # Extra bytes fields
            try:
                for ed in fmt.extra_dims:
                    all_dims.append(ed.name)
            except Exception:
                pass

            # Read first chunk for value estimation
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
                    arr = np.asarray(getattr(sample or reader, name), dtype=np.float64)
                    finite = arr[np.isfinite(arr)]
                    if finite.size == 0:
                        continue
                    mn = float(finite.min())
                    mx = float(finite.max())
                    has_data = mx > mn or mx > 0
                    result["available"].append((name, mn, mx, has_data))
                except Exception:
                    pass

    except Exception:
        # Minimal fallback: report intensity with unknown range
        result["available"] = [("intensity", 0.0, 0.0, False)]
        return result

    # Sort: useful fields with data first, then others
    def _sort_key(entry):
        name, mn, mx, has_data = entry
        prio = 0 if name in _USEFUL_FIELDS else 1
        has = 0 if has_data else 1
        return (prio, has, name)

    result["available"].sort(key=_sort_key)

    # Auto-suggest: first field in priority list that has data
    for name, mn, mx, has_data in result["available"]:
        if has_data:
            result["suggested"] = name
            break

    return result


# ---------------------------------------------------------------------------
# LAS reading (full file)
# ---------------------------------------------------------------------------

def _read_las_arrays(las_path: str, value_field: str = "intensity"):
    """
    Read X, Y, Z, and the chosen value field from LAS/LAZ.
    Returns (x, y, z, values) as float64/float32 numpy arrays.
    """
    import laspy  # type: ignore

    las = laspy.read(las_path)
    x = np.asarray(las.x, dtype=np.float64)
    y = np.asarray(las.y, dtype=np.float64)
    z = np.asarray(las.z, dtype=np.float64)

    values = None
    try:
        raw = np.asarray(getattr(las, value_field), dtype=np.float32)
        if raw.size == len(x):
            values = raw
    except Exception:
        pass

    if values is None:
        # Fallback: try common aliases
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
    """
    Bin point values onto a regular grid using IDW-2 within `radius`.
    Cells with no contributing points are NaN.
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
            dist2  = (x_pts - x_cell) ** 2 + (y_pts - y_cell) ** 2
            dist   = np.sqrt(dist2)

            in_bounds = (
                (xi >= 0) & (xi < n_x) &
                (yi >= 0) & (yi < n_y) &
                (dist <= radius)
            )
            if not np.any(in_bounds):
                continue

            xi_v   = xi[in_bounds]
            yi_v   = yi[in_bounds]
            i_v    = i_pts[in_bounds].astype(np.float64)
            dist_v = dist[in_bounds]
            w = 1.0 / (dist_v ** 2 + 1e-9)

            np.add.at(total, (yi_v, xi_v), w * i_v)
            np.add.at(wsum,  (yi_v, xi_v), w)

    grid = np.full((n_y, n_x), np.nan, dtype=np.float32)
    has_data = wsum > 0
    grid[has_data] = (total[has_data] / wsum[has_data]).astype(np.float32)
    return grid


# ---------------------------------------------------------------------------
# GeoTIFF writer
# ---------------------------------------------------------------------------

def _write_tif(
    grid: np.ndarray,
    out_path: str,
    x_min: float,
    y_min: float,
    y_max: float,
    resolution: float,
    epsg: int | None,
) -> None:
    from osgeo import gdal, osr  # type: ignore

    n_y, n_x = grid.shape
    NODATA = -9999.0
    data = np.where(np.isnan(grid), NODATA, grid).astype(np.float32)

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


# ---------------------------------------------------------------------------
# Sidecar
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
    Slice a LAS/LAZ file into N GeoTIFF rasters (one per Z interval).
    Uses IDW interpolation within `radius` metres.

    Returns list of dicts:
        path, z_from, z_to, z_center, index, name
    """
    if radius is None:
        radius = resolution * (2 ** 0.5)

    os.makedirs(output_dir, exist_ok=True)

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

        grid = _bin_with_idw(
            x[mask], y[mask], values[mask],
            x_min, y_min, n_x, n_y, resolution, radius,
        )

        z_label  = f"{z_lev:.4f}".replace(".", "_").replace("-", "m")
        tif_name = f"slice_{iz:04d}_z{z_label}.tif"
        tif_path = os.path.join(output_dir, tif_name)

        _write_tif(grid, tif_path, x_min, y_min, y_max, resolution, epsg)

        results.append({
            "path":      tif_path,
            "z_from":    round(z_from, 6),
            "z_to":      round(z_to,   6),
            "z_center":  round(float(z_lev), 6),
            "index":     iz,
            "name":      os.path.splitext(tif_name)[0],
        })

    return results
