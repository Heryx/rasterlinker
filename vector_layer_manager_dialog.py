# -*- coding: utf-8 -*-
"""
Vector layer manager for GeoSurvey Studio project catalog.
"""

import os

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QInputDialog,
    QFileDialog,
)
from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes

from .project_catalog import load_catalog, save_catalog, sanitize_filename


class VectorLayerManagerDialog(QDialog):
    def __init__(self, iface, project_root, parent=None, on_updated=None):
        super().__init__(parent)
        self.iface = iface
        self.project_root = project_root
        self.on_updated = on_updated
        self._catalog = {}
        self._records = []
        self.setWindowTitle("GeoSurvey Studio Vector Layer Manager")
        self.resize(1100, 460)
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "Manage vector layers registered in catalog: open in QGIS, relink missing paths, "
            "rename files, remove records, or delete files."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        actions = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        actions.addWidget(refresh_btn)

        open_btn = QPushButton("Open In QGIS")
        open_btn.clicked.connect(self._open_selected)
        actions.addWidget(open_btn)

        relink_btn = QPushButton("Relink Path")
        relink_btn.clicked.connect(self._relink_selected)
        actions.addWidget(relink_btn)

        rename_btn = QPushButton("Rename File")
        rename_btn.clicked.connect(self._rename_selected)
        actions.addWidget(rename_btn)

        remove_btn = QPushButton("Remove From Catalog")
        remove_btn.clicked.connect(self._remove_selected)
        actions.addWidget(remove_btn)

        delete_btn = QPushButton("Delete File + Remove")
        delete_btn.clicked.connect(self._delete_selected)
        actions.addWidget(delete_btn)

        actions.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "ID",
                "Name",
                "Geometry",
                "3D",
                "Source kind",
                "CRS",
                "Storage",
                "Exists",
                "Project path",
            ]
        )
        self.table.setSelectionBehavior(self.table.SelectRows)
        self.table.setSelectionMode(self.table.ExtendedSelection)
        self.table.setEditTriggers(self.table.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

    def _refresh(self):
        self._catalog = load_catalog(self.project_root)
        self._records = list(self._catalog.get("vector_layers", []))
        self.table.setRowCount(len(self._records))

        for row, rec in enumerate(self._records):
            rid = str(rec.get("id") or "")
            name = str(rec.get("layer_name") or rec.get("name") or "")
            geom = str(rec.get("geometry_type") or "")
            is_3d = "Yes" if bool(rec.get("is_3d")) else "No"
            source_kind = str(rec.get("source_kind") or "")
            crs = str(rec.get("crs") or "")
            storage = str(rec.get("storage_mode") or "")
            pth = str(rec.get("project_path") or "")
            exists = "Yes" if pth and os.path.exists(pth) else "No"
            cells = [rid, name, geom, is_3d, source_kind, crs, storage, exists, pth]
            for col, value in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(value))

        self.table.resizeColumnsToContents()

    def _save_and_refresh(self):
        self._catalog["vector_layers"] = self._records
        save_catalog(self.project_root, self._catalog)
        self._refresh()
        if callable(self.on_updated):
            try:
                self.on_updated()
            except Exception:
                pass

    def _selected_rows(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return []
        idxs = sorted({r.row() for r in rows})
        return idxs

    def _selected_record(self):
        rows = self._selected_rows()
        if not rows:
            return None, None
        row = rows[0]
        if row < 0 or row >= len(self._records):
            return None, None
        return row, self._records[row]

    def _is_already_loaded(self, path, layer_name):
        pnorm = os.path.normcase(os.path.abspath(path or ""))
        lname = (layer_name or "").strip().lower()
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue
            src = lyr.source() or ""
            src_base = src.split("|layername=", 1)[0]
            if os.path.normcase(os.path.abspath(src_base)) != pnorm:
                continue
            if not lname:
                return True
            if (lyr.name() or "").strip().lower() == lname:
                return True
        return False

    def _inspect_vector_file(self, path, layer_name_hint):
        layer_name = (layer_name_hint or "").strip() or os.path.splitext(os.path.basename(path))[0]
        uri = path
        if str(path).lower().endswith(".gpkg") and layer_name:
            uri = f"{path}|layername={layer_name}"
        lyr = QgsVectorLayer(uri, layer_name, "ogr")
        if not lyr.isValid():
            lyr = QgsVectorLayer(path, layer_name, "ogr")
        if not lyr.isValid():
            return None
        geom_map = {
            QgsWkbTypes.PointGeometry: "point",
            QgsWkbTypes.LineGeometry: "line",
            QgsWkbTypes.PolygonGeometry: "polygon",
        }
        try:
            geom_type = geom_map.get(lyr.geometryType(), "unknown")
        except Exception:
            geom_type = "unknown"
        try:
            is_3d = bool(QgsWkbTypes.hasZ(lyr.wkbType()))
        except Exception:
            is_3d = False
        return {
            "layer_name": lyr.name(),
            "geometry_type": geom_type,
            "is_3d": is_3d,
            "crs": lyr.crs().authid() if lyr.crs().isValid() else None,
            "storage_mode": "gpkg" if str(path).lower().endswith(".gpkg") else "file",
        }

    def _open_selected(self):
        _, rec = self._selected_record()
        if rec is None:
            QMessageBox.information(self, "Vector Layer Manager", "Select a row first.")
            return
        path = rec.get("project_path") or ""
        layer_name = rec.get("layer_name") or rec.get("name") or os.path.splitext(os.path.basename(path))[0]
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Vector Layer Manager", "Selected vector path is missing.")
            return
        if self._is_already_loaded(path, layer_name):
            QMessageBox.information(self, "Vector Layer Manager", "Layer is already loaded in QGIS.")
            return

        uri = path
        if str(path).lower().endswith(".gpkg") and layer_name:
            uri = f"{path}|layername={layer_name}"
        layer = QgsVectorLayer(uri, layer_name, "ogr")
        if not layer.isValid():
            layer = QgsVectorLayer(path, layer_name, "ogr")
        if not layer.isValid():
            QMessageBox.warning(self, "Vector Layer Manager", "Unable to open selected vector layer.")
            return
        QgsProject.instance().addMapLayer(layer)
        self.iface.setActiveLayer(layer)

    def _relink_selected(self):
        row, rec = self._selected_record()
        if rec is None:
            QMessageBox.information(self, "Vector Layer Manager", "Select a row first.")
            return
        old_path = rec.get("project_path") or ""
        start_dir = os.path.dirname(old_path) if old_path else self.project_root
        new_path, _ = QFileDialog.getOpenFileName(
            self,
            "Relink vector layer path",
            start_dir,
            "Vector files (*.gpkg *.shp *.geojson *.json *.kml *.dxf);;All files (*.*)",
        )
        if not new_path:
            return
        if not os.path.exists(new_path):
            QMessageBox.warning(self, "Vector Layer Manager", "Selected file does not exist.")
            return

        rec["project_path"] = new_path
        rec["storage_mode"] = "gpkg" if new_path.lower().endswith(".gpkg") else "file"
        inspected = self._inspect_vector_file(new_path, rec.get("layer_name") or rec.get("name") or "")
        if inspected:
            rec["layer_name"] = inspected.get("layer_name") or rec.get("layer_name") or rec.get("name") or ""
            rec["name"] = rec["layer_name"]
            rec["geometry_type"] = inspected.get("geometry_type", rec.get("geometry_type"))
            rec["is_3d"] = bool(inspected.get("is_3d", rec.get("is_3d")))
            rec["crs"] = inspected.get("crs", rec.get("crs"))
        self._records[row] = rec
        self._save_and_refresh()

    def _rename_selected(self):
        row, rec = self._selected_record()
        if rec is None:
            QMessageBox.information(self, "Vector Layer Manager", "Select a row first.")
            return
        old_path = rec.get("project_path") or ""
        if not old_path or not os.path.exists(old_path):
            QMessageBox.warning(self, "Vector Layer Manager", "Selected file does not exist on disk.")
            return

        old_name = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(self, "Rename vector file", "New file name:", text=old_name)
        if not ok or not new_name.strip():
            return
        new_name = sanitize_filename(new_name.strip())
        if "." not in os.path.basename(new_name):
            ext = os.path.splitext(old_name)[1]
            new_name = f"{new_name}{ext}"
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        if os.path.exists(new_path) and os.path.abspath(new_path) != os.path.abspath(old_path):
            QMessageBox.warning(self, "Vector Layer Manager", "A file with this name already exists.")
            return

        try:
            if os.path.abspath(new_path) != os.path.abspath(old_path):
                os.rename(old_path, new_path)
            rec["project_path"] = new_path
            self._records[row] = rec
            self._save_and_refresh()
        except Exception as e:
            QMessageBox.critical(self, "Vector Layer Manager", f"Rename failed:\n{e}")

    def _remove_selected(self):
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Vector Layer Manager", "Select one or more rows first.")
            return
        answer = QMessageBox.question(
            self,
            "Remove from catalog",
            (
                f"Remove {len(rows)} selected vector record(s) from catalog?\n"
                "Files on disk will NOT be deleted."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        for row in sorted(rows, reverse=True):
            if 0 <= row < len(self._records):
                del self._records[row]
        self._save_and_refresh()

    def _delete_selected(self):
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Vector Layer Manager", "Select one or more rows first.")
            return
        first = QMessageBox.question(
            self,
            "Delete files + remove",
            f"Delete file(s) from disk and remove {len(rows)} catalog record(s)?",
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

        errors = []
        for row in sorted(rows, reverse=True):
            if row < 0 or row >= len(self._records):
                continue
            rec = self._records[row]
            path = rec.get("project_path") or ""
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    errors.append(f"{os.path.basename(path)}: {e}")
                    continue
            del self._records[row]

        self._save_and_refresh()
        if errors:
            preview = "\n".join(errors[:10])
            if len(errors) > 10:
                preview += f"\n... and {len(errors) - 10} more."
            QMessageBox.warning(self, "Vector Layer Manager", f"Some files could not be deleted:\n{preview}")
