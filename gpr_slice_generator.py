"""PDAL-based slice generation utilities for GPR point clouds."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Optional

import numpy as np


def generate_slices_pdal(
    copc_path: str,
    output_dir: str,
    z_min: float,
    z_max: float,
    z_step: float,
    resolution: float = 0.10,
    radius: Optional[float] = None,
    output_type: str = "mean",
    amplitude_field: str = "Intensity",
    crs_epsg: Optional[int] = None,
) -> list[str]:
    """Generate GeoTIFF slices from LAS/COPC using PDAL pipelines."""
    if shutil.which("pdal") is None:
        raise EnvironmentError("pdal non trovato nel PATH. Installare PDAL.")

    if radius is None:
        radius = resolution * (2 ** 0.5)

    os.makedirs(output_dir, exist_ok=True)
    z_centers = np.arange(z_min + z_step / 2.0, z_max, z_step)
    output_paths: list[str] = []

    reader_type = "readers.copc" if copc_path.lower().endswith(".copc.laz") else "readers.las"

    for i, z_center in enumerate(z_centers):
        z_low = z_center - z_step / 2.0
        z_high = z_center + z_step / 2.0

        depth_label = f"{i:04d}_z{z_center:.4f}".replace(".", "_").replace("-", "m")
        out_tif = os.path.join(output_dir, f"slice_{depth_label}.tif")

        writer_step = {
            "type": "writers.gdal",
            "filename": out_tif,
            "gdaldriver": "GTiff",
            "resolution": resolution,
            "radius": radius,
            "output_type": output_type,
            "dimension": amplitude_field,
            "data_type": "float32",
        }
        if crs_epsg is not None:
            writer_step["override_srs"] = f"EPSG:{crs_epsg}"

        pipeline = {
            "pipeline": [
                {"type": reader_type, "filename": copc_path},
                {"type": "filters.range", "limits": f"Z[{z_low:.6f}:{z_high:.6f})"},
                writer_step,
            ]
        }

        pipeline_path = os.path.join(tempfile.gettempdir(), f"gpr_slice_pipeline_{os.getpid()}_{i}.json")
        with open(pipeline_path, "w", encoding="utf-8") as f:
            json.dump(pipeline, f, ensure_ascii=False, indent=2)

        try:
            result = subprocess.run(
                ["pdal", "pipeline", pipeline_path],
                capture_output=True,
                text=True,
                timeout=300,
            )
        finally:
            try:
                os.unlink(pipeline_path)
            except OSError:
                pass

        if result.returncode != 0:
            stderr_lower = (result.stderr or "").lower()
            if "no points" in stderr_lower or "empty" in stderr_lower:
                continue
            raise RuntimeError(f"PDAL errore slice {i}: {(result.stderr or '')[:400]}")

        if os.path.exists(out_tif):
            output_paths.append(out_tif)

    return sorted(output_paths)
