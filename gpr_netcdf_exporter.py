"""LAS → NetCDF UGRID mesh exporter (primary) and multiband GeoTIFF fallback."""

from __future__ import annotations

import os

import numpy as np


# ---------------------------------------------------------------------------
# Shared: read LAS and bin intensity onto a regular 2D grid per Z slice
# ---------------------------------------------------------------------------

def _read_las_arrays(las_path: str):
    """
    Read X, Y, Z, intensity from a LAS/LAZ file using laspy.
    Returns (x, y, z, intensity) as float64/float32 numpy arrays.
    """
    import laspy  # type: ignore

    las = laspy.read(las_path)
    x = np.asarray(las.x, dtype=np.float64)
    y = np.asarray(las.y, dtype=np.float64)
    z = np.asarray(las.z, dtype=np.float64)

    # intensity: try lowercase (laspy 2.x standard) then uppercase variants
    intensity = None
    for field in ("intensity", "Intensity", "INTENSITY"):
        try:
            intensity = np.asarray(getattr(las, field), dtype=np.float32)
            if intensity.size == len(x):
                break
        except Exception:
            intensity = None

    if intensity is None or intensity.size != len(x):
        intensity = np.ones(len(x), dtype=np.float32)

    return x, y, z, intensity


def _build_grid_params(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    resolution: float,
    z_step: float,
    z_min: float | None,
    z_max: float | None,
):
    """
    Compute grid dimensions and Z levels.
    Returns dict with x_min, y_min, x_centers, y_centers, z_levels, n_x, n_y, n_z.
    """
    x_min = float(x.min())
    y_min = float(y.min())
    x_max = float(x.max())
    y_max = float(y.max())
    if z_min is None:
        z_min = float(z.min())
    if z_max is None:
        z_max = float(z.max())

    x_centers = np.arange(x_min, x_max + resolution * 0.5, resolution)
    y_centers = np.arange(y_min, y_max + resolution * 0.5, resolution)
    z_levels = np.arange(z_min, z_max + z_step * 0.5, z_step)

    return {
        "x_min": x_min,
        "y_min": y_min,
        "x_max": x_max,
        "y_max": y_max,
        "x_centers": x_centers,
        "y_centers": y_centers,
        "z_levels": z_levels,
        "n_x": len(x_centers),
        "n_y": len(y_centers),
        "n_z": len(z_levels),
        "z_min": z_min,
        "z_max": z_max,
    }


def _bin_intensity(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    intensity: np.ndarray,
    gp: dict,
    resolution: float,
    z_step: float,
) -> list:
    """
    Bin intensity values onto a regular 2D grid for each Z slice.
    Returns list of n_z float32 arrays shaped (n_y, n_x), NaN where no points.
    """
    n_x = gp["n_x"]
    n_y = gp["n_y"]
    z_levels = gp["z_levels"]
    x_min = gp["x_min"]
    y_min = gp["y_min"]

    data = []
    for z_lev in z_levels:
        z_low = z_lev - z_step / 2.0
        z_high = z_lev + z_step / 2.0
        mask = (z >= z_low) & (z < z_high)

        grid = np.full((n_y, n_x), np.nan, dtype=np.float32)
        if np.any(mask):
            x_sel = x[mask]
            y_sel = y[mask]
            i_sel = intensity[mask]

            xi = np.clip(
                np.round((x_sel - x_min) / resolution).astype(np.int64), 0, n_x - 1
            )
            yi = np.clip(
                np.round((y_sel - y_min) / resolution).astype(np.int64), 0, n_y - 1
            )

            total = np.zeros((n_y, n_x), dtype=np.float64)
            count = np.zeros((n_y, n_x), dtype=np.int32)
            np.add.at(total, (yi, xi), i_sel.astype(np.float64))
            np.add.at(count, (yi, xi), 1)
            where_valid = count > 0
            grid[where_valid] = (total[where_valid] / count[where_valid]).astype(np.float32)

        data.append(grid)
    return data


# ---------------------------------------------------------------------------
# Primary: NetCDF UGRID mesh (QgsMeshLayer / MDAL)
# ---------------------------------------------------------------------------

