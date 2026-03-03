# -*- coding: utf-8 -*-
"""
Convert LAS/LAZ point clouds to a CF-compliant structured NetCDF mesh
for QgsMeshLayer (MDAL).

Strategy
--------
Each Z slice is written as a separate 2-D variable (y, x).  QGIS/MDAL loads
each variable as one dataset group.  The dial index maps directly to:

    renderer = layer.rendererSettings()
    renderer.setActiveScalarDatasetGroup(z_index)
    layer.setRendererSettings(renderer)
    layer.triggerRepaint()

Binning performance
-------------------
All points are sorted by Z once (O(n log n)).  Per-slice binning uses
np.searchsorted for O(log n) bounds lookup, so total work is O(n_points)
rather than O(n_z * n_points).
"""

from __future__ import annotations

import os

import numpy as np


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _z_var_name(iz: int, z_center: float) -> str:
    """
    Build a valid NetCDF / C-identifier variable name for a Z slice.
    Format: Z_p066_6970_0042  (+66.697 m, slice 42)
             Z_m005_0000_0000  (-5.000 m, slice 0)
    """
    sign = "m" if z_center < 0.0 else "p"
    z_str = f"{abs(z_center):09.4f}".replace(".", "_")
    return f"Z_{sign}{z_str}_{iz:04d}"


# --------------------------------------------------------------------------- #
# Main converter                                                               #
# --------------------------------------------------------------------------- #

