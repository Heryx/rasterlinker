import os
import re
import time

from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QFileDialog, QInputDialog, QMessageBox
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

    def _ensure_atlas_coverage_layer(self, group, rows, create_new=False):
        project = QgsProject.instance()
        project_crs = project.crs().authid() if project.crs().isValid() else "EPSG:4326"
        base_layer_name = f"AtlasCoverage_{self._atlas_safe_token(group.get('name') or 'Group', default_token='group')}"
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

        started_here = False
        try:
            if not layer.isEditable():
                started_here = bool(layer.startEditing())
            ids = [f.id() for f in layer.getFeatures()]
            if ids:
                layer.deleteFeatures(ids)
            feats = []
            fields = layer.fields()
            for row in rows:
                feat = QgsFeature(fields)
                geom = row.get("geometry")
                if geom is not None:
                    feat.setGeometry(geom)
                feat.setAttribute("coverage_id", row.get("coverage_id"))
                feat.setAttribute("ts_id", row.get("ts_id"))
                feat.setAttribute("ts_name", row.get("ts_name"))
                feat.setAttribute("group_id", row.get("group_id"))
                feat.setAttribute("group_name", row.get("group_name"))
                feat.setAttribute("depth_from", row.get("depth_from"))
                feat.setAttribute("depth_to", row.get("depth_to"))
                feat.setAttribute("depth_label", row.get("depth_label"))
                feat.setAttribute("sort_key", row.get("sort_key"))
                feat.setAttribute("raster_path", row.get("raster_path"))
                feat.setAttribute("missing_depth", row.get("missing_depth"))
                feat.setAttribute("has_geometry", row.get("has_geometry"))
                feat.setAttribute("raster_exists", row.get("raster_exists"))
                feat.setAttribute("raster_valid", row.get("raster_valid"))
                feat.setAttribute("raster_crs", row.get("raster_crs"))
                feat.setAttribute("crs_mismatch", row.get("crs_mismatch"))
                feats.append(feat)
            if feats:
                layer.addFeatures(feats)
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
        with_geom = len([r for r in rows if r.get("geometry") is not None])
        return layer, len(rows), with_geom

    def build_atlas_coverage_for_active_group(self, create_new=False):
        project_root, group = self._active_group_record()
        if not project_root or group is None:
            QMessageBox.warning(self.dlg, "Atlas Coverage", "Select one active group first.")
            return None, []
        rows = self._build_atlas_coverage_rows(project_root, group)
        layer, total, with_geom = self._ensure_atlas_coverage_layer(group, rows, create_new=create_new)
        if layer is None:
            QMessageBox.warning(self.dlg, "Atlas Coverage", "Unable to create/update coverage layer.")
            return None, []
        self.iface.messageBar().pushInfo(
            "GeoSurvey Studio",
            f"Atlas coverage refreshed for '{group.get('name')}'. Features: {total}, with geometry: {with_geom}.",
        )
        return layer, rows

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
        mode_label, ok_mode = QInputDialog.getItem(
            self.dlg,
            "Export Group Layout",
            "Map content:",
            [
                "Raster only (time-slice layer only)",
                "Current canvas view (visible layers + current extent)",
                "Map theme (visible layers from selected theme + current extent)",
            ],
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
            return None
        theme_name, ok_theme = QInputDialog.getItem(
            self.dlg,
            "Export Group Layout",
            "Map theme:",
            names,
            0,
            False,
        )
        if not ok_theme:
            return None
        ids = self._atlas_theme_visible_layer_ids(theme_name)
        return {
            "mode": "map_theme",
            "layer_ids": ids,
            "extent": canvas_extent,
            "theme_name": str(theme_name or "").strip(),
        }

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

        for existing in self._visible_plugin_group_names():
            if existing not in selected:
                self._remove_plugin_qgis_group(existing)

        for name in selected:
            self._get_or_create_plugin_qgis_group(name)

        self.populate_group_list()
        if self.dlg.groupListWidget.count() > 0:
            self.dlg.groupListWidget.setCurrentRow(0)
        self.load_raster(show_message=False)

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
        options = ["No enhancement (NoData only)", "Min/Max", "Percent Clip (2%)", "StdDev (2 sigma)"]
        mode, ok = QInputDialog.getItem(
            self.dlg,
            "Enhance Batch",
            "Enhancement mode:",
            options,
            0,
            False,
        )
        if not ok:
            return

        nodata_options = ["Keep current NoData", "Disable NoData=0"]
        nodata_mode, nodata_ok = QInputDialog.getItem(
            self.dlg,
            "Enhance Batch",
            "NoData handling:",
            nodata_options,
            0,
            False,
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
            if mode != "No enhancement (NoData only)":
                try:
                    if mode == "StdDev (2 sigma)":
                        stats = provider.bandStatistics(
                            1,
                            QgsRasterBandStats.Mean | QgsRasterBandStats.StdDev,
                        )
                        mn = float(stats.mean) - 2.0 * float(stats.stdDev)
                        mx = float(stats.mean) + 2.0 * float(stats.stdDev)
                    else:
                        stats = provider.bandStatistics(
                            1,
                            QgsRasterBandStats.Min | QgsRasterBandStats.Max,
                        )
                        mn = float(stats.minimumValue)
                        mx = float(stats.maximumValue)
                        if mode == "Percent Clip (2%)":
                            span = mx - mn
                            mn = mn + 0.02 * span
                            mx = mx - 0.02 * span
                    if self._apply_value_range_to_layer(layer, mn, mx):
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

    def export_group_layout_quick(self):
        project_root, group = self._active_group_record()
        if not project_root or group is None:
            QMessageBox.warning(self.dlg, "Export Group Layout", "Select one active group first.")
            return
        mode_label, ok_mode = QInputDialog.getItem(
            self.dlg,
            "Export Group Layout",
            "Action:",
            [
                "Quick PDF export (one file per loaded raster)",
                "Generate/refresh Atlas coverage only",
            ],
            0,
            False,
        )
        if not ok_mode:
            return
        group_name = group.get("name", "Group")
        create_new_coverage = False
        existing_coverage = self._find_atlas_coverage_layer(group.get("id"), group_name)
        if existing_coverage is not None:
            choice = QMessageBox.question(
                self.dlg,
                "Atlas Coverage",
                (
                    f"A coverage layer already exists for group '{group_name}'.\n\n"
                    "Yes: create a NEW coverage layer\n"
                    "No: refresh/reuse existing coverage layer\n"
                    "Cancel: abort"
                ),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.No,
            )
            if choice == QMessageBox.Cancel:
                return
            create_new_coverage = (choice == QMessageBox.Yes)
        _coverage_layer, coverage_rows = self.build_atlas_coverage_for_active_group(create_new=create_new_coverage)
        if coverage_rows is None:
            return
        if mode_label == "Generate/refresh Atlas coverage only":
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
        out_dir = QFileDialog.getExistingDirectory(self.dlg, "Select output folder for PDF export")
        if not out_dir:
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

        map_item = QgsLayoutItemMap(layout)
        map_item.attemptMove(QgsLayoutPoint(10, 20, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(277, 170, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(map_item)

        label_item = QgsLayoutItemLabel(layout)
        label_item.attemptMove(QgsLayoutPoint(10, 8, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(label_item)

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
        used_names = {}
        exported = 0
        context_mode = str(context.get("mode") or "raster_only").strip().lower()
        context_extent = context.get("extent")
        context_layers = self._atlas_layers_from_ids(context.get("layer_ids") or [])
        for _sort_key, _name_key, lyr, row, base_name in targets:
            try:
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
                    map_item.zoomToExtent(lyr.extent())
                elif context_extent is not None:
                    map_item.setExtent(context_extent)
                else:
                    map_item.zoomToExtent(lyr.extent())
                label_ts_name = str(row.get("ts_name") or lyr.name()) if isinstance(row, dict) else str(lyr.name())
                label_depth = str(row.get("depth_label") or "") if isinstance(row, dict) else ""
                if label_depth:
                    label_item.setText(f"{group_name} - {label_ts_name} ({label_depth})")
                else:
                    label_item.setText(f"{group_name} - {label_ts_name}")
                label_item.adjustSizeToText()
                count = used_names.get(base_name, 0)
                used_names[base_name] = count + 1
                if count > 0:
                    stem, ext = os.path.splitext(base_name)
                    file_name = f"{stem}_{count:03d}{ext}"
                else:
                    file_name = base_name
                pdf_path = os.path.join(out_dir, file_name)
                exporter = QgsLayoutExporter(layout)
                result = exporter.exportToPdf(pdf_path, QgsLayoutExporter.PdfExportSettings())
                if result == QgsLayoutExporter.Success:
                    exported += 1
            except Exception:
                continue

        layout_manager.removeLayout(layout)
        self.iface.messageBar().pushInfo(
            "GeoSurvey Studio",
            f"Quick layout export completed: {exported}/{len(layers)} PDFs.",
        )

