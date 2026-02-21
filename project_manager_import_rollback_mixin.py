# -*- coding: utf-8 -*-
"""Import outcome reporting and rollback helpers for Project Manager dialog."""

import os

from PyQt5.QtWidgets import QMessageBox
from qgis.core import QgsProject

from .project_catalog import load_catalog, save_catalog


class ProjectManagerImportRollbackMixin:
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
            self.iface.messageBar().pushWarning("GeoSurvey Studio", text)
        else:
            self.iface.messageBar().pushInfo("GeoSurvey Studio", text)

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

        for rec in records:
            pth = rec.get("project_path")
            if pth:
                layer_paths.append(pth)
                if self._remove_file_safe(pth, warnings):
                    deleted_files += 1

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
