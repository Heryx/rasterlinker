# -*- coding: utf-8 -*-
"""GPR LAS volume import helpers."""

import os

from qgis.PyQt.QtWidgets import QMessageBox, QFileDialog
from qgis.core import QgsProject, QgsRasterLayer, QgsLayerTreeLayer


class GprVolumeMixin:

    # ------------------------------------------------------------------
    # Internal: resolve the active project's volumes_3d folder
    # ------------------------------------------------------------------

    def _gpr_active_project_root(self) -> str:
        """Return the active project root from QSettings, or empty string."""
        try:
            root = (
                self.settings.value(
                    self.settings_key_active_project, "", type=str
                )
                or ""
            ).strip()
            return root
        except Exception:
            return ""

    def _gpr_volumes_in_project(self, project_root: str) -> list:
        """Return sorted list of LAS/LAZ/COPC paths inside volumes_3d/."""
        if not project_root:
            return []
        volumes_dir = os.path.join(project_root, "volumes_3d")
        if not os.path.isdir(volumes_dir):
            return []
        result = []
        for name in sorted(os.listdir(volumes_dir)):
            low = name.lower()
            if low.endswith(".las") or low.endswith(".laz"):
                result.append(os.path.join(volumes_dir, name))
        return result

    # ------------------------------------------------------------------
    # Internal: smart file-picker for slicing
    # ------------------------------------------------------------------

    def _pick_las_files_for_slicing(self) -> list:
        """
        Smart file selection before slicing:

        Case A – no active project or volumes_3d/ missing:
            Open normal file picker.

        Case B – volumes_3d/ exists but is empty:
            Warn the user that no volumes are cataloged, then offer
            to open a normal file picker anyway.

        Case C – volumes_3d/ has LAS/LAZ files:
            Ask the user:
              [Usa volume dal progetto] → file picker starting inside volumes_3d/
              [Importa file nuovo]     → normal file picker anywhere on disk
              [Annulla]                → return []
        """
        project_root = self._gpr_active_project_root()
        existing = self._gpr_volumes_in_project(project_root)
        volumes_dir = os.path.join(project_root, "volumes_3d") if project_root else ""

        # --- Case A: no project / no volumes_3d folder -----------------
        if not project_root or not os.path.isdir(volumes_dir):
            file_paths, _ = QFileDialog.getOpenFileNames(
                self.dlg,
                "Seleziona file COPC/LAS/LAZ",
                "",
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return file_paths or []

        # --- Case B: volumes_3d/ exists but empty ----------------------
        if not existing:
            answer = QMessageBox.question(
                self.dlg,
                "Nessun volume nel progetto",
                (
                    f"Nessun file LAS/LAZ trovato in:\n"
                    f"{volumes_dir}\n\n"
                    "Importa prima un LAS/LAZ dal Project Manager \n"
                    "(Import LAS/LAZ) per catalogarlo nel progetto.\n\n"
                    "Vuoi comunque selezionare un file da disco adesso?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return []
            file_paths, _ = QFileDialog.getOpenFileNames(
                self.dlg,
                "Seleziona file COPC/LAS/LAZ",
                "",
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return file_paths or []

        # --- Case C: volumes_3d/ has files -----------------------------
        n = len(existing)
        vol_preview = "\n".join(
            f"  \u2022 {os.path.basename(p)}" for p in existing[:10]
        )
        if n > 10:
            vol_preview += f"\n  \u2026 e altri {n - 10}"

        msg = QMessageBox(self.dlg)
        msg.setWindowTitle("Volumi disponibili nel progetto")
        msg.setText(
            f"Trovati {n} volume/i in:\n"
            f"{volumes_dir}\n\n"
            f"{vol_preview}\n\n"
            "Vuoi usare un volume esistente oppure importarne uno nuovo?"
        )
        msg.setIcon(QMessageBox.Question)
        btn_existing = msg.addButton(
            "\U0001f4c2  Usa volume dal progetto", QMessageBox.AcceptRole
        )
        btn_new = msg.addButton(
            "\U0001f4e5  Importa file nuovo", QMessageBox.ActionRole
        )
        msg.addButton(QMessageBox.Cancel)
        msg.exec_()

        clicked = msg.clickedButton()

        if clicked == btn_existing:
            file_paths, _ = QFileDialog.getOpenFileNames(
                self.dlg,
                "Seleziona volume da volumes_3d",
                volumes_dir,
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return file_paths or []

        if clicked == btn_new:
            file_paths, _ = QFileDialog.getOpenFileNames(
                self.dlg,
                "Seleziona file COPC/LAS/LAZ",
                "",
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return file_paths or []

        return []  # Cancel

    # ------------------------------------------------------------------
    # Public: main import action
    # ------------------------------------------------------------------

    def import_las_as_slices(self):
        from .gpr_utils import check_pdal, check_laspy

        result = check_pdal()
        if not result.get("ok"):
            QMessageBox.critical(
                self.dlg,
                "PDAL non trovato",
                str(result.get("error") or "Errore PDAL"),
            )
            return
        result_laspy = check_laspy()
        if not result_laspy.get("ok"):
            QMessageBox.critical(
                self.dlg,
                "laspy non trovato",
                str(result_laspy.get("error") or "Errore laspy"),
            )
            return

        from .gpr_las_volume import get_z_range_chunked
        from .gpr_slice_generator import generate_slices_pdal

        # Smart file selection (checks volumes_3d in active project)
        file_paths = self._pick_las_files_for_slicing()
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

        group_name = (
            (self.dlg.lineEditAreaNames.text() or "").strip() or "GPR_Slices"
        )

        total_valid_loaded = 0
        processed_files = 0
        failed_files = 0
        last_z_min = None

        root = QgsProject.instance().layerTreeRoot()

        for file_path in file_paths:
            output_dir = os.path.join(
                os.path.dirname(file_path), f"{group_name}_tifs"
            )

            try:
                z_min, z_max = get_z_range_chunked(
                    file_path, chunk_size=200_000
                )
            except Exception as e:
                failed_files += 1
                if hasattr(self, "_notify_info"):
                    self._notify_info(
                        f"Skip {os.path.basename(file_path)}: {e}",
                        duration=8,
                    )
                continue

            epsg = QgsProject.instance().crs().postgisSrid()
            if epsg == 0:
                epsg = None

            if hasattr(self, "_notify_info"):
                self._notify_info(
                    (
                        f"LAS {os.path.basename(file_path)} | "
                        f"Z[{z_min:.3f}, {z_max:.3f}] | "
                        f"dz={z_step:.3f} m | res={resolution:.3f} m"
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
                    self._notify_info(
                        f"PDAL failed on {os.path.basename(file_path)}: {e}",
                        duration=10,
                    )
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
                layer_nodes = [
                    c
                    for c in target_group.children()
                    if isinstance(c, QgsLayerTreeLayer)
                ]
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
                    f"Import LAS\u2192Slice completato. "
                    f"File OK: {processed_files}, "
                    f"file falliti: {failed_files}, "
                    f"raster caricati: {total_valid_loaded}."
                ),
                duration=12,
            )
