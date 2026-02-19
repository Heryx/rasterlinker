# -*- coding: utf-8 -*-
"""Trace Build 3D and export mixin for RasterLinker plugin."""

import os.path

from qgis.PyQt.QtWidgets import QMessageBox, QInputDialog, QFileDialog
from qgis.core import (
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsCoordinateTransform,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsVectorFileWriter,
)


class TraceBuild3DMixin:
    def _build3d_mode_choices(self):
        return [
            ("Constant Z (from depth/z_value)", "constant"),
            ("From linked z-grid", "linked_grid"),
            ("Orthometric (DTM - depth)", "orthometric"),
        ]

    def _default_build3d_output_name(self, source_layer, mode):
        suffix_map = {
            "constant": "_3D",
            "linked_grid": "_3D_grid",
            "orthometric": "_3D_ortho",
        }
        return f"{source_layer.name()}{suffix_map.get(mode, '_3D')}"

    def _unique_map_layer_name(self, base_name):
        base = (base_name or "Trace3D").strip() or "Trace3D"
        existing = {lyr.name() for lyr in QgsProject.instance().mapLayers().values()}
        if base not in existing:
            return base
        i = 1
        while True:
            candidate = f"{base}_{i:03d}"
            if candidate not in existing:
                return candidate
            i += 1

    def _choose_build_3d_mode_only(self, default_mode="constant"):
        choices = self._build3d_mode_choices()
        labels = [c[0] for c in choices]
        default_idx = 0
        for idx, (_, mode_key) in enumerate(choices):
            if mode_key == default_mode:
                default_idx = idx
                break
        chosen_label, ok = QInputDialog.getItem(
            self._ui_parent(),
            "Build 3D Batch",
            "Mode:",
            labels,
            default_idx,
            False,
        )
        if not ok:
            return None
        return choices[labels.index(chosen_label)][1]

    def _build3d_batch_source_layers(self):
        layers = []
        for lyr in QgsProject.instance().mapLayers().values():
            if not self._is_line_layer(lyr):
                continue
            # In batch mode prioritize trace-related layers to avoid converting unrelated lines.
            if hasattr(self, "_is_trace_related_line_layer"):
                try:
                    if not self._is_trace_related_line_layer(lyr):
                        continue
                except Exception:
                    continue
            layers.append(lyr)
        return layers

    def _ensure_saved_before_next_step(self, layer, action_title):
        if layer is None:
            return False
        try:
            pending = bool(layer.isEditable() and layer.isModified())
        except Exception:
            pending = False
        if not pending:
            return True

        answer = QMessageBox.question(
            self._ui_parent(),
            action_title,
            (
                "The active line layer has unsaved edits.\n"
                "Save edits now before continuing?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return False

        try:
            self.save_trace_layer_edits()
        except Exception:
            return False

        try:
            return not bool(layer.isEditable() and layer.isModified())
        except Exception:
            return True

    def _feature_depth_value(self, layer, feat):
        def _get(field_name):
            idx = layer.fields().indexOf(field_name)
            if idx < 0:
                return None
            val = feat.attribute(idx)
            return None if val in ("", None) else val

        z_value = _get("z_value")
        if z_value is not None:
            try:
                return float(z_value)
            except Exception:
                pass
        d_from = _get("depth_from")
        d_to = _get("depth_to")
        try:
            if d_from is not None and d_to is not None:
                return (float(d_from) + float(d_to)) / 2.0
            if d_from is not None:
                return float(d_from)
            if d_to is not None:
                return float(d_to)
        except Exception:
            return None
        return None

    def _geometry_with_constant_z(self, geometry, z_value):
        if geometry is None or geometry.isEmpty():
            return None
        try:
            z = float(z_value)
        except Exception:
            return None

        if geometry.isMultipart():
            parts = geometry.asMultiPolyline()
            if not parts:
                return None
            part_wkts = []
            for part in parts:
                if len(part) < 2:
                    continue
                coords = ", ".join([f"{pt.x()} {pt.y()} {z}" for pt in part])
                part_wkts.append(f"({coords})")
            if not part_wkts:
                return None
            return QgsGeometry.fromWkt(f"MULTILINESTRING Z ({', '.join(part_wkts)})")

        pts = geometry.asPolyline()
        if len(pts) < 2:
            multi = geometry.asMultiPolyline()
            pts = multi[0] if multi else []
        if len(pts) < 2:
            return None
        coords = ", ".join([f"{pt.x()} {pt.y()} {z}" for pt in pts])
        return QgsGeometry.fromWkt(f"LINESTRING Z ({coords})")

    def _sample_raster_value(self, raster_layer, point_xy, band=1):
        provider = raster_layer.dataProvider()
        if provider is None:
            return None
        try:
            sample = provider.sample(point_xy, band)
            if isinstance(sample, tuple):
                value = sample[0]
                ok = bool(sample[1]) if len(sample) > 1 else value is not None
                if not ok:
                    return None
                return float(value)
            if sample is None:
                return None
            return float(sample)
        except Exception:
            return None

    def _load_dtm_layer_from_file(self):
        raster_path, _ = QFileDialog.getOpenFileName(
            self._ui_parent(),
            "Select DTM Raster",
            "",
            "Raster files (*.tif *.tiff *.img *.asc *.vrt *.bil *.hdr)",
        )
        if not raster_path:
            return None
        layer_name = os.path.splitext(os.path.basename(raster_path))[0]
        raster_layer = QgsRasterLayer(raster_path, layer_name)
        if not raster_layer.isValid():
            QMessageBox.warning(self._ui_parent(), "Orthometric 3D", "Selected DTM raster is not valid.")
            return None
        QgsProject.instance().addMapLayer(raster_layer)
        return raster_layer

    def _geometry_with_dtm_minus_depth(self, geometry, dtm_layer, depth_value):
        if geometry is None or geometry.isEmpty() or dtm_layer is None:
            return None
        try:
            depth = float(depth_value)
        except Exception:
            return None

        def _part_wkt(part):
            if len(part) < 2:
                return None
            coords = []
            for pt in part:
                point_xy = QgsPointXY(pt.x(), pt.y())
                dtm_z = self._sample_raster_value(dtm_layer, point_xy, 1)
                if dtm_z is None:
                    return None
                coords.append(f"{pt.x()} {pt.y()} {dtm_z - depth}")
            if len(coords) < 2:
                return None
            return f"({', '.join(coords)})"

        if geometry.isMultipart():
            parts = geometry.asMultiPolyline()
            part_wkts = [w for w in (_part_wkt(part) for part in parts) if w]
            if not part_wkts:
                return None
            return QgsGeometry.fromWkt(f"MULTILINESTRING Z ({', '.join(part_wkts)})")

        pts = geometry.asPolyline()
        if len(pts) < 2:
            multi = geometry.asMultiPolyline()
            pts = multi[0] if multi else []
        part_wkt = _part_wkt(pts)
        if not part_wkt:
            return None
        return QgsGeometry.fromWkt(f"LINESTRING Z {part_wkt}")

    def _create_3d_output_layer(self, source_layer, output_name):
        crs_authid = source_layer.crs().authid() if source_layer.crs().isValid() else "EPSG:4326"
        out_layer = QgsVectorLayer(f"LineStringZ?crs={crs_authid}", output_name, "memory")
        if not out_layer.isValid():
            return None
        out_provider = out_layer.dataProvider()
        out_provider.addAttributes(list(source_layer.fields()))
        out_layer.updateFields()
        QgsProject.instance().addMapLayer(out_layer, False)
        self._get_or_create_trace_group().addLayer(out_layer)
        return out_layer

    def _feature_z_from_linked_grid(self, source_layer, feat):
        if source_layer is None or feat is None:
            return None
        z_path_idx = source_layer.fields().indexOf("z_grid_path")
        if z_path_idx < 0:
            return None
        z_grid_path = feat.attribute(z_path_idx)
        raster_layer = self._get_cached_raster_layer_by_path(z_grid_path)
        if raster_layer is None:
            return None
        geom = feat.geometry()
        point_xy = self._first_xy_from_geometry(geom)
        if point_xy is None:
            return None
        try:
            if source_layer.crs().isValid() and raster_layer.crs().isValid() and source_layer.crs() != raster_layer.crs():
                tr = QgsCoordinateTransform(source_layer.crs(), raster_layer.crs(), QgsProject.instance())
                transformed = tr.transform(point_xy)
                point_xy = QgsPointXY(transformed.x(), transformed.y())
        except Exception:
            return None
        return self._sample_raster_value(raster_layer, point_xy, 1)

    def _choose_build_3d_mode(self, source_layer, default_mode="constant"):
        choices = self._build3d_mode_choices()
        labels = [c[0] for c in choices]
        default_idx = 0
        for idx, (_, mode_key) in enumerate(choices):
            if mode_key == default_mode:
                default_idx = idx
                break

        chosen_label, ok = QInputDialog.getItem(
            self._ui_parent(),
            "Build 3D",
            "Mode:",
            labels,
            default_idx,
            False,
        )
        if not ok:
            return None, None
        mode = choices[labels.index(chosen_label)][1]

        default_name = self._default_build3d_output_name(source_layer, mode)
        output_name, ok = QInputDialog.getText(
            self._ui_parent(),
            "Build 3D",
            "Output layer name:",
            text=default_name,
        )
        if not ok or not output_name.strip():
            return None, None
        return mode, output_name.strip()

    def _precheck_build_3d(self, source_layer, mode, dtm_layer=None):
        stats = {
            "total": 0,
            "ready": 0,
            "missing_depth": 0,
            "missing_grid": 0,
            "invalid_geom": 0,
            "sample_fail": 0,
            "outside_dtm_extent": 0,
        }
        for feat in source_layer.getFeatures():
            stats["total"] += 1
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                stats["invalid_geom"] += 1
                continue

            if mode == "constant":
                depth_val = self._feature_depth_value(source_layer, feat)
                if depth_val is None:
                    stats["missing_depth"] += 1
                    continue
                if self._geometry_with_constant_z(geom, depth_val) is None:
                    stats["invalid_geom"] += 1
                    continue
                stats["ready"] += 1
                continue

            if mode == "linked_grid":
                z_val = self._feature_z_from_linked_grid(source_layer, feat)
                if z_val is None:
                    stats["missing_grid"] += 1
                    continue
                if self._geometry_with_constant_z(geom, z_val) is None:
                    stats["invalid_geom"] += 1
                    continue
                stats["ready"] += 1
                continue

            if mode == "orthometric":
                depth_val = self._feature_depth_value(source_layer, feat)
                if depth_val is None:
                    stats["missing_depth"] += 1
                    continue
                point_xy = self._first_xy_from_geometry(geom)
                if point_xy is None:
                    stats["invalid_geom"] += 1
                    continue
                if dtm_layer is not None:
                    try:
                        dtm_point = point_xy
                        if (
                            source_layer.crs().isValid()
                            and dtm_layer.crs().isValid()
                            and source_layer.crs() != dtm_layer.crs()
                        ):
                            tr = QgsCoordinateTransform(source_layer.crs(), dtm_layer.crs(), QgsProject.instance())
                            transformed = tr.transform(point_xy)
                            dtm_point = QgsPointXY(transformed.x(), transformed.y())
                        if not dtm_layer.extent().contains(dtm_point):
                            stats["outside_dtm_extent"] += 1
                            continue
                    except Exception:
                        stats["sample_fail"] += 1
                        continue
                if self._geometry_with_dtm_minus_depth(geom, dtm_layer, depth_val) is None:
                    stats["sample_fail"] += 1
                    continue
                stats["ready"] += 1
                continue

        return stats

    def _build_3d_with_mode(self, source_layer, out_layer, mode, dtm_layer=None):
        out_provider = out_layer.dataProvider()
        features_out = []
        skipped = 0
        for feat in source_layer.getFeatures():
            geom_in = feat.geometry()
            if geom_in is None or geom_in.isEmpty():
                skipped += 1
                continue

            z_used = None
            z_grid_path = ""
            geom3d = None

            if mode == "constant":
                z_used = self._feature_depth_value(source_layer, feat)
                if z_used is None:
                    skipped += 1
                    continue
                geom3d = self._geometry_with_constant_z(geom_in, z_used)
            elif mode == "linked_grid":
                z_used = self._feature_z_from_linked_grid(source_layer, feat)
                if z_used is None:
                    skipped += 1
                    continue
                z_path_idx = source_layer.fields().indexOf("z_grid_path")
                if z_path_idx >= 0:
                    z_grid_path = feat.attribute(z_path_idx) or ""
                geom3d = self._geometry_with_constant_z(geom_in, z_used)
            elif mode == "orthometric":
                depth_val = self._feature_depth_value(source_layer, feat)
                if depth_val is None:
                    skipped += 1
                    continue
                geom3d = self._geometry_with_dtm_minus_depth(geom_in, dtm_layer, depth_val)
            else:
                skipped += 1
                continue

            if geom3d is None:
                skipped += 1
                continue

            new_feat = QgsFeature(out_layer.fields())
            new_feat.setAttributes(feat.attributes())
            new_feat.setGeometry(geom3d)

            z_mode_idx = out_layer.fields().indexOf("z_mode")
            if z_mode_idx >= 0:
                mode_label = {
                    "constant": "build3d_constant_depth",
                    "linked_grid": "build3d_linked_grid",
                    "orthometric": "build3d_orthometric",
                }.get(mode, "build3d")
                new_feat.setAttribute(z_mode_idx, mode_label)

            z_source_idx = out_layer.fields().indexOf("z_source")
            if z_source_idx >= 0:
                source_label = {
                    "constant": "depth_range",
                    "linked_grid": "surfer_grid",
                    "orthometric": "dtm_minus_depth",
                }.get(mode, "unknown")
                new_feat.setAttribute(z_source_idx, source_label)

            z_value_idx = out_layer.fields().indexOf("z_value")
            if z_value_idx >= 0 and z_used is not None:
                new_feat.setAttribute(z_value_idx, float(z_used))

            z_grid_idx = out_layer.fields().indexOf("z_grid_path")
            if z_grid_idx >= 0 and z_grid_path:
                new_feat.setAttribute(z_grid_idx, z_grid_path)

            features_out.append(new_feat)

        if features_out:
            out_provider.addFeatures(features_out)
            out_layer.updateExtents()
            out_layer.triggerRepaint()
        return len(features_out), skipped

    def _run_build_3d_workflow(self, default_mode="constant", batch=False):
        if batch:
            source_layers = self._build3d_batch_source_layers()
            if not source_layers:
                QMessageBox.warning(
                    self._ui_parent(),
                    "Build 3D Batch",
                    "No trace-related line layer found in project.",
                )
                return
            pending_layers = []
            for lyr in source_layers:
                try:
                    if lyr.isEditable() and lyr.isModified():
                        pending_layers.append(lyr.name())
                except Exception:
                    continue
            if pending_layers:
                preview = "\n".join(pending_layers[:8])
                if len(pending_layers) > 8:
                    preview += f"\n... and {len(pending_layers) - 8} more."
                QMessageBox.warning(
                    self._ui_parent(),
                    "Build 3D Batch",
                    (
                        "Some layers have unsaved edits.\n"
                        "Save edits before running batch build:\n\n"
                        f"{preview}"
                    ),
                )
                return
            mode = self._choose_build_3d_mode_only(default_mode=default_mode)
            if not mode:
                return
            dtm_layer = None
            if mode == "orthometric":
                dtm_layer = self._choose_dtm_layer()
                if dtm_layer is None:
                    return

            mode_label = {
                "constant": "Constant Z (depth/z_value)",
                "linked_grid": "Linked z-grid",
                "orthometric": "Orthometric (DTM - depth)",
            }.get(mode, mode)

            per_layer = []
            totals = {
                "layers_total": 0,
                "layers_ready": 0,
                "features_total": 0,
                "features_ready": 0,
                "missing_depth": 0,
                "missing_grid": 0,
                "invalid_geom": 0,
                "sample_fail": 0,
                "outside_dtm_extent": 0,
            }
            for lyr in source_layers:
                stats = self._precheck_build_3d(lyr, mode, dtm_layer=dtm_layer)
                per_layer.append((lyr, stats))
                totals["layers_total"] += 1
                if stats.get("ready", 0) > 0:
                    totals["layers_ready"] += 1
                totals["features_total"] += int(stats.get("total", 0))
                totals["features_ready"] += int(stats.get("ready", 0))
                totals["missing_depth"] += int(stats.get("missing_depth", 0))
                totals["missing_grid"] += int(stats.get("missing_grid", 0))
                totals["invalid_geom"] += int(stats.get("invalid_geom", 0))
                totals["sample_fail"] += int(stats.get("sample_fail", 0))
                totals["outside_dtm_extent"] += int(stats.get("outside_dtm_extent", 0))

            if totals["features_ready"] <= 0:
                QMessageBox.warning(
                    self._ui_parent(),
                    "Build 3D Batch",
                    (
                        f"Mode: {mode_label}\n"
                        "No valid feature to convert in selected project layers."
                    ),
                )
                return

            layer_lines = []
            for lyr, st in per_layer[:12]:
                layer_lines.append(
                    f"- {lyr.name()}: ready {st.get('ready', 0)}/{st.get('total', 0)}"
                )
            if len(per_layer) > 12:
                layer_lines.append(f"... and {len(per_layer) - 12} more layers.")

            details = (
                f"Mode: {mode_label}\n"
                f"Layers (ready/total): {totals['layers_ready']}/{totals['layers_total']}\n"
                f"Features (ready/total): {totals['features_ready']}/{totals['features_total']}\n"
                f"Will be skipped: {totals['features_total'] - totals['features_ready']}\n\n"
                f"Details - missing depth: {totals['missing_depth']}, "
                f"missing grid: {totals['missing_grid']}, "
                f"invalid geom: {totals['invalid_geom']}, sample fail: {totals['sample_fail']}"
            )
            if mode == "orthometric":
                details += f", out of DTM extent: {totals['outside_dtm_extent']}"
            details += "\n\nLayers preview:\n" + "\n".join(layer_lines) + "\n\nContinue?"

            proceed = QMessageBox.question(
                self._ui_parent(),
                "Build 3D Batch - Preview",
                details,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if proceed != QMessageBox.Yes:
                return

            built_layers = []
            created_total = 0
            skipped_total = 0
            for lyr, st in per_layer:
                if st.get("ready", 0) <= 0:
                    continue
                out_name = self._unique_map_layer_name(self._default_build3d_output_name(lyr, mode))
                out_layer = self._create_3d_output_layer(lyr, out_name)
                if out_layer is None:
                    continue
                created, skipped = self._build_3d_with_mode(lyr, out_layer, mode, dtm_layer=dtm_layer)
                if created <= 0:
                    continue
                created_total += int(created)
                skipped_total += int(skipped)
                built_layers.append(out_layer)

            if not built_layers:
                QMessageBox.warning(
                    self._ui_parent(),
                    "Build 3D Batch",
                    "Batch run completed, but no 3D output layer was created.",
                )
                return

            self.iface.setActiveLayer(built_layers[0])
            self._notify_info(
                (
                    f"3D batch completed ({mode_label}): "
                    f"layers created: {len(built_layers)}, features created: {created_total}, skipped: {skipped_total}."
                ),
                duration=8,
            )
            return

        source_layer = self._current_trace_layer(prefer_active=True, require_trace=False)
        if source_layer is None:
            source_layer = self._select_line_layer_dialog(require_trace=False)
        if source_layer is None:
            return
        if not self._is_line_layer(source_layer):
            QMessageBox.warning(self._ui_parent(), "Build 3D", "Active layer is not a line layer.")
            return
        if not self._ensure_saved_before_next_step(source_layer, "Build 3D"):
            self._notify_info("Build 3D cancelled: save edits first.", duration=5)
            return

        mode, output_name = self._choose_build_3d_mode(source_layer, default_mode=default_mode)
        if not mode or not output_name:
            return
        dtm_layer = None
        if mode == "orthometric":
            dtm_layer = self._choose_dtm_layer()
            if dtm_layer is None:
                return

        preview = self._precheck_build_3d(source_layer, mode, dtm_layer=dtm_layer)
        if preview["ready"] <= 0:
            extra = ""
            if mode == "orthometric":
                extra = f", out of DTM extent: {preview.get('outside_dtm_extent', 0)}"
            QMessageBox.warning(
                self._ui_parent(),
                "Build 3D",
                (
                    "No valid feature to convert with selected mode.\n"
                    f"Total: {preview['total']}, missing depth: {preview['missing_depth']}, "
                    f"missing grid: {preview['missing_grid']}, invalid geom: {preview['invalid_geom']}, "
                    f"sample fail: {preview['sample_fail']}{extra}."
                ),
            )
            return

        mode_label = {
            "constant": "Constant Z (depth/z_value)",
            "linked_grid": "Linked z-grid",
            "orthometric": "Orthometric (DTM - depth)",
        }.get(mode, mode)
        extra = ""
        if mode == "orthometric":
            extra = f", out of DTM extent: {preview.get('outside_dtm_extent', 0)}"
        proceed = QMessageBox.question(
            self._ui_parent(),
            "Build 3D - Preview",
            (
                f"Mode: {mode_label}\n"
                f"Total features: {preview['total']}\n"
                f"Ready: {preview['ready']}\n"
                f"Will be skipped: {preview['total'] - preview['ready']}\n\n"
                f"Details - missing depth: {preview['missing_depth']}, "
                f"missing grid: {preview['missing_grid']}, "
                f"invalid geom: {preview['invalid_geom']}, sample fail: {preview['sample_fail']}{extra}.\n\n"
                "Continue?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if proceed != QMessageBox.Yes:
            return

        out_layer = self._create_3d_output_layer(source_layer, output_name)
        if out_layer is None:
            QMessageBox.critical(self._ui_parent(), "Build 3D", "Unable to create 3D output layer.")
            return

        created, skipped = self._build_3d_with_mode(source_layer, out_layer, mode, dtm_layer=dtm_layer)
        self._notify_info(
            f"3D build completed ({mode_label}): {created} feature(s), skipped: {skipped}.",
            duration=7,
        )
        self.iface.setActiveLayer(out_layer)

    def build_trace_3d_from_depth(self, checked=False):
        self._run_build_3d_workflow(default_mode="constant")

    def build_trace_3d_batch(self, checked=False):
        self._run_build_3d_workflow(default_mode="constant", batch=True)

    def _choose_dtm_layer(self):
        rasters = [
            lyr for lyr in QgsProject.instance().mapLayers().values()
            if isinstance(lyr, QgsRasterLayer) and lyr.isValid()
        ]
        if not rasters:
            answer = QMessageBox.question(
                self._ui_parent(),
                "Orthometric 3D",
                "No DTM raster loaded. Load one from file now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return None
            return self._load_dtm_layer_from_file()

        labels = [f"{lyr.name()} [{lyr.id()}]" for lyr in rasters] + ["Load DTM from file..."]
        label, ok = QInputDialog.getItem(
            self._ui_parent(),
            "Orthometric 3D",
            "Select DTM raster layer:",
            labels,
            0,
            False,
        )
        if not ok:
            return None
        if label == "Load DTM from file...":
            return self._load_dtm_layer_from_file()
        return rasters[labels.index(label)]

    def build_trace_3d_orthometric(self, checked=False):
        self._run_build_3d_workflow(default_mode="orthometric")

    def export_active_trace_layer(self, checked=False):
        layer = self._current_trace_layer(prefer_active=True, require_trace=False)
        if layer is None:
            layer = self._select_line_layer_dialog(require_trace=False)
        if layer is None:
            return
        if not self._ensure_saved_before_next_step(layer, "Export Line Layer"):
            self._notify_info("Export cancelled: save edits first.", duration=5)
            return

        output_path, selected_filter = QFileDialog.getSaveFileName(
            self._ui_parent(),
            "Export Line Layer",
            "",
            "GeoPackage (*.gpkg);;ESRI Shapefile (*.shp)",
        )
        if not output_path:
            return

        lower_path = output_path.lower()
        driver = "GPKG"
        layer_name = layer.name()
        if "shp" in (selected_filter or "").lower() or lower_path.endswith(".shp"):
            if not lower_path.endswith(".shp"):
                output_path += ".shp"
            driver = "ESRI Shapefile"
        else:
            if not lower_path.endswith(".gpkg"):
                output_path += ".gpkg"
            driver = "GPKG"

        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = driver
        opts.fileEncoding = "UTF-8"
        opts.layerName = layer_name
        opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
        transform_context = QgsProject.instance().transformContext()
        result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, output_path, transform_context, opts)
        err_code = result[0] if isinstance(result, tuple) else result
        err_msg = result[1] if isinstance(result, tuple) and len(result) > 1 else ""
        if err_code != QgsVectorFileWriter.NoError:
            QMessageBox.critical(self._ui_parent(), "Export Line Layer", f"Export failed: {err_msg or err_code}")
            return

        if driver == "GPKG":
            exported = QgsVectorLayer(f"{output_path}|layername={layer_name}", layer_name, "ogr")
        else:
            exported = QgsVectorLayer(output_path, layer_name, "ogr")
        if exported.isValid():
            QgsProject.instance().addMapLayer(exported)
        self._notify_info(f"Layer exported: {output_path}", duration=7)
