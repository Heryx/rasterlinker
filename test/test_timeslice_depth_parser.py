# coding=utf-8
"""Unit tests for timeslice depth filename parser."""

import unittest

from timeslice_depth_parser import TimesliceDepthParser


class TimesliceDepthParserTest(unittest.TestCase):
    def setUp(self):
        self.parser = TimesliceDepthParser(unit="m")

    def test_extract_numbers_supports_commas_and_integers(self):
        values = self.parser.extract_numbers("0050-depth(2,30)m.tif")
        self.assertEqual(values, [50.0, 2.3])

    def test_parse_depth_index_and_status(self):
        depth, status = self.parser.parse_depth("GPR_survey3_depth_0.25_group2.tif", token_index=1)
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(depth, 0.25)

    def test_parse_depth_not_found(self):
        depth, status = self.parser.parse_depth("slice_no_depth.tif", token_index=0)
        self.assertIsNone(depth)
        self.assertEqual(status, "not_found")

    def test_parse_depth_index_out_of_range(self):
        depth, status = self.parser.parse_depth("slice_0.10_group1.tif", token_index=5)
        self.assertIsNone(depth)
        self.assertEqual(status, "index_out_of_range")

    def test_manual_override_has_priority(self):
        self.parser.set_manual_override("slice_0.10_group1.tif", 9.5)
        depth, status = self.parser.parse_depth("slice_0.10_group1.tif", token_index=0)
        self.assertEqual(status, "manual")
        self.assertAlmostEqual(depth, 9.5)

        self.parser.clear_manual_override("slice_0.10_group1.tif")
        depth, status = self.parser.parse_depth("slice_0.10_group1.tif", token_index=0)
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(depth, 0.1)

    def test_parse_batch_marks_incoherent_values(self):
        files = [
            "slice_0.10.tif",
            "slice_0.20.tif",
            "slice_0.30.tif",
            "slice_0.95.tif",
        ]
        rows = self.parser.parse_batch(files, token_index=0, delta_tolerance=0.3)
        by_file = {r["file"]: r for r in rows}
        self.assertEqual(by_file["slice_0.95.tif"]["status"], "incoherent")
        self.assertEqual(by_file["slice_0.10.tif"]["status"], "ok")

    def test_preview_contains_selected_token(self):
        preview = self.parser.preview("slice-0.10_group1.tif", token_index=1)
        self.assertEqual(preview["numbers"], [0.1, 1.0])
        self.assertEqual(preview["selected"], 1.0)
        self.assertEqual(preview["status"], "ok")
        self.assertEqual(preview["unit"], "m")


if __name__ == "__main__":
    unittest.main()
