# -*- coding: utf-8 -*-
"""Unit tests for 3D volume helpers built from OGPR timeslice grids."""

import unittest

import numpy as np

from gpr_volume_3d import (
    build_3d_volume,
    extract_b_scan_crossline,
    extract_b_scan_inline,
    extract_c_scan,
    volume_axes,
)


class GprVolume3DTest(unittest.TestCase):
    def _sample(self):
        g0 = np.array([[1, 2], [3, 4]], dtype=np.float32)
        g1 = np.array([[10, 20], [30, 40]], dtype=np.float32)
        grids = [
            {"index": 1, "z_lev": 1.0, "grid": g1},
            {"index": 0, "z_lev": 0.0, "grid": g0},
        ]
        meta = {"x_min": 100.0, "y_min": 200.0, "resolution": 0.5}
        return grids, meta

    def test_build_volume_sorts_by_index(self):
        grids, _ = self._sample()
        vol = build_3d_volume(grids)
        self.assertEqual(vol.shape, (2, 2, 2))
        self.assertTrue(np.allclose(vol[0], np.array([[1, 2], [3, 4]], dtype=np.float32)))
        self.assertTrue(np.allclose(vol[1], np.array([[10, 20], [30, 40]], dtype=np.float32)))

    def test_axes_and_sections(self):
        grids, meta = self._sample()
        vol = build_3d_volume(grids)
        z_axis, y_axis, x_axis = volume_axes(grids, meta)
        self.assertTrue(np.allclose(z_axis, np.array([0.0, 1.0], dtype=np.float32)))
        self.assertTrue(np.allclose(y_axis, np.array([200.0, 200.5], dtype=np.float32)))
        self.assertTrue(np.allclose(x_axis, np.array([100.0, 100.5], dtype=np.float32)))

        cscan = extract_c_scan(vol, 1)
        inline = extract_b_scan_inline(vol, 0)
        crossline = extract_b_scan_crossline(vol, 1)

        self.assertEqual(cscan.shape, (2, 2))
        self.assertEqual(inline.shape, (2, 2))
        self.assertEqual(crossline.shape, (2, 2))
        self.assertTrue(np.allclose(cscan, np.array([[10, 20], [30, 40]], dtype=np.float32)))


if __name__ == "__main__":
    unittest.main()
