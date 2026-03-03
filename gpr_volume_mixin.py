# -*- coding: utf-8 -*-
"""GPR LAS volume import: NetCDF UGRID mesh (primary) or multiband GeoTIFF (fallback)."""

import os

from qgis.PyQt.QtWidgets import (
    QMessageBox,
    QFileDialog,
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QDoubleSpinBox,
    QDialogButtonBox,
    QLabel,
)
from qgis.core import (
    QgsProject,
    QgsMeshLayer,
    QgsRasterLayer,
    QgsSingleBandGrayRenderer,
    QgsContrastEnhancement,
)


class GprVolumeMixin:

    # ------------------------------------------------------------------
    # State initialisation (called by geosurvey_studio.py constructor)
    # ------------------------------------------------------------------

    # _gpr_mesh_layer    : QgsMeshLayer | None
    # _gpr_raster_layer  : QgsRasterLayer | None
    # _gpr_z_levels      : list[float]
    # _gpr_use_mesh      : bool

    # ------------------------------------------------------------------
    # Internal helpers: project root & existing volumes
    # ------------------------------------------------------------------

    def _gpr_active_project_root(self) -> str:
        try:
            return (
                self.settings.value(
                    self.settings_key_active_project, "", type=str
                ) or ""
            ).strip()
        except Exception:
            return ""

    def _gpr_volumes_in_project(self, project_root: str) -> list:
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
    # Smart file picker: checks volumes_3d of active project first
    # ------------------------------------------------------------------

    def _pick_las_files_for_slicing(self) -> list:
        """
        Case A – no active project / missing volumes_3d  → plain file picker.
        Case B – volumes_3d exists but empty             → warn + optional picker.
        Case C – volumes_3d has files                    → ask existing/new/cancel.
        """
        project_root = self._gpr_active_project_root()
        existing = self._gpr_volumes_in_project(project_root)
        volumes_dir = os.path.join(project_root, "volumes_3d") if project_root else ""

        # Case A
        if not project_root or not os.path.isdir(volumes_dir):
            paths, _ = QFileDialog.getOpenFileNames(
                self.dlg, "Seleziona file COPC/LAS/LAZ", "",
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return paths or []

        # Case B
        if not existing:
            ans = QMessageBox.question(
                self.dlg, "Nessun volume nel progetto",
                f"Nessun file LAS/LAZ trovato in:\n{volumes_dir}\n\n"
                "Importa prima un LAS/LAZ dal Project Manager per catalogarlo.\n\n"
                "Vuoi comunque selezionare un file da disco adesso?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return []
            paths, _ = QFileDialog.getOpenFileNames(
                self.dlg, "Seleziona file COPC/LAS/LAZ", "",
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return paths or []

        # Case C
        n = len(existing)
        preview = "\n".join(f"  \u2022 {os.path.basename(p)}" for p in existing[:10])
        if n > 10:
            preview += f"\n  \u2026 e altri {n - 10}"

        msg = QMessageBox(self.dlg)
        msg.setWindowTitle("Volumi disponibili nel progetto")
        msg.setText(
            f"Trovati {n} volume/i in:\n{volumes_dir}\n\n{preview}\n\n"
            "Vuoi usare un volume esistente oppure importarne uno nuovo?"
        )
        msg.setIcon(QMessageBox.Question)
        btn_existing = msg.addButton("\U0001f4c2  Usa volume dal progetto", QMessageBox.AcceptRole)
        btn_new = msg.addButton("\U0001f4e5  Importa file nuovo", QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Cancel)
        msg.exec_()
        clicked = msg.clickedButton()

        if clicked == btn_existing:
            paths, _ = QFileDialog.getOpenFileNames(
                self.dlg, "Seleziona volume da volumes_3d", volumes_dir,
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return paths or []
        if clicked == btn_new:
            paths, _ = QFileDialog.getOpenFileNames(
                self.dlg, "Seleziona file COPC/LAS/LAZ", "",
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return paths or []
        return []

    # ------------------------------------------------------------------
    # Z range / resolution dialog
    # ------------------------------------------------------------------

    def _ask_z_and_resolution(self, z_min_det: float, z_max_det: float) -> dict | None:
        """
        Ask the user to confirm or adjust Z min/max, Z step and XY resolution.
        Returns dict with keys z_min, z_max, z_step, resolution, or None if cancelled.
        """
        try:
            default_res = float((self.dlg.lineEditDistanceX.text() or "").strip())
        except ValueError:
            default_res = 0.10
        try:
            default_step = float((self.dlg.lineEditDistanceY.text() or "").strip())
        except ValueError:
            default_step = 0.05

        dlg = QDialog(self.dlg)
        dlg.setWindowTitle("Configura volume GPR")
        dlg.setMinimumWidth(340)
        layout = QVBoxLayout(dlg)

        info = QLabel(
            f"Range Z rilevato nel file:\n"
            f"  Z min = {z_min_det:.4f} m\n"
            f"  Z max = {z_max_det:.4f} m\n\n"
            "Modifica i parametri se necessario:"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()

        def _spin(lo, hi, dec, step, val):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(dec)
            s.setSingleStep(step)
            s.setValue(val)
            return s

        sp_zmin = _spin(-9999, 9999, 4, 0.01, z_min_det)
        sp_zmax = _spin(-9999, 9999, 4, 0.01, z_max_det)
        sp_step = _spin(0.001, 1000, 4, 0.01, default_step)
        sp_res  = _spin(0.001, 1000, 4, 0.01, default_res)

        form.addRow("Z minimo (m):", sp_zmin)
        form.addRow("Z massimo (m):", sp_zmax)
        form.addRow("Step Z — dz (m):", sp_step)
        form.addRow("Risoluzione XY (m):", sp_res)
        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec_() != QDialog.Accepted:
            return None

        return {
            "z_min": sp_zmin.value(),
            "z_max": sp_zmax.value(),
            "z_step": sp_step.value(),
            "resolution": sp_res.value(),
        }

    # ------------------------------------------------------------------
    # Dial setup & handler
    # ------------------------------------------------------------------

    def _setup_gpr_dial(self, n_z: int, z_step: float, z_min: float) -> None:
        """Configure dials and connect the GPR-specific handler."""
        self._gpr_z_step = float(z_step)
        self._gpr_z_min = float(z_min)
        self._gpr_n_slices = int(n_z)

        for ctrl_name in ("Dial", "dial2"):
            ctrl = getattr(self.dlg, ctrl_name, None)
            if ctrl is None:
                continue
            ctrl.setMinimum(0)
            ctrl.setMaximum(max(0, n_z - 1))
            ctrl.setValue(0)
            # Add GPR handler as an ADDITIONAL connection (does not replace the
            # existing update_visibility_with_dial connection in app_runtime_mixin).
            try:
                ctrl.valueChanged.disconnect(self._on_gpr_dial_changed)
            except Exception:
                pass
            ctrl.valueChanged.connect(self._on_gpr_dial_changed)

    def _on_gpr_dial_changed(self, value: int) -> None:
        """Switch the visible Z level on the mesh or raster layer."""
        use_mesh = getattr(self, "_gpr_use_mesh", False)
        z_levels = getattr(self, "_gpr_z_levels", [])

        # --- Mesh path (NetCDF UGRID) ------------------------------------
        if use_mesh:
            layer = getattr(self, "_gpr_mesh_layer", None)
            if layer is not None and layer.isValid():
                renderer = layer.rendererSettings()
                renderer.setActiveScalarDatasetGroup(value)
                layer.setRendererSettings(renderer)
                layer.triggerRepaint()
                # Update dock title bar
                if z_levels and 0 <= value < len(z_levels):
                    z = z_levels[value]
                    z_step = getattr(self, "_gpr_z_step", 0.0)
                    res = getattr(self, "_gpr_res", 0.0)
                    try:
                        self.dlg.nameRasterLabel.setText(
                            f"Z = {z:.4f} m  (\u00b1{z_step/2:.4f} m)"
                        )
                    except Exception:
                        pass
            return

        # --- Raster fallback (multiband GeoTIFF) -----------------------
        layer = getattr(self, "_gpr_raster_layer", None)
        if layer is not None and layer.isValid():
            band = value + 1  # QgsRasterLayer bands are 1-based
            renderer = QgsSingleBandGrayRenderer(layer.dataProvider(), band)
            ce = QgsContrastEnhancement(layer.dataProvider().dataType(band))
            ce.setContrastEnhancementAlgorithm(
                QgsContrastEnhancement.StretchToMinimumMaximum
            )
            renderer.setContrastEnhancement(ce)
            layer.setRenderer(renderer)
            layer.triggerRepaint()

    # ------------------------------------------------------------------
    # Public: main import action (called by "Import LAS → Slice" button)
    # ------------------------------------------------------------------

    def import_las_as_slices(self):
        """Entry point for the 'Import LAS → Slice' button."""
        from .gpr_utils import check_laspy, check_netcdf4
        from .gpr_las_volume import get_z_range_chunked

        # 1. laspy is always required (reads the LAS file)
        result_laspy = check_laspy()
        if not result_laspy.get("ok"):
            QMessageBox.critical(
                self.dlg, "laspy non trovato",
                str(result_laspy.get("error") or "Errore laspy"),
            )
            return

        # 2. Smart file picker
        file_paths = self._pick_las_files_for_slicing()
        if not file_paths:
            return

        # 3. netCDF4 check (soft — fallback to multiband TIF if missing)
        result_nc = check_netcdf4()
        can_use_mesh = result_nc.get("ok", False)
        if not can_use_mesh:
            QMessageBox.information(
                self.dlg, "netCDF4 non disponibile",
                "netCDF4 non è installato.\n"
                "Verrà usato il formato fallback (multiband GeoTIFF).\n\n"
                + str(result_nc.get("error", "")) + "\n\n"
                "Installa netCDF4 da OSGeo4W Shell per abilitare il layer mesh UGRID:\n"
                "  pip install netCDF4",
            )

        # 4. Process each file
        epsg = QgsProject.instance().crs().postgisSrid() or None
        crs_wkt = ""
        if epsg:
            try:
                from qgis.core import QgsCoordinateReferenceSystem
                crs_wkt = QgsCoordinateReferenceSystem(f"EPSG:{epsg}").toWkt()
            except Exception:
                crs_wkt = ""

        loaded_ok = 0
        failed = 0

        for file_path in file_paths:
            base = os.path.splitext(os.path.basename(file_path))[0]

            # Read Z range
            try:
                z_min_det, z_max_det = get_z_range_chunked(file_path)
            except Exception as e:
                QMessageBox.warning(
                    self.dlg, "Errore lettura Z",
                    f"Impossibile leggere il range Z da:\n{file_path}\n\nErrore: {e}",
                )
                failed += 1
                continue

            # Ask user for Z config
            params = self._ask_z_and_resolution(z_min_det, z_max_det)
            if params is None:
                return  # user cancelled

            z_min = params["z_min"]
            z_max = params["z_max"]
            z_step = params["z_step"]
            resolution = params["resolution"]

            layer_name = (
                f"LAS {base} | "
                f"Z[{z_min:.3f}, {z_max:.3f}] | "
                f"dz={z_step:.3f} m | res={resolution:.3f} m"
            )

            # --- Try primary: NetCDF UGRID mesh ---
            if can_use_mesh:
                output_nc = os.path.join(
                    os.path.dirname(file_path), f"{base}_mesh.nc"
                )
                try:
                    from .gpr_netcdf_exporter import las_to_netcdf_mesh
                    meta = las_to_netcdf_mesh(
                        file_path, output_nc, resolution, z_step,
                        z_min, z_max, epsg, crs_wkt or None,
                    )
                except Exception as e:
                    QMessageBox.critical(
                        self.dlg, "Errore esportazione NetCDF", str(e)
                    )
                    failed += 1
                    continue

                mesh_layer = QgsMeshLayer(output_nc, layer_name, "mdal")
                if mesh_layer.isValid():
                    QgsProject.instance().addMapLayer(mesh_layer)
                    # Activate first dataset group (Z_0000)
                    rset = mesh_layer.rendererSettings()
                    rset.setActiveScalarDatasetGroup(0)
                    mesh_layer.setRendererSettings(rset)

                    self._gpr_mesh_layer = mesh_layer
                    self._gpr_raster_layer = None
                    self._gpr_use_mesh = True
                    self._gpr_z_levels = meta["z_levels"]
                    self._gpr_res = resolution
                    self._setup_gpr_dial(meta["n_z"], z_step, z_min)

                    if hasattr(self, "populate_group_list"):
                        try:
                            self.populate_group_list()
                        except Exception:
                            pass

                    if hasattr(self, "_notify_info"):
                        self._notify_info(
                            f"LAS\u2192Mesh OK: {meta['n_z']} livelli Z, "
                            f"{meta['n_x']}\u00d7{meta['n_y']} celle.",
                            duration=10,
                        )
                    loaded_ok += 1
                    continue

                # mesh layer not valid → fall through to GeoTIFF
                QMessageBox.warning(
                    self.dlg, "Mesh layer non valido",
                    f"MDAL non ha accettato il NetCDF generato:\n{output_nc}\n\n"
                    "Uso il fallback multiband GeoTIFF.",
                )

            # --- Fallback: multiband GeoTIFF ---
            output_tif = os.path.join(
                os.path.dirname(file_path), f"{base}_slices.tif"
            )
            try:
                from .gpr_netcdf_exporter import las_to_multiband_tif
                meta = las_to_multiband_tif(
                    file_path, output_tif, resolution, z_step,
                    z_min, z_max, epsg,
                )
            except Exception as e:
                QMessageBox.critical(
                    self.dlg, "Errore esportazione GeoTIFF", str(e)
                )
                failed += 1
                continue

            raster_layer = QgsRasterLayer(output_tif, layer_name)
            if not raster_layer.isValid():
                QMessageBox.warning(
                    self.dlg, "Layer raster non valido",
                    f"Impossibile caricare il GeoTIFF:\n{output_tif}",
                )
                failed += 1
                continue

            QgsProject.instance().addMapLayer(raster_layer)
            self._gpr_raster_layer = raster_layer
            self._gpr_mesh_layer = None
            self._gpr_use_mesh = False
            self._gpr_z_levels = meta["z_levels"]
            self._gpr_res = resolution
            self._setup_gpr_dial(meta["n_z"], z_step, z_min)
            # Apply stretch on band 1
            self._on_gpr_dial_changed(0)

            if hasattr(self, "_notify_info"):
                self._notify_info(
                    f"LAS\u2192GeoTIFF OK (fallback): {meta['n_z']} bande.",
                    duration=10,
                )
            loaded_ok += 1

        if hasattr(self, "_notify_info"):
            self._notify_info(
                f"Import LAS\u2192Slice completato. OK: {loaded_ok}, falliti: {failed}.",
                duration=8,
            )
