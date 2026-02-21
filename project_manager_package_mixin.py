# -*- coding: utf-8 -*-
"""Package import/export actions for Project Manager dialog."""

from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox, QInputDialog

from .project_catalog import (
    export_project_package,
    export_project_package_portable,
    import_project_package,
    inspect_package_import_conflicts,
)


class ProjectManagerPackageMixin:
    def _export_package(self):
        if not self._ensure_project_ready():
            return
        mode_label, ok = QInputDialog.getItem(
            self,
            "Export Package Mode",
            "Choose export mode:",
            [
                "Full package (entire project folder)",
                "Portable package (catalog-linked assets)",
            ],
            0,
            False,
        )
        if not ok:
            return
        portable_mode = "Portable package" in str(mode_label)

        out_zip, _ = QFileDialog.getSaveFileName(
            self,
            "Export Project Package",
            "",
            "Zip archive (*.zip)",
        )
        if not out_zip:
            return
        try:
            if portable_mode:
                report = export_project_package_portable(self.project_root, out_zip)
                zip_path = report.get("zip_path")
                included = len(report.get("included_files", []) or [])
                missing = len(report.get("missing_files", []) or [])
                external = len(report.get("external_files", []) or [])
                self.iface.messageBar().pushInfo(
                    "GeoSurvey Studio",
                    (
                        f"Portable package exported: {zip_path} "
                        f"(included: {included}, missing: {missing}, external skipped: {external})"
                    ),
                )
                if missing or external:
                    details = []
                    if missing:
                        details.append(f"Missing files skipped: {missing}")
                    if external:
                        details.append(f"External files skipped: {external}")
                    QMessageBox.warning(
                        self,
                        "Portable Package Report",
                        "Portable package exported with warnings.\n\n" + "\n".join(details),
                    )
            else:
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
            preview = inspect_package_import_conflicts(zip_path, target)
            conflict_count = int(preview.get("conflict_count", 0))
            total_entries = int(preview.get("total_entries", 0))
            if conflict_count > 0:
                answer = QMessageBox.question(
                    self,
                    "Import Package",
                    (
                        f"The package contains {total_entries} file(s).\n"
                        f"{conflict_count} file(s) already exist in destination and may be overwritten.\n\n"
                        "Continue import?"
                    ),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return

            report = import_project_package(zip_path, target, return_report=True)
            self.path_edit.setText(target)
            self.project_root = target
            self.settings.setValue(self.settings_key_active_project, target)
            sync_counts = self._sync_catalog_from_existing_files()
            self.iface.messageBar().pushInfo(
                "GeoSurvey Studio",
                (
                    f"Package imported: {target} "
                    f"(entries: {report.get('total_entries', 0)}, overwritten: {report.get('overwritten_entries', 0)})"
                ),
            )
            skipped_unsafe = report.get("skipped_unsafe_entries", []) or []
            if skipped_unsafe:
                QMessageBox.warning(
                    self,
                    "Import Package Safety Report",
                    f"Skipped unsafe archive entries: {len(skipped_unsafe)}",
                )
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
