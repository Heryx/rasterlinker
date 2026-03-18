# -*- coding: utf-8 -*-
"""Pure-python unit tests for OGPR slicer core logic."""

import os
import importlib
import pathlib
import sys
import unittest

import numpy as np

_HERE = pathlib.Path(__file__).resolve()
_PLUGIN_DIR = _HERE.parents[1]
_PLUGIN_PARENT = _PLUGIN_DIR.parent
if str(_PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_PARENT))

_SLICER = importlib.import_module("rasterlinker_feature_interpolation.gpr_ogpr_slicer")
_validate_grid_georef_meta = _SLICER._validate_grid_georef_meta
compute_ogpr_slice_grids = _SLICER.compute_ogpr_slice_grids


try:
    import scipy  # noqa: F401

    HAS_SCIPY = True
except Exception:
    HAS_SCIPY = False


class _FakeChannel:
    def __init__(self, data, easting, northing, altitude):
        self.data = np.asarray(data, dtype=np.float32)
        self.easting = np.asarray(easting, dtype=np.float64)
        self.northing = np.asarray(northing, dtype=np.float64)
        self.altitude = np.asarray(altitude, dtype=np.float64)
        self.distances = None


class _FakeProfile:
    def __init__(
        self,
        path,
        channels,
        easting,
        northing,
        altitude,
        depth_max_m=2.0,
        dt_ns=0.4,
        sampling_step_m=0.05,
        epsg=32633,
    ):
        self.path = str(path)
        self._channels = [
            _FakeChannel(ch, easting=easting, northing=northing, altitude=altitude)
            for ch in channels
        ]
        self.n_channels = int(len(self._channels))
        self.n_samples = int(self._channels[0].data.shape[0]) if self._channels else 0
        self.n_slices = int(self._channels[0].data.shape[1]) if self._channels else 0
        self.depth_max_m = float(depth_max_m)
        self.dt_ns = float(dt_ns)
        self.sampling_step_m = float(sampling_step_m)
        self.epsg = int(epsg)

    def channel(self, idx):
        return self._channels[int(idx)]


def _make_profiles(n_profiles=3, n_channels=3, n_samples=64, n_traces=48):
    rng = np.random.default_rng(42)
    profiles = []
    for p in range(int(n_profiles)):
        x = 443570.0 + np.arange(n_traces, dtype=np.float64) * 0.05
        y = np.full(n_traces, 4548956.0 + p * 0.65, dtype=np.float64)
        zsurf = np.full(n_traces, 120.0 + p * 0.01, dtype=np.float64)

        z = np.linspace(0.0, 1.0, n_samples, dtype=np.float64)[:, None]
        t = np.linspace(0.0, 1.0, n_traces, dtype=np.float64)[None, :]
        chans = []
        for c in range(int(n_channels)):
            base = (
                1400.0 * np.sin(22.0 * z + 0.4 * p + 0.2 * c)
                + 750.0 * np.cos(8.0 * t + 0.25 * c)
                + 120.0 * np.sin(10.0 * z * t * (c + 1.0))
            )
            noise = rng.normal(0.0, 45.0, size=(n_samples, n_traces))
            chans.append((base + noise).astype(np.float32))
        profiles.append(
            _FakeProfile(
                path=f"fake_profile_{p}.ogpr",
                channels=chans,
                easting=x,
                northing=y,
                altitude=zsurf,
                depth_max_m=2.5,
                dt_ns=0.35,
                sampling_step_m=0.05,
                epsg=32633,
            )
        )
    return profiles


@unittest.skipUnless(HAS_SCIPY, "SciPy is required for OGPR slicer interpolation tests")
class GprOgprSlicerTest(unittest.TestCase):
    def setUp(self):
        self.profiles = _make_profiles()

    def test_compute_grids_supports_fast_and_quality_idw_modes(self):
        for mode in ("fast", "quality"):
            grids, meta = compute_ogpr_slice_grids(
                profiles=self.profiles,
                channel=-1,
                combine_method="mean",
                resolution=0.20,
                z_step=0.30,
                z_min=0.0,
                z_max=1.2,
                radius=0.40,
                extraction_mode="las_like",
                use_processing=False,
                idw_mode=mode,
                auto_radius=False,
                min_points=1,
                fill_nodata=False,
                smooth_sigma=0.0,
                emit_diagnostics=False,
                parallel_profiles=False,
            )
            self.assertGreater(len(grids), 0, f"Expected grids for mode={mode}")
            self.assertEqual(str(meta.get("idw_mode")), mode)
            self.assertIn("timing_s", meta)
            self.assertGreaterEqual(float(meta["timing_s"].get("total", -1.0)), 0.0)
            self.assertEqual(int(meta.get("n_z", 0)), len(grids))
            self.assertIn("timing_counts", meta)
            self.assertEqual(
                int(meta["timing_counts"].get("slices_computed", -1)),
                len(grids),
            )

    def test_parallel_profile_processing_metadata(self):
        grids_seq, meta_seq = compute_ogpr_slice_grids(
            profiles=self.profiles,
            resolution=0.20,
            z_step=0.35,
            z_min=0.0,
            z_max=1.2,
            radius=0.45,
            extraction_mode="las_like",
            use_processing=False,
            idw_mode="fast",
            auto_radius=False,
            emit_diagnostics=False,
            parallel_profiles=False,
            profile_workers=0,
        )
        self.assertGreater(len(grids_seq), 0)
        self.assertFalse(bool(meta_seq.get("parallel_profiles", True)))
        self.assertEqual(int(meta_seq.get("profile_workers_used", -1)), 1)

        grids_par, meta_par = compute_ogpr_slice_grids(
            profiles=self.profiles,
            resolution=0.20,
            z_step=0.35,
            z_min=0.0,
            z_max=1.2,
            radius=0.45,
            extraction_mode="las_like",
            use_processing=False,
            idw_mode="fast",
            auto_radius=False,
            emit_diagnostics=False,
            parallel_profiles=True,
            profile_workers=2,
        )
        self.assertGreater(len(grids_par), 0)
        self.assertIn("parallel_profiles", meta_par)
        self.assertGreaterEqual(int(meta_par.get("profile_workers_used", 0)), 1)

    def test_georef_validation_rejects_invalid_metadata(self):
        with self.assertRaises(ValueError):
            _validate_grid_georef_meta(
                {"x_min": 0.0, "y_min": 10.0, "y_max": 10.0, "resolution": 0.2}
            )

        with self.assertRaises(ValueError):
            _validate_grid_georef_meta(
                {"x_min": 0.0, "y_min": 0.0, "y_max": 10.0, "resolution": -0.2}
            )

    def test_georef_validation_accepts_computed_metadata(self):
        grids, meta = compute_ogpr_slice_grids(
            profiles=self.profiles,
            resolution=0.25,
            z_step=0.4,
            z_min=0.0,
            z_max=1.2,
            radius=0.45,
            extraction_mode="las_like",
            use_processing=False,
            idw_mode="fast",
            auto_radius=False,
            emit_diagnostics=False,
            parallel_profiles=False,
        )
        self.assertGreater(len(grids), 0)
        _validate_grid_georef_meta(meta, grid=np.asarray(grids[0]["grid"]))


if __name__ == "__main__":
    unittest.main()
