# -*- coding: utf-8 -*-
"""Project health panel for catalog diagnostics and quick fixes."""

import os

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QGroupBox,
    QCheckBox,
    QMessageBox,
)
from qgis.core import QgsProject

from .project_catalog import load_catalog, save_catalog, validate_catalog


class ProjectHealthDialog(QDialog):
    def __init__(self, iface, project_root, parent=None, on_updated=None):
        super().__init__(parent)
        self.iface = iface
        self.project_root = project_root
        self.on_updated = on_updated
        self._build_ui()
        self._refresh_report()

    def _build_ui(self):
        self.setWindowTitle("Project Health")
        self.resize(860, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        title = QLabel(
            "Analyze catalog consistency and run quick fixes "
            "(missing files, broken links, empty groups, missing CRS)."
        )
        title.setWordWrap(True)
        root.addWidget(title)

        self.report_edit = QPlainTextEdit(self)
        self.report_edit.setReadOnly(True)
        self.report_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        root.addWidget(self.report_edit, 1)

        fix_box = QGroupBox("Quick Fix Options", self)
        fix_layout = QVBoxLayout(fix_box)
        fix_layout.setContentsMargins(8, 8, 8, 8)
        fix_layout.setSpacing(4)

        self.fix_missing_files_cb = QCheckBox("Remove catalog records for missing files")
        self.fix_missing_files_cb.setChecked(True)
        fix_layout.addWidget(self.fix_missing_files_cb)

        self.fix_missing_zgrid_cb = QCheckBox("Clear missing linked z-grid paths on time-slices")
        self.fix_missing_zgrid_cb.setChecked(True)
        fix_layout.addWidget(self.fix_missing_zgrid_cb)

        self.fix_group_refs_cb = QCheckBox("Clean invalid group/link references")
        self.fix_group_refs_cb.setChecked(True)
        fix_layout.addWidget(self.fix_group_refs_cb)

        self.fix_empty_groups_cb = QCheckBox("Remove empty groups (except default Imported)")
        self.fix_empty_groups_cb.setChecked(True)
        fix_layout.addWidget(self.fix_empty_groups_cb)

        self.fix_assign_crs_cb = QCheckBox("Assign project CRS to time-slices missing CRS")
        self.fix_assign_crs_cb.setChecked(False)
        fix_layout.addWidget(self.fix_assign_crs_cb)

        root.addWidget(fix_box, 0)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_report)
        buttons.addWidget(self.refresh_btn)

        self.apply_btn = QPushButton("Apply Quick Fixes")
        self.apply_btn.clicked.connect(self._apply_quick_fixes)
        buttons.addWidget(self.apply_btn)

        buttons.addStretch(1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

    def _project_crs_authid(self):
        try:
            crs = QgsProject.instance().crs()
            if crs is not None and crs.isValid():
                return crs.authid()
        except Exception:
            pass
        return ""

    @staticmethod
    def _record_missing_path(rec):
        pth = str((rec or {}).get("project_path") or "").strip()
        if not pth:
            return True
        return not os.path.exists(pth)

    def _collect_report(self):
        catalog = load_catalog(self.project_root)

        issues = {"errors": [], "warnings": [], "info": []}
        ids = {
            "models_missing": set(),
            "radargrams_missing": set(),
            "timeslices_missing": set(),
            "timeslices_missing_zgrid": set(),
            "timeslices_missing_crs": set(),
            "empty_groups": set(),
        }

        models = list(catalog.get("models_3d", []))
        radargrams = list(catalog.get("radargrams", []))
        timeslices = list(catalog.get("timeslices", []))
        groups = list(catalog.get("raster_groups", []))
        links = list(catalog.get("links", []))

        project_crs = self._project_crs_authid()
        if project_crs:
            issues["info"].append(f"Project CRS: {project_crs}")
        else:
            issues["warnings"].append("Project CRS is not set/valid.")

        model_ids = {str(r.get("id") or "") for r in models if r.get("id")}
        radar_ids = {str(r.get("id") or "") for r in radargrams if r.get("id")}
        ts_ids = {str(r.get("id") or "") for r in timeslices if r.get("id")}

        for rec in models:
            rid = str(rec.get("id") or "")
            if self._record_missing_path(rec):
                ids["models_missing"].add(rid)
                issues["errors"].append(
                    f"Model missing on disk: {rid or '<no id>'} -> {rec.get('project_path') or '<empty>'}"
                )

        for rec in radargrams:
            rid = str(rec.get("id") or "")
            if self._record_missing_path(rec):
                ids["radargrams_missing"].add(rid)
                issues["errors"].append(
                    f"Radargram missing on disk: {rid or '<no id>'} -> {rec.get('project_path') or '<empty>'}"
                )

        for rec in timeslices:
            tid = str(rec.get("id") or "")
            if self._record_missing_path(rec):
                ids["timeslices_missing"].add(tid)
                issues["warnings"].append(
                    f"Time-slice missing on disk: {tid or '<no id>'} -> {rec.get('project_path') or '<empty>'}"
                )
            z_grid_path = str(rec.get("z_grid_project_path") or "").strip()
            if z_grid_path and not os.path.exists(z_grid_path):
                ids["timeslices_missing_zgrid"].add(tid)
                issues["warnings"].append(
                    f"Missing linked z-grid: {tid or '<no id>'} -> {z_grid_path}"
                )
            crs = str(rec.get("assigned_crs") or rec.get("crs") or "").strip()
            if not crs:
                ids["timeslices_missing_crs"].add(tid)
                issues["warnings"].append(f"Time-slice without CRS: {tid or '<no id>'}")

        for grp in groups:
            gid = str(grp.get("id") or "")
            gname = str(grp.get("name") or gid or "Group")
            ts_refs = list(grp.get("timeslice_ids", []) or [])
            rg_refs = list(grp.get("radargram_ids", []) or [])
            if not ts_refs and not rg_refs:
                ids["empty_groups"].add(gid)
                issues["warnings"].append(f"Empty group: {gname} ({gid})")
            unknown_ts = [tid for tid in ts_refs if tid not in ts_ids]
            unknown_rg = [rid for rid in rg_refs if rid not in radar_ids]
            if unknown_ts:
                issues["warnings"].append(
                    f"Group {gname} has invalid time-slice refs: {', '.join(unknown_ts[:5])}"
                )
            if unknown_rg:
                issues["warnings"].append(
                    f"Group {gname} has invalid radargram refs: {', '.join(unknown_rg[:5])}"
                )

        for lk in links:
            rid = lk.get("radargram_id")
            tid = lk.get("timeslice_id")
            lid = lk.get("id") or "<no id>"
            if rid and rid not in radar_ids:
                issues["warnings"].append(f"Link {lid} references unknown radargram_id: {rid}")
            if tid and tid not in ts_ids:
                issues["warnings"].append(f"Link {lid} references unknown timeslice_id: {tid}")

        try:
            validation = validate_catalog(self.project_root, catalog)
            v_errors = list(validation.get("errors", []) or [])
            v_warnings = list(validation.get("warnings", []) or [])
            if v_errors or v_warnings:
                issues["info"].append(
                    f"validate_catalog summary -> errors: {len(v_errors)}, warnings: {len(v_warnings)}"
                )
        except Exception as e:
            issues["warnings"].append(f"validate_catalog failed: {e}")

        return {"catalog": catalog, "issues": issues, "ids": ids}

    def _report_to_text(self, report):
        issues = report.get("issues", {})
        errs = list(issues.get("errors", []) or [])
        warns = list(issues.get("warnings", []) or [])
        infos = list(issues.get("info", []) or [])

        lines = [
            "RasterLinker Project Health Report",
            "",
            f"Project: {self.project_root}",
            "",
            f"Errors: {len(errs)}",
            f"Warnings: {len(warns)}",
            f"Info: {len(infos)}",
            "",
        ]

        if errs:
            lines.append("[ERRORS]")
            lines.extend(errs[:60])
            if len(errs) > 60:
                lines.append(f"... and {len(errs) - 60} more.")
            lines.append("")
        if warns:
            lines.append("[WARNINGS]")
            lines.extend(warns[:120])
            if len(warns) > 120:
                lines.append(f"... and {len(warns) - 120} more.")
            lines.append("")
        if infos:
            lines.append("[INFO]")
            lines.extend(infos[:40])
            if len(infos) > 40:
                lines.append(f"... and {len(infos) - 40} more.")
            lines.append("")
        if not errs and not warns:
            lines.append("No issues detected.")
        return "\n".join(lines)

    def _refresh_report(self):
        report = self._collect_report()
        self.report_edit.setPlainText(self._report_to_text(report))

    def _apply_quick_fixes(self):
        report = self._collect_report()
        catalog = report["catalog"]
        ids = report["ids"]
        project_crs = self._project_crs_authid()

        missing_models = set(ids.get("models_missing", set()))
        missing_radargrams = set(ids.get("radargrams_missing", set()))
        missing_timeslices = set(ids.get("timeslices_missing", set()))
        missing_zgrid_timeslices = set(ids.get("timeslices_missing_zgrid", set()))
        empty_groups = set(ids.get("empty_groups", set()))

        changes = {
            "removed_models": 0,
            "removed_radargrams": 0,
            "removed_timeslices": 0,
            "cleared_zgrid_links": 0,
            "cleaned_group_refs": 0,
            "removed_empty_groups": 0,
            "removed_links": 0,
            "assigned_timeslice_crs": 0,
        }
        changed = False

        if self.fix_missing_files_cb.isChecked():
            if missing_models:
                before = len(catalog.get("models_3d", []))
                catalog["models_3d"] = [
                    r for r in catalog.get("models_3d", []) if str(r.get("id") or "") not in missing_models
                ]
                removed = before - len(catalog.get("models_3d", []))
                if removed > 0:
                    changed = True
                    changes["removed_models"] += removed

            if missing_radargrams:
                before = len(catalog.get("radargrams", []))
                catalog["radargrams"] = [
                    r for r in catalog.get("radargrams", []) if str(r.get("id") or "") not in missing_radargrams
                ]
                removed = before - len(catalog.get("radargrams", []))
                if removed > 0:
                    changed = True
                    changes["removed_radargrams"] += removed

            if missing_timeslices:
                before = len(catalog.get("timeslices", []))
                catalog["timeslices"] = [
                    r for r in catalog.get("timeslices", []) if str(r.get("id") or "") not in missing_timeslices
                ]
                removed = before - len(catalog.get("timeslices", []))
                if removed > 0:
                    changed = True
                    changes["removed_timeslices"] += removed

        if self.fix_missing_zgrid_cb.isChecked() and missing_zgrid_timeslices:
            for rec in catalog.get("timeslices", []):
                tid = str(rec.get("id") or "")
                if tid not in missing_zgrid_timeslices:
                    continue
                if rec.get("z_grid_project_path") or rec.get("z_grid_source_path"):
                    rec["z_grid_project_path"] = None
                    rec["z_grid_source_path"] = None
                    rec["z_grid_band"] = 1
                    rec["z_grid_linked_at"] = None
                    if str(rec.get("z_source") or "").strip().lower() == "surfer_grid":
                        rec["z_source"] = "none"
                    changed = True
                    changes["cleared_zgrid_links"] += 1

        if self.fix_assign_crs_cb.isChecked() and project_crs:
            for rec in catalog.get("timeslices", []):
                assigned = str(rec.get("assigned_crs") or "").strip()
                crs = str(rec.get("crs") or "").strip()
                if not assigned and not crs:
                    rec["assigned_crs"] = project_crs
                    changed = True
                    changes["assigned_timeslice_crs"] += 1

        # Always rebuild known ids from current catalog after removals.
        known_radar = {str(r.get("id") or "") for r in catalog.get("radargrams", []) if r.get("id")}
        known_ts = {str(r.get("id") or "") for r in catalog.get("timeslices", []) if r.get("id")}

        if self.fix_group_refs_cb.isChecked():
            for grp in catalog.get("raster_groups", []):
                before_ts = len(grp.get("timeslice_ids", []) or [])
                before_rg = len(grp.get("radargram_ids", []) or [])
                grp["timeslice_ids"] = [tid for tid in (grp.get("timeslice_ids", []) or []) if tid in known_ts]
                grp["radargram_ids"] = [rid for rid in (grp.get("radargram_ids", []) or []) if rid in known_radar]
                delta = (before_ts - len(grp["timeslice_ids"])) + (before_rg - len(grp["radargram_ids"]))
                if delta > 0:
                    changed = True
                    changes["cleaned_group_refs"] += delta

            before_links = len(catalog.get("links", []) or [])
            catalog["links"] = [
                lk
                for lk in (catalog.get("links", []) or [])
                if (not lk.get("radargram_id") or lk.get("radargram_id") in known_radar)
                and (not lk.get("timeslice_id") or lk.get("timeslice_id") in known_ts)
            ]
            removed_links = before_links - len(catalog.get("links", []) or [])
            if removed_links > 0:
                changed = True
                changes["removed_links"] += removed_links

        if self.fix_empty_groups_cb.isChecked():
            before = len(catalog.get("raster_groups", []))
            catalog["raster_groups"] = [
                g
                for g in (catalog.get("raster_groups", []) or [])
                if str(g.get("id") or "") == "grp_imported"
                or (g.get("timeslice_ids") or g.get("radargram_ids"))
                or str(g.get("id") or "") not in empty_groups
            ]
            removed = before - len(catalog.get("raster_groups", []))
            if removed > 0:
                changed = True
                changes["removed_empty_groups"] += removed

        if not changed:
            QMessageBox.information(self, "Project Health", "No quick-fix change was necessary.")
            self._refresh_report()
            return

        save_catalog(self.project_root, catalog)
        if callable(self.on_updated):
            try:
                self.on_updated()
            except Exception:
                pass

        summary = (
            "Quick fixes applied:\n"
            f"- Removed models: {changes['removed_models']}\n"
            f"- Removed radargrams: {changes['removed_radargrams']}\n"
            f"- Removed time-slices: {changes['removed_timeslices']}\n"
            f"- Cleared z-grid links: {changes['cleared_zgrid_links']}\n"
            f"- Cleaned group refs: {changes['cleaned_group_refs']}\n"
            f"- Removed links: {changes['removed_links']}\n"
            f"- Removed empty groups: {changes['removed_empty_groups']}\n"
            f"- Assigned missing CRS: {changes['assigned_timeslice_crs']}"
        )
        QMessageBox.information(self, "Project Health", summary)
        self._refresh_report()
