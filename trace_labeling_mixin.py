# -*- coding: utf-8 -*-
"""Trace labeling mixin for GeoSurvey Studio plugin."""

import json

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsEditorWidgetSetup,
    QgsExpression,
    QgsFeature,
    QgsGeometry,
    QgsMessageLog,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    Qgis,
    QgsRelation,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)

from .layer_property_utils import get_layer_property, set_layer_property


class TraceLabelingMixin:
    def _is_vertex_depth_label_layer(self, layer):
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            return False
        try:
            if layer.geometryType() != QgsWkbTypes.PointGeometry:
                return False
        except Exception:
            return False
        prop_flag = str(get_layer_property(layer, "vertex_labels", default="") or "").strip()
        if prop_flag == "1":
            return True
        try:
            field_names = {f.name() for f in layer.fields()}
        except Exception:
            field_names = set()
        has_depth = ("depth_val" in field_names) or ("depth_lbl" in field_names)
        has_trace_keys = (
            ("trace_id" in field_names)
            or ("trace_fid" in field_names)
            or ("trace_layer_id" in field_names)
        )
        # Be permissive for legacy layers: trace_layer_id/trace_id may be missing.
        if has_trace_keys and has_depth:
            try:
                set_layer_property(layer, "vertex_labels", "1")
            except Exception:
                pass
            return True
        return False

    def _configure_vertex_labeling(self, layer):
        if layer is None or not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            return False
        try:
            has_depth_val = layer.fields().indexOf("depth_val") >= 0
        except Exception:
            has_depth_val = False
        try:
            has_depth_lbl = layer.fields().indexOf("depth_lbl") >= 0
        except Exception:
            has_depth_lbl = False
        if not has_depth_val and not has_depth_lbl:
            return False
        expr = None
        try:
            pal = QgsPalLayerSettings()
            pal.enabled = True
            pal.isExpression = True
            if has_depth_val:
                # Keep expression string-typed to avoid parser/provider incompatibilities.
                expr = (
                    "CASE "
                    "WHEN \"depth_val\" IS NULL THEN coalesce(\"depth_lbl\", '') "
                    "ELSE concat(round(\"depth_val\", 3), '') "
                    "END"
                )
            else:
                expr = "coalesce(\"depth_lbl\", '')"
            try:
                check_expr = QgsExpression(expr)
                if check_expr.hasParserError():
                    expr = "coalesce(\"depth_lbl\", '')"
            except Exception:
                expr = "coalesce(\"depth_lbl\", '')"
            pal.fieldName = expr
            # QGIS API compatibility:
            # newer versions expect Qgis.LabelPlacement enum, while older
            # versions use QgsPalLayerSettings.OverPoint.
            try:
                pal.placement = Qgis.LabelPlacement.OverPoint
            except Exception:
                try:
                    pal.placement = QgsPalLayerSettings.OverPoint
                except Exception:
                    pass
            try:
                pal.displayAll = True
            except Exception:
                pass
            txt = QgsTextFormat()
            txt.setSize(8)
            txt.setColor(QColor(25, 25, 25))
            buf = QgsTextBufferSettings()
            buf.setEnabled(True)
            buf.setSize(0.9)
            buf.setColor(QColor(255, 255, 255))
            txt.setBuffer(buf)
            pal.setFormat(txt)
            layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
            try:
                layer.setScaleBasedVisibility(False)
            except Exception:
                pass
            return True
        except Exception as exc:
            try:
                QgsMessageLog.logMessage(
                    f"Vertex labeling configure failed for '{layer.name()}': {exc}",
                    "GeoSurvey Studio",
                    Qgis.Critical,
                )
            except Exception:
                pass
            return False

    def _vertex_labels_enabled_for_mode(self):
        mode = "off"
        try:
            mode = str(getattr(self, "_trace_depth_pick_mode", lambda: "off")() or "off").strip().lower()
        except Exception:
            mode = str(getattr(self, "trace_depth_pick_mode", "off") or "off").strip().lower()
        return mode in ("min", "mid", "max")

    def _apply_vertex_label_mode_to_layers(self):
        enabled = self._vertex_labels_enabled_for_mode()
        try:
            layers = list(QgsProject.instance().mapLayers().values())
        except Exception:
            layers = []
        for lyr in layers:
            try:
                if not self._is_vertex_depth_label_layer(lyr):
                    continue
                try:
                    set_layer_property(lyr, "vertex_labels", "1")
                except Exception:
                    pass
                self._configure_vertex_labeling(lyr)
                lyr.setLabelsEnabled(bool(enabled))
                if enabled:
                    try:
                        root = QgsProject.instance().layerTreeRoot()
                        node = root.findLayer(lyr.id()) if root is not None else None
                        if node is not None and hasattr(node, "setItemVisibilityChecked"):
                            node.setItemVisibilityChecked(True)
                    except Exception:
                        pass
                lyr.triggerRepaint()
            except Exception:
                continue
        try:
            self.iface.mapCanvas().refreshAllLayers()
        except Exception:
            pass

    def _pick_depth_value_for_display(self, d0, d1):
        lo = None
        hi = None
        try:
            if d0 not in (None, ""):
                lo = float(d0)
        except Exception:
            lo = None
        try:
            if d1 not in (None, ""):
                hi = float(d1)
        except Exception:
            hi = None
        if lo is None and hi is None:
            return None
        if lo is None:
            return hi
        if hi is None:
            return lo
        if hi < lo:
            lo, hi = hi, lo
        mode = "mid"
        try:
            mode = str(getattr(self, "_trace_depth_pick_mode", lambda: "mid")() or "mid").strip().lower()
        except Exception:
            mode = str(getattr(self, "trace_depth_pick_mode", "mid") or "mid").strip().lower()
        if mode == "min":
            return lo
        if mode == "max":
            return hi
        return (lo + hi) / 2.0

    def _ensure_trace_vertex_relation(self, source_layer, label_layer):
        if source_layer is None or label_layer is None:
            return
        if not source_layer.isValid() or not label_layer.isValid():
            return
        if source_layer.fields().indexOf("trace_id") < 0:
            return
        if label_layer.fields().indexOf("trace_id") < 0:
            return

        relation_id = f"gss_trace_vertices_{source_layer.id()}"
        relation_name = f"{source_layer.name()} vertices"
        mgr = QgsProject.instance().relationManager()
        try:
            current = mgr.relation(relation_id)
            if (
                current is not None
                and current.isValid()
                and current.referencedLayerId() == source_layer.id()
                and current.referencingLayerId() == label_layer.id()
            ):
                return
            if current is not None and current.isValid():
                mgr.removeRelation(current.id())
        except Exception:
            pass

        relation = QgsRelation()
        relation.setId(relation_id)
        relation.setName(relation_name)
        relation.setReferencedLayer(source_layer.id())   # parent (line)
        relation.setReferencingLayer(label_layer.id())   # child (vertices)
        relation.addFieldPair("trace_id", "trace_id")
        try:
            if relation.isValid():
                mgr.addRelation(relation)
        except Exception:
            pass

    def _iter_geometry_vertices_xy(self, geometry):
        if geometry is None or geometry.isEmpty():
            return []
        vertices = []
        try:
            if geometry.isMultipart():
                for part in geometry.asMultiPolyline() or []:
                    for pt in part or []:
                        vertices.append(QgsPointXY(pt.x(), pt.y()))
            else:
                for pt in geometry.asPolyline() or []:
                    vertices.append(QgsPointXY(pt.x(), pt.y()))
        except Exception:
            return []
        return vertices

    def _depth_label_from_trace_feature(self, layer, feat):
        if layer is None or feat is None:
            return "missing_z", None, "m"

        def _attr(name, default=None):
            idx = layer.fields().indexOf(name)
            if idx < 0:
                return default
            val = feat.attribute(idx)
            return default if val in (None, "") else val

        unit = str(_attr("depth_unit", "m") or "m").strip() or "m"
        z_value = _attr("z_value", None)
        depth_from = _attr("depth_from", None)
        depth_to = _attr("depth_to", None)

        try:
            if depth_from is not None and depth_to is not None:
                d0 = float(depth_from)
                d1 = float(depth_to)
                return f"{d0:.2f}-{d1:.2f} {unit}", self._pick_depth_value_for_display(d0, d1), unit
            if depth_from is not None:
                d0 = float(depth_from)
                return f"{d0:.2f} {unit}", d0, unit
            if depth_to is not None:
                d1 = float(depth_to)
                return f"{d1:.2f} {unit}", d1, unit
            if z_value is not None:
                zv = float(z_value)
                return f"{zv:.2f} {unit}", zv, unit
        except Exception:
            pass
        return "missing_z", None, unit

    def _per_vertex_depth_map(self, layer, feat):
        if layer is None or feat is None:
            return {}
        idx = layer.fields().indexOf("vertex_depths")
        if idx < 0:
            return {}
        raw = feat.attribute(idx)
        if raw in (None, ""):
            return {}
        try:
            items = json.loads(str(raw))
        except Exception:
            return {}
        if not isinstance(items, list):
            return {}
        by_idx = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                v_idx = int(item.get("i") or 0)
            except Exception:
                continue
            if v_idx <= 0:
                continue
            depth_val = item.get("d")
            depth_min = item.get("dmin")
            depth_max = item.get("dmax")
            try:
                if depth_val not in (None, ""):
                    depth_val = float(depth_val)
                else:
                    depth_val = None
            except Exception:
                depth_val = None
            try:
                if depth_min not in (None, ""):
                    depth_min = float(depth_min)
                else:
                    depth_min = None
            except Exception:
                depth_min = None
            try:
                if depth_max not in (None, ""):
                    depth_max = float(depth_max)
                else:
                    depth_max = None
            except Exception:
                depth_max = None
            unit = str(item.get("u") or "m").strip() or "m"
            # Always derive display value from interval when available, so switching
            # Min/Mid/Max mode updates existing vertex labels without redrawing.
            if depth_min is not None or depth_max is not None:
                depth_val = self._pick_depth_value_for_display(depth_min, depth_max)
            if depth_min is not None and depth_max is not None and abs(depth_max - depth_min) > 1e-9:
                label = f"{depth_min:.2f}-{depth_max:.2f} {unit}"
            elif depth_val is None and depth_min is not None:
                depth_val = self._pick_depth_value_for_display(depth_min, depth_max)
                label = f"{depth_min:.2f} {unit}"
            elif depth_val is None and depth_max is not None:
                depth_val = self._pick_depth_value_for_display(depth_min, depth_max)
                label = f"{depth_max:.2f} {unit}"
            elif depth_val is None:
                label = "missing_z"
            else:
                label = f"{depth_val:.2f} {unit}"
            by_idx[v_idx] = {
                "depth_val": depth_val,
                "depth_min": depth_min,
                "depth_max": depth_max,
                "depth_unit": unit,
                "depth_lbl": label,
            }
        return by_idx

    def _find_trace_vertex_label_layer(self, source_layer):
        if source_layer is None:
            return None
        source_layer_id = source_layer.id()
        label_layer = None
        fallback_by_name = None
        sole_candidate = None
        candidates = []
        for lyr in QgsProject.instance().mapLayers().values():
            if not self._is_vertex_depth_label_layer(lyr):
                continue
            candidates.append(lyr)
            trace_lid_prop = str(get_layer_property(lyr, "trace_layer_id", default="") or "").strip()
            if trace_lid_prop and trace_lid_prop != str(source_layer_id):
                # Keep as possible fallback by naming.
                try:
                    l_name = str(lyr.name() or "").strip().lower()
                    s_name = str(source_layer.name() or "").strip().lower()
                    if s_name and l_name.startswith(s_name) and "vertex" in l_name:
                        fallback_by_name = lyr
                except Exception:
                    pass
                continue
            if not trace_lid_prop:
                idx = lyr.fields().indexOf("trace_layer_id")
                if idx >= 0:
                    found = False
                    for feat in lyr.getFeatures():
                        try:
                            if str(feat.attribute(idx) or "").strip() == str(source_layer_id):
                                found = True
                                break
                        except Exception:
                            continue
                    if not found:
                        try:
                            l_name = str(lyr.name() or "").strip().lower()
                            s_name = str(source_layer.name() or "").strip().lower()
                            if s_name and l_name.startswith(s_name) and "vertex" in l_name:
                                fallback_by_name = lyr
                        except Exception:
                            pass
                        continue
                else:
                    try:
                        l_name = str(lyr.name() or "").strip().lower()
                        s_name = str(source_layer.name() or "").strip().lower()
                        if s_name and l_name.startswith(s_name) and "vertex" in l_name:
                            fallback_by_name = lyr
                    except Exception:
                        pass
                    continue
                set_layer_property(lyr, "trace_layer_id", str(source_layer_id))
            label_layer = lyr
            break

        if label_layer is None:
            if fallback_by_name is not None:
                label_layer = fallback_by_name
                try:
                    set_layer_property(label_layer, "trace_layer_id", str(source_layer_id))
                except Exception:
                    pass
            elif len(candidates) == 1:
                # Last-resort fallback for legacy projects with a single vertex layer.
                sole_candidate = candidates[0]
                label_layer = sole_candidate
                try:
                    set_layer_property(label_layer, "trace_layer_id", str(source_layer_id))
                except Exception:
                    pass

        return label_layer

    def _ensure_trace_vertex_label_layer(self, source_layer):
        if source_layer is None:
            return None
        source_layer_id = source_layer.id()
        label_layer = self._find_trace_vertex_label_layer(source_layer)

        if label_layer is None:
            crs_authid = source_layer.crs().authid() if source_layer.crs().isValid() else "EPSG:4326"
            layer_name = f"{source_layer.name()} | Vertex depth"
            uri = (
                f"Point?crs={crs_authid}"
                "&field=trace_fid:int"
                "&field=trace_id:string(64)"
                "&field=vertex_idx:int"
                "&field=depth_lbl:string(64)"
                "&field=depth_val:double"
                "&field=depth_min:double"
                "&field=depth_max:double"
                "&field=depth_unit:string(16)"
                "&field=trace_layer_id:string(64)"
            )
            label_layer = QgsVectorLayer(uri, layer_name, "memory")
            if not label_layer.isValid():
                return None

            storage_mode = self._trace_vector_storage_mode()
            if storage_mode == "gpkg":
                persisted, _out_path, _err = self._persist_vector_layer_to_project_gpkg(
                    label_layer,
                    layer_name,
                    source_kind="vertex_depth_labels",
                )
                if persisted is not None:
                    label_layer = persisted

        # Backward-compatible schema update for old vertex layers.
        try:
            existing = {f.name() for f in label_layer.fields()}
            to_add = []
            if "depth_min" not in existing:
                from qgis.core import QgsField
                from qgis.PyQt.QtCore import QVariant
                to_add.append(QgsField("depth_min", QVariant.Double))
            if "depth_max" not in existing:
                from qgis.core import QgsField
                from qgis.PyQt.QtCore import QVariant
                to_add.append(QgsField("depth_max", QVariant.Double))
            if to_add:
                label_layer.dataProvider().addAttributes(to_add)
                label_layer.updateFields()
        except Exception:
            pass

        try:
            symbol = QgsMarkerSymbol.createSimple(
                {
                    "name": "circle",
                    "size": "1.8",
                    "color": "255,220,90,200",
                    "outline_color": "40,40,40,220",
                    "outline_width": "0.25",
                }
            )
            if symbol is not None and label_layer.renderer() is not None:
                label_layer.renderer().setSymbol(symbol)
        except Exception:
            pass

        self._configure_vertex_labeling(label_layer)
        try:
            label_layer.setLabelsEnabled(self._vertex_labels_enabled_for_mode())
        except Exception:
            pass

        # Avoid noisy warnings like:
        # "Relazione mancante nella configurazione" on trace_id field
        # by forcing a plain text editor widget on vertex metadata keys.
        try:
            for field_name in ("trace_id", "trace_fid", "vertex_idx", "trace_layer_id"):
                idx = label_layer.fields().indexOf(field_name)
                if idx < 0:
                    continue
                label_layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("TextEdit", {}))
        except Exception:
            pass

        set_layer_property(label_layer, "vertex_labels", "1")
        set_layer_property(label_layer, "trace_layer_id", str(source_layer_id))
        if QgsProject.instance().mapLayer(label_layer.id()) is None:
            QgsProject.instance().addMapLayer(label_layer, False)
            if hasattr(self, "_add_layer_to_trace_group_top"):
                self._add_layer_to_trace_group_top(label_layer)
            else:
                self._get_or_create_trace_group().addLayer(label_layer)
        # Keep label points visible by default when managed by plugin.
        try:
            root = QgsProject.instance().layerTreeRoot()
            node = root.findLayer(label_layer.id()) if root is not None else None
            if node is not None and hasattr(node, "setItemVisibilityChecked"):
                node.setItemVisibilityChecked(True)
            if hasattr(self, "_get_or_create_trace_group"):
                grp = self._get_or_create_trace_group()
                if grp is not None and hasattr(grp, "setItemVisibilityChecked"):
                    grp.setItemVisibilityChecked(True)
        except Exception:
            pass
        self._ensure_trace_vertex_relation(source_layer, label_layer)
        return label_layer

    def _sync_trace_vertex_depth_labels(self, layer=None, create_if_missing=False):
        source_layer = layer
        if source_layer is None:
            source_layer = self._current_trace_layer(prefer_active=True, require_trace=True)
        if not self._is_trace_layer(source_layer):
            return

        if create_if_missing:
            label_layer = self._ensure_trace_vertex_label_layer(source_layer)
        else:
            label_layer = self._find_trace_vertex_label_layer(source_layer)
        if label_layer is None:
            return

        provider = label_layer.dataProvider()
        if provider is None:
            return

        existing_ids = [f.id() for f in label_layer.getFeatures()]
        if existing_ids:
            try:
                provider.deleteFeatures(existing_ids)
            except Exception:
                pass

        new_features = []
        trace_id_idx = source_layer.fields().indexOf("trace_id")
        for feat in source_layer.getFeatures():
            geom = feat.geometry()
            vertices = self._iter_geometry_vertices_xy(geom)
            if not vertices:
                continue

            per_vertex = self._per_vertex_depth_map(source_layer, feat)
            depth_lbl, depth_val, depth_unit = self._depth_label_from_trace_feature(source_layer, feat)
            trace_id = feat.attribute(trace_id_idx) if trace_id_idx >= 0 else ""
            for vertex_idx, point_xy in enumerate(vertices, start=1):
                vertex_meta = per_vertex.get(vertex_idx) or {}
                current_lbl = vertex_meta.get("depth_lbl", depth_lbl)
                current_val = vertex_meta.get("depth_val", depth_val)
                current_min = vertex_meta.get("depth_min", None)
                current_max = vertex_meta.get("depth_max", None)
                current_unit = vertex_meta.get("depth_unit", depth_unit)
                row = QgsFeature(label_layer.fields())
                row.setGeometry(QgsGeometry.fromPointXY(point_xy))
                row.setAttribute("trace_fid", int(feat.id()))
                row.setAttribute("trace_id", trace_id or f"fid_{feat.id()}")
                row.setAttribute("vertex_idx", int(vertex_idx))
                row.setAttribute("depth_lbl", current_lbl)
                row.setAttribute("depth_val", current_val)
                if label_layer.fields().indexOf("depth_min") >= 0:
                    row.setAttribute("depth_min", current_min)
                if label_layer.fields().indexOf("depth_max") >= 0:
                    row.setAttribute("depth_max", current_max)
                row.setAttribute("depth_unit", current_unit)
                row.setAttribute("trace_layer_id", source_layer.id())
                new_features.append(row)

        if new_features:
            try:
                provider.addFeatures(new_features)
            except Exception:
                return
        label_layer.updateExtents()
        self._configure_vertex_labeling(label_layer)
        try:
            set_layer_property(label_layer, "vertex_labels", "1")
        except Exception:
            pass
        try:
            label_layer.setLabelsEnabled(self._vertex_labels_enabled_for_mode())
        except Exception:
            pass
        label_layer.triggerRepaint()
