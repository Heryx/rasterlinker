import os
import re
import time
import tempfile

from qgis.PyQt.QtCore import Qt, QVariant, QSizeF, QSize
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPdfWriter
from qgis.PyQt.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QWidget, QHBoxLayout, QLabel, QPushButton
from qgis.core import (
    QgsContrastEnhancement,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    Qgis,
    QgsLayoutExporter,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsPrintLayout,
    QgsProject,
    QgsRasterBandStats,
    QgsRasterLayer,
    QgsMessageLog,
    QgsVectorLayer,
    QgsRectangle,
    QgsFillSymbol,
    QgsSingleSymbolRenderer,
    QgsUnitTypes,
    QgsWkbTypes,
)

from .group_import_dialog import GroupImportDialog
from .project_catalog import load_catalog, update_raster_group
from .layer_property_utils import get_layer_property, set_layer_property


class CatalogToolsMixin:
    def _safe_float(self, value):
        if value in (None, ""):
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _format_depth_label(self, depth_from, depth_to, unit="m"):
        d0 = self._safe_float(depth_from)
        d1 = self._safe_float(depth_to)
        unit_txt = (str(unit or "m").strip() or "m")
        if d0 is None and d1 is None:
            return ""
        if d0 is None:
            return f"{d1:g} {unit_txt}"
        if d1 is None:
            return f"{d0:g} {unit_txt}"
        lo, hi = (d0, d1) if d0 <= d1 else (d1, d0)
        if abs(hi - lo) <= 1e-12:
            return f"{lo:g} {unit_txt}"
        return f"{lo:g}-{hi:g} {unit_txt}"

    def _atlas_safe_token(self, text, default_token="item"):
        raw = str(text or "").strip()
        if not raw:
            return default_token
        token = re.sub(r"[^A-Za-z0-9_\-]+", "_", raw).strip("_")
        return token or default_token

    def _coverage_unique_layer_name(self, base_name):
        base = str(base_name or "").strip() or "AtlasCoverage"
        names = {str(lyr.name() or "").strip() for lyr in QgsProject.instance().mapLayers().values()}
        if base not in names:
            return base
        idx = 2
        while True:
            candidate = f"{base}_{idx:03d}"
            if candidate not in names:
                return candidate
            idx += 1

    def _coverage_layer_epoch(self, layer):
        try:
            return float(get_layer_property(layer, "atlas_coverage_epoch", default=0) or 0)
        except Exception:
            return 0.0

    def _apply_atlas_coverage_style(self, layer):
        if layer is None or not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            return
        try:
            symbol = QgsFillSymbol.createSimple(
                {
                    "color": "0,0,0,0",
                    "outline_color": "210,55,45,255",
                    "outline_style": "dash",
                    "outline_width": "0.66",
                }
            )
            if symbol is None:
                return
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            layer.setOpacity(1.0)
            layer.triggerRepaint()
            return
        except Exception:
            pass
        try:
            renderer = layer.renderer()
            symbol = renderer.symbol() if renderer is not None else None
            if symbol is None:
                return
            symbol.setColor(QColor(0, 0, 0, 0))
            for sym_layer in symbol.symbolLayers():
                try:
                    sym_layer.setStrokeColor(QColor(210, 55, 45))
                    sym_layer.setStrokeStyle(Qt.DashLine)
                    sym_layer.setStrokeWidth(0.66)
                    if hasattr(sym_layer, "setFillColor"):
                        sym_layer.setFillColor(QColor(0, 0, 0, 0))
                except Exception:
                    continue
            layer.triggerRepaint()
        except Exception:
            return

    def _get_or_create_atlas_coverage_group(self):
        plugin_root = self._get_plugin_root_group()
        target_name = "Atlas Coverage"
        group = next(
            (
                g for g in plugin_root.children()
                if isinstance(g, QgsLayerTreeGroup) and str(g.name() or "").strip() == target_name
            ),
            None,
        )
        if group is None:
            group = plugin_root.addGroup(target_name)
        return group

    def _find_atlas_coverage_layers(self, group_id=None, group_name=None):
        gid = str(group_id or "").strip()
        gname = str(group_name or "").strip().lower()
        matches = []
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
                continue
            try:
                if lyr.geometryType() != QgsWkbTypes.PolygonGeometry:
                    continue
            except Exception:
                continue
            source_kind = str(get_layer_property(lyr, "source_kind", default="") or "").strip().lower()
            atlas_gid = str(get_layer_property(lyr, "atlas_group_id", default="") or "").strip()
            atlas_gname = str(get_layer_property(lyr, "atlas_group_name", default="") or "").strip().lower()
            if source_kind != "atlas_coverage":
                continue
            if gid and atlas_gid == gid:
                matches.append(lyr)
                continue
            if gname and atlas_gname and atlas_gname == gname:
                matches.append(lyr)
                continue
            if gname and not atlas_gname:
                token = self._atlas_safe_token(gname, default_token="")
                if token and str(lyr.name() or "").lower().startswith(f"atlascoverage_{token}"):
                    matches.append(lyr)
        matches.sort(key=self._coverage_layer_epoch, reverse=True)
        return matches

    def _find_atlas_coverage_layer(self, group_id, group_name=None):
        matches = self._find_atlas_coverage_layers(group_id=group_id, group_name=group_name)
        if matches:
            return matches[0]
        return None

    def _coverage_polygon_for_raster(self, raster_path):
        info = {
            "geometry": None,
            "raster_exists": 0,
            "raster_valid": 0,
            "raster_crs": "",
            "crs_mismatch": 0,
        }
        path = str(raster_path or "").strip()
        if not path or not os.path.exists(path):
            return info
        info["raster_exists"] = 1
        lyr = QgsRasterLayer(path, "__atlas_cov__", "gdal")
        if not lyr.isValid():
            return info
        info["raster_valid"] = 1
        rect = lyr.extent()
        if rect is None or rect.isEmpty():
            return info
        src = lyr.crs()
        dst = QgsProject.instance().crs()
        if src.isValid():
            info["raster_crs"] = src.authid() or ""
        if src.isValid() and dst.isValid() and src.authid() != dst.authid():
            info["crs_mismatch"] = 1
            try:
                tr = QgsCoordinateTransform(src, dst, QgsProject.instance())
                rect = tr.transformBoundingBox(rect)
            except Exception:
                return info
        info["geometry"] = QgsGeometry.fromRect(rect)
        return info

    def _build_atlas_coverage_rows(self, project_root, group):
        data = load_catalog(project_root)
        group_id = str(group.get("id") or "").strip()
        group_name = str(group.get("name") or "Group").strip() or "Group"
        ts_by_id = {str(t.get("id") or "").strip(): t for t in data.get("timeslices", []) if isinstance(t, dict)}
        rows = []

        for tid in group.get("timeslice_ids", []) or []:
            ts = ts_by_id.get(str(tid or "").strip())
            if not ts:
                continue
            ts_id = str(ts.get("id") or "").strip()
            ts_name = str(ts.get("normalized_name") or ts.get("name") or ts_id).strip() or ts_id
            raster_path = str(ts.get("project_path") or "").strip()
            depth_from = self._safe_float(ts.get("depth_from"))
            depth_to = self._safe_float(ts.get("depth_to"))
            depth_label = self._format_depth_label(depth_from, depth_to, ts.get("unit") or "m")
            cov = self._coverage_polygon_for_raster(raster_path)
            geom = cov.get("geometry")
            if depth_from is None and depth_to is None:
                sort_tuple = (1e12, 1e12, ts_name.lower(), ts_id)
            else:
                lo = depth_from if depth_from is not None else depth_to
                hi = depth_to if depth_to is not None else depth_from
                if lo is None:
                    lo = 1e12
                if hi is None:
                    hi = lo
                if hi < lo:
                    lo, hi = hi, lo
                sort_tuple = (float(lo), float(hi), ts_name.lower(), ts_id)
            rows.append(
                {
                    "geometry": geom,
                    "_sort": sort_tuple,
                    "coverage_id": f"{group_id}::{ts_id}",
                    "ts_id": ts_id,
                    "ts_name": ts_name,
                    "group_id": group_id,
                    "group_name": group_name,
                    "depth_from": depth_from,
                    "depth_to": depth_to,
                    "depth_label": depth_label,
                    "raster_path": raster_path,
                    "missing_depth": 1 if (depth_from is None and depth_to is None) else 0,
                    "has_geometry": 1 if geom is not None else 0,
                    "raster_exists": int(cov.get("raster_exists") or 0),
                    "raster_valid": int(cov.get("raster_valid") or 0),
                    "raster_crs": str(cov.get("raster_crs") or ""),
                    "crs_mismatch": int(cov.get("crs_mismatch") or 0),
                }
            )

        rows.sort(key=lambda r: r.get("_sort"))
        for idx, row in enumerate(rows, start=1):
            row["sort_key"] = idx
        return rows

    def _ensure_atlas_coverage_layer(self, group, rows, create_new=False, page_opts=None):
        project = QgsProject.instance()
        project_crs = project.crs().authid() if project.crs().isValid() else "EPSG:4326"
        page_tag = ""
        if isinstance(page_opts, dict):
            p = self._atlas_safe_token(page_opts.get("page_token"), default_token="")
            o = self._atlas_safe_token(page_opts.get("orientation_token"), default_token="")
            d = self._atlas_safe_token(f"{int(page_opts.get('dpi') or 300)}dpi", default_token="")
            bits = [x for x in (p, o, d) if x]
            if bits:
                page_tag = "_" + "_".join(bits)
        base_layer_name = (
            f"AtlasCoverage_{self._atlas_safe_token(group.get('name') or 'Group', default_token='group')}{page_tag}"
        )
        layer_name = self._coverage_unique_layer_name(base_layer_name) if create_new else base_layer_name
        existing = None
        if not create_new:
            existing = self._find_atlas_coverage_layer(group.get("id"), group.get("name"))

        required_fields = (
            ("coverage_id", QVariant.String, 256),
            ("ts_id", QVariant.String, 256),
            ("ts_name", QVariant.String, 512),
            ("group_id", QVariant.String, 256),
            ("group_name", QVariant.String, 256),
            ("depth_from", QVariant.Double, 0),
            ("depth_to", QVariant.Double, 0),
            ("depth_label", QVariant.String, 64),
            ("sort_key", QVariant.Int, 0),
            ("raster_path", QVariant.String, 1024),
            ("missing_depth", QVariant.Int, 0),
            ("has_geometry", QVariant.Int, 0),
            ("raster_exists", QVariant.Int, 0),
            ("raster_valid", QVariant.Int, 0),
            ("raster_crs", QVariant.String, 64),
            ("crs_mismatch", QVariant.Int, 0),
        )

        layer = existing
        if layer is None:
            fields_uri = []
            for field_name, field_type, length in required_fields:
                if field_type == QVariant.Double:
                    fields_uri.append(f"field={field_name}:double")
                elif field_type == QVariant.Int:
                    fields_uri.append(f"field={field_name}:integer")
                else:
                    if length and int(length) > 0:
                        fields_uri.append(f"field={field_name}:string({int(length)})")
                    else:
                        fields_uri.append(f"field={field_name}:string")
            uri = f"Polygon?crs={project_crs}&" + "&".join(fields_uri)
            layer = QgsVectorLayer(uri, layer_name, "memory")
            if not layer.isValid():
                return None, 0, 0
            set_layer_property(layer, "source_kind", "atlas_coverage")
            set_layer_property(layer, "atlas_group_id", str(group.get("id") or ""))
            set_layer_property(layer, "atlas_group_name", str(group.get("name") or ""))
            set_layer_property(layer, "atlas_coverage_epoch", f"{time.time():.6f}")
            project.addMapLayer(layer, False)
            self._get_or_create_atlas_coverage_group().addLayer(layer)
        else:
            try:
                if layer.name() != layer_name:
                    layer.setName(layer_name)
            except Exception:
                pass
            set_layer_property(layer, "source_kind", "atlas_coverage")
            set_layer_property(layer, "atlas_group_id", str(group.get("id") or ""))
            set_layer_property(layer, "atlas_group_name", str(group.get("name") or ""))
            set_layer_property(layer, "atlas_coverage_epoch", f"{time.time():.6f}")
            try:
                atlas_group = self._get_or_create_atlas_coverage_group()
                root = QgsProject.instance().layerTreeRoot()
                node = root.findLayer(layer.id()) if root is not None else None
                if node is not None and node.parent() is not atlas_group:
                    parent = node.parent()
                    clone = node.clone()
                    atlas_group.addChildNode(clone)
                    if parent is not None:
                        parent.removeChildNode(node)
            except Exception:
                pass
            provider = layer.dataProvider()
            if provider is not None:
                missing = []
                existing_names = {f.name() for f in layer.fields()}
                for field_name, field_type, length in required_fields:
                    if field_name in existing_names:
                        continue
                    fld = QgsField(field_name, field_type)
                    if field_type == QVariant.String and length and int(length) > 0:
                        fld.setLength(int(length))
                    missing.append(fld)
                if missing:
                    try:
                        provider.addAttributes(missing)
                        layer.updateFields()
                    except Exception:
                        pass

        frame_geom = self._atlas_build_frame_geometry(rows, page_opts)
        missing_depth_count = len([r for r in rows if int(r.get("missing_depth") or 0) == 1])
        crs_mismatch_count = len([r for r in rows if int(r.get("crs_mismatch") or 0) == 1])
        has_any_raster = 1 if any(int(r.get("raster_exists") or 0) == 1 for r in rows) else 0
        has_any_valid_raster = 1 if any(int(r.get("raster_valid") or 0) == 1 for r in rows) else 0

        started_here = False
        try:
            if not layer.isEditable():
                started_here = bool(layer.startEditing())
            ids = [f.id() for f in layer.getFeatures()]
            if ids:
                layer.deleteFeatures(ids)
            fields = layer.fields()
            feat = QgsFeature(fields)
            if frame_geom is not None and not frame_geom.isEmpty():
                feat.setGeometry(frame_geom)
            feat.setAttribute("coverage_id", f"{str(group.get('id') or '')}::coverage")
            feat.setAttribute("ts_id", "")
            feat.setAttribute("ts_name", f"{str(group.get('name') or 'Group')} coverage")
            feat.setAttribute("group_id", str(group.get("id") or ""))
            feat.setAttribute("group_name", str(group.get("name") or ""))
            feat.setAttribute("depth_from", None)
            feat.setAttribute("depth_to", None)
            feat.setAttribute("depth_label", f"{len(rows)} time-slices")
            feat.setAttribute("sort_key", 1)
            feat.setAttribute("raster_path", "")
            feat.setAttribute("missing_depth", int(missing_depth_count))
            feat.setAttribute("has_geometry", 1 if (frame_geom is not None and not frame_geom.isEmpty()) else 0)
            feat.setAttribute("raster_exists", int(has_any_raster))
            feat.setAttribute("raster_valid", int(has_any_valid_raster))
            feat.setAttribute("raster_crs", QgsProject.instance().crs().authid() if QgsProject.instance().crs().isValid() else "")
            feat.setAttribute("crs_mismatch", int(crs_mismatch_count))
            layer.addFeature(feat)
            if started_here:
                layer.commitChanges()
            layer.triggerRepaint()
        except Exception:
            try:
                if started_here:
                    layer.rollBack()
            except Exception:
                pass

        self._apply_atlas_coverage_style(layer)
        with_geom = 1 if (frame_geom is not None and not frame_geom.isEmpty()) else 0
        return layer, 1, with_geom

    def build_atlas_coverage_for_active_group(self, create_new=False, rows=None, page_opts=None):
        project_root, group = self._active_group_record()
        if not project_root or group is None:
            QMessageBox.warning(self.dlg, "Atlas Coverage", "Select one active group first.")
            return None, []
        base_rows = rows if rows is not None else self._build_atlas_coverage_rows(project_root, group)
        layer, total, with_geom = self._ensure_atlas_coverage_layer(
            group,
            base_rows,
            create_new=create_new,
            page_opts=page_opts,
        )
        if layer is None:
            QMessageBox.warning(self.dlg, "Atlas Coverage", "Unable to create/update coverage layer.")
            return None, []
        self.iface.messageBar().pushInfo(
            "GeoSurvey Studio",
            f"Atlas coverage refreshed for '{group.get('name')}'. Features: {total}, with geometry: {with_geom}.",
        )
        return layer, base_rows

    def _normalized_data_path(self, src):
        p = str(src or "").strip()
        if not p:
            return ""
        p = p.split("|", 1)[0].strip()
        if not p:
            return ""
        try:
            return os.path.normcase(os.path.abspath(p))
        except Exception:
            return p

    def _atlas_export_validation_report(self, group_name, rows):
        rows = list(rows or [])
        if not rows:
            QMessageBox.warning(self.dlg, "Atlas Export Validation", "Coverage is empty for selected group.")
            return False
        missing_depth = [r for r in rows if int(r.get("missing_depth") or 0) == 1]
        missing_file = [r for r in rows if int(r.get("raster_exists") or 0) == 0]
        invalid_raster = [r for r in rows if int(r.get("raster_exists") or 0) == 1 and int(r.get("raster_valid") or 0) == 0]
        no_geom = [r for r in rows if int(r.get("has_geometry") or 0) == 0]
        crs_mismatch = [r for r in rows if int(r.get("crs_mismatch") or 0) == 1]

        if not (missing_depth or missing_file or invalid_raster or no_geom or crs_mismatch):
            return True

        def _preview(items, key="ts_name", limit=5):
            vals = [str(it.get(key) or it.get("ts_id") or "?") for it in items[:limit]]
            if len(items) > limit:
                vals.append(f"... +{len(items) - limit} more")
            return ", ".join(vals)

        lines = [
            f"Group: {group_name}",
            f"Coverage features: {len(rows)}",
            "",
            "Validation summary:",
            f"- Missing depth metadata: {len(missing_depth)}",
            f"- Missing raster path/file: {len(missing_file)}",
            f"- Invalid raster layers: {len(invalid_raster)}",
            f"- No valid geometry for atlas page: {len(no_geom)}",
            f"- CRS mismatch vs project: {len(crs_mismatch)}",
        ]

        if missing_file:
            lines.append(f"\nMissing file examples: {_preview(missing_file)}")
        if no_geom:
            lines.append(f"\nNo-geometry examples: {_preview(no_geom)}")
        if crs_mismatch:
            lines.append(f"\nCRS mismatch examples: {_preview(crs_mismatch)}")

        lines.append("\nContinue anyway?")
        answer = QMessageBox.question(
            self.dlg,
            "Atlas Export Validation",
            "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return answer == QMessageBox.Yes

    def _atlas_theme_visible_layer_ids(self, theme_name):
        project = QgsProject.instance()
        coll = project.mapThemeCollection() if project is not None else None
        if coll is None:
            return []
        try:
            ids = list(coll.mapThemeVisibleLayerIds(theme_name))
            if ids:
                return [str(i) for i in ids if str(i).strip()]
        except Exception:
            pass
        try:
            layers = list(coll.mapThemeVisibleLayers(theme_name))
            out = []
            for lyr in layers:
                try:
                    lid = str(lyr.id() or "").strip()
                    if lid:
                        out.append(lid)
                except Exception:
                    continue
            return out
        except Exception:
            return []

    def _atlas_layers_from_ids(self, layer_ids):
        project = QgsProject.instance()
        result = []
        seen = set()
        for lid in layer_ids or []:
            key = str(lid or "").strip()
            if not key or key in seen:
                continue
            lyr = project.mapLayer(key) if project is not None else None
            if lyr is None:
                continue
            seen.add(key)
            result.append(lyr)
        return result

    def _atlas_export_map_context(self):
        map_content_combo = getattr(self, "export_map_content_combo", None)
        theme_combo = getattr(self, "export_theme_combo", None)
        if map_content_combo is not None:
            try:
                if hasattr(self, "_refresh_export_theme_combo"):
                    self._refresh_export_theme_combo()
            except Exception:
                pass
            mode_text = str(map_content_combo.currentText() or "").strip().lower()
            canvas = self.iface.mapCanvas() if self.iface is not None else None
            canvas_extent = QgsRectangle(canvas.extent()) if canvas is not None else None
            if mode_text.startswith("raster only"):
                return {"mode": "raster_only", "layer_ids": [], "extent": None, "theme_name": ""}
            if mode_text.startswith("current canvas"):
                ids = []
                try:
                    ids = [str(lyr.id() or "").strip() for lyr in (canvas.layers() or []) if lyr is not None]
                    ids = [i for i in ids if i]
                except Exception:
                    ids = []
                return {"mode": "canvas_view", "layer_ids": ids, "extent": canvas_extent, "theme_name": ""}
            theme_name = str(theme_combo.currentText() or "").strip() if theme_combo is not None else ""
            if not theme_name or theme_name.startswith("<"):
                QMessageBox.warning(
                    self.dlg,
                    "Export Group Layout",
                    "No map theme selected. Choose Raster only / Current canvas view, or select a valid map theme.",
                )
                return None
            ids = self._atlas_theme_visible_layer_ids(theme_name)
            return {"mode": "map_theme", "layer_ids": ids, "extent": canvas_extent, "theme_name": theme_name}

        options = [
            "Raster only (time-slice layer only)",
            "Current canvas view (visible layers + current extent)",
            "Map theme (visible layers from selected theme + current extent)",
        ]
        while True:
            mode_label, ok_mode = QInputDialog.getItem(
                self.dlg,
                "Export Group Layout",
                "Map content:",
                options,
                0,
                False,
            )
            if not ok_mode:
                return None

            canvas = self.iface.mapCanvas() if self.iface is not None else None
            canvas_extent = QgsRectangle(canvas.extent()) if canvas is not None else None
            if mode_label.startswith("Raster only"):
                return {
                    "mode": "raster_only",
                    "layer_ids": [],
                    "extent": None,
                    "theme_name": "",
                }

            if mode_label.startswith("Current canvas view"):
                ids = []
                try:
                    ids = [str(lyr.id() or "").strip() for lyr in (canvas.layers() or []) if lyr is not None]
                    ids = [i for i in ids if i]
                except Exception:
                    ids = []
                return {
                    "mode": "canvas_view",
                    "layer_ids": ids,
                    "extent": canvas_extent,
                    "theme_name": "",
                }

            project = QgsProject.instance()
            coll = project.mapThemeCollection() if project is not None else None
            names = []
            try:
                names = list(coll.mapThemes()) if coll is not None else []
            except Exception:
                names = []
            names = [str(n).strip() for n in names if str(n).strip()]
            if not names:
                QMessageBox.warning(
                    self.dlg,
                    "Export Group Layout",
                    "No map themes found in current project. Create a map theme or choose another map content mode.",
                )
                continue
            theme_name, ok_theme = QInputDialog.getItem(
                self.dlg,
                "Export Group Layout",
                "Map theme:",
                names,
                0,
                False,
            )
            if not ok_theme:
                continue
            ids = self._atlas_theme_visible_layer_ids(theme_name)
            return {
                "mode": "map_theme",
                "layer_ids": ids,
                "extent": canvas_extent,
                "theme_name": str(theme_name or "").strip(),
            }

    def _atlas_export_page_settings(self):
        page_combo = getattr(self, "export_page_size_combo", None)
        orient_combo = getattr(self, "export_orientation_combo", None)
        dpi_combo = getattr(self, "export_dpi_combo", None)
        scale_spin = getattr(self, "export_scale_spin", None)
        custom_unit_combo = getattr(self, "export_custom_unit_combo", None)
        custom_w_spin = getattr(self, "export_custom_w_spin", None)
        custom_h_spin = getattr(self, "export_custom_h_spin", None)
        if page_combo is not None and orient_combo is not None and dpi_combo is not None and scale_spin is not None:
            page_label_norm = str(page_combo.currentText() or "").strip().upper()
            orientation_label = str(orient_combo.currentText() or "Landscape").strip()
            dpi_label = str(dpi_combo.currentText() or "300").strip()
            unit_label = str(custom_unit_combo.currentText() or "cm").strip() if custom_unit_combo is not None else "cm"
            width_val = float(custom_w_spin.value()) if custom_w_spin is not None else 21.0
            height_val = float(custom_h_spin.value()) if custom_h_spin is not None else 29.7
            scale_den = float(scale_spin.value())
            if scale_den <= 0:
                scale_den = 1000.0
            page_mm = {
                "A6": (105.0, 148.0),
                "A5": (148.0, 210.0),
                "A4": (210.0, 297.0),
                "A3": (297.0, 420.0),
                "A2": (420.0, 594.0),
                "A1": (594.0, 841.0),
            }
            if page_label_norm == "CUSTOM":
                if str(unit_label).lower() == "inch":
                    base_w = float(width_val) * 25.4
                    base_h = float(height_val) * 25.4
                    custom_tag = f"{float(width_val):g}in_x_{float(height_val):g}in"
                else:
                    base_w = float(width_val) * 10.0
                    base_h = float(height_val) * 10.0
                    custom_tag = f"{float(width_val):g}cm_x_{float(height_val):g}cm"
            else:
                base_w, base_h = page_mm.get(page_label_norm, (210.0, 297.0))
                custom_tag = ""
            if str(orientation_label).lower().startswith("land"):
                width_mm, height_mm = max(base_w, base_h), min(base_w, base_h)
            else:
                width_mm, height_mm = min(base_w, base_h), max(base_w, base_h)
            try:
                dpi_val = int(dpi_label)
            except Exception:
                dpi_val = 300
            page_token = page_label_norm if page_label_norm != "CUSTOM" else f"CUSTOM_{custom_tag}"
            orientation_token = "landscape" if str(orientation_label).lower().startswith("land") else "portrait"
            out = {
                "page_size_label": page_label_norm,
                "orientation_label": str(orientation_label),
                "page_token": page_token,
                "orientation_token": orientation_token,
                "width_mm": float(width_mm),
                "height_mm": float(height_mm),
                "dpi": int(dpi_val),
                "scale_denominator": float(scale_den),
            }
            out.update(self._atlas_layout_metrics(out))
            return out

        page_label, ok_page = QInputDialog.getItem(
            self.dlg,
            "Export Group Layout",
            "Page size:",
            ["A6", "A5", "A4", "A3", "A2", "A1", "Custom"],
            0,
            False,
        )
        if not ok_page:
            return None

        orientation_label, ok_orient = QInputDialog.getItem(
            self.dlg,
            "Export Group Layout",
            "Orientation:",
            ["Landscape", "Portrait"],
            0,
            False,
        )
        if not ok_orient:
            return None

        dpi_label, ok_dpi = QInputDialog.getItem(
            self.dlg,
            "Export Group Layout",
            "Resolution (DPI):",
            ["150", "300", "600"],
            1,
            False,
        )
        if not ok_dpi:
            return None

        page_label_norm = str(page_label or "").strip().upper()
        page_mm = {
            "A6": (105.0, 148.0),
            "A5": (148.0, 210.0),
            "A4": (210.0, 297.0),
            "A3": (297.0, 420.0),
            "A2": (420.0, 594.0),
            "A1": (594.0, 841.0),
        }
        if page_label_norm == "CUSTOM":
            unit_label, ok_unit = QInputDialog.getItem(
                self.dlg,
                "Export Group Layout",
                "Custom page unit:",
                ["cm", "inch"],
                0,
                False,
            )
            if not ok_unit:
                return None
            width_val, ok_w = QInputDialog.getDouble(
                self.dlg,
                "Export Group Layout",
                f"Custom page width ({unit_label}):",
                21.0 if str(unit_label) == "cm" else 8.27,
                0.1,
                5000.0,
                2,
            )
            if not ok_w:
                return None
            height_val, ok_h = QInputDialog.getDouble(
                self.dlg,
                "Export Group Layout",
                f"Custom page height ({unit_label}):",
                29.7 if str(unit_label) == "cm" else 11.69,
                0.1,
                5000.0,
                2,
            )
            if not ok_h:
                return None
            if str(unit_label).lower() == "inch":
                base_w = float(width_val) * 25.4
                base_h = float(height_val) * 25.4
                custom_tag = f"{float(width_val):g}in_x_{float(height_val):g}in"
            else:
                base_w = float(width_val) * 10.0
                base_h = float(height_val) * 10.0
                custom_tag = f"{float(width_val):g}cm_x_{float(height_val):g}cm"
        else:
            base_w, base_h = page_mm.get(page_label_norm, (210.0, 297.0))
            custom_tag = ""

        if str(orientation_label).lower().startswith("land"):
            width_mm, height_mm = max(base_w, base_h), min(base_w, base_h)
        else:
            width_mm, height_mm = min(base_w, base_h), max(base_w, base_h)

        try:
            dpi_val = int(str(dpi_label).strip())
        except Exception:
            dpi_val = 300

        default_scale = 1000.0
        try:
            canvas = self.iface.mapCanvas() if self.iface is not None else None
            if canvas is not None:
                default_scale = float(canvas.scale())
        except Exception:
            default_scale = 1000.0
        if default_scale <= 0:
            default_scale = 1000.0
        scale_den, ok_scale = QInputDialog.getDouble(
            self.dlg,
            "Export Group Layout",
            "Scale denominator (1 : n):",
            float(default_scale),
            1.0,
            1e9,
            2,
        )
        if not ok_scale:
            return None

        page_token = page_label_norm if page_label_norm != "CUSTOM" else f"CUSTOM_{custom_tag}"
        orientation_token = "landscape" if str(orientation_label).lower().startswith("land") else "portrait"
        out = {
            "page_size_label": page_label_norm,
            "orientation_label": str(orientation_label),
            "page_token": page_token,
            "orientation_token": orientation_token,
            "width_mm": float(width_mm),
            "height_mm": float(height_mm),
            "dpi": int(dpi_val),
            "scale_denominator": float(scale_den),
        }
        out.update(self._atlas_layout_metrics(out))
        return out

    def _atlas_layout_metrics(self, page_opts):
        page_w = float(page_opts.get("width_mm") or 297.0)
        page_h = float(page_opts.get("height_mm") or 210.0)
        margin = max(5.0, min(12.0, page_w * 0.03))
        top_band = max(10.0, min(14.0, page_h * 0.06))
        map_y = margin + top_band + 2.0
        bottom_reserved = max(12.0, min(18.0, page_h * 0.08))
        map_w = max(20.0, page_w - (2.0 * margin))
        map_h = max(20.0, page_h - map_y - bottom_reserved)
        north_x = page_w - margin - 12.0
        north_y = margin
        scale_y = page_h - margin + 1.0
        return {
            "margin_mm": float(margin),
            "top_band_mm": float(top_band),
            "map_x_mm": float(margin),
            "map_y_mm": float(map_y),
            "map_w_mm": float(map_w),
            "map_h_mm": float(map_h),
            "north_x_mm": float(north_x),
            "north_y_mm": float(north_y),
            "scale_x_mm": float(margin),
            "scale_y_mm": float(scale_y),
        }

    def _atlas_rows_from_coverage_layer(self, layer):
        if layer is None or not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            return []
        field_names = {f.name() for f in layer.fields()}

        def _attr(feat, name, default=None):
            if name not in field_names:
                return default
            try:
                return feat[name]
            except Exception:
                return default

        rows = []
        for feat in layer.getFeatures():
            geom = feat.geometry()
            has_geom = 1 if geom is not None and not geom.isEmpty() else 0
            rows.append(
                {
                    "geometry": QgsGeometry(geom) if has_geom else None,
                    "coverage_id": _attr(feat, "coverage_id", ""),
                    "ts_id": _attr(feat, "ts_id", ""),
                    "ts_name": _attr(feat, "ts_name", ""),
                    "group_id": _attr(feat, "group_id", ""),
                    "group_name": _attr(feat, "group_name", ""),
                    "depth_from": _attr(feat, "depth_from", None),
                    "depth_to": _attr(feat, "depth_to", None),
                    "depth_label": _attr(feat, "depth_label", ""),
                    "sort_key": _attr(feat, "sort_key", 10**9),
                    "raster_path": _attr(feat, "raster_path", ""),
                    "missing_depth": int(_attr(feat, "missing_depth", 0) or 0),
                    "has_geometry": has_geom,
                    "raster_exists": int(_attr(feat, "raster_exists", 0) or 0),
                    "raster_valid": int(_attr(feat, "raster_valid", 0) or 0),
                    "raster_crs": str(_attr(feat, "raster_crs", "") or ""),
                    "crs_mismatch": int(_attr(feat, "crs_mismatch", 0) or 0),
                }
            )
        rows.sort(key=lambda r: (int(r.get("sort_key") or 10**9), str(r.get("ts_name") or "").lower()))
        return rows

    def _atlas_rows_union_extent(self, rows):
        rect = None
        for row in rows or []:
            geom = row.get("geometry") if isinstance(row, dict) else None
            try:
                if geom is None or geom.isEmpty():
                    continue
                bbox = geom.boundingBox()
                if bbox is None or bbox.isEmpty():
                    continue
                if rect is None:
                    rect = QgsRectangle(bbox)
                else:
                    rect.combineExtentWith(bbox)
            except Exception:
                continue
        return rect

    def _atlas_coverage_extent(self, layer):
        rect = None
        if layer is None or not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            return None
        for feat in layer.getFeatures():
            try:
                geom = feat.geometry()
                if geom is None or geom.isEmpty():
                    continue
                bbox = geom.boundingBox()
                if bbox is None or bbox.isEmpty():
                    continue
                if rect is None:
                    rect = QgsRectangle(bbox)
                else:
                    rect.combineExtentWith(bbox)
            except Exception:
                continue
        return rect

    def _atlas_build_frame_geometry(self, rows, page_opts):
        union_extent = self._atlas_rows_union_extent(rows)
        if union_extent is None or union_extent.isEmpty():
            try:
                canvas = self.iface.mapCanvas() if self.iface is not None else None
                if canvas is not None:
                    union_extent = QgsRectangle(canvas.extent())
            except Exception:
                union_extent = None
        if union_extent is None or union_extent.isEmpty():
            return None

        scale_den = float(page_opts.get("scale_denominator") or 0.0) if isinstance(page_opts, dict) else 0.0
        frame_w_mm = float(page_opts.get("map_w_mm") or 0.0) if isinstance(page_opts, dict) else 0.0
        frame_h_mm = float(page_opts.get("map_h_mm") or 0.0) if isinstance(page_opts, dict) else 0.0
        if scale_den <= 0 or frame_w_mm <= 0 or frame_h_mm <= 0:
            return QgsGeometry.fromRect(union_extent)

        meters_to_map = 1.0
        try:
            crs = QgsProject.instance().crs()
            if crs.isValid():
                meters_to_map = float(
                    QgsUnitTypes.fromUnitToUnitFactor(QgsUnitTypes.DistanceMeters, crs.mapUnits())
                )
        except Exception:
            meters_to_map = 1.0
        if meters_to_map <= 0:
            meters_to_map = 1.0

        frame_w = (frame_w_mm / 1000.0) * scale_den * meters_to_map
        frame_h = (frame_h_mm / 1000.0) * scale_den * meters_to_map
        if frame_w <= 0 or frame_h <= 0:
            return QgsGeometry.fromRect(union_extent)

        center = union_extent.center()
        rect = QgsRectangle(
            center.x() - (frame_w / 2.0),
            center.y() - (frame_h / 2.0),
            center.x() + (frame_w / 2.0),
            center.y() + (frame_h / 2.0),
        )
        return QgsGeometry.fromRect(rect)

    def _plugin_root_group_names(self):
        primary = str(getattr(self, "plugin_layer_root_name", "") or "").strip()
        names = []
        for name in (primary, "RasterLinker", "rasterlinker"):
            n = str(name or "").strip()
            if n and n not in names:
                names.append(n)
        return names

    def _active_project_root(self):
        if self.project_manager_dialog is not None and self.project_manager_dialog.project_root:
            return self.project_manager_dialog.project_root
        if self.project_manager_dialog is not None:
            candidate = self.project_manager_dialog.path_edit.text().strip()
            if candidate:
                return candidate
        stored = self.settings.value(self.settings_key_active_project, "", type=str)
        if stored:
            return stored
        return ""

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

    def _require_project_root(self, notify=True):
        project_root = self._active_project_root()
        if not project_root:
            if notify:
                QMessageBox.warning(
                    self.dlg,
                    "Project Required",
                    "Open GeoSurvey Studio Project Manager and create/open a project first.",
                )
            return None
        return project_root

    def _get_plugin_root_group(self):
        root = QgsProject.instance().layerTreeRoot()
        aliases = self._plugin_root_group_names()
        group = None
        for alias in aliases:
            group = next(
                (
                    g for g in root.children()
                    if isinstance(g, QgsLayerTreeGroup) and str(g.name() or "").strip() == alias
                ),
                None,
            )
            if group is not None:
                break
        if group is None:
            group = root.addGroup(self.plugin_layer_root_name)
        else:
            # Normalize legacy root naming without changing children content.
            try:
                if str(group.name() or "").strip() != str(self.plugin_layer_root_name):
                    group.setName(self.plugin_layer_root_name)
            except Exception:
                pass
        return group

    def _get_or_create_plugin_qgis_group(self, group_name):
        plugin_root = self._get_plugin_root_group()
        group = next(
            (
                g for g in plugin_root.children()
                if isinstance(g, QgsLayerTreeGroup) and g.name() == group_name
            ),
            None,
        )
        if group is None:
            group = plugin_root.addGroup(group_name)
        return group

    def _find_plugin_root_group(self):
        root = QgsProject.instance().layerTreeRoot()
        for alias in self._plugin_root_group_names():
            found = next(
                (
                    g for g in root.children()
                    if isinstance(g, QgsLayerTreeGroup) and str(g.name() or "").strip() == alias
                ),
                None,
            )
            if found is not None:
                return found
        return None

    def _remove_plugin_qgis_group(self, group_name):
        plugin_root = self._find_plugin_root_group()
        if plugin_root is None:
            return
        target = next(
            (
                g for g in plugin_root.children()
                if isinstance(g, QgsLayerTreeGroup) and g.name() == group_name
            ),
            None,
        )
        if target is not None:
            plugin_root.removeChildNode(target)

    def _iter_plugin_raster_layers(self):
        plugin_root = self._find_plugin_root_group()
        if plugin_root is None:
            return

        for group in plugin_root.children():
            if not isinstance(group, QgsLayerTreeGroup):
                continue
            for child in group.children():
                if not isinstance(child, QgsLayerTreeLayer):
                    continue
                layer = child.layer()
                if isinstance(layer, QgsRasterLayer):
                    yield layer

    def _apply_minmax_to_layer(self, layer, return_reason=False):
        def _result(ok, reason):
            if return_reason:
                return bool(ok), str(reason or "")
            return bool(ok)

        if layer is None:
            return _result(False, "Layer is None.")

        direct_error = ""
        # Try direct layer API first (best compatibility when available).
        if hasattr(layer, "setContrastEnhancement"):
            try:
                layer.setContrastEnhancement(
                    QgsContrastEnhancement.StretchToMinimumMaximum,
                )
                layer.triggerRepaint()
                return _result(True, "Applied via layer.setContrastEnhancement().")
            except Exception as e1:
                direct_error = str(e1)
                try:
                    from qgis.core import QgsRasterMinMaxOrigin
                    layer.setContrastEnhancement(
                        QgsContrastEnhancement.StretchToMinimumMaximum,
                        QgsRasterMinMaxOrigin.MinMax,
                    )
                    layer.triggerRepaint()
                    return _result(True, "Applied via setContrastEnhancement(..., MinMax).")
                except Exception as e2:
                    direct_error = f"{direct_error} | fallback MinMax failed: {e2}"

        # Fallback: set renderer min/max based on band statistics.
        provider = layer.dataProvider()
        if provider is None:
            return _result(False, f"No data provider. Direct API error: {direct_error}")

        renderer = layer.renderer()
        if renderer is None:
            return _result(False, f"No renderer. Direct API error: {direct_error}")

        def _band_minmax(band_idx):
            try:
                stats = provider.bandStatistics(int(band_idx), QgsRasterBandStats.Min | QgsRasterBandStats.Max)
                mn = float(stats.minimumValue)
                mx = float(stats.maximumValue)
                if mx <= mn:
                    return None
                return mn, mx
            except Exception:
                return None

        applied = False

        first_band_range = _band_minmax(1)
        try:
            if first_band_range is not None:
                minimum, maximum = first_band_range
                if hasattr(renderer, "setClassificationMin"):
                    renderer.setClassificationMin(float(minimum))
                    applied = True
                if hasattr(renderer, "setClassificationMax"):
                    renderer.setClassificationMax(float(maximum))
                    applied = True

            if hasattr(renderer, "contrastEnhancement"):
                ce = renderer.contrastEnhancement()
                if ce is not None and first_band_range is not None:
                    minimum, maximum = first_band_range
                    ce.setMinimumValue(float(minimum))
                    ce.setMaximumValue(float(maximum))
                    ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum, True)
                    applied = True

            rgb_defs = (
                ("redBand", "redContrastEnhancement", "setRedContrastEnhancement"),
                ("greenBand", "greenContrastEnhancement", "setGreenContrastEnhancement"),
                ("blueBand", "blueContrastEnhancement", "setBlueContrastEnhancement"),
            )
            for band_getter_name, ce_getter_name, ce_setter_name in rgb_defs:
                if not hasattr(renderer, band_getter_name) or not hasattr(renderer, ce_getter_name):
                    continue
                try:
                    band_idx = int(getattr(renderer, band_getter_name)())
                except Exception:
                    continue
                if band_idx <= 0:
                    continue
                rng = _band_minmax(band_idx)
                if rng is None:
                    continue
                ce = getattr(renderer, ce_getter_name)()
                if ce is None:
                    continue
                mn, mx = rng
                ce.setMinimumValue(float(mn))
                ce.setMaximumValue(float(mx))
                ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum, True)
                setter = getattr(renderer, ce_setter_name, None)
                if callable(setter):
                    setter(ce)
                applied = True
        except Exception as e:
            return _result(False, f"Renderer enhancement error: {e}")

        if applied:
            layer.triggerRepaint()
            return _result(True, "Applied via renderer contrast enhancement.")
        reason = "No supported renderer enhancement path."
        if first_band_range is None:
            reason = "Unable to compute valid min/max statistics (band 1)."
        if direct_error:
            reason = f"{reason} Direct API error: {direct_error}"
        return _result(False, reason)

    def _catalog_groups_by_name(self, project_root):
        catalog = load_catalog(project_root)
        groups = catalog.get("raster_groups", [])
        return {g.get("name"): g for g in groups if g.get("name")}

    def _visible_plugin_group_names(self):
        plugin_root = self._find_plugin_root_group()
        if plugin_root is None:
            return []
        return [g.name() for g in plugin_root.children() if isinstance(g, QgsLayerTreeGroup)]

    def _apply_group_visibility_selection(self, group_names):
        project_root = self._require_project_root()
        if not project_root:
            return
        by_name = self._catalog_groups_by_name(project_root)
        selected = [name for name in group_names if name in by_name]

        # Respect pinned groups: do not remove pinned groups when deselected
        pinned = set(self._pinned_group_names())
        for existing in self._visible_plugin_group_names():
            if existing not in selected and existing not in pinned:
                self._remove_plugin_qgis_group(existing)

        for name in selected:
            self._get_or_create_plugin_qgis_group(name)

        self.populate_group_list()
        if self.dlg.groupListWidget.count() > 0:
            self.dlg.groupListWidget.setCurrentRow(0)
        self.load_raster(show_message=False)

    def _is_group_pinned_in_catalog(self, group_id):
        project_root = self._require_project_root()
        if not project_root:
            return False
        catalog = load_catalog(project_root)
        for grp in catalog.get("raster_groups", []) or []:
            if grp.get("id") == group_id:
                return bool(grp.get("pinned", False))
        return False

    def _pinned_group_names(self):
        """Return group names that are pinned according to the UI widgets (or catalog fallback)."""
        if self.dlg is None or not hasattr(self.dlg, "groupListWidget"):
            return []
        result = []
        lw = self.dlg.groupListWidget
        for i in range(lw.count()):
            item = lw.item(i)
            if item is None:
                continue
            widget = lw.itemWidget(item)
            if widget is None:
                # fallback to catalog-stored flag
                gid = str(item.data(Qt.UserRole) or "").strip()
                if gid and self._is_group_pinned_in_catalog(gid):
                    result.append(str(item.data(Qt.UserRole + 1) or "").strip())
                continue
            pin_btn = widget.findChild(QPushButton, "pinButton")
            if pin_btn is not None and getattr(pin_btn, "isChecked", lambda: False)():
                name = str(item.data(Qt.UserRole + 1) or "").strip()
                if name:
                    result.append(name)
        return result

    def _build_group_list_item(self, group_name, group_id, pinned=False):
        item_widget = QWidget()
        row_layout = QHBoxLayout(item_widget)
        row_layout.setContentsMargins(4, 1, 4, 1)
        row_layout.setSpacing(4)

        name_label = QLabel(group_name)
        name_label.setStyleSheet("font-size: 9pt;")
        row_layout.addWidget(name_label, 1)

        pin_btn = QPushButton("📌")
        pin_btn.setObjectName("pinButton")
        pin_btn.setCheckable(True)
        pin_btn.setChecked(bool(pinned))
        pin_btn.setFixedSize(22, 22)
        pin_btn.setToolTip("Keep this group always visible (not controlled by dial or selection)")
        pin_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; font-size: 11pt; }"
            "QPushButton:checked { color: #e87f00; }"
            "QPushButton:unchecked { color: #aaaaaa; }"
        )
        # connect will be done from populate_group_list to capture correct group_id context
        row_layout.addWidget(pin_btn)

        item_widget.setProperty("group_name", group_name)
        return item_widget

    def _on_group_pin_toggled(self, group_id, is_pinned):
        project_root = self._require_project_root()
        if not project_root:
            return
        try:
            update_raster_group(project_root, group_id, {"pinned": bool(is_pinned)})
        except Exception:
            # fallback: load and save catalog
            catalog = load_catalog(project_root)
            for grp in catalog.get("raster_groups", []) or []:
                if grp.get("id") == group_id:
                    grp["pinned"] = bool(is_pinned)
            from .project_catalog import save_catalog
            try:
                save_catalog(project_root, catalog)
            except Exception:
                pass

        # visible behavior
        if is_pinned:
            try:
                name = None
                # try to find an item with this id to get name
                if self.dlg is not None and hasattr(self.dlg, "groupListWidget"):
                    for i in range(self.dlg.groupListWidget.count()):
                        it = self.dlg.groupListWidget.item(i)
                        if it is not None and str(it.data(Qt.UserRole) or "") == str(group_id):
                            name = str(it.data(Qt.UserRole + 1) or "").strip() or str(it.text() or "").strip()
                            break
                if name:
                    self._get_or_create_plugin_qgis_group(name)
                    self.load_raster(show_message=False)
            except Exception:
                pass
        else:
            try:
                # if not selected, remove group from layer tree
                sel = set(self._selected_group_names())
                # find name by id
                name = None
                if self.dlg is not None and hasattr(self.dlg, "groupListWidget"):
                    for i in range(self.dlg.groupListWidget.count()):
                        it = self.dlg.groupListWidget.item(i)
                        if it is not None and str(it.data(Qt.UserRole) or "") == str(group_id):
                            name = str(it.data(Qt.UserRole + 1) or "").strip() or str(it.text() or "").strip()
                            break
                if name and name not in sel:
                    self._remove_plugin_qgis_group(name)
            except Exception:
                pass

    def open_group_import_dialog(self):
        project_root = self._require_project_root()
        if not project_root:
            return
        by_name = self._catalog_groups_by_name(project_root)
        groups = [g for g in by_name.values() if g.get("timeslice_ids")]
        if not groups:
            QMessageBox.information(self.dlg, "Import Groups", "No groups with images found in this project.")
            return

        dlg = GroupImportDialog(groups, self._visible_plugin_group_names(), self.dlg)
        if dlg.exec_() != dlg.Accepted:
            return
        self._apply_group_visibility_selection(dlg.selected_group_names())

    def enhance_loaded_images_minmax(self):
        total = 0
        enhanced = 0
        failed = []
        for layer in self._iter_plugin_raster_layers() or []:
            total += 1
            ok, reason = self._apply_minmax_to_layer(layer, return_reason=True)
            if ok:
                enhanced += 1
            else:
                layer_name = layer.name() if layer is not None else "Unknown layer"
                detail = f"{layer_name}: {reason or 'unknown reason'}"
                failed.append(detail)
                QgsMessageLog.logMessage(detail, "GeoSurvey Studio", level=Qgis.Warning)

        if total == 0:
            QMessageBox.information(
                self.dlg,
                "Enhance Min/Max",
                "No loaded images found in GeoSurvey Studio groups.",
            )
            return

        self.iface.messageBar().pushInfo(
            "GeoSurvey Studio",
            f"Enhance Min/Max applied: {enhanced}/{total} layers.",
        )
        if failed:
            self.iface.messageBar().pushWarning(
                "GeoSurvey Studio",
                f"Enhance Min/Max skipped {len(failed)} layer(s). See Log Messages for details.",
            )

    def _iter_group_raster_layers(self, group_name):
        group = self._get_or_create_plugin_qgis_group(group_name)
        for child in group.children():
            if isinstance(child, QgsLayerTreeLayer) and isinstance(child.layer(), QgsRasterLayer):
                yield child.layer()

    def _selected_group_names(self):
        if self.dlg is None:
            return []
        return [it.text().strip() for it in self.dlg.groupListWidget.selectedItems() if it.text().strip()]

    def _apply_value_range_to_layer(self, layer, minimum, maximum):
        if minimum is None or maximum is None:
            return False
        if float(maximum) <= float(minimum):
            return False

        renderer = layer.renderer()
        if renderer is None:
            return False
        applied = False
        try:
            if hasattr(renderer, "setClassificationMin"):
                renderer.setClassificationMin(float(minimum))
                applied = True
            if hasattr(renderer, "setClassificationMax"):
                renderer.setClassificationMax(float(maximum))
                applied = True
            if hasattr(renderer, "contrastEnhancement"):
                ce = renderer.contrastEnhancement()
                if ce is not None:
                    ce.setMinimumValue(float(minimum))
                    ce.setMaximumValue(float(maximum))
                    ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum, True)
                    applied = True
            if applied:
                layer.triggerRepaint()
            return applied
        except Exception:
            return False

    def _range_contains_zero(self, rng):
        try:
            min_attr = getattr(rng, "min", None)
            if callable(min_attr):
                mn = min_attr()
            else:
                min_attr = getattr(rng, "minimumValue", None)
                mn = min_attr() if callable(min_attr) else min_attr

            max_attr = getattr(rng, "max", None)
            if callable(max_attr):
                mx = max_attr()
            else:
                max_attr = getattr(rng, "maximumValue", None)
                mx = max_attr() if callable(max_attr) else max_attr

            mn = float(mn)
            mx = float(mx)
            return mn <= 0.0 <= mx
        except Exception:
            return False

    def _disable_zero_nodata_on_layer(self, layer):
        provider = layer.dataProvider()
        if provider is None:
            return False

        changed = False
        band_count = 0
        try:
            band_count = int(provider.bandCount())
        except Exception:
            band_count = 0

        for band in range(1, band_count + 1):
            try:
                if hasattr(provider, "userNoDataValues") and hasattr(provider, "setUserNoDataValue"):
                    ranges = list(provider.userNoDataValues(band) or [])
                    filtered = [r for r in ranges if not self._range_contains_zero(r)]
                    if len(filtered) != len(ranges):
                        provider.setUserNoDataValue(band, filtered)
                        changed = True
            except Exception:
                pass

            try:
                if hasattr(provider, "sourceNoDataValue") and hasattr(provider, "setUseSourceNoDataValue"):
                    src_no_data = provider.sourceNoDataValue(band)
                    if src_no_data is not None and abs(float(src_no_data)) < 1e-12:
                        provider.setUseSourceNoDataValue(band, False)
                        changed = True
            except Exception:
                pass

        if changed:
            try:
                layer.triggerRepaint()
            except Exception:
                pass
        return changed

    def enhance_batch_options(self):
        # Legge il tipo di enhancement dalla combo UI
        mode_combo = getattr(self, "enhance_contrast_mode_combo", None)
        mode = str(mode_combo.currentText()).strip() if mode_combo is not None else "Stretch to Min/Max"

        # Legge il radio attivo per determinare i valori di Min/Max
        radio_user = getattr(self, "enhance_radio_user_defined", None)
        radio_cumul = getattr(self, "enhance_radio_cumulative", None)
        radio_stddev = getattr(self, "enhance_radio_stddev", None)

        accuracy_combo = getattr(self, "enhance_accuracy_combo", None)
        use_estimated = True
        if accuracy_combo is not None:
            use_estimated = str(accuracy_combo.currentText()).lower().startswith("estimated")

        # Legge il tipo di enhancement dal QGIS enum corretto
        from qgis.core import QgsContrastEnhancement, QgsRasterMinMaxOrigin
        enhancement_algorithm_map = {
            "Stretch to Min/Max": QgsContrastEnhancement.StretchToMinimumMaximum,
            "Stretch and Clip to Min/Max": QgsContrastEnhancement.StretchAndClipToMinimumMaximum,
            "Clip to Min/Max": QgsContrastEnhancement.ClipToMinimumMaximum,
            "No Enhancement": QgsContrastEnhancement.NoEnhancement,
        }
        enhancement_algorithm = enhancement_algorithm_map.get(mode, QgsContrastEnhancement.StretchToMinimumMaximum)

        nodata_options = ["Keep current NoData", "Disable NoData=0"]
        nodata_mode, nodata_ok = QInputDialog.getItem(
            self.dlg, "Enhance Batch", "NoData handling:", nodata_options, 0, False,
        )
        if not nodata_ok:
            return
        disable_zero_nodata = nodata_mode == "Disable NoData=0"

        selected_groups = self._selected_group_names()
        layers = []
        if selected_groups:
            for name in selected_groups:
                layers.extend(list(self._iter_group_raster_layers(name)))
        else:
            layers = list(self._iter_plugin_raster_layers() or [])

        if not layers:
            QMessageBox.information(self.dlg, "Enhance Batch", "No loaded raster layers found.")
            return

        enhanced = 0
        nodata_updated = 0

        for layer in layers:
            provider = layer.dataProvider()
            if provider is None:
                continue

            if enhancement_algorithm != QgsContrastEnhancement.NoEnhancement:
                try:
                    # Calcola mn/mx in base al radio selezionato
                    if radio_user is not None and radio_user.isChecked():
                        mn = float(self.enhance_user_min_spin.value())
                        mx = float(self.enhance_user_max_spin.value())
                    elif radio_cumul is not None and radio_cumul.isChecked():
                        pct_lo = float(self.enhance_cumulative_min_spin.value()) / 100.0
                        pct_hi = float(self.enhance_cumulative_max_spin.value()) / 100.0
                        stats = provider.bandStatistics(
                            1, QgsRasterBandStats.Min | QgsRasterBandStats.Max
                        )
                        span = float(stats.maximumValue) - float(stats.minimumValue)
                        mn = float(stats.minimumValue) + pct_lo * span
                        mx = float(stats.minimumValue) + pct_hi * span
                    elif radio_stddev is not None and radio_stddev.isChecked():
                        factor = float(self.enhance_stddev_factor_spin.value())
                        stats = provider.bandStatistics(
                            1, QgsRasterBandStats.Mean | QgsRasterBandStats.StdDev
                        )
                        mn = float(stats.mean) - factor * float(stats.stdDev)
                        mx = float(stats.mean) + factor * float(stats.stdDev)
                    else:
                        # Default: Min/Max reale
                        stats = provider.bandStatistics(
                            1, QgsRasterBandStats.Min | QgsRasterBandStats.Max
                        )
                        mn = float(stats.minimumValue)
                        mx = float(stats.maximumValue)

                    if mx > mn:
                        renderer = layer.renderer()
                        if renderer is not None:
                            ce = renderer.contrastEnhancement() if hasattr(renderer, "contrastEnhancement") else None
                            if ce is not None:
                                ce.setMinimumValue(mn)
                                ce.setMaximumValue(mx)
                                ce.setContrastEnhancementAlgorithm(enhancement_algorithm, True)
                            if hasattr(renderer, "setClassificationMin"):
                                renderer.setClassificationMin(mn)
                            if hasattr(renderer, "setClassificationMax"):
                                renderer.setClassificationMax(mx)
                            layer.triggerRepaint()
                            enhanced += 1
                except Exception:
                    pass

            if disable_zero_nodata:
                try:
                    if self._disable_zero_nodata_on_layer(layer):
                        nodata_updated += 1
                except Exception:
                    pass

        msg = f"Enhance Batch ({mode}) applied: {enhanced}/{len(layers)} layers."
        if disable_zero_nodata:
            msg += f" NoData=0 disabled: {nodata_updated}/{len(layers)} layers."
        self.iface.messageBar().pushInfo("GeoSurvey Studio", msg)

    def _active_group_item(self):
        if self.dlg is None:
            return None
        return self.dlg.groupListWidget.currentItem()

    def _active_group_record(self):
        project_root = self._require_project_root()
        if not project_root:
            return None, None
        item = self._active_group_item()
        if item is None:
            return project_root, None
        group_id = item.data(Qt.UserRole)
        catalog = load_catalog(project_root)
        rec = next((g for g in catalog.get("raster_groups", []) if g.get("id") == group_id), None)
        return project_root, rec

    def save_selected_group_style(self):
        project_root, group = self._active_group_record()
        if not project_root or group is None:
            QMessageBox.warning(self.dlg, "Save Group Style", "Select one active group first.")
            return
        group_name = group.get("name", "Group")
        layers = list(self._iter_group_raster_layers(group_name))
        if not layers:
            QMessageBox.warning(self.dlg, "Save Group Style", "No loaded layers found for the selected group.")
            return
        style_dir = os.path.join(project_root, "metadata", "group_styles")
        os.makedirs(style_dir, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_\-]+", "_", group_name).strip("_") or "group"
        style_path = os.path.join(style_dir, f"{safe_name}.qml")
        ok_msg = layers[0].saveNamedStyle(style_path)
        if isinstance(ok_msg, tuple):
            ok = bool(ok_msg[0])
        else:
            ok = bool(ok_msg)
        if not ok:
            QMessageBox.warning(self.dlg, "Save Group Style", "Unable to save style file.")
            return
        update_raster_group(project_root, group.get("id"), {"style_qml_path": style_path})
        self.iface.messageBar().pushInfo("GeoSurvey Studio", f"Group style saved: {style_path}")

    def load_selected_group_style(self):
        project_root, group = self._active_group_record()
        if not project_root or group is None:
            QMessageBox.warning(self.dlg, "Load Group Style", "Select one active group first.")
            return
        style_path = (group.get("style_qml_path") or "").strip()
        if not style_path or not os.path.exists(style_path):
            QMessageBox.warning(self.dlg, "Load Group Style", "No saved style found for this group.")
            return
        group_name = group.get("name", "Group")
        layers = list(self._iter_group_raster_layers(group_name))
        if not layers:
            QMessageBox.warning(self.dlg, "Load Group Style", "No loaded layers found for the selected group.")
            return
        applied = 0
        for lyr in layers:
            try:
                result = lyr.loadNamedStyle(style_path)
                ok = bool(result[0]) if isinstance(result, tuple) else bool(result)
                if ok:
                    lyr.triggerRepaint()
                    applied += 1
            except Exception:
                continue
        self.iface.messageBar().pushInfo("GeoSurvey Studio", f"Group style loaded: {applied}/{len(layers)} layers.")

    def _atlas_export_coverage_mode(self):
        combo = getattr(self, "export_coverage_mode_combo", None)
        if combo is None:
            return None
        txt = str(combo.currentText() or "").strip().lower()
        if txt.startswith("use existing"):
            return "use_existing"
        if txt.startswith("refresh"):
            return "refresh_existing"
        if txt.startswith("create new"):
            return "create_new"
        return None

    def _atlas_export_output_mode(self):
        combo = getattr(self, "export_mode_combo", None)
        if combo is None:
            return "batch_single_pdf"
        txt = str(combo.currentText() or "").strip().lower()
        if txt.startswith("single visible"):
            return "single_visible"
        return "batch_single_pdf"

    def _visible_raster_layer_ids_for_group(self, group_name):
        ids = []
        try:
            group = self._get_or_create_plugin_qgis_group(str(group_name or "").strip())
            for child in group.children():
                if not isinstance(child, QgsLayerTreeLayer):
                    continue
                lyr = child.layer()
                if not isinstance(lyr, QgsRasterLayer):
                    continue
                if child.isVisible():
                    lid = str(lyr.id() or "").strip()
                    if lid:
                        ids.append(lid)
        except Exception:
            return []
        return ids

    def generate_atlas_coverage_from_export_tab(self):
        project_root, group = self._active_group_record()
        if not project_root or group is None:
            QMessageBox.warning(self.dlg, "Atlas Coverage", "Select one active group first.")
            return
        page_opts = self._atlas_export_page_settings()
        if page_opts is None:
            return
        ts_rows = self._build_atlas_coverage_rows(project_root, group)
        existing = self._find_atlas_coverage_layer(group.get("id"), group.get("name"))
        mode = self._atlas_export_coverage_mode() or "refresh_existing"
        create_new = False
        if mode == "use_existing":
            if existing is None:
                mode = "refresh_existing"
            else:
                self.iface.messageBar().pushInfo(
                    "GeoSurvey Studio",
                    f"Using existing Atlas coverage: {existing.name()}",
                )
                return
        if mode == "create_new":
            create_new = True
        self.build_atlas_coverage_for_active_group(
            create_new=create_new,
            rows=ts_rows,
            page_opts=page_opts,
        )

    def export_group_layout_quick(self):
        project_root, group = self._active_group_record()
        if not project_root or group is None:
            QMessageBox.warning(self.dlg, "Export Group Layout", "Select one active group first.")
            return
        group_name = group.get("name", "Group")
        page_opts = self._atlas_export_page_settings()
        if page_opts is None:
            return
        ts_rows = self._build_atlas_coverage_rows(project_root, group)
        existing_coverage = self._find_atlas_coverage_layer(group.get("id"), group_name)
        coverage_layer = None
        coverage_rows = None
        cov_mode = self._atlas_export_coverage_mode()
        if cov_mode is None and existing_coverage is not None:
            cov_label, ok_cov = QInputDialog.getItem(
                self.dlg,
                "Atlas Coverage",
                "Coverage source:",
                [
                    "Use existing coverage geometry (keep manual edits)",
                    "Refresh existing coverage from raster extents",
                    "Create NEW coverage layer",
                ],
                0,
                False,
            )
            if not ok_cov:
                return
            cov_mode = "use_existing" if cov_label.startswith("Use existing") else (
                "refresh_existing" if cov_label.startswith("Refresh") else "create_new"
            )
        if cov_mode is None:
            cov_mode = "refresh_existing"
        if cov_mode == "use_existing":
            if existing_coverage is None:
                cov_mode = "refresh_existing"
            else:
                coverage_layer = existing_coverage
                coverage_rows = ts_rows
        if cov_mode == "refresh_existing":
            coverage_layer, _ = self.build_atlas_coverage_for_active_group(
                create_new=False,
                rows=ts_rows,
                page_opts=page_opts,
            )
            coverage_rows = ts_rows
        elif cov_mode == "create_new":
            coverage_layer, _ = self.build_atlas_coverage_for_active_group(
                create_new=True,
                rows=ts_rows,
                page_opts=page_opts,
            )
            coverage_rows = ts_rows
        if coverage_rows is None:
            return
        if not self._atlas_export_validation_report(group_name, coverage_rows):
            return
        layers = list(self._iter_group_raster_layers(group_name))
        if not layers:
            QMessageBox.warning(self.dlg, "Export Group Layout", "No loaded layers for the selected group.")
            return
        context = self._atlas_export_map_context()
        if context is None:
            return

        project = QgsProject.instance()
        layout_manager = project.layoutManager()
        layout_name = "_GeoSurveyStudio_QuickExport"
        old = layout_manager.layoutByName(layout_name)
        if old is not None:
            layout_manager.removeLayout(old)

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName(layout_name)
        layout_manager.addLayout(layout)

        page_width_mm = float(page_opts.get("width_mm") or 297.0)
        page_height_mm = float(page_opts.get("height_mm") or 210.0)
        metrics = self._atlas_layout_metrics(page_opts)
        page = layout.pageCollection().page(0)
        try:
            orient = (
                QgsLayoutItemPage.Landscape
                if str(page_opts.get("orientation_label") or "").lower().startswith("land")
                else QgsLayoutItemPage.Portrait
            )
            page.setPageSize(str(page_opts.get("page_size_label") or "A4"), orient)
        except Exception:
            try:
                page.setPageSize(QgsLayoutSize(page_width_mm, page_height_mm, QgsUnitTypes.LayoutMillimeters))
            except Exception:
                pass

        margin = float(metrics.get("margin_mm") or 10.0)
        top_band_y = float(metrics.get("margin_mm") or 8.0)
        map_x = float(metrics.get("map_x_mm") or margin)
        map_y = float(metrics.get("map_y_mm") or 20.0)
        map_w = float(metrics.get("map_w_mm") or max(20.0, page_width_mm - (2.0 * margin)))
        map_h = float(metrics.get("map_h_mm") or max(20.0, page_height_mm - map_y - 22.0))

        map_item = QgsLayoutItemMap(layout)
        map_item.attemptMove(QgsLayoutPoint(map_x, map_y, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(map_w, map_h, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(map_item)

        title_item = QgsLayoutItemLabel(layout)
        title_item.attemptMove(QgsLayoutPoint(margin, top_band_y, QgsUnitTypes.LayoutMillimeters))
        try:
            title_item.setFont(QFont("Arial", 11))
        except Exception:
            pass
        layout.addLayoutItem(title_item)

        scale_item = QgsLayoutItemScaleBar(layout)
        scale_item.setLinkedMap(map_item)
        scale_item.setStyle("Single Box")
        try:
            scale_item.setUnits(QgsUnitTypes.DistanceMeters)
            scale_item.setUnitLabel("m")
        except Exception:
            pass
        scale_item.applyDefaultSize()
        scale_item.attemptMove(
            QgsLayoutPoint(float(metrics.get("scale_x_mm") or margin), float(metrics.get("scale_y_mm") or (page_height_mm - 10.0)), QgsUnitTypes.LayoutMillimeters)
        )
        layout.addLayoutItem(scale_item)

        north_item = QgsLayoutItemPicture(layout)
        north_item.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        north_item.attemptMove(
            QgsLayoutPoint(float(metrics.get("north_x_mm") or (page_width_mm - 22.0)), float(metrics.get("north_y_mm") or top_band_y), QgsUnitTypes.LayoutMillimeters)
        )
        north_item.attemptResize(QgsLayoutSize(12, 12, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(north_item)

        by_path = {}
        by_name = {}
        for row in coverage_rows:
            key = self._normalized_data_path(row.get("raster_path"))
            if key:
                by_path[key] = row
            name_key = str(row.get("ts_name") or "").strip().lower()
            if name_key and name_key not in by_name:
                by_name[name_key] = row
        project_token = self._atlas_safe_token(os.path.basename(project_root), "project")
        group_token = self._atlas_safe_token(group_name, "group")

        targets = []
        for lyr in layers:
            row = by_path.get(self._normalized_data_path(lyr.source()))
            if row is None:
                row = by_name.get(str(lyr.name() or "").strip().lower())
            sort_key = int(row.get("sort_key")) if isinstance(row, dict) and row.get("sort_key") is not None else 10**9
            depth_label = str(row.get("depth_label") or "") if isinstance(row, dict) else ""
            ts_name = str(row.get("ts_name") or lyr.name() or "") if isinstance(row, dict) else str(lyr.name() or "")
            depth_token = self._atlas_safe_token(depth_label, "nodepth") if depth_label else "nodepth"
            ts_token = self._atlas_safe_token(ts_name, "timeslice")
            base_name = f"{project_token}_{group_token}_{depth_token}_{ts_token}.pdf"
            targets.append((sort_key, str(ts_name).lower(), lyr, row, base_name))

        targets.sort(key=lambda it: (it[0], it[1]))
        exported = 0
        context_mode = str(context.get("mode") or "raster_only").strip().lower()
        context_extent = context.get("extent")
        context_layers = self._atlas_layers_from_ids(context.get("layer_ids") or [])
        coverage_extent = self._atlas_coverage_extent(coverage_layer)
        format_token = self._atlas_safe_token(
            f"{page_opts.get('page_token')}_{page_opts.get('orientation_token')}",
            "A4_landscape",
        )

        output_mode = self._atlas_export_output_mode()
        if output_mode == "single_visible":
            visible_ids = set(self._visible_raster_layer_ids_for_group(group_name))
            if visible_ids:
                filtered = [t for t in targets if str(t[2].id()) in visible_ids]
            else:
                filtered = []
            targets = filtered[:1] if filtered else (targets[:1] if targets else [])
        if not targets:
            layout_manager.removeLayout(layout)
            QMessageBox.warning(self.dlg, "Export Group Layout", "No export target layers found.")
            return

        def _apply_target(lyr, row):
            row_geom = row.get("geometry") if isinstance(row, dict) else None
            row_extent = None
            try:
                if row_geom is not None and not row_geom.isEmpty():
                    row_extent = row_geom.boundingBox()
                    if row_extent is not None and row_extent.isEmpty():
                        row_extent = None
            except Exception:
                row_extent = None
            if context_mode == "raster_only":
                map_layers = [lyr]
            else:
                map_layers = list(context_layers)
                if all(str(x.id()) != str(lyr.id()) for x in map_layers):
                    map_layers.insert(0, lyr)
                if not map_layers:
                    map_layers = [lyr]
            map_item.setLayers(map_layers)
            if context_mode == "raster_only":
                if coverage_extent is not None and not coverage_extent.isEmpty():
                    map_item.setExtent(coverage_extent)
                elif row_extent is not None:
                    map_item.setExtent(row_extent)
                else:
                    map_item.zoomToExtent(lyr.extent())
            elif context_extent is not None:
                map_item.setExtent(context_extent)
            elif row_extent is not None:
                map_item.setExtent(row_extent)
            else:
                map_item.zoomToExtent(lyr.extent())
            label_ts_name = str(row.get("ts_name") or lyr.name()) if isinstance(row, dict) else str(lyr.name())
            label_depth = str(row.get("depth_label") or "") if isinstance(row, dict) else ""
            if label_depth:
                title_item.setText(f"{group_name} - {label_ts_name} ({label_depth})")
            else:
                title_item.setText(f"{group_name} - {label_ts_name}")
            title_item.adjustSizeToText()

        project_token = self._atlas_safe_token(os.path.basename(project_root), "project")
        group_token = self._atlas_safe_token(group_name, "group")
        if output_mode == "batch_single_pdf":
            default_name = f"{project_token}_{group_token}_{format_token}_batch.pdf"
            pdf_path, _ = QFileDialog.getSaveFileName(
                self.dlg,
                "Export Batch PDF",
                os.path.join(project_root, default_name),
                "PDF files (*.pdf)",
            )
            if not pdf_path:
                layout_manager.removeLayout(layout)
                return
            try:
                writer = QPdfWriter(pdf_path)
                writer.setPageSizeMM(QSizeF(page_width_mm, page_height_mm))
                writer.setResolution(int(page_opts.get("dpi") or 300))
                painter = QPainter(writer)
                tmp_dir = tempfile.mkdtemp(prefix="gss_pdf_")
                first_page = True
                for _sort_key, _name_key, lyr, row, _base_name in targets:
                    _apply_target(lyr, row)
                    img_path = os.path.join(tmp_dir, f"page_{exported+1:04d}.png")
                    img_settings = QgsLayoutExporter.ImageExportSettings()
                    img_settings.dpi = int(page_opts.get("dpi") or 300)
                    img_res = QgsLayoutExporter(layout).exportToImage(img_path, img_settings)
                    if img_res != QgsLayoutExporter.Success:
                        continue
                    img = QImage(img_path)
                    if img.isNull():
                        continue
                    if not first_page:
                        writer.newPage()
                    first_page = False
                    painter.drawImage(painter.viewport(), img)
                    exported += 1
                painter.end()
                try:
                    for fn in os.listdir(tmp_dir):
                        try:
                            os.remove(os.path.join(tmp_dir, fn))
                        except Exception:
                            pass
                    os.rmdir(tmp_dir)
                except Exception:
                    pass
            except Exception:
                pass
        else:
            _sort_key, _name_key, lyr, row, _base_name = targets[0]
            ts_token = self._atlas_safe_token(str(row.get("ts_name") or lyr.name() or "timeslice"), "timeslice")
            default_name = f"{project_token}_{group_token}_{format_token}_{ts_token}.pdf"
            pdf_path, _ = QFileDialog.getSaveFileName(
                self.dlg,
                "Export Single Visible PDF",
                os.path.join(project_root, default_name),
                "PDF files (*.pdf)",
            )
            if not pdf_path:
                layout_manager.removeLayout(layout)
                return
            try:
                _apply_target(lyr, row)
                exporter = QgsLayoutExporter(layout)
                pdf_settings = QgsLayoutExporter.PdfExportSettings()
                pdf_settings.dpi = int(page_opts.get("dpi") or 300)
                result = exporter.exportToPdf(pdf_path, pdf_settings)
                if result == QgsLayoutExporter.Success:
                    exported = 1
            except Exception:
                pass

        layout_manager.removeLayout(layout)
        self.iface.messageBar().pushInfo(
            "GeoSurvey Studio",
            (
                f"Quick layout export completed: {exported}/{len(targets)} page(s). "
                f"Page: {page_opts.get('page_size_label')} {page_opts.get('orientation_label')}, "
                f"DPI: {page_opts.get('dpi')}."
            ),
        )

