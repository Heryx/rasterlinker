# -*- coding: utf-8 -*-
"""
Simple catalog editor for project records.
"""

import os

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QInputDialog,
)

from .project_catalog import load_catalog, save_catalog, sanitize_filename


class CatalogEditorDialog(QDialog):
    def __init__(self, project_root, parent=None, open_timeslice_manager_callback=None):
        super().__init__(parent)
        self.project_root = project_root
        self.open_timeslice_manager_callback = open_timeslice_manager_callback
        self.setWindowTitle("GeoSurvey Studio 3D/Radargram Editor")
        self.resize(880, 420)
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        scope_info = QLabel(
            "This editor manages only 3D models and radargrams. "
            "Use Time-slice Manager for time-slices and groups."
        )
        scope_info.setWordWrap(True)
        layout.addWidget(scope_info)

        top = QHBoxLayout()
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("3D Models", "models_3d")
        self.kind_combo.addItem("Radargrams", "radargrams")
        self.kind_combo.currentIndexChanged.connect(self._refresh)
        top.addWidget(self.kind_combo)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        top.addWidget(refresh_btn)

        rename_btn = QPushButton("Rename")
        rename_btn.clicked.connect(self._rename_selected)
        top.addWidget(rename_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_selected)
        top.addWidget(remove_btn)

        delete_btn = QPushButton("Delete File + Remove")
        delete_btn.clicked.connect(self._delete_file_and_remove_selected)
        top.addWidget(delete_btn)

        if callable(self.open_timeslice_manager_callback):
            switch_btn = QPushButton("Open Time-slice Manager")
            switch_btn.clicked.connect(self._open_timeslice_manager)
            top.addWidget(switch_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        top.addWidget(close_btn)
        layout.addLayout(top)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ID", "Name", "Project path"])
        self.table.setSelectionBehavior(self.table.SelectRows)
        self.table.setEditTriggers(self.table.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

    def _records(self):
        kind = self.kind_combo.currentData()
        data = load_catalog(self.project_root)
        return data, kind, data.get(kind, [])

    def _refresh(self):
        _, _, records = self._records()
        self.table.setRowCount(len(records))
        for i, rec in enumerate(records):
            rid = rec.get("id", "")
            name = rec.get("normalized_name") or rec.get("name") or rec.get("file_name") or ""
            pth = rec.get("project_path", "")
            self.table.setItem(i, 0, QTableWidgetItem(str(rid)))
            self.table.setItem(i, 1, QTableWidgetItem(str(name)))
            self.table.setItem(i, 2, QTableWidgetItem(str(pth)))
        self.table.resizeColumnsToContents()

    def _selected_row(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return rows[0].row()

    def _rename_selected(self):
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "3D/Radargram Editor", "Select a row first.")
            return

        data, kind, records = self._records()
        rec = records[row]
        old_path = rec.get("project_path")
        if not old_path or not os.path.exists(old_path):
            QMessageBox.warning(self, "3D/Radargram Editor", "Selected file does not exist on disk.")
            return

        old_name = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(self, "Rename file", "New file name:", text=old_name)
        if not ok or not new_name.strip():
            return

        new_name = sanitize_filename(new_name.strip())
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        if os.path.exists(new_path) and os.path.abspath(new_path) != os.path.abspath(old_path):
            QMessageBox.warning(self, "3D/Radargram Editor", "A file with this name already exists.")
            return

        try:
            if os.path.abspath(new_path) != os.path.abspath(old_path):
                os.rename(old_path, new_path)
            rec["project_path"] = new_path
            rec["normalized_name"] = new_name
            save_catalog(self.project_root, data)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "3D/Radargram Editor", f"Rename failed:\n{e}")

    def _remove_selected(self):
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "3D/Radargram Editor", "Select a row first.")
            return

        data, kind, records = self._records()
        rec = records[row]
        name = rec.get("normalized_name") or rec.get("id") or "record"
        answer = QMessageBox.question(
            self,
            "Remove record",
            f"Remove '{name}' from catalog?\nFile on disk will NOT be deleted.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        del records[row]
        data[kind] = records
        save_catalog(self.project_root, data)
        self._refresh()

    def _delete_file_and_remove_selected(self):
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "3D/Radargram Editor", "Select a row first.")
            return

        data, kind, records = self._records()
        rec = records[row]
        pth = rec.get("project_path")
        name = rec.get("normalized_name") or rec.get("id") or "record"
        if not pth or not os.path.exists(pth):
            QMessageBox.warning(self, "3D/Radargram Editor", "Selected file is missing on disk.")
            return

        first = QMessageBox.question(
            self,
            "Delete file",
            f"Delete file from disk and remove '{name}' from catalog?\n\n{pth}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if first != QMessageBox.Yes:
            return

        second = QMessageBox.question(
            self,
            "Confirm irreversible action",
            "This operation cannot be undone. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if second != QMessageBox.Yes:
            return

        try:
            os.remove(pth)
            del records[row]
            data[kind] = records
            save_catalog(self.project_root, data)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "3D/Radargram Editor", f"Delete failed:\n{e}")

    def _open_timeslice_manager(self):
        if not callable(self.open_timeslice_manager_callback):
            return
        self.close()
        try:
            self.open_timeslice_manager_callback()
        except Exception as e:
            QMessageBox.warning(
                self,
                "3D/Radargram Editor",
                f"Unable to open Time-slice Manager:\n{e}",
            )