def las_to_netcdf_mesh(
    las_path: str,
    output_nc: str,
    resolution: float = 0.10,
    z_step: float = 0.05,
    z_min: float | None = None,
    z_max: float | None = None,
    epsg: int | None = None,
    progress_callback=None,
) -> dict:
    """
    Convert a LAS/LAZ file to a structured-grid NetCDF mesh.

    Parameters
    ----------
    las_path          : Input LAS / LAZ file.
    output_nc         : Output .nc path (parent dir is created if needed).
    resolution        : XY cell size in file coordinate units (metres).
    z_step            : Z slice thickness in same units.
    z_min, z_max      : Z range override (None = use file extents).
    epsg              : EPSG code for CRS metadata (optional).
    progress_callback : callable(current_slice: int, total_slices: int).

    Returns
    -------
    dict:
        output_nc, z_levels (list[float]), n_slices, var_names (list[str]),
        n_x, n_y, x_origin, y_origin, resolution, z_step, epsg
    """
    try:
        import laspy  # type: ignore
    except ImportError:
        raise ImportError("laspy non installato")
    try:
        import netCDF4 as nc4  # type: ignore
    except ImportError:
        raise ImportError("netCDF4 non installato")

    # ------------------------------------------------------------------ #
    # 1. Read point cloud                                                  #
    # ------------------------------------------------------------------ #
    las = laspy.read(las_path)
    x_pts = np.asarray(las.x, dtype=np.float64)
    y_pts = np.asarray(las.y, dtype=np.float64)
    z_pts = np.asarray(las.z, dtype=np.float64)

    try:
        i_pts = np.asarray(las.intensity, dtype=np.float32)
    except Exception:
        i_pts = np.ones(len(x_pts), dtype=np.float32)

    # ------------------------------------------------------------------ #
    # 2. Z range clip                                                      #
    # ------------------------------------------------------------------ #
    z_min_use = float(z_min) if z_min is not None else float(z_pts.min())
    z_max_use = float(z_max) if z_max is not None else float(z_pts.max())

    mask_z = (z_pts >= z_min_use) & (z_pts <= z_max_use)
    x_pts  = x_pts[mask_z]
    y_pts  = y_pts[mask_z]
    z_pts  = z_pts[mask_z]
    i_pts  = i_pts[mask_z]

    if len(x_pts) == 0:
        raise ValueError(
            f"Nessun punto nel range Z [{z_min_use:.4f}, {z_max_use:.4f}]"
        )

    # ------------------------------------------------------------------ #
    # 3. Regular XY grid                                                   #
    # ------------------------------------------------------------------ #
    x_origin = float(x_pts.min())
    y_origin = float(y_pts.min())
    x_end    = float(x_pts.max())
    y_end    = float(y_pts.max())

    x_grid = np.arange(x_origin, x_end + resolution * 0.5, resolution)
    y_grid = np.arange(y_origin, y_end + resolution * 0.5, resolution)
    n_x = len(x_grid)
    n_y = len(y_grid)

    # ------------------------------------------------------------------ #
    # 4. Z levels (slice centres)                                          #
    # ------------------------------------------------------------------ #
    z_levels = np.arange(
        z_min_use + z_step * 0.5,
        z_max_use + z_step * 0.501,   # small epsilon so last step is included
        z_step,
    )
    n_z = len(z_levels)
    if n_z == 0:
        raise ValueError(f"Range Z troppo piccolo per z_step={z_step}")

    # ------------------------------------------------------------------ #
    # 5. Pre-compute grid indices; sort all points by Z for fast slicing   #
    # ------------------------------------------------------------------ #
    xi_all = np.round((x_pts - x_origin) / resolution).astype(np.int32)
    yi_all = np.round((y_pts - y_origin) / resolution).astype(np.int32)
    valid  = (xi_all >= 0) & (xi_all < n_x) & (yi_all >= 0) & (yi_all < n_y)

    xi_v = xi_all[valid]
    yi_v = yi_all[valid]
    z_v  = z_pts[valid]
    i_v  = i_pts[valid]

    sort_idx = np.argsort(z_v, kind="stable")
    xi_v = xi_v[sort_idx]
    yi_v = yi_v[sort_idx]
    z_v  = z_v[sort_idx]
    i_v  = i_v[sort_idx]

    # ------------------------------------------------------------------ #
    # 6. Write NetCDF                                                       #
    # ------------------------------------------------------------------ #
    out_dir = os.path.dirname(os.path.abspath(output_nc))
    os.makedirs(out_dir, exist_ok=True)

    ds = nc4.Dataset(output_nc, "w", format="NETCDF4")
    ds.createDimension("y", n_y)
    ds.createDimension("x", n_x)

    # X coordinate variable (MDAL CF driver recognises axis/standard_name)
    xv = ds.createVariable("x", "f8", ("x",))
    xv[:] = x_grid
    xv.units         = "m"
    xv.long_name     = "X easting"
    xv.standard_name = "projection_x_coordinate"
    xv.axis          = "X"

    # Y coordinate variable
    yv = ds.createVariable("y", "f8", ("y",))
    yv[:] = y_grid
    yv.units         = "m"
    yv.long_name     = "Y northing"
    yv.standard_name = "projection_y_coordinate"
    yv.axis          = "Y"

    # CRS reference variable (CF convention)
    if epsg:
        crs_v = ds.createVariable("crs", "i4")
        crs_v.grid_mapping_name = "transverse_mercator"
        crs_v.epsg_code         = int(epsg)
        crs_v.long_name         = f"CRS EPSG:{epsg}"
        crs_v.crs_wkt           = f"EPSG:{epsg}"

    # Global attributes
    ds.Conventions = "CF-1.6"
    ds.featureType = "grid"
    ds.title       = f"GPR volume \u2013 {os.path.basename(las_path)}"
    ds.source      = las_path
    ds.z_step      = float(z_step)
    ds.resolution  = float(resolution)
    ds.n_slices    = n_z
    if epsg:
        ds.epsg = int(epsg)

    # ------------------------------------------------------------------ #
    # 7. One 2-D variable per Z slice                                       #
    # ------------------------------------------------------------------ #
    var_names: list[str] = []

    for iz, z_center in enumerate(z_levels):
        z_low  = z_center - z_step * 0.5
        z_high = z_center + z_step * 0.5

        lo = int(np.searchsorted(z_v, z_low,  side="left"))
        hi = int(np.searchsorted(z_v, z_high, side="left"))

        vname = _z_var_name(iz, z_center)
        var_names.append(vname)

        v = ds.createVariable(
            vname, "f4", ("y", "x"),
            fill_value=np.nan,
            zlib=True, complevel=4,
        )
        v.long_name     = f"GPR intensity @ Z = {z_center:.4f} m"
        v.units         = "counts"
        v.missing_value = np.nan
        v.z_center      = float(z_center)
        v.slice_index   = iz
        if epsg:
            v.grid_mapping = "crs"

        if hi > lo:
            xi_s = xi_v[lo:hi]
            yi_s = yi_v[lo:hi]
            i_s  = i_v[lo:hi]

            total = np.zeros((n_y, n_x), dtype=np.float64)
            count = np.zeros((n_y, n_x), dtype=np.int32)
            np.add.at(total, (yi_s, xi_s), i_s.astype(np.float64))
            np.add.at(count, (yi_s, xi_s), 1)

            v[:] = np.where(
                count > 0,
                (total / count).astype(np.float32),
                np.nan,
            )
        else:
            v[:] = np.nan  # empty slice

        if progress_callback:
            progress_callback(iz + 1, n_z)

    ds.close()

    return {
        "output_nc":  output_nc,
        "z_levels":   z_levels.tolist(),
        "n_slices":   n_z,
        "var_names":  var_names,
        "n_x":        n_x,
        "n_y":        n_y,
        "x_origin":   x_origin,
        "y_origin":   y_origin,
        "resolution": resolution,
        "z_step":     z_step,
        "epsg":       epsg,
    }
