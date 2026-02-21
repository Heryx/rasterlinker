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

from .project_catalog import (
    ensure_project_structure,
    load_catalog,
    load_catalog_with_info,
    inspect_catalog_compatibility,
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
from .radargram_metadata import inspect_radargram
from .catalog_editor_dialog import CatalogEditorDialog
from .link_editor_dialog import LinkEditorDialog
from .timeslice_group_manager_dialog import TimesliceGroupManagerDialog
from .project_health_dialog import ProjectHealthDialog
from .vector_layer_manager_dialog import VectorLayerManagerDialog
from .project_manager_import_rollback_mixin import ProjectManagerImportRollbackMixin
from .project_manager_package_mixin import ProjectManagerPackageMixin
from .project_manager_radargram_validation_mixin import ProjectManagerRadargramValidationMixin
from .project_manager_timeslice_import_mixin import ProjectManagerTimesliceImportMixin


class ProjectManagerDialog(
    ProjectManagerImportRollbackMixin,
    ProjectManagerPackageMixin,
    ProjectManagerRadargramValidationMixin,
    ProjectManagerTimesliceImportMixin,
    QDialog,
):
    def __init__(self, iface, parent=None, on_project_updated=None):
        super().__init__(parent)
        self.iface = iface
        self.project_root = ""
        self.on_project_updated = on_project_updated
        self.settings = QSettings()
        self.settings_key_active_project = "GeoSurveyStudio/active_project_root"
        self.settings_key_default_import_crs = "GeoSurveyStudio/default_import_crs_authid"
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
        self.setWindowTitle("GeoSurvey Studio Project Manager")
        self.resize(760, 300)
        self._cached_plugin_version = None
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
            "Time-slice Manager handles time-slices and raster groups.\n"
            "Vector Layer Manager handles vector layers in project catalog."
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

        vector_mgr_btn = QPushButton("Vector Layer Manager")
        vector_mgr_btn.clicked.connect(self._open_vector_layer_manager)
        vector_mgr_btn.setMinimumHeight(28)
        catalog_layout.addWidget(vector_mgr_btn, 5, 0)

        health_btn = QPushButton("Project Health")
        health_btn.clicked.connect(self._open_project_health)
        health_btn.setMinimumHeight(28)
        catalog_layout.addWidget(health_btn, 5, 1)

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

    def _plugin_version(self):
        cached = str(self._cached_plugin_version or "").strip()
        if cached:
            return cached
        try:
            meta_path = os.path.join(os.path.dirname(__file__), "metadata.txt")
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip().lower().startswith("version="):
                            self._cached_plugin_version = line.split("=", 1)[1].strip()
                            break
        except Exception:
            self._cached_plugin_version = ""
        return str(self._cached_plugin_version or "").strip()

    def _ensure_catalog_compatible(self, project_root):
        plugin_ver = self._plugin_version()
        comp = inspect_catalog_compatibility(project_root, plugin_version=plugin_ver)
        status = str(comp.get("status") or "").strip()

        if status == "invalid_catalog":
            QMessageBox.critical(
                self,
                "Project Catalog Error",
                (
                    "Unable to read project catalog.\n\n"
                    f"Error: {comp.get('error') or 'unknown error'}\n\n"
                    "Fix or replace metadata/project_catalog.json before continuing."
                ),
            )
            return False

        if status == "future_catalog":
            raw_ver = comp.get("raw_catalog_version")
            answer = QMessageBox.question(
                self,
                "Catalog Newer Than Plugin",
                (
                    f"This project catalog is newer than the current plugin support.\n\n"
                    f"Catalog version: {raw_ver}\n"
                    f"Supported version: {comp.get('supported_catalog_version')}\n\n"
                    "You may continue, but some features might not work correctly.\n"
                    "Continue?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return False

        try:
            _catalog, info = load_catalog_with_info(
                project_root,
                plugin_version=plugin_ver,
                create_backup_on_migrate=True,
            )
        except Exception as e:
            QMessageBox.critical(self, "Project Catalog Error", f"Unable to load catalog:\n{e}")
            return False

        applied = list(info.get("applied_migrations") or [])
        backup_path = str(info.get("backup_path") or "").strip()
        if applied:
            self.iface.messageBar().pushInfo(
                "GeoSurvey Studio",
                (
                    f"Catalog migrated to v{info.get('final_version')} "
                    f"(from v{info.get('raw_version')})."
                ),
            )
            if backup_path:
                self.iface.messageBar().pushInfo(
                    "GeoSurvey Studio",
                    f"Pre-migration backup created: {backup_path}",
                )
        return True

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
                text="GeoSurveyStudioProject",
            )
            if not ok or not folder_name.strip():
                return
            folder = os.path.join(parent_dir, folder_name.strip())
            self.path_edit.setText(folder)

        ensure_project_structure(folder)
        if not self._ensure_catalog_compatible(folder):
            return
        self.project_root = folder
        self.settings.setValue(self.settings_key_active_project, folder)
        sync_counts = self._sync_catalog_from_existing_files()
        sync_msg = (
            f" (synced: timeslices {sync_counts['timeslices']}, "
            f"radargrams {sync_counts['radargrams']}, models {sync_counts['models']})"
            if any(sync_counts.values())
            else ""
        )
        self.iface.messageBar().pushInfo("GeoSurvey Studio", f"Project ready: {folder}")
        if sync_msg:
            self.iface.messageBar().pushInfo("GeoSurvey Studio", f"Existing data recognized{sync_msg}.")
        self._notify_project_updated()

    def _ensure_project_ready(self):
        if self.project_root:
            return True
        folder = self.path_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, "Project Manager", "Create/Open a project first.")
            return False
        ensure_project_structure(folder)
        if not self._ensure_catalog_compatible(folder):
            return False
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

    def _import_timeslices(self):
        if not self._ensure_project_ready():
            return
        if self._timeslice_import_active:
            self.iface.messageBar().pushWarning(
                "GeoSurvey Studio",
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
                self.iface.messageBar().pushWarning("GeoSurvey Studio", "Time-slice import cancelled by user.")
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
            self.iface.messageBar().pushWarning("GeoSurvey Studio", "No valid time-slice files to import.")
            return

        records, validation_skipped, validation_cancelled = self._validate_timeslice_records_before_import(
            records,
            scope_label="selected image(s)",
        )
        if validation_cancelled:
            self.iface.messageBar().pushWarning("GeoSurvey Studio", "Time-slice import cancelled by user.")
            return
        if not records:
            self.iface.messageBar().pushWarning("GeoSurvey Studio", "No files left to import after validation.")
            return

        target_project_root = self.project_root
        task_requested = len(records)
        import_task = TimesliceImportTask(
            target_project_root,
            records,
            description="GeoSurvey Studio: Importing time-slices",
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
                        "GeoSurvey Studio",
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
        self.iface.messageBar().pushInfo("GeoSurvey Studio", "Time-slice import started in background.")
        return

    def _import_las_laz(self):
        if not self._ensure_project_ready():
            return
        if self._las_import_active:
            self.iface.messageBar().pushWarning(
                "GeoSurvey Studio",
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
            description="GeoSurvey Studio: Importing LAS/LAZ",
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
        self.iface.messageBar().pushInfo("GeoSurvey Studio", "LAS/LAZ import started in background.")

    def _import_radargrams(self):
        if not self._ensure_project_ready():
            return
        if self._radargram_import_active:
            self.iface.messageBar().pushWarning(
                "GeoSurvey Studio",
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
            self.iface.messageBar().pushWarning("GeoSurvey Studio", "Radargram import cancelled by user.")
            return
        if not file_paths:
            self.iface.messageBar().pushWarning("GeoSurvey Studio", "No files left to import after validation.")
            return

        target_project_root = self.project_root
        task_requested = len(file_paths)
        import_task = RadargramImportTask(
            target_project_root,
            file_paths,
            description="GeoSurvey Studio: Importing radargrams",
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
        self.iface.messageBar().pushInfo("GeoSurvey Studio", "Radargram import started in background.")

    def _import_manifest(self):
        if not self._ensure_project_ready():
            return
        if self._manifest_import_active:
            self.iface.messageBar().pushWarning("GeoSurvey Studio", "Manifest import is already running.")
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
                "GeoSurvey Studio",
                (
                    f"Manifest import completed (models: {counters['models']}, "
                    f"radargrams: {counters['radargrams']}, timeslices: {counters['timeslices']})."
                ),
            )
            if state["cancelled"]:
                self.iface.messageBar().pushWarning("GeoSurvey Studio", "Manifest import cancelled by user.")
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
                description="GeoSurvey Studio: Manifest import - time-slices",
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
                description="GeoSurvey Studio: Manifest import - radargrams",
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
                description="GeoSurvey Studio: Manifest import - models",
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
            self.iface.messageBar().pushInfo("GeoSurvey Studio", "Manifest import started in background.")
        except Exception as e:
            failures.append(f"manifest pipeline start: {e}")
            _finish_manifest()

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

    def _open_vector_layer_manager(self):
        if not self._ensure_project_ready():
            return
        dlg = VectorLayerManagerDialog(
            self.iface,
            self.project_root,
            self,
            on_updated=self._notify_project_updated,
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
            self.iface.messageBar().pushInfo("GeoSurvey Studio", "Validation passed: no issues found.")
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
            "GeoSurvey Studio",
            f"Reload complete. Point-cloud layers added: {reloaded_models}",
        )

    def _cleanup_catalog(self):
        if not self._ensure_project_ready():
            return
        if self._cleanup_active:
            self.iface.messageBar().pushWarning("GeoSurvey Studio", "Catalog cleanup is already running.")
            return
        cleanup_task = CatalogCleanupTask(self.project_root)
        self._cleanup_active = True
        if self.cleanup_btn is not None:
            self.cleanup_btn.setEnabled(False)

        def _on_cleanup_finished(done_task, _ok):
            try:
                if done_task.cancelled:
                    self.iface.messageBar().pushWarning("GeoSurvey Studio", "Catalog cleanup cancelled by user.")
                    return
                if done_task.error_message:
                    QMessageBox.warning(self, "Catalog cleanup", done_task.error_message)
                    return

                removed_models = int(done_task.removed_models)
                removed_radargrams = int(done_task.removed_radargrams)
                self.iface.messageBar().pushInfo(
                    "GeoSurvey Studio",
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
        self.iface.messageBar().pushInfo("GeoSurvey Studio", "Catalog cleanup started in background.")

