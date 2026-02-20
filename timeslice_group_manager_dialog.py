# -*- coding: utf-8 -*-
"""
Time-slice and group manager for GeoSurvey Studio projects.
"""

import os

from PyQt5.QtCore import Qt, QItemSelectionModel
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QTabWidget,
    QLabel,
    QComboBox,
    QLineEdit,
    QPushButton,
    QTableView,
    QAbstractItemView,
    QMessageBox,
    QInputDialog,
    QProgressDialog,
    QApplication,
)
from qgis.core import QgsCoordinateReferenceSystem

from .project_catalog import load_catalog, save_catalog, sanitize_filename, create_raster_group
from .timeslice_group_table_models import TimesliceTableModel, GroupTableModel


class TimesliceGroupManagerDialog(QDialog):
    def __init__(self, project_root, parent=None, on_updated=None, open_catalog_editor_callback=None):
        super().__init__(parent)
        self.project_root = project_root
        self.on_updated = on_updated
        self.open_catalog_editor_callback = open_catalog_editor_callback
        self._catalog = {}
        self.setWindowTitle("GeoSurvey Studio Time-slice / Group Manager")
        self.resize(1180, 560)
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        info = QLabel(
            "Manage time-slice files and raster groups: rename (single/batch), delete, "
            "set assigned CRS, set textual depth range, and assign/remove images from groups."
        )
        info.setWordWrap(True)
        root.addWidget(info)

        tabs = QTabWidget(self)
        root.addWidget(tabs)

        ts_tab = QDialog(self)
        ts_layout = QVBoxLayout(ts_tab)
        ts_layout.setContentsMargins(0, 0, 0, 0)
        ts_layout.setSpacing(6)

        top_filter = QHBoxLayout()
        top_filter.addWidget(QLabel("Filter by group:"))
        self.filter_group_combo = QComboBox()
        self.filter_group_combo.currentIndexChanged.connect(self._refresh_timeslice_table)
        top_filter.addWidget(self.filter_group_combo, 1)

        top_filter.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("name, id, path, group, CRS, depth...")
        self.search_edit.textChanged.connect(self._refresh_timeslice_table)
        top_filter.addWidget(self.search_edit, 2)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        top_filter.addWidget(refresh_btn)
        ts_layout.addLayout(top_filter)

        actions_row1 = QHBoxLayout()
        self.rename_btn = QPushButton("Rename Selected")
        self.rename_btn.clicked.connect(self._rename_selected)
        actions_row1.addWidget(self.rename_btn)

        self.batch_rename_btn = QPushButton("Batch Rename")
        self.batch_rename_btn.clicked.connect(self._batch_rename_selected)
        actions_row1.addWidget(self.batch_rename_btn)

        self.remove_btn = QPushButton("Remove From Catalog")
        self.remove_btn.clicked.connect(self._remove_selected_catalog_only)
        actions_row1.addWidget(self.remove_btn)

        self.delete_btn = QPushButton("Delete File + Remove")
        self.delete_btn.clicked.connect(self._delete_selected)
        actions_row1.addWidget(self.delete_btn)
        ts_layout.addLayout(actions_row1)

        actions_row2 = QGridLayout()
        actions_row2.addWidget(QLabel("Group:"), 0, 0)
        self.group_action_combo = QComboBox()
        actions_row2.addWidget(self.group_action_combo, 0, 1, 1, 3)

        self.assign_btn = QPushButton("Assign To Group")
        self.assign_btn.clicked.connect(self._assign_selected_to_group)
        actions_row2.addWidget(self.assign_btn, 1, 0, 1, 1)

        self.unassign_btn = QPushButton("Remove From Group")
        self.unassign_btn.clicked.connect(self._remove_selected_from_group)
        actions_row2.addWidget(self.unassign_btn, 1, 1, 1, 1)

        self.set_crs_btn = QPushButton("Set Assigned CRS")
        self.set_crs_btn.clicked.connect(self._set_assigned_crs_single)
        actions_row2.addWidget(self.set_crs_btn, 1, 2, 1, 1)

        self.clear_crs_btn = QPushButton("Clear Assigned CRS")
        self.clear_crs_btn.clicked.connect(self._clear_assigned_crs_selected)
        actions_row2.addWidget(self.clear_crs_btn, 1, 3, 1, 1)

        self.set_depth_btn = QPushButton("Set Depth Range")
        self.set_depth_btn.clicked.connect(self._set_depth_range_selected)
        actions_row2.addWidget(self.set_depth_btn, 2, 0, 1, 1)

        self.clear_depth_btn = QPushButton("Clear Depth")
        self.clear_depth_btn.clicked.connect(self._clear_depth_range_selected)
        actions_row2.addWidget(self.clear_depth_btn, 2, 1, 1, 1)

        self.sequence_depth_btn = QPushButton("Auto Depth Sequence")
        self.sequence_depth_btn.clicked.connect(self._auto_depth_sequence_selected)
        actions_row2.addWidget(self.sequence_depth_btn, 2, 2, 1, 2)
        ts_layout.addLayout(actions_row2)

        self.ts_table = QTableView(self)
        self.ts_model = TimesliceTableModel(self)
        self.ts_table.setModel(self.ts_model)
        self.ts_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ts_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.ts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ts_table.setSortingEnabled(True)
        self.ts_table.horizontalHeader().setStretchLastSection(True)
        ts_layout.addWidget(self.ts_table)

        tabs.addTab(ts_tab, "Time-slices")

        grp_tab = QDialog(self)
        grp_layout = QVBoxLayout(grp_tab)
        grp_layout.setContentsMargins(0, 0, 0, 0)
        grp_layout.setSpacing(6)

        grp_actions = QHBoxLayout()
        create_group_btn = QPushButton("Create Group")
        create_group_btn.clicked.connect(self._create_group)
        grp_actions.addWidget(create_group_btn)

        rename_group_btn = QPushButton("Rename Group")
        rename_group_btn.clicked.connect(self._rename_group)
        grp_actions.addWidget(rename_group_btn)

        delete_group_btn = QPushButton("Delete Group")
        delete_group_btn.clicked.connect(self._delete_group)
        grp_actions.addWidget(delete_group_btn)

        grp_refresh_btn = QPushButton("Refresh")
        grp_refresh_btn.clicked.connect(self._refresh)
        grp_actions.addWidget(grp_refresh_btn)
        grp_layout.addLayout(grp_actions)

        self.group_table = QTableView(self)
        self.group_model = GroupTableModel(self)
        self.group_table.setModel(self.group_model)
        self.group_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.group_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.group_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.group_table.setSortingEnabled(True)
        self.group_table.horizontalHeader().setStretchLastSection(True)
        group_selection_model = self.group_table.selectionModel()
        if group_selection_model is not None:
            group_selection_model.selectionChanged.connect(
                lambda _selected, _deselected: self._sync_filter_from_group_selection()
            )
        grp_layout.addWidget(self.group_table)

        tabs.addTab(grp_tab, "Groups")

        footer = QHBoxLayout()
        if callable(self.open_catalog_editor_callback):
            open_catalog_btn = QPushButton("Open 3D/Radargram Editor")
            open_catalog_btn.clicked.connect(self._open_catalog_editor)
            footer.addWidget(open_catalog_btn)
        footer.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

    def _refresh(self):
        self._catalog = load_catalog(self.project_root)
        self._refresh_group_combos()
        self._refresh_group_table()
        self._refresh_timeslice_table()

    def _save_and_refresh(self):
        save_catalog(self.project_root, self._catalog)
        self._refresh()
        if callable(self.on_updated):
            try:
                self.on_updated()
            except Exception:
                pass

    def _refresh_group_combos(self):
        groups = list(self._catalog.get("raster_groups", []))
        prev_filter = self.filter_group_combo.currentData()
        prev_action = self.group_action_combo.currentData()

        self.filter_group_combo.blockSignals(True)
        self.group_action_combo.blockSignals(True)

        self.filter_group_combo.clear()
        self.filter_group_combo.addItem("All", None)

        self.group_action_combo.clear()
        for g in groups:
            gid = g.get("id")
            name = g.get("name") or gid or "Group"
            label = f"{name} [{gid}]"
            self.filter_group_combo.addItem(label, gid)
            self.group_action_combo.addItem(label, gid)

        if prev_filter is not None:
            idx = self.filter_group_combo.findData(prev_filter)
            if idx >= 0:
                self.filter_group_combo.setCurrentIndex(idx)
        if prev_action is not None:
            idx = self.group_action_combo.findData(prev_action)
            if idx >= 0:
                self.group_action_combo.setCurrentIndex(idx)

        self.filter_group_combo.blockSignals(False)
        self.group_action_combo.blockSignals(False)

    def _timeslice_membership_map(self):
        membership = {}
        for g in self._catalog.get("raster_groups", []):
            gname = g.get("name") or g.get("id") or "Group"
            for tid in g.get("timeslice_ids", []):
                membership.setdefault(tid, []).append(gname)
        return membership

    def _format_depth_range(self, rec):
        depth_from = rec.get("depth_from")
        depth_to = rec.get("depth_to")
        unit = (rec.get("unit") or "m").strip() or "m"
        if depth_from is None and depth_to is None:
            return ""

        def _fmt(val):
            try:
                txt = f"{float(val):.3f}"
                txt = txt.rstrip("0").rstrip(".")
                return txt
            except Exception:
                return str(val)

        if depth_from is None:
            return f"to {_fmt(depth_to)} {unit}"
        if depth_to is None:
            return f"from {_fmt(depth_from)} {unit}"
        return f"{_fmt(depth_from)} - {_fmt(depth_to)} {unit}"

    def _parse_depth_value(self, text):
        txt = (text or "").strip()
        if not txt:
            return None
        try:
            return float(txt.replace(",", "."))
        except Exception:
            return None

    def _refresh_timeslice_table(self):
        timeslices = list(self._catalog.get("timeslices", []))
        filter_gid = self.filter_group_combo.currentData()
        search_text = (self.search_edit.text() or "").strip().lower()
        selected_tids = set(self._selected_timeslice_ids())
        allowed = None
        if filter_gid:
            group = next((g for g in self._catalog.get("raster_groups", []) if g.get("id") == filter_gid), None)
            allowed = set(group.get("timeslice_ids", []) if group else [])

        membership = self._timeslice_membership_map()
        rows = []
        for rec in timeslices:
            tid = rec.get("id")
            if allowed is not None and tid not in allowed:
                continue
            depth_txt = self._format_depth_range(rec)
            if search_text:
                name = rec.get("normalized_name") or rec.get("name") or ""
                pth = rec.get("project_path") or ""
                grp_txt = ", ".join(sorted(membership.get(tid, [])))
                crs_txt = rec.get("crs") or ""
                assigned_txt = rec.get("assigned_crs") or ""
                hay = " | ".join(
                    [
                        str(tid or ""),
                        str(name),
                        str(depth_txt),
                        str(grp_txt),
                        str(crs_txt),
                        str(assigned_txt),
                        str(pth),
                    ]
                ).lower()
                if search_text not in hay:
                    continue
            rows.append(rec)

        model_rows = []
        for rec in rows:
            tid = rec.get("id") or ""
            name = rec.get("normalized_name") or rec.get("name") or ""
            depth_txt = self._format_depth_range(rec)
            pth = rec.get("project_path") or ""
            exists = "Yes" if pth and os.path.exists(pth) else "No"
            groups_txt = ", ".join(sorted(membership.get(tid, [])))
            crs_txt = rec.get("crs") or ""
            assigned_txt = rec.get("assigned_crs") or ""

            model_rows.append(
                {
                    "id": str(tid),
                    "name": str(name),
                    "depth_range": str(depth_txt),
                    "groups": str(groups_txt),
                    "crs": str(crs_txt),
                    "assigned_crs": str(assigned_txt),
                    "project_path": str(pth),
                    "exists": str(exists),
                }
            )

        model_rows.sort(key=lambda r: (r.get("name") or r.get("id") or "").lower())
        self.ts_model.set_rows(model_rows)
        self.ts_table.resizeColumnsToContents()

        selection_model = self.ts_table.selectionModel()
        if selection_model is not None:
            selection_model.clearSelection()
            for row, row_data in enumerate(model_rows):
                if row_data.get("id") not in selected_tids:
                    continue
                idx = self.ts_model.index(row, 0)
                if idx.isValid():
                    selection_model.select(
                        idx,
                        QItemSelectionModel.Select | QItemSelectionModel.Rows,
                    )

    def _refresh_group_table(self):
        groups = list(self._catalog.get("raster_groups", []))
        selected_gid = self._selected_group_id()
        model_rows = []
        for g in groups:
            gid = g.get("id") or ""
            name = g.get("name") or ""
            count = len(g.get("timeslice_ids", []))
            model_rows.append(
                {
                    "id": str(gid),
                    "name": str(name),
                    "timeslice_count": count,
                }
            )

        model_rows.sort(key=lambda r: (r.get("name") or r.get("id") or "").lower())
        self.group_model.set_rows(model_rows)
        self.group_table.resizeColumnsToContents()

        if selected_gid:
            for row_idx, row_data in enumerate(model_rows):
                if row_data.get("id") == selected_gid:
                    idx = self.group_model.index(row_idx, 0)
                    if idx.isValid():
                        self.group_table.selectRow(row_idx)
                    break

    def _selected_timeslice_ids(self):
        if self.ts_table is None or self.ts_table.selectionModel() is None:
            return []
        rows = sorted({idx.row() for idx in self.ts_table.selectionModel().selectedRows(0)})
        tids = []
        for row in rows:
            payload = self.ts_model.row_payload(row)
            tid = payload.get("id") if isinstance(payload, dict) else None
            if tid:
                tids.append(tid)
        return tids

    def _selected_group_id(self):
        if self.group_table is None or self.group_table.selectionModel() is None:
            return None
        rows = self.group_table.selectionModel().selectedRows(0)
        if not rows:
            return None
        row = rows[0].row()
        payload = self.group_model.row_payload(row)
        if not isinstance(payload, dict):
            return None
        return payload.get("id")

    def _timeslice_record_by_id(self, tid):
        return next((r for r in self._catalog.get("timeslices", []) if r.get("id") == tid), None)

    def _group_record_by_id(self, gid):
        return next((g for g in self._catalog.get("raster_groups", []) if g.get("id") == gid), None)

    def _ensure_imported_group(self):
        imported = self._group_record_by_id("grp_imported")
        if imported is None:
            imported = {
                "id": "grp_imported",
                "name": "Imported",
                "radargram_ids": [],
                "timeslice_ids": [],
            }
            self._catalog.setdefault("raster_groups", []).append(imported)
        imported.setdefault("timeslice_ids", [])
        return imported

    def _worldfile_suffixes(self, path):
        ext = os.path.splitext(path)[1].lower()
        by_ext = {
            ".tif": [".tfw", ".tifw", ".wld"],
            ".tiff": [".tfw", ".tifw", ".wld"],
            ".png": [".pgw", ".wld"],
            ".jpg": [".jgw", ".wld"],
            ".jpeg": [".jgw", ".wld"],
            ".bmp": [".bpw", ".wld"],
            ".img": [".wld"],
            ".asc": [".wld"],
        }
        suffixes = list(by_ext.get(ext, []))
        if ".wld" not in suffixes:
            suffixes.append(".wld")
        return suffixes

    def _existing_sidecars(self, path):
        sidecars = []
        base, _ = os.path.splitext(path)
        for suffix in self._worldfile_suffixes(path):
            wf = base + suffix
            if os.path.exists(wf):
                sidecars.append(wf)
        aux = path + ".aux.xml"
        if os.path.exists(aux):
            sidecars.append(aux)
        return sidecars

    def _rename_file_with_sidecars(self, old_path, new_path):
        warnings = []
        old_path_abs = os.path.abspath(old_path)
        new_path_abs = os.path.abspath(new_path)
        sidecars = self._existing_sidecars(old_path_abs)

        if old_path_abs != new_path_abs:
            os.rename(old_path_abs, new_path_abs)

        old_base, _ = os.path.splitext(old_path_abs)
        new_base, _ = os.path.splitext(new_path_abs)
        for old_side in sidecars:
            try:
                if old_side.endswith(".aux.xml"):
                    new_side = new_path_abs + ".aux.xml"
                else:
                    suffix = old_side[len(old_base):]
                    new_side = new_base + suffix
                if os.path.abspath(old_side) == os.path.abspath(new_side):
                    continue
                if os.path.exists(new_side):
                    warnings.append(f"Skipped sidecar rename (target exists): {new_side}")
                    continue
                os.rename(old_side, new_side)
            except Exception as e:
                warnings.append(f"Sidecar rename failed for {old_side}: {e}")
        return warnings

    def _rename_selected(self):
        tids = self._selected_timeslice_ids()
        if len(tids) != 1:
            QMessageBox.information(self, "Time-slice Manager", "Select exactly one time-slice.")
            return
        rec = self._timeslice_record_by_id(tids[0])
        if rec is None:
            QMessageBox.warning(self, "Time-slice Manager", "Selected record not found in catalog.")
            return
        old_path = rec.get("project_path") or ""
        if not old_path or not os.path.exists(old_path):
            QMessageBox.warning(self, "Time-slice Manager", "Selected file does not exist on disk.")
            return

        old_name = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(self, "Rename time-slice", "New file name:", text=old_name)
        if not ok or not new_name.strip():
            return

        new_name = sanitize_filename(new_name.strip())
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        if os.path.exists(new_path) and os.path.abspath(new_path) != os.path.abspath(old_path):
            QMessageBox.warning(self, "Time-slice Manager", "A file with this name already exists.")
            return

        try:
            warnings = self._rename_file_with_sidecars(old_path, new_path)
            rec["project_path"] = new_path
            rec["normalized_name"] = new_name
            rec["name"] = os.path.splitext(new_name)[0]
            self._save_and_refresh()
            if warnings:
                QMessageBox.warning(self, "Rename completed with warnings", "\n".join(warnings[:10]))
        except Exception as e:
            QMessageBox.critical(self, "Time-slice Manager", f"Rename failed:\n{e}")

    def _batch_rename_selected(self):
        tids = self._selected_timeslice_ids()
        if not tids:
            QMessageBox.information(self, "Time-slice Manager", "Select one or more time-slices.")
            return

        prefix, ok = QInputDialog.getText(self, "Batch rename", "Filename prefix:", text="timeslice_")
        if not ok:
            return
        prefix = sanitize_filename(prefix.strip())
        if not prefix:
            QMessageBox.warning(self, "Time-slice Manager", "Prefix cannot be empty.")
            return

        start_idx, ok = QInputDialog.getInt(self, "Batch rename", "Start index:", value=1, min=0, max=1000000)
        if not ok:
            return
        padding, ok = QInputDialog.getInt(self, "Batch rename", "Number padding:", value=3, min=1, max=8)
        if not ok:
            return

        records = []
        for tid in tids:
            rec = self._timeslice_record_by_id(tid)
            if rec is not None:
                records.append(rec)
        if not records:
            QMessageBox.warning(self, "Time-slice Manager", "No valid selected records.")
            return

        records.sort(key=lambda r: (r.get("normalized_name") or r.get("name") or r.get("id") or "").lower())
        old_paths = {os.path.abspath(r.get("project_path") or "") for r in records}
        plan = []
        for i, rec in enumerate(records):
            old_path = rec.get("project_path") or ""
            if not old_path or not os.path.exists(old_path):
                continue
            _, ext = os.path.splitext(old_path)
            new_name = f"{prefix}{str(start_idx + i).zfill(padding)}{ext.lower()}"
            new_name = sanitize_filename(new_name)
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            plan.append((rec, old_path, new_path, new_name))

        if not plan:
            QMessageBox.warning(self, "Time-slice Manager", "No selected files are available on disk.")
            return

        target_paths = [os.path.abspath(p[2]) for p in plan]
        if len(target_paths) != len(set(target_paths)):
            QMessageBox.warning(self, "Time-slice Manager", "Batch rename would generate duplicate target names.")
            return
        for _, old_path, new_path, _ in plan:
            old_abs = os.path.abspath(old_path)
            new_abs = os.path.abspath(new_path)
            if old_abs == new_abs:
                continue
            if os.path.exists(new_abs) and new_abs not in old_paths:
                QMessageBox.warning(self, "Time-slice Manager", f"Target file already exists:\n{new_abs}")
                return

        progress = QProgressDialog("Batch renaming time-slices...", "Cancel", 0, len(plan), self)
        progress.setWindowTitle("Batch Rename")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        warnings = []
        renamed = 0
        for idx, (rec, old_path, new_path, new_name) in enumerate(plan, start=1):
            if progress.wasCanceled():
                break
            progress.setLabelText(f"Renaming {idx}/{len(plan)}: {os.path.basename(old_path)}")
            QApplication.processEvents()
            try:
                side_warns = self._rename_file_with_sidecars(old_path, new_path)
                warnings.extend(side_warns)
                rec["project_path"] = new_path
                rec["normalized_name"] = new_name
                rec["name"] = os.path.splitext(new_name)[0]
                renamed += 1
            except Exception as e:
                warnings.append(f"Rename failed for {os.path.basename(old_path)}: {e}")
            progress.setValue(idx)
            QApplication.processEvents()

        progress.close()
        self._save_and_refresh()

        msg = f"Batch rename completed: {renamed}/{len(plan)} files."
        if warnings:
            msg += "\n\nWarnings:\n" + "\n".join(warnings[:12])
            extra = len(warnings) - min(len(warnings), 12)
            if extra > 0:
                msg += f"\n... and {extra} more."
            QMessageBox.warning(self, "Batch rename", msg)
        else:
            QMessageBox.information(self, "Batch rename", msg)

    def _remove_selected_catalog_only(self):
        tids = self._selected_timeslice_ids()
        if not tids:
            QMessageBox.information(self, "Time-slice Manager", "Select one or more time-slices.")
            return
        answer = QMessageBox.question(
            self,
            "Remove records",
            (
                f"Remove {len(tids)} record(s) from catalog?\n"
                "Files on disk will NOT be deleted."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        tids_set = set(tids)
        self._catalog["timeslices"] = [r for r in self._catalog.get("timeslices", []) if r.get("id") not in tids_set]
        for g in self._catalog.get("raster_groups", []):
            g["timeslice_ids"] = [tid for tid in g.get("timeslice_ids", []) if tid not in tids_set]
        self._catalog["links"] = [lk for lk in self._catalog.get("links", []) if lk.get("timeslice_id") not in tids_set]
        self._save_and_refresh()

    def _delete_selected(self):
        tids = self._selected_timeslice_ids()
        if not tids:
            QMessageBox.information(self, "Time-slice Manager", "Select one or more time-slices.")
            return

        answer = QMessageBox.question(
            self,
            "Delete files",
            (
                f"Delete {len(tids)} file(s) from disk and remove them from catalog?\n"
                "Related worldfiles/aux files will be deleted when found."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        records = [self._timeslice_record_by_id(tid) for tid in tids]
        records = [r for r in records if r is not None]
        if not records:
            return

        progress = QProgressDialog("Deleting time-slice files...", "Cancel", 0, len(records), self)
        progress.setWindowTitle("Delete Time-slices")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        deleted_ids = set()
        warnings = []
        for idx, rec in enumerate(records, start=1):
            if progress.wasCanceled():
                break
            pth = rec.get("project_path") or ""
            progress.setLabelText(f"Deleting {idx}/{len(records)}: {os.path.basename(pth)}")
            QApplication.processEvents()
            try:
                if pth and os.path.exists(pth):
                    for side in self._existing_sidecars(pth):
                        try:
                            os.remove(side)
                        except Exception as e:
                            warnings.append(f"Sidecar delete failed: {os.path.basename(side)} ({e})")
                    os.remove(pth)
                deleted_ids.add(rec.get("id"))
            except Exception as e:
                warnings.append(f"Delete failed for {os.path.basename(pth)}: {e}")
            progress.setValue(idx)
            QApplication.processEvents()

        progress.close()

        if deleted_ids:
            self._catalog["timeslices"] = [
                r for r in self._catalog.get("timeslices", []) if r.get("id") not in deleted_ids
            ]
            for g in self._catalog.get("raster_groups", []):
                g["timeslice_ids"] = [tid for tid in g.get("timeslice_ids", []) if tid not in deleted_ids]
            self._catalog["links"] = [
                lk for lk in self._catalog.get("links", []) if lk.get("timeslice_id") not in deleted_ids
            ]
            self._save_and_refresh()

        if warnings:
            preview = "\n".join(warnings[:12])
            extra = len(warnings) - min(len(warnings), 12)
            if extra > 0:
                preview += f"\n... and {extra} more."
            QMessageBox.warning(self, "Delete warnings", preview)

    def _set_assigned_crs_single(self):
        tids = self._selected_timeslice_ids()
        if len(tids) != 1:
            QMessageBox.information(self, "Set Assigned CRS", "Select exactly one time-slice.")
            return
        rec = self._timeslice_record_by_id(tids[0])
        if rec is None:
            QMessageBox.warning(self, "Set Assigned CRS", "Selected record not found.")
            return
        current = (rec.get("assigned_crs") or rec.get("crs") or "EPSG:32633").strip()
        text, ok = QInputDialog.getText(
            self,
            "Set Assigned CRS",
            "Enter EPSG code (32633) or AUTHID (EPSG:32633):",
            text=current,
        )
        if not ok or not text.strip():
            return

        raw = text.strip().upper()
        authid = raw if raw.startswith("EPSG:") else f"EPSG:{raw}"
        crs = QgsCoordinateReferenceSystem(authid)
        if not crs.isValid():
            QMessageBox.warning(self, "Set Assigned CRS", "Invalid CRS.")
            return

        rec["assigned_crs"] = crs.authid()
        self._save_and_refresh()

    def _clear_assigned_crs_selected(self):
        tids = self._selected_timeslice_ids()
        if not tids:
            QMessageBox.information(self, "Clear Assigned CRS", "Select one or more time-slices.")
            return
        changed = 0
        for tid in tids:
            rec = self._timeslice_record_by_id(tid)
            if rec is None:
                continue
            if rec.get("assigned_crs"):
                rec.pop("assigned_crs", None)
                changed += 1
        if changed <= 0:
            return
        self._save_and_refresh()

    def _set_depth_range_selected(self):
        tids = self._selected_timeslice_ids()
        if not tids:
            QMessageBox.information(self, "Set Depth Range", "Select one or more time-slices.")
            return

        from_txt, ok = QInputDialog.getText(
            self,
            "Set Depth Range",
            "Depth from:",
            text="0.0",
        )
        if not ok:
            return
        to_txt, ok = QInputDialog.getText(
            self,
            "Set Depth Range",
            "Depth to:",
            text="0.1",
        )
        if not ok:
            return
        unit_txt, ok = QInputDialog.getText(
            self,
            "Set Depth Range",
            "Unit:",
            text="m",
        )
        if not ok:
            return

        depth_from = self._parse_depth_value(from_txt)
        depth_to = self._parse_depth_value(to_txt)
        if depth_from is None or depth_to is None:
            QMessageBox.warning(self, "Set Depth Range", "Depth values must be numeric.")
            return
        if depth_to < depth_from:
            depth_from, depth_to = depth_to, depth_from
        unit = (unit_txt or "").strip() or "m"

        changed = 0
        for tid in tids:
            rec = self._timeslice_record_by_id(tid)
            if rec is None:
                continue
            rec["depth_from"] = depth_from
            rec["depth_to"] = depth_to
            rec["unit"] = unit
            changed += 1
        if changed > 0:
            self._save_and_refresh()

    def _clear_depth_range_selected(self):
        tids = self._selected_timeslice_ids()
        if not tids:
            QMessageBox.information(self, "Clear Depth", "Select one or more time-slices.")
            return
        changed = 0
        for tid in tids:
            rec = self._timeslice_record_by_id(tid)
            if rec is None:
                continue
            if rec.get("depth_from") is not None or rec.get("depth_to") is not None:
                rec["depth_from"] = None
                rec["depth_to"] = None
                changed += 1
        if changed > 0:
            self._save_and_refresh()

    def _auto_depth_sequence_selected(self):
        tids = self._selected_timeslice_ids()
        if len(tids) < 1:
            QMessageBox.information(self, "Auto Depth Sequence", "Select one or more time-slices.")
            return

        start_txt, ok = QInputDialog.getText(self, "Auto Depth Sequence", "Start depth:", text="0.0")
        if not ok:
            return
        thickness_txt, ok = QInputDialog.getText(self, "Auto Depth Sequence", "Slice thickness:", text="0.1")
        if not ok:
            return
        unit_txt, ok = QInputDialog.getText(self, "Auto Depth Sequence", "Unit:", text="m")
        if not ok:
            return

        start_depth = self._parse_depth_value(start_txt)
        thickness = self._parse_depth_value(thickness_txt)
        if start_depth is None or thickness is None or thickness <= 0:
            QMessageBox.warning(self, "Auto Depth Sequence", "Start and thickness must be valid numbers.")
            return
        unit = (unit_txt or "").strip() or "m"

        selected = []
        for tid in tids:
            rec = self._timeslice_record_by_id(tid)
            if rec is not None:
                selected.append(rec)
        selected.sort(key=lambda r: (r.get("normalized_name") or r.get("name") or r.get("id") or "").lower())

        for idx, rec in enumerate(selected):
            depth_from = start_depth + idx * thickness
            depth_to = depth_from + thickness
            rec["depth_from"] = depth_from
            rec["depth_to"] = depth_to
            rec["unit"] = unit
        if selected:
            self._save_and_refresh()

    def _assign_selected_to_group(self):
        tids = self._selected_timeslice_ids()
        if not tids:
            QMessageBox.information(self, "Assign To Group", "Select one or more time-slices.")
            return
        gid = self.group_action_combo.currentData()
        if not gid:
            QMessageBox.warning(self, "Assign To Group", "Select a target group.")
            return
        target = self._group_record_by_id(gid)
        if target is None:
            QMessageBox.warning(self, "Assign To Group", "Target group not found.")
            return

        merged = list(dict.fromkeys((target.get("timeslice_ids") or []) + tids))
        target["timeslice_ids"] = merged
        if gid != "grp_imported":
            imported = self._group_record_by_id("grp_imported")
            if imported is not None:
                imported["timeslice_ids"] = [tid for tid in imported.get("timeslice_ids", []) if tid not in set(tids)]
        self._save_and_refresh()

    def _remove_selected_from_group(self):
        tids = self._selected_timeslice_ids()
        if not tids:
            QMessageBox.information(self, "Remove From Group", "Select one or more time-slices.")
            return
        gid = self.group_action_combo.currentData()
        if not gid:
            QMessageBox.warning(self, "Remove From Group", "Select a source group.")
            return
        target = self._group_record_by_id(gid)
        if target is None:
            QMessageBox.warning(self, "Remove From Group", "Group not found.")
            return

        tids_set = set(tids)
        target["timeslice_ids"] = [tid for tid in target.get("timeslice_ids", []) if tid not in tids_set]

        imported = self._ensure_imported_group()
        if gid != "grp_imported":
            membership = {}
            for g in self._catalog.get("raster_groups", []):
                for tid in g.get("timeslice_ids", []):
                    membership[tid] = membership.get(tid, 0) + 1
            for tid in tids:
                if membership.get(tid, 0) <= 0 and tid not in imported.get("timeslice_ids", []):
                    imported["timeslice_ids"].append(tid)
        self._save_and_refresh()

    def _create_group(self):
        name, ok = QInputDialog.getText(self, "Create Group", "Group name:")
        if not ok or not name.strip():
            return
        group, created = create_raster_group(self.project_root, name.strip())
        self._catalog = load_catalog(self.project_root)
        self._refresh()
        if not created:
            QMessageBox.information(self, "Create Group", f"Group '{group.get('name')}' already exists.")

    def _rename_group(self):
        gid = self._selected_group_id()
        if not gid:
            QMessageBox.information(self, "Rename Group", "Select one group in the Groups tab.")
            return
        if gid == "grp_imported":
            QMessageBox.information(self, "Rename Group", "The default 'Imported' group cannot be renamed.")
            return
        rec = self._group_record_by_id(gid)
        if rec is None:
            return
        current = rec.get("name") or ""
        new_name, ok = QInputDialog.getText(self, "Rename Group", "New group name:", text=current)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        exists = any(
            (g.get("name") or "").strip().lower() == new_name.lower() and g.get("id") != gid
            for g in self._catalog.get("raster_groups", [])
        )
        if exists:
            QMessageBox.warning(self, "Rename Group", "A group with this name already exists.")
            return
        rec["name"] = new_name
        self._save_and_refresh()

    def _delete_group(self):
        gid = self._selected_group_id()
        if not gid:
            QMessageBox.information(self, "Delete Group", "Select one group in the Groups tab.")
            return
        if gid == "grp_imported":
            QMessageBox.information(self, "Delete Group", "The default 'Imported' group cannot be deleted.")
            return
        rec = self._group_record_by_id(gid)
        if rec is None:
            return
        name = rec.get("name") or gid
        ts_count = len(rec.get("timeslice_ids", []))
        answer = QMessageBox.question(
            self,
            "Delete Group",
            (
                f"Delete group '{name}'?\n\n"
                f"Time-slices linked to this group: {ts_count}\n"
                "They will be moved to 'Imported'."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        imported = self._ensure_imported_group()
        moved = list(dict.fromkeys((imported.get("timeslice_ids") or []) + (rec.get("timeslice_ids") or [])))
        imported["timeslice_ids"] = moved
        self._catalog["raster_groups"] = [g for g in self._catalog.get("raster_groups", []) if g.get("id") != gid]
        self._save_and_refresh()

    def _sync_filter_from_group_selection(self):
        gid = self._selected_group_id()
        if gid is None:
            return
        idx = self.filter_group_combo.findData(gid)
        if idx >= 0:
            self.filter_group_combo.setCurrentIndex(idx)

    def _open_catalog_editor(self):
        if not callable(self.open_catalog_editor_callback):
            return
        self.close()
        try:
            self.open_catalog_editor_callback()
        except Exception as e:
            QMessageBox.warning(
                self,
                "Time-slice Manager",
                f"Unable to open 3D/Radargram Editor:\n{e}",
            )
