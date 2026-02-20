# -*- coding: utf-8 -*-
"""
Simple Project Manager dialog for 2D/3D project folder workflow.
"""

import os
import json
import shutil

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QInputDialog,
    QDialog,
    QProgressDialog,
    QApplication,
)
from qgis.PyQt.QtCore import QSettings, Qt
from qgis.core import QgsProject, QgsPointCloudLayer, QgsRasterLayer
from qgis.gui import QgsProjectionSelectionDialog

from .project_catalog import (
    ensure_project_structure,
    load_catalog,
    save_catalog,
    register_model_3d,
    register_radargram,
    register_timeslice,
    register_timeslices_batch,
    save_radargram_sidecar,
    add_radargram_to_default_group,
    create_raster_group,
    assign_timeslices_to_group,
    remove_timeslices_from_group,
    add_timeslice_to_default_group,
    validate_catalog,
    link_surfer_grid_into_project,
    export_project_package,
    import_project_package,
    utc_now_iso,
)
from .background_tasks import (
    TimesliceImportTask,
    LasLazImportTask,
    RadargramImportTask,
    CatalogCleanupTask,
    start_task_with_progress_dialog,
)
from .pointcloud_metadata import inspect_las_laz
from .radargram_metadata import inspect_radargram, find_worldfile
from .catalog_editor_dialog import CatalogEditorDialog
from .link_editor_dialog import LinkEditorDialog
from .timeslice_group_manager_dialog import TimesliceGroupManagerDialog
from .project_health_dialog import ProjectHealthDialog


