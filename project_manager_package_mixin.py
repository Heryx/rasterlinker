# -*- coding: utf-8 -*-
"""Package import/export actions for Project Manager dialog."""

from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

from .project_catalog import export_project_package, import_project_package


class ProjectManagerPackageMixin:
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
            self.iface.messageBar().pushInfo("GeoSurvey Studio", f"Package exported: {zip_path}")
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
            self.iface.messageBar().pushInfo("GeoSurvey Studio", f"Package imported: {target}")
            if any(sync_counts.values()):
                self.iface.messageBar().pushInfo(
                    "GeoSurvey Studio",
                    (
                        "Existing data recognized "
                        f"(timeslices {sync_counts['timeslices']}, "
                        f"radargrams {sync_counts['radargrams']}, models {sync_counts['models']})."
                    ),
                )
            self._notify_project_updated()
        except Exception as e:
            QMessageBox.critical(self, "Import Package", f"Unable to import package:\n{e}")
