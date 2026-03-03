# -*- coding: utf-8 -*-
"""GPR LAS volume import helpers."""

import os

from qgis.PyQt.QtWidgets import QMessageBox, QFileDialog
from qgis.core import QgsProject, QgsRasterLayer, QgsLayerTreeLayer


class GprVolumeMixin:
    def import_las_as_slices(self):
        from .gpr_utils import check_pdal, check_laspy

        result = check_pdal()
        if not result.get("ok"):
            QMessageBox.critical(self.dlg, "PDAL non trovato", str(result.get("error") or "Errore PDAL"))
            return
        result_laspy = check_laspy()
        if not result_laspy.get("ok"):
            QMessageBox.critical(self.dlg, "laspy non trovato", str(result_laspy.get("error") or "Errore laspy"))
            return

        from .gpr_las_volume import get_z_range_chunked
        from .gpr_slice_generator import generate_slices_pdal

        file_paths, _ = QFileDialog.getOpenFileNames(
            self.dlg,
            "Seleziona file COPC/LAS/LAZ",
            "",
            "Point Cloud (*.copc.laz *.las *.laz)",
        )
        if not file_paths:
            return

        try:
            resolution = float((self.dlg.lineEditDistanceX.text() or "").strip())
        except ValueError:
            resolution = 0.10

        try:
            z_step = float((self.dlg.lineEditDistanceY.text() or "").strip())
        except ValueError:
            z_step = 0.05

        group_name = (self.dlg.lineEditAreaNames.text() or "").strip() or "GPR_Slices"

        total_valid_loaded = 0
        processed_files = 0
        failed_files = 0
        last_z_min = None

        root = QgsProject.instance().layerTreeRoot()

        for file_path in file_paths:
            output_dir = os.path.join(os.path.dirname(file_path), f"{group_name}_tifs")

            try:
                z_min, z_max = get_z_range_chunked(file_path, chunk_size=200_000)
            except Exception as e:
                failed_files += 1
                if hasattr(self, "_notify_info"):
                    self._notify_info(f"Skip {os.path.basename(file_path)}: {e}", duration=8)
                continue

            epsg = QgsProject.instance().crs().postgisSrid()
            if epsg == 0:
                epsg = None

            if hasattr(self, "_notify_info"):
                self._notify_info(
                    (
                        f"LAS {os.path.basename(file_path)} | "
                        f"Z[{z_min:.3f}, {z_max:.3f}] | dz={z_step:.3f} m | res={resolution:.3f} m"
                    ),
                    duration=20,
                )

            try:
                tif_paths = generate_slices_pdal(
                    copc_path=file_path,
                    output_dir=output_dir,
                    z_min=z_min,
                    z_max=z_max,
                    z_step=z_step,
                    resolution=resolution,
                    output_type="mean",
                    crs_epsg=epsg,
                )
            except Exception as e:
                failed_files += 1
                if hasattr(self, "_notify_info"):
                    self._notify_info(f"PDAL failed on {os.path.basename(file_path)}: {e}", duration=10)
                continue

            target_group = next(
                (
                    g
                    for g in root.children()
                    if hasattr(g, "name") and g.name() == group_name
                ),
                None,
            )
            if target_group is None:
                target_group = root.addGroup(group_name)

            valid_added_this_file = 0
            for tif_path in tif_paths:
                layer_name = os.path.basename(tif_path)
                raster_layer = QgsRasterLayer(tif_path, layer_name)
                if not raster_layer.isValid():
                    continue
                QgsProject.instance().addMapLayer(raster_layer, False)
                target_group.addLayer(raster_layer)
                valid_added_this_file += 1
                total_valid_loaded += 1

            if valid_added_this_file > 0:
                layer_nodes = [c for c in target_group.children() if isinstance(c, QgsLayerTreeLayer)]
                for idx, child in enumerate(layer_nodes):
                    child.setItemVisibilityChecked(idx == 0)
                processed_files += 1
                last_z_min = z_min
            else:
                failed_files += 1

        n = int(total_valid_loaded)
        self._gpr_z_min = float(last_z_min) if last_z_min is not None else 0.0
        self._gpr_z_step = float(z_step)
        self._gpr_n_slices = n
        self._gpr_pc_layer = None

        for control_name in ("Dial", "dial2"):
            control = getattr(self.dlg, control_name, None)
            if control is None:
                continue
            control.setMinimum(0)
            control.setMaximum(max(0, n - 1))
            control.setValue(0)

        if hasattr(self, "populate_group_list"):
            try:
                self.populate_group_list()
            except Exception:
                pass

        if hasattr(self, "_notify_info"):
            self._notify_info(
                (
                    f"Import LAS->Slice completato. File OK: {processed_files}, "
                    f"file falliti: {failed_files}, raster caricati: {total_valid_loaded}."
                ),
                duration=12,
            )
