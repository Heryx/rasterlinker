# -*- coding: utf-8 -*-
"""
Simple Project Manager dialog for 2D/3D project folder workflow.
"""

import os
import json

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
)
from qgis.core import QgsProject, QgsPointCloudLayer

from .project_catalog import (
    ensure_project_structure,
    load_catalog,
    save_catalog,
    register_model_3d,
    register_radargram,
    normalize_copy_into_project,
    export_project_package,
    import_project_package,
    utc_now_iso,
)
from .pointcloud_metadata import inspect_las_laz
from .radargram_metadata import inspect_radargram
from .catalog_editor_dialog import CatalogEditorDialog


class ProjectManagerDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.project_root = ""
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

        create_btn = QPushButton("Create/Open Project")
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

        import_manifest_btn = QPushButton("Import Manifest")
        import_manifest_btn.clicked.connect(self._import_manifest)
        import_manifest_btn.setMinimumHeight(28)
        import_layout.addWidget(import_manifest_btn, 1, 0, 1, 2)

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

        cleanup_btn = QPushButton("Cleanup Catalog")
        cleanup_btn.clicked.connect(self._cleanup_catalog)
        cleanup_btn.setMinimumHeight(28)
        catalog_layout.addWidget(cleanup_btn, 1, 1)

        reload_btn = QPushButton("Reload Layers")
        reload_btn.clicked.connect(self._reload_imported_layers)
        reload_btn.setMinimumHeight(28)
        catalog_layout.addWidget(reload_btn, 2, 0, 1, 2)

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

        close_btn = QPushButton("Close")
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
            QMessageBox.warning(self, "Project Manager", "Please select a project folder.")
            return
        ensure_project_structure(folder)
        self.project_root = folder
        self.iface.messageBar().pushInfo("RasterLinker", f"Project ready: {folder}")

    def _ensure_project_ready(self):
        if self.project_root:
            return True
        folder = self.path_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, "Project Manager", "Create/Open a project first.")
            return False
        ensure_project_structure(folder)
        self.project_root = folder
        return True

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

        imported = 0
        for file_path in file_paths:
            try:
                project_path, normalized_name = normalize_copy_into_project(
                    self.project_root, "radargrams", file_path
                )
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
                register_radargram(self.project_root, meta)
                imported += 1
            except Exception as e:
                QMessageBox.warning(self, "Import warning", f"{file_path}\n{e}")

        self.iface.messageBar().pushInfo("RasterLinker", f"Radargrams import completed ({imported}).")

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
                register_radargram(self.project_root, meta)
                imported_radargrams += 1
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
            f"Manifest import completed (models: {imported_models}, radargrams: {imported_radargrams}).",
        )

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
            self.iface.messageBar().pushInfo("RasterLinker", f"Package imported: {target}")
        except Exception as e:
            QMessageBox.critical(self, "Import Package", f"Unable to import package:\n{e}")

    def _open_catalog_editor(self):
        if not self._ensure_project_ready():
            return
        dlg = CatalogEditorDialog(self.project_root, self)
        dlg.exec_()

    def _view_catalog_summary(self):
        if not self._ensure_project_ready():
            return
        catalog = load_catalog(self.project_root)
        msg = (
            f"Project: {self.project_root}\n"
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
        catalog = load_catalog(self.project_root)
        issues = []

        for model in catalog.get("models_3d", []):
            p = model.get("project_path")
            if not p or not os.path.exists(p):
                issues.append(f"Missing model file: {p}")
            if not model.get("crs"):
                issues.append(f"Model without CRS: {model.get('normalized_name') or model.get('name')}")

        for rg in catalog.get("radargrams", []):
            p = rg.get("project_path")
            if not p or not os.path.exists(p):
                issues.append(f"Missing radargram file: {p}")

        if not issues:
            self.iface.messageBar().pushInfo("RasterLinker", "Validation passed: no issues found.")
            return

        preview = "\n".join(issues[:30])
        if len(issues) > 30:
            preview += f"\n... and {len(issues) - 30} more issues."
        QMessageBox.warning(self, "Validation report", preview)

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
