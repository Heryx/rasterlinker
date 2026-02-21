# -*- coding: utf-8 -*-
"""Radargram source validation helpers for Project Manager dialog."""

import os
import shutil

from PyQt5.QtWidgets import QApplication, QProgressDialog, QMessageBox
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject, QgsRasterLayer

from .project_catalog import load_catalog
from .radargram_metadata import find_worldfile


class ProjectManagerRadargramValidationMixin:
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
