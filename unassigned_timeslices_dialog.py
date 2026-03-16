# -*- coding: utf-8 -*-
"""
Dialog to inspect and manage Unassigned / No_CRS time-slices.
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTableView,
    QInputDialog,
    QMessageBox,
)

from .timeslice_group_table_models import TimesliceTableModel
from .project_catalog import assign_timeslices_to_group, remove_timeslices_from_group, load_catalog, save_catalog, create_raster_group


class UnassignedTimeslicesDialog(QDialog):
    def __init__(self, project_root, rows, parent=None):
        super().__init__(parent)
        self.project_root = project_root
        self.setWindowTitle("Verify Unassigned Time-slices")
        self.resize(900, 480)
        self.rows = rows or []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        info = QLabel("Select time-slices to assign to a group, remove from catalog, or delete from disk.")
        root.addWidget(info)

        self.table = QTableView(self)
        self.model = TimesliceTableModel(self)
        # TimesliceTableModel expects rows as payloads; transform to minimal display rows
        model_rows = []
        for r in self.rows:
            model_rows.append({
                "id": r.get("id") or "",
                "name": r.get("normalized_name") or r.get("name") or "",
                "depth_range": "",
                "groups": "",
                "crs": r.get("crs") or "",
                "assigned_crs": r.get("assigned_crs") or "",
                "project_path": r.get("project_path") or "",
                "exists": "Yes" if (r.get("project_path") and r.get("project_path")) else "No",
            })
        self.model.set_rows(model_rows)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(self.table.SelectRows)
        self.table.setSelectionMode(self.table.ExtendedSelection)
        root.addWidget(self.table)

        btn_row = QHBoxLayout()
        assign_btn = QPushButton("Assign to Group")
        assign_btn.clicked.connect(self._assign_selected)
        btn_row.addWidget(assign_btn)

        remove_btn = QPushButton("Remove from Catalog")
        remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(remove_btn)

        delete_btn = QPushButton("Delete File + Remove")
        delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(delete_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _selected_ids(self):
        sel = self.table.selectionModel()
        if sel is None:
            return []
        rows = sorted({idx.row() for idx in sel.selectedRows(0)})
        ids = [self.model.row_payload(r).get("id") for r in rows]
        return [i for i in ids if i]

    def _assign_selected(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.warning(self, "Assign", "Select at least one time-slice.")
            return
        group_name, ok = QInputDialog.getText(self, "Assign to group", "Enter group name (existing or new):", text="TimeSlices")
        if not ok or not group_name.strip():
            return
        try:
            grp, _ = create_raster_group(self.project_root, group_name.strip())
            assign_timeslices_to_group(self.project_root, grp.get("id"), ids)
            # remove from imported
            remove_timeslices_from_group(self.project_root, "grp_imported", ids)
            save_catalog(self.project_root, load_catalog(self.project_root))
            QMessageBox.information(self, "Assign", f"Assigned {len(ids)} time-slices to group: {grp.get('name')}")
            self.close()
        except Exception as e:
            QMessageBox.warning(self, "Assign error", str(e))

    def _remove_selected(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.warning(self, "Remove", "Select at least one time-slice.")
            return
        try:
            remove_timeslices_from_group(self.project_root, "grp_imported", ids)
            save_catalog(self.project_root, load_catalog(self.project_root))
            QMessageBox.information(self, "Remove", f"Removed {len(ids)} records from catalog.")
            self.close()
        except Exception as e:
            QMessageBox.warning(self, "Remove error", str(e))

    def _delete_selected(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.warning(self, "Delete", "Select at least one time-slice.")
            return
        confirm = QMessageBox.question(self, "Delete files", f"Delete {len(ids)} files from disk and remove from catalog?", QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        catalog = load_catalog(self.project_root)
        times = {t.get('id'): t for t in catalog.get('timeslices', [])}
        deleted = 0
        removed_ids = []
        for tid in ids:
            rec = times.get(tid)
            if rec:
                p = rec.get('project_path')
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                        deleted += 1
                except Exception:
                    pass
                removed_ids.append(tid)
        # remove from catalog timeslices and groups
        catalog['timeslices'] = [t for t in catalog.get('timeslices', []) if t.get('id') not in removed_ids]
        for g in catalog.get('raster_groups', []):
            g['timeslice_ids'] = [tid for tid in g.get('timeslice_ids', []) if tid not in removed_ids]
        save_catalog(self.project_root, catalog)
        QMessageBox.information(self, "Delete", f"Deleted files: {deleted}. Removed {len(removed_ids)} records from catalog.")
        self.close()