def las_to_netcdf_mesh(
    las_path: str,
    output_nc: str,
    resolution: float = 0.10,
    z_step: float = 0.05,
    z_min: float | None = None,
    z_max: float | None = None,
    epsg: int | None = None,
    crs_wkt: str | None = None,
) -> dict:
    """
    Convert a LAS/LAZ point cloud to a UGRID-1.0 compliant NetCDF file
    readable as a QgsMeshLayer.

    Dataset groups layout (one per Z level):
        Z_0000 → shallowest Z level
        Z_0001 → next level
        ...
    Each group has a single face-centred intensity dataset.
    The QGIS dial drives setActiveScalarDatasetGroup(group_index).

    Returns metadata dict: output_path, z_levels, n_z, n_x, n_y, epsg.
    """
    import netCDF4 as nc  # type: ignore

    x, y, z, intensity = _read_las_arrays(las_path)
    gp = _build_grid_params(x, y, z, resolution, z_step, z_min, z_max)
    data_list = _bin_intensity(x, y, z, intensity, gp, resolution, z_step)

    n_x = gp["n_x"]
    n_y = gp["n_y"]
    n_z = gp["n_z"]
    x_min = gp["x_min"]
    y_min = gp["y_min"]
    x_centers = gp["x_centers"]
    y_centers = gp["y_centers"]
    z_levels = gp["z_levels"]

    n_faces = n_x * n_y
    n_nodes = (n_x + 1) * (n_y + 1)
    n_node_cols = n_x + 1
    FILL = np.float32(-9999.0)

    # --- NetCDF file ---------------------------------------------------
    ds = nc.Dataset(output_nc, "w", format="NETCDF4")

    ds.createDimension("nMesh2d_node", n_nodes)
    ds.createDimension("nMesh2d_face", n_faces)
    ds.createDimension("nMaxMesh2d_face_nodes", 4)

    # Topology variable (tells MDAL this is a UGRID mesh)
    mv = ds.createVariable("Mesh2d", "i4")
    mv.cf_role = "mesh_topology"
    mv.long_name = "GPR 2D structured quad mesh"
    mv.topology_dimension = np.int32(2)
    mv.node_coordinates = "Mesh2d_node_x Mesh2d_node_y"
    mv.face_node_connectivity = "Mesh2d_face_nodes"
    mv.face_coordinates = "Mesh2d_face_x Mesh2d_face_y"
    mv[:] = np.int32(0)

    # Node coordinates (cell corners)
    x_nodes = x_min - resolution / 2.0 + np.arange(n_node_cols) * resolution
    y_nodes = y_min - resolution / 2.0 + np.arange(n_y + 1) * resolution
    xx_n, yy_n = np.meshgrid(x_nodes, y_nodes)  # (n_y+1, n_x+1)

    vx = ds.createVariable("Mesh2d_node_x", "f8", ("nMesh2d_node",))
    vx[:] = xx_n.ravel()
    vx.standard_name = "projection_x_coordinate"
    vx.units = "m"
    vx.long_name = "x-coordinate of mesh nodes"

    vy = ds.createVariable("Mesh2d_node_y", "f8", ("nMesh2d_node",))
    vy[:] = yy_n.ravel()
    vy.standard_name = "projection_y_coordinate"
    vy.units = "m"
    vy.long_name = "y-coordinate of mesh nodes"

    # Face centres
    xx_f, yy_f = np.meshgrid(x_centers, y_centers)  # (n_y, n_x)

    fx = ds.createVariable("Mesh2d_face_x", "f8", ("nMesh2d_face",))
    fx[:] = xx_f.ravel()
    fx.standard_name = "projection_x_coordinate"
    fx.units = "m"

    fy = ds.createVariable("Mesh2d_face_y", "f8", ("nMesh2d_face",))
    fy[:] = yy_f.ravel()
    fy.standard_name = "projection_y_coordinate"
    fy.units = "m"

    # Face–node connectivity (CCW: SW, SE, NE, NW)
    IY, IX = np.meshgrid(np.arange(n_y), np.arange(n_x), indexing="ij")
    iy_f = IY.ravel()
    ix_f = IX.ravel()
    n_sw = iy_f * n_node_cols + ix_f
    n_se = n_sw + 1
    n_ne = n_sw + n_node_cols + 1
    n_nw = n_sw + n_node_cols
    face_conn = np.column_stack([n_sw, n_se, n_ne, n_nw]).astype(np.int32)

    fn = ds.createVariable(
        "Mesh2d_face_nodes", "i4", ("nMesh2d_face", "nMaxMesh2d_face_nodes")
    )
    fn[:] = face_conn
    fn.cf_role = "face_node_connectivity"
    fn.start_index = np.int32(0)
    fn.long_name = "Vertex nodes of each face"

    # CRS variable
    grid_mapping_name = "unknown_crs"
    if epsg:
        crs_v = ds.createVariable("crs", "i4")
        crs_v.epsg_code = f"EPSG:{epsg}"
        crs_v.long_name = "coordinate reference system"
        if crs_wkt:
            crs_v.spatial_ref = crs_wkt
            crs_v.crs_wkt = crs_wkt
        grid_mapping_name = "crs"

    # Data variables — one per Z level (= one MDAL dataset group each)
    for iz, z_lev in enumerate(z_levels):
        vname = f"Z_{iz:04d}"
        dv = ds.createVariable(
            vname,
            "f4",
            ("nMesh2d_face",),
            fill_value=FILL,
            zlib=True,
            complevel=4,
        )
        dv.long_name = f"GPR intensity at Z = {z_lev:.4f} m"
        dv.units = "counts"
        dv.location = "face"
        dv.mesh = "Mesh2d"
        dv.coordinates = "Mesh2d_face_x Mesh2d_face_y"
        if epsg:
            dv.grid_mapping = grid_mapping_name

        flat = data_list[iz].ravel()
        flat = np.where(np.isnan(flat), FILL, flat)
        dv[:] = flat

    ds.Conventions = "CF-1.6 UGRID-1.0"
    ds.title = f"GPR mesh volume from {os.path.basename(las_path)}"
    ds.history = f"Created by GeoSurvey Studio gpr_netcdf_exporter"
    ds.close()

    return {
        "output_path": output_nc,
        "z_levels": z_levels.tolist(),
        "n_z": int(n_z),
        "n_x": int(n_x),
        "n_y": int(n_y),
        "x_min": float(x_min),
        "y_min": float(y_min),
        "resolution": float(resolution),
        "epsg": epsg,
    }


