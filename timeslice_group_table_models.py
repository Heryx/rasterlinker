# -*- coding: utf-8 -*-
"""Table models for Time-slice / Group manager."""

from PyQt5.QtCore import QAbstractTableModel, Qt


class _BaseRowsModel(QAbstractTableModel):
    HEADERS = []
    COLUMN_KEYS = ()
    NUMERIC_KEYS = set()

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
        key = self.COLUMN_KEYS[col_idx]

        if role in (Qt.DisplayRole, Qt.EditRole):
            value = row.get(key, "")
            return "" if value is None else str(value)

        if role == Qt.UserRole:
            return row

        return None

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = list(rows or [])
        self.endResetModel()

    def row_payload(self, row_idx):
        if row_idx < 0 or row_idx >= len(self._rows):
            return None
        return self._rows[row_idx]

    def sort(self, column, order=Qt.AscendingOrder):
        if column < 0 or column >= len(self.COLUMN_KEYS):
            return
        key = self.COLUMN_KEYS[column]
        reverse = order == Qt.DescendingOrder

        def _norm_num(value):
            try:
                return float(value)
            except Exception:
                return float("inf")

        def _norm_txt(value):
            return str(value or "").lower()

        self.layoutAboutToBeChanged.emit()
        if key in self.NUMERIC_KEYS:
            with_val = [r for r in self._rows if r.get(key) not in (None, "")]
            without_val = [r for r in self._rows if r.get(key) in (None, "")]
            with_val.sort(key=lambda r: _norm_num(r.get(key)), reverse=reverse)
            self._rows = with_val + without_val
        else:
            self._rows.sort(key=lambda r: _norm_txt(r.get(key)), reverse=reverse)
        self.layoutChanged.emit()


class TimesliceTableModel(_BaseRowsModel):
    HEADERS = ["ID", "Name", "Depth Range", "Groups", "CRS", "Assigned CRS", "Project path", "Exists"]
    COLUMN_KEYS = (
        "id",
        "name",
        "depth_range",
        "groups",
        "crs",
        "assigned_crs",
        "project_path",
        "exists",
    )


class GroupTableModel(_BaseRowsModel):
    HEADERS = ["ID", "Name", "Time-slices"]
    COLUMN_KEYS = ("id", "name", "timeslice_count")
    NUMERIC_KEYS = {"timeslice_count"}

