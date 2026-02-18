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
    save_radargram_sidecar,
    add_radargram_to_default_group,
    create_raster_group,
    assign_timeslices_to_group,
    remove_timeslices_from_group,
    validate_catalog,
    normalize_copy_into_project,
    export_project_package,
    import_project_package,
    utc_now_iso,
)
from .pointcloud_metadata import inspect_las_laz
from .radargram_metadata import inspect_radargram, find_worldfile
from .catalog_editor_dialog import CatalogEditorDialog
from .link_editor_dialog import LinkEditorDialog
from .timeslice_group_manager_dialog import TimesliceGroupManagerDialog


class ProjectManagerDialog(QDialog):
    def __init__(self, iface, parent=None, on_project_updated=None):
        super().__init__(parent)
        self.iface = iface
        self.project_root = ""
        self.on_project_updated = on_project_updated
        self.settings = QSettings()
        self.settings_key_active_project = "RasterLinker/active_project_root"
        self.settings_key_default_import_crs = "RasterLinker/default_import_crs_authid"
        self.setWindowTitle("RasterLinker Project Manager")
        self.resize(760, 300)
        self._build_ui()
        self._apply_styles()

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

        import_las_btn = QPushButton("Import LAS/LAZ")
        import_las_btn.clicked.connect(self._import_las_laz)
        import_las_btn.setMinimumHeight(28)
        import_layout.addWidget(import_las_btn, 0, 0)

        import_rg_btn = QPushButton("Import Radargrams")
        import_rg_btn.clicked.connect(self._import_radargrams)
        import_rg_btn.setMinimumHeight(28)
        import_layout.addWidget(import_rg_btn, 0, 1)

        import_ts_btn = QPushButton("Import Time-slices")
        import_ts_btn.clicked.connect(self._import_timeslices)
        import_ts_btn.setMinimumHeight(28)
        import_layout.addWidget(import_ts_btn, 1, 0)

        import_manifest_btn = QPushButton("Import Manifest")
        import_manifest_btn.clicked.connect(self._import_manifest)
        import_manifest_btn.setMinimumHeight(28)
        import_layout.addWidget(import_manifest_btn, 1, 1)

        catalog_box = QGroupBox("Catalog & QA")
        catalog_layout = QGridLayout(catalog_box)
        catalog_layout.setHorizontalSpacing(8)
        catalog_layout.setVerticalSpacing(8)

        view_catalog_btn = QPushButton("View Catalog")
        view_catalog_btn.clicked.connect(self._view_catalog_summary)
        view_catalog_btn.setMinimumHeight(28)
        catalog_layout.addWidget(view_catalog_btn, 0, 0)

        edit_catalog_btn = QPushButton("Catalog Editor")
        edit_catalog_btn.clicked.connect(self._open_catalog_editor)
        edit_catalog_btn.setMinimumHeight(28)
        catalog_layout.addWidget(edit_catalog_btn, 0, 1)

        validate_btn = QPushButton("Validate")
        validate_btn.clicked.connect(self._validate_project)
        validate_btn.setMinimumHeight(28)
        catalog_layout.addWidget(validate_btn, 1, 0)

        crs_btn = QPushButton("Set Import CRS")
        crs_btn.clicked.connect(self._choose_default_import_crs)
        crs_btn.setMinimumHeight(28)
        catalog_layout.addWidget(crs_btn, 1, 1)

        cleanup_btn = QPushButton("Cleanup Catalog")
        cleanup_btn.clicked.connect(self._cleanup_catalog)
        cleanup_btn.setMinimumHeight(28)
        catalog_layout.addWidget(cleanup_btn, 2, 0)

        reload_btn = QPushButton("Reload Layers")
        reload_btn.clicked.connect(self._reload_imported_layers)
        reload_btn.setMinimumHeight(28)
        catalog_layout.addWidget(reload_btn, 2, 1)

        links_btn = QPushButton("Link Editor")
        links_btn.clicked.connect(self._open_link_editor)
        links_btn.setMinimumHeight(28)
        catalog_layout.addWidget(links_btn, 3, 0)

        ts_mgr_btn = QPushButton("Time-slice Manager")
        ts_mgr_btn.clicked.connect(self._open_timeslice_group_manager)
        ts_mgr_btn.setMinimumHeight(28)
        catalog_layout.addWidget(ts_mgr_btn, 3, 1)

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

    def _timeslice_georef_warnings(self, meta):
        warnings = []
        extent = meta.get("extent") or {}
        xmin = extent.get("xmin")
        xmax = extent.get("xmax")
        ymin = extent.get("ymin")
        ymax = extent.get("ymax")
        crs_authid = (meta.get("crs") or "").strip()
        project_crs = QgsProject.instance().crs()

        if not crs_authid:
            warnings.append("Missing CRS in raster metadata.")
        elif project_crs.isValid() and crs_authid != project_crs.authid():
            warnings.append(f"CRS mismatch: raster {crs_authid}, project {project_crs.authid()}.")

        if None in (xmin, xmax, ymin, ymax):
            return warnings

        x_span = float(xmax) - float(xmin)
        y_span = float(ymax) - float(ymin)
        if x_span <= 0 or y_span <= 0:
            warnings.append("Invalid extent (non-positive width/height).")
            return warnings

        if max(abs(float(xmin)), abs(float(xmax)), abs(float(ymin)), abs(float(ymax))) < 1.0:
            warnings.append("Extent is very close to origin (0,0).")

        if max(abs(float(xmin)), abs(float(xmax)), abs(float(ymin)), abs(float(ymax))) > 1e8:
            warnings.append("Extent coordinates are unusually large.")

        return warnings

    def _import_timeslices(self):
        if not self._ensure_project_ready():
            return

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Time-slice raster files",
            "",
            "Rasters (*.tif *.tiff *.png *.jpg *.jpeg *.asc *.img);;All files (*.*)",
        )
        if not file_paths:
            return

        imported = 0
        imported_ids = []
        imported_paths = []
        georef_warnings = []
        total = len(file_paths)
        progress = QProgressDialog("Importing time-slices...", "Cancel", 0, total, self)
        progress.setWindowTitle("Import Time-slices")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        progress.setValue(0)

        cancelled = False
        failures = []
        for idx, file_path in enumerate(file_paths, start=1):
            if progress.wasCanceled():
                cancelled = True
                break
            progress.setLabelText(f"Importing {idx}/{total}: {os.path.basename(file_path)}")
            QApplication.processEvents()
            try:
                project_path, normalized_name = normalize_copy_into_project(
                    self.project_root, "timeslices_2d", file_path
                )
                meta = self._inspect_timeslice(project_path)
                warn_list = self._timeslice_georef_warnings(meta)
                meta.update(
                    {
                        "id": f"timeslice_{utc_now_iso()}_{normalized_name}",
                        "name": os.path.splitext(normalized_name)[0],
                        "normalized_name": normalized_name,
                        "source_path": file_path,
                        "project_path": project_path,
                        "imported_at": utc_now_iso(),
                    }
                )
                if warn_list:
                    meta["georef_warnings"] = warn_list
                    georef_warnings.append((normalized_name, warn_list))
                register_timeslice(self.project_root, meta)
                imported_ids.append(meta.get("id"))
                imported_paths.append(project_path)
                imported += 1
            except Exception as e:
                failures.append(f"{os.path.basename(file_path)}: {e}")
            progress.setValue(idx)
            QApplication.processEvents()

        progress.close()

        if failures:
            preview = "\n".join(failures[:10])
            more = len(failures) - min(len(failures), 10)
            if more > 0:
                preview += f"\n... and {more} more."
            QMessageBox.warning(self, "Import warning", preview)

        self.iface.messageBar().pushInfo("RasterLinker", f"Time-slices import completed ({imported}).")
        if cancelled:
            self.iface.messageBar().pushWarning("RasterLinker", "Time-slice import cancelled by user.")
        if georef_warnings:
            preview_lines = []
            for name, warns in georef_warnings[:12]:
                preview_lines.append(f"{name}: {'; '.join(warns)}")
            more = len(georef_warnings) - len(preview_lines)
            if more > 0:
                preview_lines.append(f"... and {more} more.")
            QMessageBox.warning(
                self,
                "Time-slice Georeference Warnings",
                "Potential georeference issues detected:\n\n" + "\n".join(preview_lines),
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
            group, _ = create_raster_group(self.project_root, group_name.strip())
            assign_timeslices_to_group(self.project_root, group.get("id"), imported_ids)
            remove_timeslices_from_group(self.project_root, "grp_imported", imported_ids)
            loaded_now = self._load_timeslice_paths_into_qgis_group(imported_paths, group.get("name", "TimeSlices"))
            self.iface.messageBar().pushInfo(
                "RasterLinker",
                f"Imported time-slices assigned to group: {group.get('name')} (loaded: {loaded_now}).",
            )
            self._notify_project_updated()
        except Exception as e:
            QMessageBox.warning(self, "Group assignment warning", str(e))

    def _import_las_laz(self):
        if not self._ensure_project_ready():
            return

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select LAS/LAZ files",
            "",
            "Point cloud (*.las *.laz)",
        )
        if not file_paths:
            return

        for file_path in file_paths:
            try:
                project_path, normalized_name = normalize_copy_into_project(
                    self.project_root, "volumes_3d", file_path
                )
                meta = inspect_las_laz(project_path)
                meta.update(
                    {
                        "id": f"model_{utc_now_iso()}_{normalized_name}",
                        "normalized_name": normalized_name,
                        "source_path": file_path,
                        "project_path": project_path,
                        "imported_at": utc_now_iso(),
                    }
                )
                register_model_3d(self.project_root, meta)

                layer_name = os.path.basename(project_path)
                pc_layer = QgsPointCloudLayer(project_path, layer_name, "pdal")
                if pc_layer.isValid():
                    QgsProject.instance().addMapLayer(pc_layer)
                else:
                    self.iface.messageBar().pushWarning(
                        "RasterLinker",
                        (
                            "Point cloud copied and cataloged, but not loaded in canvas. "
                            "Try exporting as uncompressed LAS (recommended 1.2/1.4) and re-import."
                        ),
                    )
            except Exception as e:
                QMessageBox.warning(self, "Import warning", f"{file_path}\n{e}")

        self.iface.messageBar().pushInfo("RasterLinker", "LAS/LAZ import completed.")

    def _import_radargrams(self):
        if not self._ensure_project_ready():
            return

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Radargram files",
            "",
            "Radargrams (*.rd3 *.rad *.dzt *.npy *.csv *.txt *.png *.jpg *.jpeg *.tif *.tiff);;All files (*.*)",
        )
        if not file_paths:
            return

        proceed = self._preflight_radargram_import(file_paths)
        if not proceed:
            return

        imported = 0
        mapped = 0
        catalog_only = 0
        for file_path in file_paths:
            try:
                project_path, normalized_name = normalize_copy_into_project(
                    self.project_root, "radargrams", file_path
                )
                self._copy_radargram_worldfile_if_present(file_path, project_path)
                meta = inspect_radargram(project_path)
                meta.update(
                    {
                        "id": f"radargram_{utc_now_iso()}_{normalized_name}",
                        "normalized_name": normalized_name,
                        "source_path": file_path,
                        "project_path": project_path,
                        "imported_at": utc_now_iso(),
                    }
                )
                geo_info = self._classify_radargram_georef(project_path)
                meta.update(geo_info)
                register_radargram(self.project_root, meta)
                save_radargram_sidecar(self.project_root, meta)
                add_radargram_to_default_group(self.project_root, meta.get("id"))
                if meta.get("import_mode") == "mapped":
                    mapped += 1
                else:
                    catalog_only += 1
                imported += 1
            except Exception as e:
                QMessageBox.warning(self, "Import warning", f"{file_path}\n{e}")

        self.iface.messageBar().pushInfo(
            "RasterLinker",
            f"Radargrams import completed ({imported}) - mapped: {mapped}, catalog-only: {catalog_only}.",
        )

    def _import_manifest(self):
        if not self._ensure_project_ready():
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

        imported_models = 0
        imported_radargrams = 0
        imported_timeslices = 0

        for item in manifest.get("models_3d", []):
            source_path = item.get("path") or item.get("source_path")
            if not source_path or not os.path.isfile(source_path):
                continue
            try:
                project_path, normalized_name = normalize_copy_into_project(
                    self.project_root, "volumes_3d", source_path
                )
                meta = inspect_las_laz(project_path)
                meta.update(
                    {
                        "id": f"model_{utc_now_iso()}_{normalized_name}",
                        "normalized_name": normalized_name,
                        "source_path": source_path,
                        "project_path": project_path,
                        "imported_at": utc_now_iso(),
                        "manifest_source": manifest_path,
                    }
                )
                register_model_3d(self.project_root, meta)
                imported_models += 1
            except Exception:
                continue

        for item in manifest.get("radargrams", []):
            source_path = item.get("path") or item.get("source_path")
            if not source_path or not os.path.isfile(source_path):
                continue
            try:
                project_path, normalized_name = normalize_copy_into_project(
                    self.project_root, "radargrams", source_path
                )
                self._copy_radargram_worldfile_if_present(source_path, project_path)
                meta = inspect_radargram(project_path)
                meta.update(
                    {
                        "id": f"radargram_{utc_now_iso()}_{normalized_name}",
                        "normalized_name": normalized_name,
                        "source_path": source_path,
                        "project_path": project_path,
                        "imported_at": utc_now_iso(),
                        "manifest_source": manifest_path,
                    }
                )
                geo_info = self._classify_radargram_georef(project_path)
                meta.update(geo_info)
                register_radargram(self.project_root, meta)
                save_radargram_sidecar(self.project_root, meta)
                add_radargram_to_default_group(self.project_root, meta.get("id"))
                imported_radargrams += 1
            except Exception:
                continue

        for item in manifest.get("timeslices", []):
            source_path = item.get("path") or item.get("source_path")
            if not source_path or not os.path.isfile(source_path):
                continue
            try:
                project_path, normalized_name = normalize_copy_into_project(
                    self.project_root, "timeslices_2d", source_path
                )
                meta = self._inspect_timeslice(project_path)
                meta.update(
                    {
                        "id": f"timeslice_{utc_now_iso()}_{normalized_name}",
                        "name": item.get("name") or os.path.splitext(normalized_name)[0],
                        "normalized_name": normalized_name,
                        "source_path": source_path,
                        "project_path": project_path,
                        "imported_at": utc_now_iso(),
                        "manifest_source": manifest_path,
                    }
                )
                register_timeslice(self.project_root, meta)
                add_timeslice_to_default_group(self.project_root, meta.get("id"))
                imported_timeslices += 1
            except Exception:
                continue

        # Optional passthrough of links/timeslices if present.
        if any(k in manifest for k in ("links", "timeslices")):
            catalog = load_catalog(self.project_root)
            catalog["links"].extend(manifest.get("links", []))
            catalog["timeslices"].extend(manifest.get("timeslices", []))
            save_catalog(self.project_root, catalog)

        self.iface.messageBar().pushInfo(
            "RasterLinker",
            (
                f"Manifest import completed (models: {imported_models}, "
                f"radargrams: {imported_radargrams}, timeslices: {imported_timeslices})."
            ),
        )

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

    def _preflight_radargram_import(self, file_paths):
        catalog = load_catalog(self.project_root)
        existing_project_names = {
            (r.get("normalized_name") or "").lower() for r in catalog.get("radargrams", [])
        }
        existing_source_paths = {
            os.path.normcase(r.get("source_path") or "") for r in catalog.get("radargrams", [])
        }

        possible_catalog_only = 0
        duplicate_sources = 0
        duplicate_names = 0

        for file_path in file_paths:
            ext = os.path.splitext(file_path)[1].lower()
            if ext in {".png", ".jpg", ".jpeg", ".bmp"} and not find_worldfile(file_path):
                possible_catalog_only += 1
            if os.path.normcase(file_path) in existing_source_paths:
                duplicate_sources += 1
            norm_name = os.path.basename(file_path).lower()
            if norm_name in existing_project_names:
                duplicate_names += 1

        if not (possible_catalog_only or duplicate_sources or duplicate_names):
            return True

        msg = (
            f"Preflight checks:\n"
            f"- Image files without worldfile (likely catalog-only): {possible_catalog_only}\n"
            f"- Already imported source paths: {duplicate_sources}\n"
            f"- Potential duplicate names in project: {duplicate_names}\n\n"
            "Continue import anyway?"
        )
        answer = QMessageBox.question(
            self,
            "Radargram Import Preflight",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

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
        dlg = CatalogEditorDialog(self.project_root, self)
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
        )
        dlg.exec_()
        self._notify_project_updated()

    def _view_catalog_summary(self):
        if not self._ensure_project_ready():
            return
        catalog = load_catalog(self.project_root)
        msg = (
            f"Project: {self.project_root}\n"
            f"Schema version: {catalog.get('schema_version')}\n"
            f"Models 3D: {len(catalog.get('models_3d', []))}\n"
            f"Radargrams: {len(catalog.get('radargrams', []))}\n"
            f"Timeslices: {len(catalog.get('timeslices', []))}\n"
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
        catalog = load_catalog(self.project_root)

        before_models = len(catalog.get("models_3d", []))
        before_radargrams = len(catalog.get("radargrams", []))

        catalog["models_3d"] = [
            m for m in catalog.get("models_3d", [])
            if m.get("project_path") and os.path.exists(m.get("project_path"))
        ]
        catalog["radargrams"] = [
            r for r in catalog.get("radargrams", [])
            if r.get("project_path") and os.path.exists(r.get("project_path"))
        ]

        save_catalog(self.project_root, catalog)

        removed_models = before_models - len(catalog["models_3d"])
        removed_radargrams = before_radargrams - len(catalog["radargrams"])
        self.iface.messageBar().pushInfo(
            "RasterLinker",
            f"Catalog cleanup done. Removed models: {removed_models}, radargrams: {removed_radargrams}",
        )
