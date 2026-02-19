# -*- coding: utf-8 -*-
"""Unit tests for table models used by RasterLinker."""

import unittest


try:
    from PyQt5.QtCore import Qt

    from trace_info_table_model import TraceInfoTableModel
    from timeslice_group_table_models import GroupTableModel, TimesliceTableModel

    HAS_QT = True
except Exception:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "Qt bindings are not available")
class TableModelsTest(unittest.TestCase):
    def test_trace_info_model_exposes_display_and_payload(self):
        model = TraceInfoTableModel()
        rows = [
            {
                "fid": 7,
                "trace_id": "tr_007",
                "timeslice": "slice_01",
                "depth_text": "0.1 - 0.2 m",
                "z_mode": "depth_range",
                "length_text": "12.45",
            }
        ]
        model.set_rows(rows)

        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.columnCount(), 6)

        idx_trace = model.index(0, 1)
        self.assertEqual(model.data(idx_trace, Qt.DisplayRole), "tr_007")

        idx_payload = model.index(0, 0)
        payload = model.data(idx_payload, Qt.UserRole)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload.get("fid"), 7)

    def test_timeslice_model_sort_and_payload(self):
        model = TimesliceTableModel()
        model.set_rows(
            [
                {
                    "id": "ts_2",
                    "name": "B",
                    "depth_range": "0.2-0.3 m",
                    "groups": "G2",
                    "crs": "EPSG:32633",
                    "assigned_crs": "",
                    "project_path": "b.tif",
                    "exists": "Yes",
                },
                {
                    "id": "ts_1",
                    "name": "A",
                    "depth_range": "0.1-0.2 m",
                    "groups": "G1",
                    "crs": "EPSG:32633",
                    "assigned_crs": "",
                    "project_path": "a.tif",
                    "exists": "No",
                },
            ]
        )

        model.sort(1, Qt.AscendingOrder)  # by name
        first = model.row_payload(0)
        second = model.row_payload(1)
        self.assertEqual(first.get("id"), "ts_1")
        self.assertEqual(second.get("id"), "ts_2")

    def test_group_model_numeric_sort(self):
        model = GroupTableModel()
        model.set_rows(
            [
                {"id": "g1", "name": "Gamma", "timeslice_count": 10},
                {"id": "g2", "name": "Alpha", "timeslice_count": 2},
                {"id": "g3", "name": "Beta", "timeslice_count": 5},
            ]
        )

        model.sort(2, Qt.AscendingOrder)  # timeslice_count
        ordered = [model.row_payload(i).get("id") for i in range(model.rowCount())]
        self.assertEqual(ordered, ["g2", "g3", "g1"])


if __name__ == "__main__":
    unittest.main()

