"""LAS/LAZ → GeoTIFF slice generator with IDW interpolation."""

from __future__ import annotations

import json
import os

import numpy as np


# ---------------------------------------------------------------------------
# LAS reading
# ---------------------------------------------------------------------------

def _read_las_arrays(las_path: str):
    """
    Read X, Y, Z, intensity from LAS/LAZ using laspy 2.x.
    Returns (x, y, z, intensity) as float64/float32 numpy arrays.
    """
    import laspy  # type: ignore

    las = laspy.read(las_path)
    x = np.asarray(las.x, dtype=np.float64)
    y = np.asarray(las.y, dtype=np.float64)
    z = np.asarray(las.z, dtype=np.float64)

    intensity = None
    for field in ("intensity", "Intensity", "INTENSITY"):
        try:
            val = np.asarray(getattr(las, field), dtype=np.float32)
            if val.size == len(x):
                intensity = val
                break
        except Exception:
            pass

    if intensity is None:
        intensity = np.ones(len(x), dtype=np.float32)

    return x, y, z, intensity


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
    Bin point intensities onto a regular grid using IDW within `radius`.

    For each grid cell, accumulates contributions from all points
    within `radius` metres, weighted by 1/distance^2 (IDW-2).
    Cells with no contributing points are left as NaN.

    When radius <= resolution/2, falls back to fast nearest-cell mean
    (no distance weighting needed).
    """
    total = np.zeros((n_y, n_x), dtype=np.float64)
    wsum  = np.zeros((n_y, n_x), dtype=np.float64)

    r_cells = max(0, int(np.ceil(radius / resolution)))

    for diy in range(-r_cells, r_cells + 1):
        for dix in range(-r_cells, r_cells + 1):
            # Candidate cell for each point, shifted by (dix, diy)
            xi = np.round((x_pts - x_min) / resolution).astype(np.int64) + dix
            yi = np.round((y_pts - y_min) / resolution).astype(np.int64) + diy

            # Distance from each point to the shifted cell centre
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

            # IDW weight: 1/dist^2 (with small epsilon to avoid /0 at dist==0)
            w = 1.0 / (dist_v ** 2 + 1e-9)

            np.add.at(total, (yi_v, xi_v), w * i_v)
            np.add.at(wsum,  (yi_v, xi_v), w)

    grid = np.full((n_y, n_x), np.nan, dtype=np.float32)
    has_data = wsum > 0
    grid[has_data] = (total[has_data] / wsum[has_data]).astype(np.float32)
    return grid


# ---------------------------------------------------------------------------
# GeoTIFF writer (uses GDAL, always available in QGIS)
# ---------------------------------------------------------------------------

def _write_tif(
    grid: np.ndarray,   # shape (n_y, n_x), NaN = nodata
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
    # Top-left corner = (x_min - res/2, y_max + res/2); pixel = resolution
    ds.SetGeoTransform(
        (x_min - resolution / 2.0, resolution, 0.0,
         y_max + resolution / 2.0, 0.0, -resolution)
    )
    if epsg:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(int(epsg))
        ds.SetProjection(srs.ExportToWkt())

    band = ds.GetRasterBand(1)
    band.WriteArray(np.flipud(data))  # flip: row 0 = northernmost
    band.SetNoDataValue(NODATA)
    band.FlushCache()
    ds.FlushCache()
    ds = None


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
) -> list[dict]:
    """
    Slice a LAS/LAZ file into N GeoTIFF rasters (one per Z interval).

    Parameters
    ----------
    las_path    : path to source LAS/LAZ
    output_dir  : directory where TIFFs are written
    resolution  : XY grid cell size (metres)
    z_step      : thickness of each Z slice (metres)
    z_min/z_max : Z range (auto-detected from data if None)
    radius      : IDW search radius (metres); defaults to resolution * sqrt(2)
    epsg        : EPSG code for the output CRS

    Returns
    -------
    list of dicts, one per slice:
        path      : absolute path to GeoTIFF
        z_from    : bottom of slice (m)
        z_to      : top of slice (m)
        z_center  : centre of slice (m)
        index     : 0-based slice index
        name      : filename stem
    """
    if radius is None:
        radius = resolution * (2 ** 0.5)

    os.makedirs(output_dir, exist_ok=True)

    x, y, z, intensity = _read_las_arrays(las_path)

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
    results = []

    for iz, z_lev in enumerate(z_levels):
        z_from = float(z_lev - z_step / 2.0)
        z_to   = float(z_lev + z_step / 2.0)

        mask = (z >= z_from) & (z < z_to)
        if not np.any(mask):
            continue  # empty slice — skip (no TIF)

        grid = _bin_with_idw(
            x[mask], y[mask], intensity[mask],
            x_min, y_min, n_x, n_y, resolution, radius,
        )

        z_label  = f"{z_lev:.4f}".replace(".", "_").replace("-", "m")
        tif_name = f"slice_{iz:04d}_z{z_label}.tif"
        tif_path = os.path.join(output_dir, tif_name)

        _write_tif(grid, tif_path, x_min, y_min, y_max, resolution, epsg)

        results.append({
            "path":     tif_path,
            "z_from":   round(z_from, 6),
            "z_to":     round(z_to,   6),
            "z_center": round(float(z_lev), 6),
            "index":    iz,
            "name":     os.path.splitext(tif_name)[0],
        })

    return results
