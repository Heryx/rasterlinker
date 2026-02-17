# -*- coding: utf-8 -*-
"""
Simple Project Manager dialog for 2D/3D project folder workflow.
"""

import os

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QMessageBox,
)
from qgis.core import QgsProject, QgsPointCloudLayer

from .project_catalog import ensure_project_structure, register_model_3d
from .pointcloud_metadata import inspect_las_laz


class ProjectManagerDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.project_root = ""
        self.setWindowTitle("RasterLinker Project Manager")
        self.resize(620, 160)
        self._build_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)

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

        actions = QHBoxLayout()
        create_btn = QPushButton("Create/Open Project")
        create_btn.clicked.connect(self._create_or_open_project)
        actions.addWidget(create_btn)

        import_las_btn = QPushButton("Import LAS/LAZ")
        import_las_btn.clicked.connect(self._import_las_laz)
        actions.addWidget(import_las_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        actions.addWidget(close_btn)
        root_layout.addLayout(actions)

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

    def _import_las_laz(self):
        if not self.project_root:
            folder = self.path_edit.text().strip()
            if not folder:
                QMessageBox.warning(self, "Project Manager", "Create/Open a project first.")
                return
            ensure_project_structure(folder)
            self.project_root = folder

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
                meta = inspect_las_laz(file_path)
                register_model_3d(self.project_root, meta)

                layer_name = os.path.basename(file_path)
                pc_layer = QgsPointCloudLayer(file_path, layer_name, "pdal")
                if pc_layer.isValid():
                    QgsProject.instance().addMapLayer(pc_layer)
            except Exception as e:
                QMessageBox.warning(self, "Import warning", f"{file_path}\n{e}")

        self.iface.messageBar().pushInfo("RasterLinker", "LAS/LAZ import completed.")