# ---------------------------------------------------------------------------
# Fallback: multiband GeoTIFF (no external deps beyond GDAL which QGIS has)
# ---------------------------------------------------------------------------

def las_to_multiband_tif(
    las_path: str,
    output_tif: str,
    resolution: float = 0.10,
    z_step: float = 0.05,
    z_min: float | None = None,
    z_max: float | None = None,
    epsg: int | None = None,
) -> dict:
    """
    Convert LAS to a multiband GeoTIFF (1 band per Z slice).
    Used as fallback when netCDF4 is not available.
    Loaded as QgsRasterLayer; dial switches the rendered band.
    """
    from osgeo import gdal, osr  # type: ignore

    x, y, z, intensity = _read_las_arrays(las_path)
    gp = _build_grid_params(x, y, z, resolution, z_step, z_min, z_max)
    data_list = _bin_intensity(x, y, z, intensity, gp, resolution, z_step)

    n_x = gp["n_x"]
    n_y = gp["n_y"]
    n_z = gp["n_z"]
    x_min = gp["x_min"]
    y_min = gp["y_min"]
    y_max = gp["y_max"]
    z_levels = gp["z_levels"]
    NODATA = -9999.0

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(
        output_tif,
        n_x,
        n_y,
        n_z,
        gdal.GDT_Float32,
        options=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"],
    )

    # GeoTransform: top-left corner, positive X, negative Y
    gt = (
        x_min - resolution / 2.0,
        resolution,
        0.0,
        y_max + resolution / 2.0,
        0.0,
        -resolution,
    )
    ds.SetGeoTransform(gt)

    if epsg:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(int(epsg))
        ds.SetProjection(srs.ExportToWkt())

    for iz, z_lev in enumerate(z_levels):
        band = ds.GetRasterBand(iz + 1)
        # Raster Y=0 is top (north) → flip the grid (stored bottom-first)
        band.WriteArray(np.flipud(data_list[iz]))
        band.SetNoDataValue(NODATA)
        band.SetDescription(f"Z={z_lev:.4f}m")
        band.FlushCache()

    ds.FlushCache()
    ds = None

    return {
        "output_path": output_tif,
        "z_levels": z_levels.tolist(),
        "n_z": int(n_z),
        "n_x": int(n_x),
        "n_y": int(n_y),
        "x_min": float(x_min),
        "y_min": float(y_min),
        "resolution": float(resolution),
        "epsg": epsg,
    }
