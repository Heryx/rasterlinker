# -*- coding: utf-8 -*-
"""Trace vector storage mixin for RasterLinker plugin."""

import os.path

from qgis.PyQt.QtWidgets import QInputDialog
from qgis.core import QgsProject, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes

from .project_catalog import ensure_project_structure, register_vector_layer, sanitize_filename


class TraceStorageMixin:
    def _trace_vector_storage_mode(self):
        default = "memory"
        settings = getattr(self, "settings", None)
        if settings is None:
            return default
        key = self._settings_key("trace/vector_storage_mode") if hasattr(self, "_settings_key") else "RasterLinker/trace/vector_storage_mode"
        mode = str(settings.value(key, default) or default).strip().lower()
        return mode if mode in ("memory", "gpkg") else default

    def _set_trace_vector_storage_mode(self, mode):
        mode_txt = str(mode or "").strip().lower()
        if mode_txt not in ("memory", "gpkg"):
            return
        settings = getattr(self, "settings", None)
        if settings is None:
            return
        key = self._settings_key("trace/vector_storage_mode") if hasattr(self, "_settings_key") else "RasterLinker/trace/vector_storage_mode"
        settings.setValue(key, mode_txt)

    def _prompt_trace_vector_storage_mode(self, title="Create 2D Line Layer"):
        choices = [
            ("Temporary layer (memory)", "memory"),
            ("Persistent layer (GeoPackage in project folder)", "gpkg"),
        ]
        labels = [label for label, _mode in choices]
        current_mode = self._trace_vector_storage_mode()
        default_idx = 0
        for idx, (_label, mode_key) in enumerate(choices):
            if mode_key == current_mode:
                default_idx = idx
                break
        label, ok = QInputDialog.getItem(
            self._ui_parent(),
            title,
            "Storage:",
            labels,
            default_idx,
            False,
        )
        if not ok:
            return None
        selected_mode = next((mode for txt, mode in choices if txt == label), "memory")
        self._set_trace_vector_storage_mode(selected_mode)
        return selected_mode

    def _trace_vector_output_dir(self):
        project_root = self._require_project_root(notify=False) if hasattr(self, "_require_project_root") else None
        if not project_root:
            return None
        try:
            paths = ensure_project_structure(project_root)
            out_dir = paths.get("vector_layers")
        except Exception:
            out_dir = None
        if not out_dir:
            out_dir = os.path.join(project_root, "vector_layers")
            os.makedirs(out_dir, exist_ok=True)
        return out_dir

    def _unique_output_path(self, directory, filename):
        os.makedirs(directory, exist_ok=True)
        candidate = os.path.join(directory, filename)
        if not os.path.exists(candidate):
            return candidate
        stem, ext = os.path.splitext(filename)
        i = 1
        while True:
            candidate = os.path.join(directory, f"{stem}_{i:03d}{ext}")
            if not os.path.exists(candidate):
                return candidate
            i += 1

    def _persist_vector_layer_to_project_gpkg(self, source_layer, layer_name, source_kind="generic"):
        if source_layer is None or not source_layer.isValid():
            return None, None, "Invalid source layer."
        out_dir = self._trace_vector_output_dir()
        if not out_dir:
            return None, None, "No active RasterLinker project folder is linked."

        file_name = sanitize_filename(f"{layer_name}.gpkg")
        if not file_name.lower().endswith(".gpkg"):
            file_name += ".gpkg"
        output_path = self._unique_output_path(out_dir, file_name)

        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.fileEncoding = "UTF-8"
        opts.layerName = layer_name
        opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
        transform_context = QgsProject.instance().transformContext()
        result = QgsVectorFileWriter.writeAsVectorFormatV3(source_layer, output_path, transform_context, opts)
        err_code = result[0] if isinstance(result, tuple) else result
        err_msg = result[1] if isinstance(result, tuple) and len(result) > 1 else ""
        if err_code != QgsVectorFileWriter.NoError:
            return None, output_path, str(err_msg or err_code)

        loaded = QgsVectorLayer(f"{output_path}|layername={layer_name}", layer_name, "ogr")
        if not loaded.isValid():
            loaded = QgsVectorLayer(output_path, layer_name, "ogr")
        if not loaded.isValid():
            return None, output_path, "GeoPackage layer was written but cannot be loaded."

        loaded.setCustomProperty("rasterlinker/storage_mode", "gpkg")
        loaded.setCustomProperty("rasterlinker/storage_path", output_path)
        loaded.setCustomProperty("rasterlinker/source_kind", str(source_kind or "generic"))

        project_root = self._require_project_root(notify=False) if hasattr(self, "_require_project_root") else None
        if project_root:
            geom_map = {
                QgsWkbTypes.PointGeometry: "point",
                QgsWkbTypes.LineGeometry: "line",
                QgsWkbTypes.PolygonGeometry: "polygon",
            }
            try:
                geom_type = geom_map.get(loaded.geometryType(), "unknown")
            except Exception:
                geom_type = "unknown"
            try:
                is_3d = bool(QgsWkbTypes.hasZ(loaded.wkbType()))
            except Exception:
                is_3d = False
            crs_authid = loaded.crs().authid() if loaded.crs().isValid() else None
            vector_id = f"vector_{sanitize_filename(os.path.splitext(os.path.basename(output_path))[0])}"
            try:
                register_vector_layer(
                    project_root,
                    {
                        "id": vector_id,
                        "name": loaded.name(),
                        "layer_name": loaded.name(),
                        "project_path": output_path,
                        "source_path": "",
                        "geometry_type": geom_type,
                        "is_3d": is_3d,
                        "crs": crs_authid,
                        "storage_mode": "gpkg",
                        "source_kind": str(source_kind or "generic"),
                    },
                )
            except Exception:
                # Catalog registration should not block layer creation.
                pass
        return loaded, output_path, ""
