# -*- coding: utf-8 -*-
"""Trace labeling mixin for RasterLinker plugin."""

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsFeature,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)


class TraceLabelingMixin:
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
                return f"{d0:.2f}-{d1:.2f} {unit}", (d0 + d1) / 2.0, unit
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

    def _ensure_trace_vertex_label_layer(self, source_layer):
        if source_layer is None:
            return None
        source_layer_id = source_layer.id()
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
                continue
            try:
                if lyr.geometryType() != QgsWkbTypes.PointGeometry:
                    continue
            except Exception:
                continue
            if str(lyr.customProperty("rasterlinker_vertex_labels", "")) != "1":
                continue
            if str(lyr.customProperty("rasterlinker_trace_layer_id", "")) != str(source_layer_id):
                continue
            return lyr

        crs_authid = source_layer.crs().authid() if source_layer.crs().isValid() else "EPSG:4326"
        layer_name = f"{source_layer.name()} | Vertex depth"
        uri = (
            f"Point?crs={crs_authid}"
            "&field=trace_fid:int"
            "&field=trace_id:string(64)"
            "&field=vertex_idx:int"
            "&field=depth_lbl:string(64)"
            "&field=depth_val:double"
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

        try:
            pal = QgsPalLayerSettings()
            pal.enabled = True
            pal.fieldName = "depth_lbl"
            pal.placement = QgsPalLayerSettings.OverPoint
            txt = QgsTextFormat()
            txt.setSize(8)
            txt.setColor(QColor(25, 25, 25))
            buf = QgsTextBufferSettings()
            buf.setEnabled(True)
            buf.setSize(0.9)
            buf.setColor(QColor(255, 255, 255))
            txt.setBuffer(buf)
            pal.setFormat(txt)
            label_layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
            label_layer.setLabelsEnabled(True)
        except Exception:
            pass

        label_layer.setCustomProperty("rasterlinker_vertex_labels", "1")
        label_layer.setCustomProperty("rasterlinker_trace_layer_id", str(source_layer_id))
        QgsProject.instance().addMapLayer(label_layer, False)
        self._get_or_create_trace_group().addLayer(label_layer)
        return label_layer

    def _sync_trace_vertex_depth_labels(self, layer=None):
        source_layer = layer
        if source_layer is None:
            source_layer = self._current_trace_layer(prefer_active=True, require_trace=True)
        if not self._is_trace_layer(source_layer):
            return

        label_layer = self._ensure_trace_vertex_label_layer(source_layer)
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

            depth_lbl, depth_val, depth_unit = self._depth_label_from_trace_feature(source_layer, feat)
            trace_id = feat.attribute(trace_id_idx) if trace_id_idx >= 0 else ""
            for vertex_idx, point_xy in enumerate(vertices, start=1):
                row = QgsFeature(label_layer.fields())
                row.setGeometry(QgsGeometry.fromPointXY(point_xy))
                row.setAttribute("trace_fid", int(feat.id()))
                row.setAttribute("trace_id", trace_id or f"fid_{feat.id()}")
                row.setAttribute("vertex_idx", int(vertex_idx))
                row.setAttribute("depth_lbl", depth_lbl)
                row.setAttribute("depth_val", depth_val)
                row.setAttribute("depth_unit", depth_unit)
                row.setAttribute("trace_layer_id", source_layer.id())
                new_features.append(row)

        if new_features:
            try:
                provider.addFeatures(new_features)
            except Exception:
                return
        label_layer.updateExtents()
        label_layer.triggerRepaint()
