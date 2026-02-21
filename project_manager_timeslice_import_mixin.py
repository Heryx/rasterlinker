# -*- coding: utf-8 -*-
"""Time-slice import helpers for Project Manager dialog."""

import os

from PyQt5.QtWidgets import QDialog, QInputDialog, QMessageBox
from qgis.core import QgsProject, QgsRasterLayer
from qgis.gui import QgsProjectionSelectionDialog


class ProjectManagerTimesliceImportMixin:
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
        self.iface.messageBar().pushInfo("GeoSurvey Studio", f"Default import CRS set to {selected.authid()}")

    def _get_or_create_qgis_group(self, group_name):
        root = QgsProject.instance().layerTreeRoot()
        plugin_root = next(
            (
                g
                for g in root.children()
                if hasattr(g, "name") and g.name() == "GeoSurvey Studio"
            ),
            None,
        )
        if plugin_root is None:
            plugin_root = root.addGroup("GeoSurvey Studio")

        target = next(
            (
                g
                for g in plugin_root.children()
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