class ProjectManagerDialog(QDialog):
    def __init__(self, iface, parent=None, on_project_updated=None):
        super().__init__(parent)
        self.iface = iface
        self.project_root = ""
        self.on_project_updated = on_project_updated
        self.settings = QSettings()
        self.settings_key_active_project = "RasterLinker/active_project_root"
        self.settings_key_default_import_crs = "RasterLinker/default_import_crs_authid"
        self._timeslice_import_active = False
        self._las_import_active = False
        self._radargram_import_active = False
        self._cleanup_active = False
        self._manifest_import_active = False
        self.import_las_btn = None
        self.import_rg_btn = None
        self.import_ts_btn = None
        self.import_manifest_btn = None
        self.cleanup_btn = None
        self.setWindowTitle("RasterLinker Project Manager")
        self.resize(760, 300)
        self._build_ui()
        self._apply_styles()
        try:
            stored_root = (self.settings.value(self.settings_key_active_project, "", type=str) or "").strip()
            if stored_root:
                self.path_edit.setText(stored_root)
                if os.path.isdir(stored_root):
                    self.project_root = stored_root
        except Exception:
            pass

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        title = QLabel("Create/Open a project folder for 2D/3D geophysics data")
        root_layout.addWidget(title)

        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Project folder path")
        row.addWidget(self.path_edit)

        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_folder)
        row.addWidget(browse_btn)
        root_layout.addLayout(row)

        create_btn = QPushButton("Create Project")
        create_btn.clicked.connect(self._create_or_open_project)
        create_btn.setMinimumWidth(180)
        create_btn.setMinimumHeight(30)
        root_layout.addWidget(create_btn)

        sections_grid = QGridLayout()
        sections_grid.setHorizontalSpacing(12)
        sections_grid.setVerticalSpacing(10)

        import_box = QGroupBox("Import")
        import_layout = QGridLayout(import_box)
        import_layout.setHorizontalSpacing(8)
        import_layout.setVerticalSpacing(8)

        self.import_las_btn = QPushButton("Import LAS/LAZ")
        self.import_las_btn.clicked.connect(self._import_las_laz)
        self.import_las_btn.setMinimumHeight(28)
        import_layout.addWidget(self.import_las_btn, 0, 0)

        self.import_rg_btn = QPushButton("Import Radargrams")
        self.import_rg_btn.clicked.connect(self._import_radargrams)
        self.import_rg_btn.setMinimumHeight(28)
        import_layout.addWidget(self.import_rg_btn, 0, 1)

        self.import_ts_btn = QPushButton("Import Time-slices")
        self.import_ts_btn.clicked.connect(self._import_timeslices)
        self.import_ts_btn.setMinimumHeight(28)
        import_layout.addWidget(self.import_ts_btn, 1, 0)

        self.import_manifest_btn = QPushButton("Import Manifest")
        self.import_manifest_btn.clicked.connect(self._import_manifest)
        self.import_manifest_btn.setMinimumHeight(28)
        import_layout.addWidget(self.import_manifest_btn, 1, 1)

        catalog_box = QGroupBox("Data Catalog & QA")
        catalog_layout = QGridLayout(catalog_box)
        catalog_layout.setHorizontalSpacing(8)
        catalog_layout.setVerticalSpacing(8)

        catalog_scope_label = QLabel(
            "3D/Radargram Editor manages volumes and radargrams.\n"
            "Time-slice Manager handles time-slices and raster groups."
        )
        catalog_scope_label.setWordWrap(True)
        catalog_layout.addWidget(catalog_scope_label, 0, 0, 1, 2)

        view_catalog_btn = QPushButton("View Catalog")
        view_catalog_btn.clicked.connect(self._view_catalog_summary)
        view_catalog_btn.setMinimumHeight(28)
        catalog_layout.addWidget(view_catalog_btn, 1, 0)

        edit_catalog_btn = QPushButton("3D/Radargram Editor")
        edit_catalog_btn.clicked.connect(self._open_catalog_editor)
        edit_catalog_btn.setMinimumHeight(28)
        catalog_layout.addWidget(edit_catalog_btn, 1, 1)

        validate_btn = QPushButton("Validate")
        validate_btn.clicked.connect(self._validate_project)
        validate_btn.setMinimumHeight(28)
        catalog_layout.addWidget(validate_btn, 2, 0)

        crs_btn = QPushButton("Set Import CRS")
        crs_btn.clicked.connect(self._choose_default_import_crs)
        crs_btn.setMinimumHeight(28)
        catalog_layout.addWidget(crs_btn, 2, 1)

        self.cleanup_btn = QPushButton("Cleanup Catalog")
        self.cleanup_btn.clicked.connect(self._cleanup_catalog)
        self.cleanup_btn.setMinimumHeight(28)
        catalog_layout.addWidget(self.cleanup_btn, 3, 0)

        reload_btn = QPushButton("Reload Layers")
        reload_btn.clicked.connect(self._reload_imported_layers)
        reload_btn.setMinimumHeight(28)
        catalog_layout.addWidget(reload_btn, 3, 1)

        links_btn = QPushButton("Link Editor")
        links_btn.clicked.connect(self._open_link_editor)
        links_btn.setMinimumHeight(28)
        catalog_layout.addWidget(links_btn, 4, 0)

        ts_mgr_btn = QPushButton("Time-slice/Group Manager")
        ts_mgr_btn.clicked.connect(self._open_timeslice_group_manager)
        ts_mgr_btn.setMinimumHeight(28)
        catalog_layout.addWidget(ts_mgr_btn, 4, 1)

        health_btn = QPushButton("Project Health")
        health_btn.clicked.connect(self._open_project_health)
        health_btn.setMinimumHeight(28)
        catalog_layout.addWidget(health_btn, 5, 0, 1, 2)

        package_box = QGroupBox("Package")
        package_layout = QGridLayout(package_box)
        package_layout.setHorizontalSpacing(8)
        package_layout.setVerticalSpacing(8)

        export_pkg_btn = QPushButton("Export Package")
        export_pkg_btn.clicked.connect(self._export_package)
        export_pkg_btn.setMinimumHeight(28)
        package_layout.addWidget(export_pkg_btn, 0, 0)

        import_pkg_btn = QPushButton("Import Package")
        import_pkg_btn.clicked.connect(self._import_package)
        import_pkg_btn.setMinimumHeight(28)
        package_layout.addWidget(import_pkg_btn, 0, 1)

        close_btn = QPushButton("OK")
        close_btn.clicked.connect(self.close)
        close_btn.setMinimumHeight(30)
        package_layout.addWidget(close_btn, 1, 0, 1, 2)

        sections_grid.addWidget(import_box, 0, 0)
        sections_grid.addWidget(catalog_box, 0, 1)
        sections_grid.addWidget(package_box, 1, 0, 1, 2)
        root_layout.addLayout(sections_grid)

    def _apply_styles(self):
        self.setStyleSheet(
            """
            QGroupBox {
                border: 1px solid #cfd8dc;
                border-radius: 6px;
                margin-top: 10px;
                font-weight: 600;
                padding: 8px 6px 6px 6px;
                background: #fbfcfd;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px 0 4px;
                color: #37474f;
            }
            QPushButton {
                padding: 4px 8px;
            }
            """
        )

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select project folder")
        if folder:
            self.path_edit.setText(folder)

    def _create_or_open_project(self):
        folder = self.path_edit.text().strip()
        if not folder:
            parent_dir = QFileDialog.getExistingDirectory(self, "Select parent folder for project")
            if not parent_dir:
                return
            folder_name, ok = QInputDialog.getText(
                self,
                "Create Project Folder",
                "Project folder name:",
                text="RasterLinkerProject",
            )
            if not ok or not folder_name.strip():
                return
            folder = os.path.join(parent_dir, folder_name.strip())
            self.path_edit.setText(folder)

        ensure_project_structure(folder)
        self.project_root = folder
        self.settings.setValue(self.settings_key_active_project, folder)
        sync_counts = self._sync_catalog_from_existing_files()
        sync_msg = (
            f" (synced: timeslices {sync_counts['timeslices']}, "
            f"radargrams {sync_counts['radargrams']}, models {sync_counts['models']})"
            if any(sync_counts.values())
            else ""
        )
        self.iface.messageBar().pushInfo("RasterLinker", f"Project ready: {folder}")
        if sync_msg:
            self.iface.messageBar().pushInfo("RasterLinker", f"Existing data recognized{sync_msg}.")
        self._notify_project_updated()

    def _ensure_project_ready(self):
        if self.project_root:
            return True
        folder = self.path_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, "Project Manager", "Create/Open a project first.")
            return False
        ensure_project_structure(folder)
        self.project_root = folder
        self.settings.setValue(self.settings_key_active_project, folder)
        self._notify_project_updated()
        return True

    def _sync_catalog_from_existing_files(self):
        """
        Scan existing project folders and register files not yet present in catalog.
        """
        catalog = load_catalog(self.project_root)
        counts = {"timeslices": 0, "radargrams": 0, "models": 0}

        existing_timeslice_paths = {
            os.path.normcase(os.path.abspath(r.get("project_path", "")))
            for r in catalog.get("timeslices", [])
            if r.get("project_path")
        }
        existing_radargram_paths = {
            os.path.normcase(os.path.abspath(r.get("project_path", "")))
            for r in catalog.get("radargrams", [])
            if r.get("project_path")
        }
        existing_model_paths = {
            os.path.normcase(os.path.abspath(r.get("project_path", "")))
            for r in catalog.get("models_3d", [])
            if r.get("project_path")
        }

        timeslice_dir = os.path.join(self.project_root, "timeslices_2d")
        radargram_dir = os.path.join(self.project_root, "radargrams")
        model_dir = os.path.join(self.project_root, "volumes_3d")

        # Import existing time-slices
        if os.path.isdir(timeslice_dir):
            for name in sorted(os.listdir(timeslice_dir)):
                project_path = os.path.join(timeslice_dir, name)
                if not os.path.isfile(project_path):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".asc", ".img"}:
                    continue
                norm_path = os.path.normcase(os.path.abspath(project_path))
                if norm_path in existing_timeslice_paths:
                    continue

                try:
                    meta = self._inspect_timeslice(project_path)
                    meta.update(
                        {
                            "id": f"timeslice_{utc_now_iso()}_{name}",
                            "name": os.path.splitext(name)[0],
                            "normalized_name": name,
                            "source_path": project_path,
                            "project_path": project_path,
                            "imported_at": utc_now_iso(),
                        }
                    )
                    link_info = link_surfer_grid_into_project(
                        self.project_root,
                        reference_raster_path=project_path,
                        source_raster_path=project_path,
                    )
                    if link_info:
                        meta.update(link_info)
                    register_timeslice(self.project_root, meta)
                    existing_timeslice_paths.add(norm_path)
                    counts["timeslices"] += 1
                except Exception:
                    continue

        # Import existing radargrams
        if os.path.isdir(radargram_dir):
            for name in sorted(os.listdir(radargram_dir)):
                project_path = os.path.join(radargram_dir, name)
                if not os.path.isfile(project_path):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in {".rd3", ".rad", ".dzt", ".npy", ".csv", ".txt", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
                    continue
                norm_path = os.path.normcase(os.path.abspath(project_path))
                if norm_path in existing_radargram_paths:
                    continue
                try:
                    meta = inspect_radargram(project_path)
                    meta.update(
                        {
                            "id": f"radargram_{utc_now_iso()}_{name}",
                            "normalized_name": name,
                            "source_path": project_path,
                            "project_path": project_path,
                            "imported_at": utc_now_iso(),
                        }
                    )
                    geo_info = self._classify_radargram_georef(project_path)
                    meta.update(geo_info)
                    register_radargram(self.project_root, meta)
                    save_radargram_sidecar(self.project_root, meta)
                    add_radargram_to_default_group(self.project_root, meta.get("id"))
                    existing_radargram_paths.add(norm_path)
                    counts["radargrams"] += 1
                except Exception:
                    continue

        # Import existing 3D models
        if os.path.isdir(model_dir):
            for name in sorted(os.listdir(model_dir)):
                project_path = os.path.join(model_dir, name)
                if not os.path.isfile(project_path):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in {".las", ".laz"}:
                    continue
                norm_path = os.path.normcase(os.path.abspath(project_path))
                if norm_path in existing_model_paths:
                    continue
                try:
                    meta = inspect_las_laz(project_path)
                    meta.update(
                        {
                            "id": f"model_{utc_now_iso()}_{name}",
                            "normalized_name": name,
                            "source_path": project_path,
                            "project_path": project_path,
                            "imported_at": utc_now_iso(),
                        }
                    )
                    register_model_3d(self.project_root, meta)
                    existing_model_paths.add(norm_path)
                    counts["models"] += 1
                except Exception:
                    continue

        return counts

    def _notify_project_updated(self):
        if callable(self.on_project_updated):
            try:
                self.on_project_updated()
            except Exception:
                pass

    @staticmethod
    def _build_issue_preview(items, limit=10):
        rows = [str(it) for it in (items or []) if str(it).strip()]
        if not rows:
            return ""
        preview = "\n".join(rows[:limit])
        more = len(rows) - min(len(rows), limit)
        if more > 0:
            preview += f"\n... and {more} more."
        return preview

    def _show_import_failures(self, title, failures):
        preview = self._build_issue_preview(failures, limit=10)
        if preview:
            QMessageBox.warning(self, title, preview)

    def _report_import_outcome(
        self,
        import_label,
        requested,
        imported,
        failed,
        cancelled=False,
        validation_skipped=0,
        extras=None,
    ):
        parts = [
            f"requested: {int(max(0, requested))}",
            f"imported: {int(max(0, imported))}",
            f"failed: {int(max(0, failed))}",
        ]
        if validation_skipped > 0:
            parts.append(f"validation skipped: {int(validation_skipped)}")
        for key, value in (extras or []):
            parts.append(f"{key}: {value}")
        text = f"{import_label} import finished ({', '.join(parts)})."
        if cancelled:
            text += " Cancelled by user."

        if cancelled or int(failed) > 0:
            self.iface.messageBar().pushWarning("RasterLinker", text)
        else:
            self.iface.messageBar().pushInfo("RasterLinker", text)

    @staticmethod
    def _normalize_source_for_compare(source):
        raw = str(source or "").strip()
        if not raw:
            return ""
        base = raw.split("|", 1)[0].strip()
        try:
            return os.path.normcase(os.path.abspath(base))
        except Exception:
            return os.path.normcase(base)

    def _remove_loaded_layers_for_paths(self, paths):
        wanted = {
            self._normalize_source_for_compare(p)
            for p in (paths or [])
            if self._normalize_source_for_compare(p)
        }
        if not wanted:
            return 0
        project = QgsProject.instance()
        to_remove = []
        for lid, lyr in project.mapLayers().items():
            src = self._normalize_source_for_compare(lyr.source())
            if src and src in wanted:
                to_remove.append(lid)
        if to_remove:
            project.removeMapLayers(to_remove)
        return len(to_remove)

    @staticmethod
    def _remove_file_safe(path, warnings):
        pth = str(path or "").strip()
        if not pth:
            return False
        try:
            if os.path.exists(pth):
                os.remove(pth)
                return True
        except Exception as e:
            warnings.append(f"Delete failed for {os.path.basename(pth)}: {e}")
        return False

    @staticmethod
    def _radargram_sidecar_path(project_root, radargram_id):
        rid = str(radargram_id or "").replace(":", "_")
        if not rid:
            return ""
        return os.path.join(project_root, "metadata", "radargram_sidecars", f"{rid}.json")

    def _ask_partial_import_rollback(self, import_label, imported_count, failed_count, cancelled):
        if int(imported_count) <= 0:
            return False
        if not cancelled and int(failed_count) <= 0:
            return False

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle(f"{import_label} import - partial result")
        msg.setText(
            (
                f"{import_label} import ended with partial results.\n\n"
                f"Imported/copied this run: {int(imported_count)}\n"
                f"Issues: {int(failed_count)}"
                + ("\nStatus: cancelled by user" if cancelled else "")
                + "\n\nChoose action:"
            )
        )
        rollback_btn = msg.addButton("Rollback Imported", QMessageBox.DestructiveRole)
        keep_btn = msg.addButton("Keep Imported", QMessageBox.AcceptRole)
        msg.setDefaultButton(rollback_btn)
        msg.exec_()
        return msg.clickedButton() == rollback_btn

    def _rollback_timeslice_import(self, project_root, imported_records):
        records = [r for r in (imported_records or []) if isinstance(r, dict)]
        tids = {r.get("id") for r in records if r.get("id")}
        warnings = []
        deleted_files = 0
        layer_paths = []

        # Remove copied raster files.
        for rec in records:
            pth = rec.get("project_path")
            if pth:
                layer_paths.append(pth)
                if self._remove_file_safe(pth, warnings):
                    deleted_files += 1

        # Remove copied z-grids only when not referenced by remaining catalog records.
        z_grid_candidates = []
        for rec in records:
            z_path = rec.get("z_grid_project_path")
            if rec.get("z_grid_copied") and z_path:
                z_grid_candidates.append(z_path)
        if z_grid_candidates:
            try:
                catalog = load_catalog(project_root)
            except Exception:
                catalog = {}
            in_use = {
                self._normalize_source_for_compare(ts.get("z_grid_project_path"))
                for ts in catalog.get("timeslices", [])
                if ts.get("id") not in tids and ts.get("z_grid_project_path")
            }
            for z_path in z_grid_candidates:
                norm_z = self._normalize_source_for_compare(z_path)
                if norm_z and norm_z in in_use:
                    continue
                if self._remove_file_safe(z_path, warnings):
                    deleted_files += 1

        # Remove catalog references.
        removed_records = 0
        if tids:
            data = load_catalog(project_root)
            before = len(data.get("timeslices", []))
            data["timeslices"] = [r for r in data.get("timeslices", []) if r.get("id") not in tids]
            removed_records = max(0, before - len(data.get("timeslices", [])))
            for g in data.get("raster_groups", []):
                g["timeslice_ids"] = [tid for tid in g.get("timeslice_ids", []) if tid not in tids]
            data["links"] = [lk for lk in data.get("links", []) if lk.get("timeslice_id") not in tids]
            save_catalog(project_root, data)

        removed_layers = self._remove_loaded_layers_for_paths(layer_paths)
        return {
            "removed_records": removed_records,
            "deleted_files": deleted_files,
            "removed_layers": removed_layers,
            "warnings": warnings,
        }

    def _rollback_las_import(self, project_root, imported_files, model_ids):
        files = [r for r in (imported_files or []) if isinstance(r, dict)]
        mids = {v for v in (model_ids or []) if v}
        warnings = []
        deleted_files = 0
        layer_paths = []

        for rec in files:
            pth = rec.get("project_path")
            if pth:
                layer_paths.append(pth)
                if self._remove_file_safe(pth, warnings):
                    deleted_files += 1

        removed_records = 0
        if mids:
            data = load_catalog(project_root)
            before = len(data.get("models_3d", []))
            data["models_3d"] = [r for r in data.get("models_3d", []) if r.get("id") not in mids]
            removed_records = max(0, before - len(data.get("models_3d", [])))
            save_catalog(project_root, data)

        removed_layers = self._remove_loaded_layers_for_paths(layer_paths)
        return {
            "removed_records": removed_records,
            "deleted_files": deleted_files,
            "removed_layers": removed_layers,
            "warnings": warnings,
        }

    def _rollback_radargram_import(self, project_root, imported_files, radargram_ids):
        files = [r for r in (imported_files or []) if isinstance(r, dict)]
        rids = {v for v in (radargram_ids or []) if v}
        warnings = []
        deleted_files = 0
        layer_paths = []

        for rec in files:
            pth = rec.get("project_path")
            if pth:
                layer_paths.append(pth)
                if self._remove_file_safe(pth, warnings):
                    deleted_files += 1
            wf = rec.get("worldfile_path")
            if wf and self._remove_file_safe(wf, warnings):
                deleted_files += 1

        for rid in rids:
            sidecar = self._radargram_sidecar_path(project_root, rid)
            if sidecar:
                self._remove_file_safe(sidecar, warnings)

        removed_records = 0
        if rids:
            data = load_catalog(project_root)
            before = len(data.get("radargrams", []))
            data["radargrams"] = [r for r in data.get("radargrams", []) if r.get("id") not in rids]
            removed_records = max(0, before - len(data.get("radargrams", [])))
            for g in data.get("raster_groups", []):
                g["radargram_ids"] = [rid for rid in g.get("radargram_ids", []) if rid not in rids]
            data["links"] = [lk for lk in data.get("links", []) if lk.get("radargram_id") not in rids]
            save_catalog(project_root, data)

        removed_layers = self._remove_loaded_layers_for_paths(layer_paths)
        return {
            "removed_records": removed_records,
            "deleted_files": deleted_files,
            "removed_layers": removed_layers,
            "warnings": warnings,
        }

    def _get_preferred_import_crs(self):
        authid = (self.settings.value(self.settings_key_default_import_crs, "", type=str) or "").strip()
        if authid:
            try:
                from qgis.core import QgsCoordinateReferenceSystem
                crs = QgsCoordinateReferenceSystem(authid)
                if crs.isValid():
                    return crs
            except Exception:
                pass
        return QgsProject.instance().crs()

    def _choose_default_import_crs(self):
        selected = None
        try:
            dlg = QgsProjectionSelectionDialog(self)
            current = self._get_preferred_import_crs()
            if current is not None and current.isValid() and hasattr(dlg, "setCrs"):
                dlg.setCrs(current)

            result = None
            if hasattr(dlg, "exec_"):
                result = dlg.exec_()
            elif hasattr(dlg, "exec"):
                result = dlg.exec()

            if result in (QDialog.Accepted, 1, True) and hasattr(dlg, "crs"):
                selected = dlg.crs()
        except Exception:
            selected = None

        if selected is None or not selected.isValid():
            epsg_text, ok = QInputDialog.getText(
                self,
                "Set Import CRS",
                "Enter EPSG code (example: 32633) or AUTHID (example: EPSG:32633):",
                text="EPSG:32633",
            )
            if not ok or not epsg_text.strip():
                return
            try:
                from qgis.core import QgsCoordinateReferenceSystem
                raw = epsg_text.strip().upper()
                authid = raw if raw.startswith("EPSG:") else f"EPSG:{raw}"
                selected = QgsCoordinateReferenceSystem(authid)
            except Exception:
                selected = None

        if selected is None or not selected.isValid():
            QMessageBox.warning(self, "Set Import CRS", "Invalid CRS selection.")
            return

        self.settings.setValue(self.settings_key_default_import_crs, selected.authid())
        self.iface.messageBar().pushInfo("RasterLinker", f"Default import CRS set to {selected.authid()}")

    def _get_or_create_qgis_group(self, group_name):
        root = QgsProject.instance().layerTreeRoot()
        plugin_root = next(
            (
                g for g in root.children()
                if hasattr(g, "name") and g.name() == "RasterLinker"
            ),
            None,
        )
        if plugin_root is None:
            plugin_root = root.addGroup("RasterLinker")

        target = next(
            (
                g for g in plugin_root.children()
                if hasattr(g, "name") and g.name() == group_name
            ),
            None,
        )
        if target is None:
            target = plugin_root.addGroup(group_name)
        return target

    def _load_timeslice_paths_into_qgis_group(self, paths, group_name):
        if not paths:
            return 0
        target_group = self._get_or_create_qgis_group(group_name)
        existing_sources = {layer.source() for layer in QgsProject.instance().mapLayers().values()}
        target_crs = self._get_preferred_import_crs()
        loaded = 0
        for path in paths:
            if not path or not os.path.exists(path) or path in existing_sources:
                continue
            layer_name = os.path.basename(path)
            raster_layer = QgsRasterLayer(path, layer_name)
            if not raster_layer.isValid():
                continue
            if not raster_layer.crs().isValid() and target_crs.isValid():
                raster_layer.setCrs(target_crs)
            QgsProject.instance().addMapLayer(raster_layer, False)
            target_group.addLayer(raster_layer)
            existing_sources.add(path)
            loaded += 1
        return loaded

    def _inspect_timeslice(self, file_path):
        layer = QgsRasterLayer(file_path, os.path.basename(file_path))
        if not layer.isValid():
            return {"is_valid_raster": False}
        extent = layer.extent()
        return {
            "is_valid_raster": True,
            "crs": layer.crs().authid() if layer.crs().isValid() else None,
            "width": layer.width(),
            "height": layer.height(),
            "band_count": layer.bandCount(),
            "extent": {
                "xmin": extent.xMinimum(),
                "xmax": extent.xMaximum(),
                "ymin": extent.yMinimum(),
                "ymax": extent.yMaximum(),
            },
        }

    def _timeslice_georef_issues(self, meta):
        warnings = []
        extent = meta.get("extent") or {}
        xmin = extent.get("xmin")
        xmax = extent.get("xmax")
        ymin = extent.get("ymin")
        ymax = extent.get("ymax")
        crs_authid = (meta.get("crs") or "").strip()
        project_crs = QgsProject.instance().crs()

        missing_crs = False
        crs_mismatch = False
        suspicious_extent = False

        if not crs_authid:
            missing_crs = True
            warnings.append("Missing CRS in raster metadata.")
        elif project_crs.isValid() and crs_authid != project_crs.authid():
            crs_mismatch = True
            warnings.append(f"CRS mismatch: raster {crs_authid}, project {project_crs.authid()}.")

        if None in (xmin, xmax, ymin, ymax):
            suspicious_extent = True
            warnings.append("Missing extent metadata.")
        else:
            x_span = float(xmax) - float(xmin)
            y_span = float(ymax) - float(ymin)
            if x_span <= 0 or y_span <= 0:
                suspicious_extent = True
                warnings.append("Invalid extent (non-positive width/height).")
            else:
                max_abs = max(abs(float(xmin)), abs(float(xmax)), abs(float(ymin)), abs(float(ymax)))
                if max_abs < 1.0:
                    suspicious_extent = True
                    warnings.append("Extent is very close to origin (0,0).")
                if max_abs > 1e8:
                    suspicious_extent = True
                    warnings.append("Extent coordinates are unusually large.")

        return {
            "warnings": warnings,
            "missing_crs": missing_crs,
            "crs_mismatch": crs_mismatch,
            "suspicious_extent": suspicious_extent,
            "has_issue": bool(missing_crs or crs_mismatch or suspicious_extent),
        }

    def _timeslice_georef_warnings(self, meta):
        return list((self._timeslice_georef_issues(meta) or {}).get("warnings") or [])

    def _ensure_preferred_import_crs(self):
        preferred = self._get_preferred_import_crs()
        if preferred is not None and preferred.isValid():
            return preferred
        self._choose_default_import_crs()
        preferred = self._get_preferred_import_crs()
        if preferred is not None and preferred.isValid():
            return preferred
        return None

    def _validate_timeslice_records_before_import(self, records, scope_label="selected images"):
        if not records:
            return records, 0, False

        issue_rows = []
        missing_crs_count = 0
        mismatch_count = 0
        suspicious_count = 0

        for rec in records:
            meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
            issues = self._timeslice_georef_issues(meta)
            rec["warnings"] = list(issues.get("warnings") or [])
            rec["issues"] = issues
            if issues.get("missing_crs"):
                missing_crs_count += 1
            if issues.get("crs_mismatch"):
                mismatch_count += 1
            if issues.get("suspicious_extent"):
                suspicious_count += 1
            if issues.get("has_issue"):
                issue_rows.append(rec)

        if not issue_rows:
            return records, 0, False

        details = []
        for rec in issue_rows[:12]:
            name = os.path.basename(rec.get("source_path") or "")
            warns = "; ".join(rec.get("warnings") or [])
            details.append(f"- {name}: {warns}")
        extra = len(issue_rows) - len(details)
        if extra > 0:
            details.append(f"... and {extra} more.")

        summary = (
            f"Detected issues in {len(issue_rows)} / {len(records)} {scope_label}.\n"
            f"- Missing CRS: {missing_crs_count}\n"
            f"- CRS mismatch: {mismatch_count}\n"
            f"- Suspicious extent: {suspicious_count}\n\n"
            "Choose how to proceed:\n"
            "- Assign Default CRS + Import All: fills missing CRS only.\n"
            "- Import All Anyway: keep all selected files unchanged.\n"
            "- Skip Problematic: import only files without any issue.\n\n"
            + "\n".join(details)
        )

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Time-slice Import Validation")
        msg.setText("Potential georeference issues detected.")
        msg.setInformativeText(summary)
        assign_btn = None
        if missing_crs_count > 0:
            assign_btn = msg.addButton("Assign Default CRS + Import All", QMessageBox.AcceptRole)
        import_all_btn = msg.addButton("Import All Anyway", QMessageBox.AcceptRole)
        safe_btn = msg.addButton("Skip Problematic", QMessageBox.ActionRole)
        cancel_btn = msg.addButton(QMessageBox.Cancel)
        msg.setDefaultButton(safe_btn)
        msg.exec_()

        clicked = msg.clickedButton()
        if clicked == cancel_btn:
            return records, 0, True

        if clicked == safe_btn:
            filtered = [r for r in records if not ((r.get("issues") or {}).get("has_issue"))]
            skipped = max(0, len(records) - len(filtered))
            return filtered, skipped, False

        if assign_btn is not None and clicked == assign_btn:
            preferred = self._ensure_preferred_import_crs()
            if preferred is None or not preferred.isValid():
                return records, 0, True
            authid = preferred.authid()
            for rec in records:
                issues = rec.get("issues") or {}
                if issues.get("missing_crs"):
                    rec["assigned_crs"] = authid
                    rec_meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
                    rec_meta["crs"] = authid
                    rec["meta"] = rec_meta
                    rec["issues"] = self._timeslice_georef_issues(rec_meta)
                    rec["warnings"] = list((rec.get("issues") or {}).get("warnings") or [])
            return records, 0, False

        if clicked == import_all_btn:
            return records, 0, False

        return records, 0, True

    def _import_timeslices(self):
        if not self._ensure_project_ready():
            return
        if self._timeslice_import_active:
            self.iface.messageBar().pushWarning(
                "RasterLinker",
                "A time-slice import is already running.",
            )
            return

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Time-slice raster files",
            "",
            "Rasters (*.tif *.tiff *.png *.jpg *.jpeg *.asc *.img);;All files (*.*)",
        )
        if not file_paths:
            return

        records = []
        invalid = []
        scan_total = len(file_paths)
        scan_progress = QProgressDialog("Analyzing selected images...", "Cancel", 0, scan_total, self)
        scan_progress.setWindowTitle("Import Time-slices - Analyze")
        scan_progress.setWindowModality(Qt.WindowModal)
        scan_progress.setMinimumDuration(0)
        scan_progress.setAutoClose(True)
        scan_progress.setAutoReset(True)
        scan_progress.setValue(0)
        for idx, src_path in enumerate(file_paths, start=1):
            if scan_progress.wasCanceled():
                scan_progress.close()
                self.iface.messageBar().pushWarning("RasterLinker", "Time-slice import cancelled by user.")
                return
            scan_progress.setLabelText(f"Analyzing {idx}/{scan_total}: {os.path.basename(src_path)}")
            QApplication.processEvents()
            meta = self._inspect_timeslice(src_path)
            if not meta.get("is_valid_raster"):
                invalid.append(os.path.basename(src_path))
                scan_progress.setValue(idx)
                QApplication.processEvents()
                continue
            records.append(
                {
                    "source_path": src_path,
                    "meta": meta,
                    "warnings": self._timeslice_georef_warnings(meta),
                    "issues": self._timeslice_georef_issues(meta),
                    "assigned_crs": None,
                }
            )
            scan_progress.setValue(idx)
            QApplication.processEvents()
        scan_progress.close()

        if invalid:
            preview = "\n".join(invalid[:10])
            more = len(invalid) - min(len(invalid), 10)
            if more > 0:
                preview += f"\n... and {more} more."
            QMessageBox.warning(
                self,
                "Invalid raster files",
                "Some selected files are not valid rasters and will be skipped:\n\n" + preview,
            )

        if not records:
            self.iface.messageBar().pushWarning("RasterLinker", "No valid time-slice files to import.")
            return

        records, validation_skipped, validation_cancelled = self._validate_timeslice_records_before_import(
            records,
            scope_label="selected image(s)",
        )
        if validation_cancelled:
            self.iface.messageBar().pushWarning("RasterLinker", "Time-slice import cancelled by user.")
            return
        if not records:
            self.iface.messageBar().pushWarning("RasterLinker", "No files left to import after validation.")
            return

        target_project_root = self.project_root
        task_requested = len(records)
        import_task = TimesliceImportTask(
            target_project_root,
            records,
            description="RasterLinker: Importing time-slices",
        )
        self._timeslice_import_active = True
        if self.import_ts_btn is not None:
            self.import_ts_btn.setEnabled(False)

        def _on_import_finished(done_task, _ok):
            imported = 0
            linked_grids = int(done_task.linked_grids)
            imported_ids = []
            imported_paths = []
            cancelled = bool(done_task.cancelled)
            failures = list(done_task.failed)
            rollback_stats = None

            try:
                task_imported_records = list(done_task.imported_records)
                if task_imported_records:
                    try:
                        register_timeslices_batch(target_project_root, task_imported_records)
                        imported = len(task_imported_records)
                        imported_ids = [rec.get("id") for rec in task_imported_records if rec.get("id")]
                        imported_paths = [rec.get("project_path") for rec in task_imported_records if rec.get("project_path")]
                    except Exception as e:
                        failures.append(f"catalog write: {e}")
                        imported = 0
                        imported_ids = []
                        imported_paths = []

                if self._ask_partial_import_rollback(
                    "Time-slice",
                    imported_count=len(task_imported_records),
                    failed_count=len(failures),
                    cancelled=cancelled,
                ):
                    rollback_stats = self._rollback_timeslice_import(target_project_root, task_imported_records)
                    imported = 0
                    linked_grids = 0
                    imported_ids = []
                    imported_paths = []
                    for w in rollback_stats.get("warnings", []):
                        failures.append(f"rollback: {w}")

                extras = [("linked z-grids", linked_grids)]
                if rollback_stats:
                    extras.extend(
                        [
                            ("rollback deleted files", rollback_stats.get("deleted_files", 0)),
                            ("rollback removed records", rollback_stats.get("removed_records", 0)),
                        ]
                    )
                self._show_import_failures("Time-slice import warnings", failures)
                self._report_import_outcome(
                    "Time-slice",
                    requested=task_requested,
                    imported=imported,
                    failed=len(failures),
                    cancelled=cancelled,
                    validation_skipped=validation_skipped,
                    extras=extras,
                )
                self._notify_project_updated()

                if imported <= 0:
                    return

                group_name, ok = QInputDialog.getText(
                    self,
                    "New Image Group",
                    (
                        f"{imported} time-slice image(s) imported successfully.\n"
                        "Enter the group name to assign and load them:"
                    ),
                    text="TimeSlices",
                )
                if not ok or not group_name.strip():
                    QMessageBox.information(
                        self,
                        "Group assignment required",
                        (
                            "Time-slices were imported into the project folder and catalog,\n"
                            "but not assigned to a visible group."
                        ),
                    )
                    return

                try:
                    group, _ = create_raster_group(target_project_root, group_name.strip())
                    assign_timeslices_to_group(target_project_root, group.get("id"), imported_ids)
                    remove_timeslices_from_group(target_project_root, "grp_imported", imported_ids)
                    loaded_now = self._load_timeslice_paths_into_qgis_group(imported_paths, group.get("name", "TimeSlices"))
                    self.iface.messageBar().pushInfo(
                        "RasterLinker",
                        f"Imported time-slices assigned to group: {group.get('name')} (loaded: {loaded_now}).",
                    )
                    self._notify_project_updated()
                except Exception as e:
                    QMessageBox.warning(self, "Group assignment warning", str(e))
            finally:
                self._timeslice_import_active = False
                if self.import_ts_btn is not None:
                    self.import_ts_btn.setEnabled(True)

        start_task_with_progress_dialog(
            import_task,
            self,
            "Importing time-slices...",
            "Import Time-slices",
            on_finished=_on_import_finished,
        )
        self.iface.messageBar().pushInfo("RasterLinker", "Time-slice import started in background.")
        return

    def _import_las_laz(self):
        if not self._ensure_project_ready():
            return
        if self._las_import_active:
            self.iface.messageBar().pushWarning(
                "RasterLinker",
                "A LAS/LAZ import is already running.",
            )
            return

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select LAS/LAZ files",
            "",
            "Point cloud (*.las *.laz)",
        )
        if not file_paths:
            return

        target_project_root = self.project_root
        task_requested = len(file_paths)
        import_task = LasLazImportTask(
            target_project_root,
            file_paths,
            description="RasterLinker: Importing LAS/LAZ",
        )
        self._las_import_active = True
        if self.import_las_btn is not None:
            self.import_las_btn.setEnabled(False)

        def _on_import_finished(done_task, _ok):
            imported = 0
            loaded = 0
            failed = list(done_task.failed)
            cancelled = bool(done_task.cancelled)
            rollback_stats = None
            registered_model_ids = []

            try:
                for rec in done_task.imported_files:
                    source_path = rec.get("source_path") or ""
                    project_path = rec.get("project_path") or ""
                    normalized_name = rec.get("normalized_name") or os.path.basename(project_path)
                    imported_at = rec.get("imported_at") or utc_now_iso()
                    try:
                        meta = inspect_las_laz(project_path)
                        meta.update(
                            {
                                "id": f"model_{imported_at}_{normalized_name}",
                                "normalized_name": normalized_name,
                                "source_path": source_path,
                                "project_path": project_path,
                                "imported_at": imported_at,
                            }
                        )
                        register_model_3d(target_project_root, meta)
                        if meta.get("id"):
                            registered_model_ids.append(meta.get("id"))
                        imported += 1

                        layer_name = os.path.basename(project_path)
                        pc_layer = QgsPointCloudLayer(project_path, layer_name, "pdal")
                        if pc_layer.isValid():
                            QgsProject.instance().addMapLayer(pc_layer)
                            loaded += 1
                        else:
                            failed.append(
                                (
                                    f"{layer_name}: copied and cataloged, but not loaded in canvas. "
                                    "Try exporting as uncompressed LAS (recommended 1.2/1.4) and re-import."
                                )
                            )
                    except Exception as e:
                        label = os.path.basename(source_path) if source_path else normalized_name
                        failed.append(f"{label}: {e}")

                if self._ask_partial_import_rollback(
                    "LAS/LAZ",
                    imported_count=len(done_task.imported_files),
                    failed_count=len(failed),
                    cancelled=cancelled,
                ):
                    rollback_stats = self._rollback_las_import(
                        target_project_root,
                        list(done_task.imported_files),
                        registered_model_ids,
                    )
                    imported = 0
                    loaded = 0
                    for w in rollback_stats.get("warnings", []):
                        failed.append(f"rollback: {w}")

                extras = [("loaded in canvas", loaded)]
                if rollback_stats:
                    extras.extend(
                        [
                            ("rollback deleted files", rollback_stats.get("deleted_files", 0)),
                            ("rollback removed records", rollback_stats.get("removed_records", 0)),
                        ]
                    )
                self._show_import_failures("LAS/LAZ import warnings", failed)
                self._report_import_outcome(
                    "LAS/LAZ",
                    requested=task_requested,
                    imported=imported,
                    failed=len(failed),
                    cancelled=cancelled,
                    extras=extras,
                )
                self._notify_project_updated()
            finally:
                self._las_import_active = False
                if self.import_las_btn is not None:
                    self.import_las_btn.setEnabled(True)

        start_task_with_progress_dialog(
            import_task,
            self,
            "Importing LAS/LAZ files...",
            "Import LAS/LAZ",
            on_finished=_on_import_finished,
        )
        self.iface.messageBar().pushInfo("RasterLinker", "LAS/LAZ import started in background.")

    def _import_radargrams(self):
        if not self._ensure_project_ready():
            return
        if self._radargram_import_active:
            self.iface.messageBar().pushWarning(
                "RasterLinker",
                "A radargram import is already running.",
            )
            return

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Radargram files",
            "",
            "Radargrams (*.rd3 *.rad *.dzt *.npy *.csv *.txt *.png *.jpg *.jpeg *.tif *.tiff);;All files (*.*)",
        )
        if not file_paths:
            return

        file_paths, validation_skipped, validation_cancelled = self._validate_radargram_sources_before_import(
            file_paths,
            scope_label="selected radargram(s)",
        )
        if validation_cancelled:
            self.iface.messageBar().pushWarning("RasterLinker", "Radargram import cancelled by user.")
            return
        if not file_paths:
            self.iface.messageBar().pushWarning("RasterLinker", "No files left to import after validation.")
            return

        target_project_root = self.project_root
        task_requested = len(file_paths)
        import_task = RadargramImportTask(
            target_project_root,
            file_paths,
            description="RasterLinker: Importing radargrams",
        )
        self._radargram_import_active = True
        if self.import_rg_btn is not None:
            self.import_rg_btn.setEnabled(False)

        def _on_import_finished(done_task, _ok):
            imported = 0
            mapped = 0
            catalog_only = 0
            failed = list(done_task.failed)
            cancelled = bool(done_task.cancelled)
            rollback_stats = None
            registered_radargram_ids = []

            try:
                for rec in done_task.imported_files:
                    source_path = rec.get("source_path") or ""
                    project_path = rec.get("project_path") or ""
                    normalized_name = rec.get("normalized_name") or os.path.basename(project_path)
                    imported_at = rec.get("imported_at") or utc_now_iso()
                    try:
                        meta = inspect_radargram(project_path)
                        meta.update(
                            {
                                "id": f"radargram_{imported_at}_{normalized_name}",
                                "normalized_name": normalized_name,
                                "source_path": source_path,
                                "project_path": project_path,
                                "imported_at": imported_at,
                            }
                        )
                        geo_info = self._classify_radargram_georef(project_path)
                        meta.update(geo_info)
                        if rec.get("worldfile_path") and not meta.get("worldfile_path"):
                            meta["worldfile_path"] = rec.get("worldfile_path")
                        register_radargram(target_project_root, meta)
                        if meta.get("id"):
                            registered_radargram_ids.append(meta.get("id"))
                        save_radargram_sidecar(target_project_root, meta)
                        add_radargram_to_default_group(target_project_root, meta.get("id"))
                        if meta.get("import_mode") == "mapped":
                            mapped += 1
                        else:
                            catalog_only += 1
                        imported += 1
                    except Exception as e:
                        label = os.path.basename(source_path) if source_path else normalized_name
                        failed.append(f"{label}: {e}")

                if self._ask_partial_import_rollback(
                    "Radargram",
                    imported_count=len(done_task.imported_files),
                    failed_count=len(failed),
                    cancelled=cancelled,
                ):
                    rollback_stats = self._rollback_radargram_import(
                        target_project_root,
                        list(done_task.imported_files),
                        registered_radargram_ids,
                    )
                    imported = 0
                    mapped = 0
                    catalog_only = 0
                    for w in rollback_stats.get("warnings", []):
                        failed.append(f"rollback: {w}")

                extras = [("mapped", mapped), ("catalog-only", catalog_only)]
                if rollback_stats:
                    extras.extend(
                        [
                            ("rollback deleted files", rollback_stats.get("deleted_files", 0)),
                            ("rollback removed records", rollback_stats.get("removed_records", 0)),
                        ]
                    )
                self._show_import_failures("Radargram import warnings", failed)
                self._report_import_outcome(
                    "Radargram",
                    requested=task_requested,
                    imported=imported,
                    failed=len(failed),
                    cancelled=cancelled,
                    validation_skipped=validation_skipped,
                    extras=extras,
                )
                self._notify_project_updated()
            finally:
                self._radargram_import_active = False
                if self.import_rg_btn is not None:
                    self.import_rg_btn.setEnabled(True)

        start_task_with_progress_dialog(
            import_task,
            self,
            "Importing radargrams...",
            "Import Radargrams",
            on_finished=_on_import_finished,
        )
        self.iface.messageBar().pushInfo("RasterLinker", "Radargram import started in background.")

    def _import_manifest(self):
        if not self._ensure_project_ready():
            return
        if self._manifest_import_active:
            self.iface.messageBar().pushWarning("RasterLinker", "Manifest import is already running.")
            return

        manifest_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select manifest.json",
            "",
            "JSON (*.json)",
        )
        if not manifest_path:
            return

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Manifest Error", f"Invalid manifest file:\n{e}")
            return

        target_project_root = self.project_root
        model_sources = []
        radar_sources = []
        timeslice_items = []
        failures = []

        for item in manifest.get("models_3d", []):
            source_path = item.get("path") or item.get("source_path")
            if not source_path or not os.path.isfile(source_path):
                if source_path:
                    failures.append(f"models_3d missing source: {source_path}")
                continue
            model_sources.append(source_path)

        for item in manifest.get("radargrams", []):
            source_path = item.get("path") or item.get("source_path")
            if not source_path or not os.path.isfile(source_path):
                if source_path:
                    failures.append(f"radargrams missing source: {source_path}")
                continue
            radar_sources.append(source_path)

        ts_name_by_source = {}
        for item in manifest.get("timeslices", []):
            source_path = item.get("path") or item.get("source_path")
            if not source_path or not os.path.isfile(source_path):
                if source_path:
                    failures.append(f"timeslices missing source: {source_path}")
                continue
            timeslice_items.append(item)
            ts_name = (item.get("name") or "").strip()
            if ts_name:
                ts_name_by_source[os.path.normcase(os.path.abspath(source_path))] = ts_name

        counters = {
            "models": 0,
            "radargrams": 0,
            "timeslices": 0,
        }
        state = {"cancelled": False}

        self._manifest_import_active = True
        if self.import_manifest_btn is not None:
            self.import_manifest_btn.setEnabled(False)

        def _finish_manifest():
            try:
                if manifest.get("links"):
                    catalog = load_catalog(target_project_root)
                    catalog["links"].extend(manifest.get("links", []))
                    save_catalog(target_project_root, catalog)
            except Exception as e:
                failures.append(f"manifest links merge: {e}")

            if failures:
                preview = "\n".join(failures[:15])
                more = len(failures) - min(len(failures), 15)
                if more > 0:
                    preview += f"\n... and {more} more."
                QMessageBox.warning(self, "Manifest import warnings", preview)

            self.iface.messageBar().pushInfo(
                "RasterLinker",
                (
                    f"Manifest import completed (models: {counters['models']}, "
                    f"radargrams: {counters['radargrams']}, timeslices: {counters['timeslices']})."
                ),
            )
            if state["cancelled"]:
                self.iface.messageBar().pushWarning("RasterLinker", "Manifest import cancelled by user.")
            self._notify_project_updated()
            self._manifest_import_active = False
            if self.import_manifest_btn is not None:
                self.import_manifest_btn.setEnabled(True)

        def _start_timeslice_stage():
            records = []
            for item in timeslice_items:
                source_path = item.get("path") or item.get("source_path")
                if not source_path:
                    continue
                meta = self._inspect_timeslice(source_path)
                if not meta.get("is_valid_raster"):
                    failures.append(f"timeslice invalid raster: {source_path}")
                    continue
                records.append(
                    {
                        "source_path": source_path,
                        "meta": meta,
                        "warnings": self._timeslice_georef_warnings(meta),
                        "issues": self._timeslice_georef_issues(meta),
                        "assigned_crs": None,
                    }
                )

            records, validation_skipped, validation_cancelled = self._validate_timeslice_records_before_import(
                records,
                scope_label="manifest time-slice(s)",
            )
            if validation_cancelled:
                state["cancelled"] = True
                _finish_manifest()
                return
            if validation_skipped > 0:
                failures.append(f"timeslice validation skipped {validation_skipped} file(s)")

            if not records:
                _finish_manifest()
                return

            ts_task = TimesliceImportTask(
                target_project_root,
                records,
                description="RasterLinker: Manifest import - time-slices",
            )

            def _on_timeslice_done(done_task, _ok):
                try:
                    if done_task.cancelled:
                        state["cancelled"] = True
                    failures.extend(list(done_task.failed))
                    imported_records = list(done_task.imported_records)
                    for rec in imported_records:
                        src_key = os.path.normcase(os.path.abspath(rec.get("source_path") or ""))
                        ts_name = ts_name_by_source.get(src_key)
                        if ts_name:
                            rec["name"] = ts_name
                        rec["manifest_source"] = manifest_path
                    if imported_records:
                        register_timeslices_batch(target_project_root, imported_records)
                        for rec in imported_records:
                            tid = rec.get("id")
                            if tid:
                                add_timeslice_to_default_group(target_project_root, tid)
                        counters["timeslices"] += len(imported_records)
                except Exception as e:
                    failures.append(f"timeslice stage: {e}")
                finally:
                    _finish_manifest()

            start_task_with_progress_dialog(
                ts_task,
                self,
                "Manifest import: importing time-slices...",
                "Import Manifest - Time-slices",
                on_finished=_on_timeslice_done,
            )

        def _start_radar_stage():
            if not radar_sources:
                if state["cancelled"]:
                    _finish_manifest()
                else:
                    _start_timeslice_stage()
                return

            selected_sources, validation_skipped, validation_cancelled = self._validate_radargram_sources_before_import(
                radar_sources,
                scope_label="manifest radargram(s)",
            )
            if validation_cancelled:
                state["cancelled"] = True
                _finish_manifest()
                return
            if validation_skipped > 0:
                failures.append(f"radargram validation skipped {validation_skipped} file(s)")
            if not selected_sources:
                if state["cancelled"]:
                    _finish_manifest()
                else:
                    _start_timeslice_stage()
                return

            rg_task = RadargramImportTask(
                target_project_root,
                selected_sources,
                description="RasterLinker: Manifest import - radargrams",
            )

            def _on_radar_done(done_task, _ok):
                try:
                    if done_task.cancelled:
                        state["cancelled"] = True
                    failures.extend(list(done_task.failed))
                    for rec in done_task.imported_files:
                        source_path = rec.get("source_path") or ""
                        project_path = rec.get("project_path") or ""
                        normalized_name = rec.get("normalized_name") or os.path.basename(project_path)
                        imported_at = rec.get("imported_at") or utc_now_iso()
                        try:
                            meta = inspect_radargram(project_path)
                            meta.update(
                                {
                                    "id": f"radargram_{imported_at}_{normalized_name}",
                                    "normalized_name": normalized_name,
                                    "source_path": source_path,
                                    "project_path": project_path,
                                    "imported_at": imported_at,
                                    "manifest_source": manifest_path,
                                }
                            )
                            geo_info = self._classify_radargram_georef(project_path)
                            meta.update(geo_info)
                            if rec.get("worldfile_path") and not meta.get("worldfile_path"):
                                meta["worldfile_path"] = rec.get("worldfile_path")
                            register_radargram(target_project_root, meta)
                            save_radargram_sidecar(target_project_root, meta)
                            add_radargram_to_default_group(target_project_root, meta.get("id"))
                            counters["radargrams"] += 1
                        except Exception as e:
                            label = os.path.basename(source_path) if source_path else normalized_name
                            failures.append(f"radargram {label}: {e}")
                except Exception as e:
                    failures.append(f"radargram stage: {e}")
                finally:
                    if state["cancelled"]:
                        _finish_manifest()
                    else:
                        _start_timeslice_stage()

            start_task_with_progress_dialog(
                rg_task,
                self,
                "Manifest import: importing radargrams...",
                "Import Manifest - Radargrams",
                on_finished=_on_radar_done,
            )

        def _start_model_stage():
            if not model_sources:
                _start_radar_stage()
                return

            model_task = LasLazImportTask(
                target_project_root,
                model_sources,
                description="RasterLinker: Manifest import - models",
            )

            def _on_model_done(done_task, _ok):
                try:
                    if done_task.cancelled:
                        state["cancelled"] = True
                    failures.extend(list(done_task.failed))
                    for rec in done_task.imported_files:
                        source_path = rec.get("source_path") or ""
                        project_path = rec.get("project_path") or ""
                        normalized_name = rec.get("normalized_name") or os.path.basename(project_path)
                        imported_at = rec.get("imported_at") or utc_now_iso()
                        try:
                            meta = inspect_las_laz(project_path)
                            meta.update(
                                {
                                    "id": f"model_{imported_at}_{normalized_name}",
                                    "normalized_name": normalized_name,
                                    "source_path": source_path,
                                    "project_path": project_path,
                                    "imported_at": imported_at,
                                    "manifest_source": manifest_path,
                                }
                            )
                            register_model_3d(target_project_root, meta)
                            counters["models"] += 1
                        except Exception as e:
                            label = os.path.basename(source_path) if source_path else normalized_name
                            failures.append(f"model {label}: {e}")
                except Exception as e:
                    failures.append(f"model stage: {e}")
                finally:
                    if state["cancelled"]:
                        _finish_manifest()
                    else:
                        _start_radar_stage()

            start_task_with_progress_dialog(
                model_task,
                self,
                "Manifest import: importing models...",
                "Import Manifest - Models",
                on_finished=_on_model_done,
            )

        try:
            _start_model_stage()
            self.iface.messageBar().pushInfo("RasterLinker", "Manifest import started in background.")
        except Exception as e:
            failures.append(f"manifest pipeline start: {e}")
            _finish_manifest()

    def _classify_radargram_georef(self, project_path):
        """
        Classify imported radargram as 'mapped' or 'catalog_only' based on available georeference.
        """
        ext = os.path.splitext(project_path)[1].lower()
        worldfile = find_worldfile(project_path)
        has_worldfile = bool(worldfile)

        if ext in {".png", ".jpg", ".jpeg", ".bmp"}:
            if has_worldfile:
                return {
                    "import_mode": "mapped",
                    "georef_level": "worldfile",
                    "worldfile_path": worldfile,
                }
            return {
                "import_mode": "catalog_only",
                "georef_level": "none",
                "worldfile_path": None,
                "georef_warning": "Missing worldfile for image radargram.",
            }

        if ext in {".tif", ".tiff"}:
            raster = QgsRasterLayer(project_path, "radargram_probe")
            if raster.isValid() and raster.crs().isValid():
                return {
                    "import_mode": "mapped",
                    "georef_level": "embedded",
                    "worldfile_path": worldfile,
                    "crs": raster.crs().authid(),
                }
            if has_worldfile:
                return {
                    "import_mode": "mapped",
                    "georef_level": "worldfile",
                    "worldfile_path": worldfile,
                }
            return {
                "import_mode": "catalog_only",
                "georef_level": "none",
                "worldfile_path": None,
                "georef_warning": "TIFF without embedded georeference or worldfile.",
            }

        return {
            "import_mode": "catalog_only",
            "georef_level": "none",
            "worldfile_path": None,
            "georef_warning": "Format imported as catalog-only (non-raster georeference not available).",
        }

    def _copy_radargram_worldfile_if_present(self, source_path, project_path):
        source_wf = find_worldfile(source_path)
        if not source_wf:
            return None
        _, wf_ext = os.path.splitext(source_wf)
        target_wf = os.path.splitext(project_path)[0] + wf_ext.lower()
        shutil.copy2(source_wf, target_wf)
        return target_wf

    def _analyze_radargram_source(
        self,
        source_path,
        existing_project_names=None,
        existing_source_paths=None,
        seen_source_paths=None,
    ):
        existing_project_names = existing_project_names or set()
        existing_source_paths = existing_source_paths or set()
        seen_source_paths = seen_source_paths or set()

        src_abs = os.path.abspath(source_path)
        src_key = os.path.normcase(src_abs)
        name = os.path.basename(source_path)
        ext = os.path.splitext(name)[1].lower()
        has_worldfile = bool(find_worldfile(source_path))

        duplicate_source = src_key in existing_source_paths or src_key in seen_source_paths
        duplicate_name = name.lower() in existing_project_names

        likely_catalog_only = False
        crs_mismatch = False
        suspicious_extent = False
        warnings = []

        if duplicate_source:
            warnings.append("Source path already imported in catalog (or duplicated in this selection).")
        if duplicate_name:
            warnings.append("A file with the same name already exists in project catalog.")

        if ext in {".png", ".jpg", ".jpeg", ".bmp"}:
            if not has_worldfile:
                likely_catalog_only = True
                warnings.append("Image without worldfile: it will be imported as catalog-only.")
        elif ext in {".tif", ".tiff"}:
            layer = QgsRasterLayer(source_path, "radargram_probe_source")
            if layer.isValid():
                if layer.crs().isValid():
                    project_crs = QgsProject.instance().crs()
                    if project_crs.isValid() and layer.crs().authid() != project_crs.authid():
                        crs_mismatch = True
                        warnings.append(
                            f"CRS mismatch: raster {layer.crs().authid()}, project {project_crs.authid()}."
                        )
                elif not has_worldfile:
                    likely_catalog_only = True
                    warnings.append("TIFF without embedded georeference or worldfile: catalog-only import.")

                extent = layer.extent()
                if extent is None or extent.isNull() or extent.isEmpty():
                    suspicious_extent = True
                    warnings.append("Invalid raster extent.")
                else:
                    xmin = float(extent.xMinimum())
                    xmax = float(extent.xMaximum())
                    ymin = float(extent.yMinimum())
                    ymax = float(extent.yMaximum())
                    x_span = xmax - xmin
                    y_span = ymax - ymin
                    if x_span <= 0 or y_span <= 0:
                        suspicious_extent = True
                        warnings.append("Invalid extent (non-positive width/height).")
                    else:
                        max_abs = max(abs(xmin), abs(xmax), abs(ymin), abs(ymax))
                        if max_abs < 1.0:
                            suspicious_extent = True
                            warnings.append("Extent is very close to origin (0,0).")
                        if max_abs > 1e8:
                            suspicious_extent = True
                            warnings.append("Extent coordinates are unusually large.")
            elif not has_worldfile:
                likely_catalog_only = True
                warnings.append("TIFF is not readable as georeferenced raster and has no worldfile.")

        has_issue = bool(
            duplicate_source
            or duplicate_name
            or likely_catalog_only
            or crs_mismatch
            or suspicious_extent
        )
        return {
            "source_path": source_path,
            "source_key": src_key,
            "name": name,
            "ext": ext,
            "duplicate_source": duplicate_source,
            "duplicate_name": duplicate_name,
            "likely_catalog_only": likely_catalog_only,
            "crs_mismatch": crs_mismatch,
            "suspicious_extent": suspicious_extent,
            "has_issue": has_issue,
            "warnings": warnings,
        }

    def _validate_radargram_sources_before_import(self, file_paths, scope_label="selected radargram(s)"):
        paths = [p for p in (file_paths or []) if p]
        if not paths:
            return [], 0, False

        catalog = load_catalog(self.project_root)
        existing_project_names = {
            (r.get("normalized_name") or "").strip().lower()
            for r in catalog.get("radargrams", [])
            if (r.get("normalized_name") or "").strip()
        }
        existing_source_paths = {
            os.path.normcase(os.path.abspath(r.get("source_path") or ""))
            for r in catalog.get("radargrams", [])
            if (r.get("source_path") or "").strip()
        }

        analyses = []
        seen_source_paths = set()
        total = len(paths)
        progress = QProgressDialog("Analyzing selected radargrams...", "Cancel", 0, total, self)
        progress.setWindowTitle("Radargram Import - Analyze")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        progress.setValue(0)

        for idx, source_path in enumerate(paths, start=1):
            if progress.wasCanceled():
                progress.close()
                return paths, 0, True
            progress.setLabelText(f"Analyzing {idx}/{total}: {os.path.basename(source_path)}")
            QApplication.processEvents()
            analysis = self._analyze_radargram_source(
                source_path,
                existing_project_names=existing_project_names,
                existing_source_paths=existing_source_paths,
                seen_source_paths=seen_source_paths,
            )
            analyses.append(analysis)
            seen_source_paths.add(analysis.get("source_key"))
            progress.setValue(idx)
            QApplication.processEvents()

        progress.close()

        issue_rows = [a for a in analyses if a.get("has_issue")]
        if not issue_rows:
            return paths, 0, False

        duplicate_source_count = sum(1 for a in analyses if a.get("duplicate_source"))
        duplicate_name_count = sum(1 for a in analyses if a.get("duplicate_name"))
        catalog_only_count = sum(1 for a in analyses if a.get("likely_catalog_only"))
        mismatch_count = sum(1 for a in analyses if a.get("crs_mismatch"))
        suspicious_extent_count = sum(1 for a in analyses if a.get("suspicious_extent"))

        details = []
        for a in issue_rows[:12]:
            details.append(f"- {a.get('name')}: {'; '.join(a.get('warnings') or [])}")
        extra = len(issue_rows) - len(details)
        if extra > 0:
            details.append(f"... and {extra} more.")

        summary = (
            f"Detected issues in {len(issue_rows)} / {len(analyses)} {scope_label}.\n"
            f"- Duplicate source paths: {duplicate_source_count}\n"
            f"- Duplicate names: {duplicate_name_count}\n"
            f"- Likely catalog-only (missing georef on images): {catalog_only_count}\n"
            f"- CRS mismatch: {mismatch_count}\n"
            f"- Suspicious extent: {suspicious_extent_count}\n\n"
            "Choose how to proceed:\n"
            "- Import All Anyway: keep all selected files.\n"
            "- Skip Duplicates: remove duplicate source/name entries only.\n"
            "- Skip Problematic: import only files without any issue.\n\n"
            + "\n".join(details)
        )

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Radargram Import Validation")
        msg.setText("Potential import issues detected.")
        msg.setInformativeText(summary)
        import_all_btn = msg.addButton("Import All Anyway", QMessageBox.AcceptRole)
        skip_duplicates_btn = None
        if duplicate_source_count > 0 or duplicate_name_count > 0:
            skip_duplicates_btn = msg.addButton("Skip Duplicates", QMessageBox.ActionRole)
        skip_problematic_btn = msg.addButton("Skip Problematic", QMessageBox.ActionRole)
        cancel_btn = msg.addButton(QMessageBox.Cancel)
        msg.setDefaultButton(skip_problematic_btn)
        msg.exec_()

        clicked = msg.clickedButton()
        if clicked == cancel_btn:
            return paths, 0, True
        if skip_duplicates_btn is not None and clicked == skip_duplicates_btn:
            selected = [
                a.get("source_path")
                for a in analyses
                if not (a.get("duplicate_source") or a.get("duplicate_name"))
            ]
            skipped = max(0, len(analyses) - len(selected))
            return selected, skipped, False
        if clicked == skip_problematic_btn:
            selected = [a.get("source_path") for a in analyses if not a.get("has_issue")]
            skipped = max(0, len(analyses) - len(selected))
            return selected, skipped, False
        if clicked == import_all_btn:
            return paths, 0, False

        return paths, 0, True

    def _export_package(self):
        if not self._ensure_project_ready():
            return
        out_zip, _ = QFileDialog.getSaveFileName(
            self,
            "Export Project Package",
            "",
            "Zip archive (*.zip)",
        )
        if not out_zip:
            return
        try:
            zip_path = export_project_package(self.project_root, out_zip)
            self.iface.messageBar().pushInfo("RasterLinker", f"Package exported: {zip_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Package", f"Unable to export package:\n{e}")

    def _import_package(self):
        zip_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Project Package",
            "",
            "Zip archive (*.zip)",
        )
        if not zip_path:
            return
        target = QFileDialog.getExistingDirectory(self, "Select destination project folder")
        if not target:
            return
        try:
            import_project_package(zip_path, target)
            self.path_edit.setText(target)
            self.project_root = target
            self.settings.setValue(self.settings_key_active_project, target)
            sync_counts = self._sync_catalog_from_existing_files()
            self.iface.messageBar().pushInfo("RasterLinker", f"Package imported: {target}")
            if any(sync_counts.values()):
                self.iface.messageBar().pushInfo(
                    "RasterLinker",
                    (
                        "Existing data recognized "
                        f"(timeslices {sync_counts['timeslices']}, "
                        f"radargrams {sync_counts['radargrams']}, models {sync_counts['models']})."
                    ),
                )
            self._notify_project_updated()
        except Exception as e:
            QMessageBox.critical(self, "Import Package", f"Unable to import package:\n{e}")

    def _open_catalog_editor(self):
        if not self._ensure_project_ready():
            return
        dlg = CatalogEditorDialog(
            self.project_root,
            self,
            open_timeslice_manager_callback=self._open_timeslice_group_manager,
        )
        dlg.exec_()

    def _open_link_editor(self):
        if not self._ensure_project_ready():
            return
        dlg = LinkEditorDialog(self.project_root, self)
        dlg.exec_()

    def _open_timeslice_group_manager(self):
        if not self._ensure_project_ready():
            return
        dlg = TimesliceGroupManagerDialog(
            self.project_root,
            self,
            on_updated=self._notify_project_updated,
            open_catalog_editor_callback=self._open_catalog_editor,
        )
        dlg.exec_()
        self._notify_project_updated()

    def _open_project_health(self):
        if not self._ensure_project_ready():
            return
        dlg = ProjectHealthDialog(
            self.iface,
            self.project_root,
            self,
            on_updated=self._notify_project_updated,
        )
        dlg.exec_()

    def _view_catalog_summary(self):
        if not self._ensure_project_ready():
            return
        catalog = load_catalog(self.project_root)
        catalog_version = catalog.get("catalog_version", catalog.get("schema_version"))
        msg = (
            f"Project: {self.project_root}\n"
            f"Catalog version: {catalog_version}\n"
            f"Models 3D: {len(catalog.get('models_3d', []))}\n"
            f"Radargrams: {len(catalog.get('radargrams', []))}\n"
            f"Timeslices: {len(catalog.get('timeslices', []))}\n"
            f"Vector layers: {len(catalog.get('vector_layers', []))}\n"
            f"Links: {len(catalog.get('links', []))}\n"
            f"Catalog file: metadata/project_catalog.json"
        )
        QMessageBox.information(self, "Catalog Summary", msg)

    def _validate_project(self):
        if not self._ensure_project_ready():
            return
        report = validate_catalog(self.project_root)
        errors = report.get("errors", [])
        warnings = report.get("warnings", [])
        if not errors and not warnings:
            self.iface.messageBar().pushInfo("RasterLinker", "Validation passed: no issues found.")
            return

        lines = [f"Errors: {len(errors)}", f"Warnings: {len(warnings)}", ""]
        if errors:
            lines.append("[Errors]")
            lines.extend(errors[:20])
            if len(errors) > 20:
                lines.append(f"... and {len(errors) - 20} more errors.")
            lines.append("")
        if warnings:
            lines.append("[Warnings]")
            lines.extend(warnings[:20])
            if len(warnings) > 20:
                lines.append(f"... and {len(warnings) - 20} more warnings.")

        title = "Validation report"
        if errors:
            QMessageBox.warning(self, title, "\n".join(lines))
        else:
            QMessageBox.information(self, title, "\n".join(lines))

    def _reload_imported_layers(self):
        if not self._ensure_project_ready():
            return
        catalog = load_catalog(self.project_root)
        project = QgsProject.instance()
        existing_sources = {layer.source() for layer in project.mapLayers().values()}

        reloaded_models = 0
        for model in catalog.get("models_3d", []):
            p = model.get("project_path")
            if not p or not os.path.exists(p):
                continue
            if p in existing_sources:
                continue
            layer_name = model.get("normalized_name") or os.path.basename(p)
            pc_layer = QgsPointCloudLayer(p, layer_name, "pdal")
            if pc_layer.isValid():
                project.addMapLayer(pc_layer)
                reloaded_models += 1

        self.iface.messageBar().pushInfo(
            "RasterLinker",
            f"Reload complete. Point-cloud layers added: {reloaded_models}",
        )

    def _cleanup_catalog(self):
        if not self._ensure_project_ready():
            return
        if self._cleanup_active:
            self.iface.messageBar().pushWarning("RasterLinker", "Catalog cleanup is already running.")
            return
        cleanup_task = CatalogCleanupTask(self.project_root)
        self._cleanup_active = True
        if self.cleanup_btn is not None:
            self.cleanup_btn.setEnabled(False)

        def _on_cleanup_finished(done_task, _ok):
            try:
                if done_task.cancelled:
                    self.iface.messageBar().pushWarning("RasterLinker", "Catalog cleanup cancelled by user.")
                    return
                if done_task.error_message:
                    QMessageBox.warning(self, "Catalog cleanup", done_task.error_message)
                    return

                removed_models = int(done_task.removed_models)
                removed_radargrams = int(done_task.removed_radargrams)
                self.iface.messageBar().pushInfo(
                    "RasterLinker",
                    f"Catalog cleanup done. Removed models: {removed_models}, radargrams: {removed_radargrams}",
                )
                self._notify_project_updated()
            finally:
                self._cleanup_active = False
                if self.cleanup_btn is not None:
                    self.cleanup_btn.setEnabled(True)

        start_task_with_progress_dialog(
            cleanup_task,
            self,
            "Cleaning catalog...",
            "Catalog Cleanup",
            on_finished=_on_cleanup_finished,
        )
        self.iface.messageBar().pushInfo("RasterLinker", "Catalog cleanup started in background.")
