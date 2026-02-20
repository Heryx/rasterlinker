# -*- coding: utf-8 -*-
"""Qt table model for GeoSurvey Studio line info rows."""

from qgis.PyQt.QtCore import QAbstractTableModel, Qt


class TraceInfoTableModel(QAbstractTableModel):
    HEADERS = ["FID", "Trace ID", "Time-slice", "Depth", "Z mode", "Length"]
    COLUMN_KEYS = ("fid", "trace_id", "timeslice", "depth_text", "z_mode", "length_text")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []

    def rowCount(self, parent=None):
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent=None):
        if parent is not None and parent.isValid():
            return 0
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        if orientation == Qt.Vertical:
            return section + 1
        return None

    def data(self, index, role=Qt.DisplayRole):
        if index is None or not index.isValid():
            return None
        row_idx = index.row()
        col_idx = index.column()
        if row_idx < 0 or row_idx >= len(self._rows):
            return None
        if col_idx < 0 or col_idx >= len(self.COLUMN_KEYS):
            return None
        row = self._rows[row_idx]

        if role in (Qt.DisplayRole, Qt.EditRole):
            key = self.COLUMN_KEYS[col_idx]
            value = row.get(key, "")
            return "" if value is None else str(value)

        if role == Qt.UserRole:
            return row

        if role == Qt.TextAlignmentRole and col_idx in (0, 3, 5):
            return int(Qt.AlignRight | Qt.AlignVCenter)

        return None

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = list(rows or [])
        self.endResetModel()

    def row_payload(self, row_idx):
        if row_idx < 0 or row_idx >= len(self._rows):
            return None
        return self._rows[row_idx]
