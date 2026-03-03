# -*- coding: utf-8 -*-
"""
GPR LAS volume import – integrates with the project catalog exactly like
timeslices: output goes to timeslices_2d/<group_name>/, slices are registered
in the catalog, and the existing dial / update_visibility_with_dial works
without any modification.
"""

import os

from qgis.PyQt.QtWidgets import (
    QMessageBox,
    QFileDialog,
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QDoubleSpinBox,
    QLineEdit,
    QDialogButtonBox,
    QLabel,
    QCheckBox,
)
from qgis.core import QgsProject


class GprVolumeMixin:

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
            if name.lower().endswith((".las", ".laz")):
                result.append(os.path.join(volumes_dir, name))
        return result

    # ------------------------------------------------------------------
    # Smart file picker (Case A/B/C)
    # ------------------------------------------------------------------

    def _pick_las_files_for_slicing(self) -> list:
        project_root = self._gpr_active_project_root()
        existing = self._gpr_volumes_in_project(project_root)
        volumes_dir = os.path.join(project_root, "volumes_3d") if project_root else ""

        # Case A – no project / no volumes_3d
        if not project_root or not os.path.isdir(volumes_dir):
            paths, _ = QFileDialog.getOpenFileNames(
                self.dlg, "Seleziona file LAS/LAZ", "",
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return paths or []

        # Case B – folder exists but empty
        if not existing:
            ans = QMessageBox.question(
                self.dlg, "Nessun volume nel progetto",
                f"Nessun file LAS/LAZ in:\n{volumes_dir}\n\n"
                "Importa prima un LAS dal Project Manager per catalogarlo.\n\n"
                "Vuoi comunque selezionare un file da disco adesso?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return []
            paths, _ = QFileDialog.getOpenFileNames(
                self.dlg, "Seleziona file LAS/LAZ", "",
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return paths or []

        # Case C – volumes present
        n = len(existing)
        preview = "\n".join(f"  \u2022 {os.path.basename(p)}" for p in existing[:10])
        if n > 10:
            preview += f"\n  \u2026 e altri {n - 10}"
        msg = QMessageBox(self.dlg)
        msg.setWindowTitle("Volumi nel progetto")
        msg.setText(
            f"Trovati {n} volume/i in:\n{volumes_dir}\n\n{preview}\n\n"
            "Usa un volume esistente o importa un file nuovo?"
        )
        msg.setIcon(QMessageBox.Question)
        btn_ex  = msg.addButton("\U0001f4c2  Usa volume dal progetto", QMessageBox.AcceptRole)
        btn_new = msg.addButton("\U0001f4e5  Importa file nuovo", QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Cancel)
        msg.exec_()
        clicked = msg.clickedButton()
        if clicked == btn_ex:
            paths, _ = QFileDialog.getOpenFileNames(
                self.dlg, "Seleziona volume", volumes_dir,
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return paths or []
        if clicked == btn_new:
            paths, _ = QFileDialog.getOpenFileNames(
                self.dlg, "Seleziona file LAS/LAZ", "",
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return paths or []
        return []

    # ------------------------------------------------------------------
    # Configuration dialog (Z range, step, resolution, radius, group name)
    # with optional pre-fill from saved sidecar
    # ------------------------------------------------------------------

    def _ask_slice_params(
        self,
        z_min_det: float,
        z_max_det: float,
        default_group: str = "",
        saved: dict | None = None,   # pre-fill from sidecar
        is_reslice: bool = False,
    ) -> dict | None:
        try:
            default_res  = float((self.dlg.lineEditDistanceX.text() or "").strip())
        except ValueError:
            default_res  = 0.10
        try:
            default_step = float((self.dlg.lineEditDistanceY.text() or "").strip())
        except ValueError:
            default_step = 0.05

        # Use saved params as starting point when re-slicing
        if saved:
            z_min_det  = saved.get("z_min",  z_min_det)
            z_max_det  = saved.get("z_max",  z_max_det)
            default_res  = saved.get("resolution",  default_res)
            default_step = saved.get("z_step", default_step)

        default_radius = saved.get("radius", default_res * (2 ** 0.5)) if saved else default_res * (2 ** 0.5)

        dlg = QDialog(self.dlg)
        dlg.setWindowTitle("Re-slice: modifica parametri" if is_reslice else "Configura slice LAS")
        dlg.setMinimumWidth(360)
        layout = QVBoxLayout(dlg)

        info = QLabel(
            ("Parametri precedenti caricati dal sidecar.\n" if is_reslice else "") +
            f"Range Z rilevato: [{z_min_det:.4f}, {z_max_det:.4f}] m"
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

        sp_zmin   = _spin(-9999, 9999, 4, 0.01, z_min_det)
        sp_zmax   = _spin(-9999, 9999, 4, 0.01, z_max_det)
        sp_step   = _spin(0.001, 1000, 4, 0.01, default_step)
        sp_res    = _spin(0.001, 1000, 4, 0.01, default_res)
        sp_radius = _spin(0.001, 1000, 4, 0.01, default_radius)

        le_group = QLineEdit()
        le_group.setText(saved.get("group_name", default_group) if saved else default_group)
        le_group.setPlaceholderText("Nome del gruppo (cartella output)")

        form.addRow("Z minimo (m):",        sp_zmin)
        form.addRow("Z massimo (m):",        sp_zmax)
        form.addRow("Step Z — dz (m):",     sp_step)
        form.addRow("Risoluzione XY (m):",  sp_res)
        form.addRow("Radius IDW (m):",       sp_radius)
        form.addRow("Nome gruppo:",           le_group)
        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec_() != QDialog.Accepted:
            return None

        return {
            "z_min":      sp_zmin.value(),
            "z_max":      sp_zmax.value(),
            "z_step":     sp_step.value(),
            "resolution": sp_res.value(),
            "radius":     sp_radius.value(),
            "group_name": le_group.text().strip() or default_group,
        }

    # ------------------------------------------------------------------
    # Catalog registration helpers
    # ------------------------------------------------------------------

    def _register_las_slices_in_catalog(
        self,
        project_root: str,
        group_name: str,
        slices: list,
        epsg: int | None,
        reslice: bool = False,
    ) -> str:
        """
        Register generated TIF slices as timeslice records in the project
        catalog, create (or reuse) a raster group, and assign them.
        Returns the group ID.
        """
        from .project_catalog import (
            load_catalog, save_catalog,
            create_raster_group, register_timeslices_batch,
            assign_timeslices_to_group, utc_now_iso,
        )

        crs_authid = f"EPSG:{epsg}" if epsg else None

        if reslice:
            # Remove old timeslice records that belonged to this group.
            catalog = load_catalog(project_root)
            grp = next(
                (g for g in catalog.get("raster_groups", [])
                 if (g.get("name") or "").strip().lower() == group_name.strip().lower()),
                None
            )
            if grp:
                old_ids = set(grp.get("timeslice_ids", []))
                catalog["timeslices"] = [
                    t for t in catalog.get("timeslices", [])
                    if t.get("id") not in old_ids
                ]
                grp["timeslice_ids"] = []
                save_catalog(project_root, catalog)

        group_record, _ = create_raster_group(project_root, group_name)
        group_id = group_record["id"]

        now = utc_now_iso()
        records = []
        for sl in slices:
            rec_id = f"timeslice_{now}_{sl['index']:04d}"
            records.append({
                "id":          rec_id,
                "name":        sl["name"],
                "project_path": sl["path"],
                "depth_from":  sl["z_from"],
                "depth_to":    sl["z_to"],
                "unit":        "m",
                "crs":         crs_authid,
                "z_source":    "las_volume",
                "imported_at": now,
            })

        register_timeslices_batch(project_root, records)
        assign_timeslices_to_group(
            project_root, group_id, [r["id"] for r in records]
        )
        return group_id

    # ------------------------------------------------------------------
    # Public: main import action
    # ------------------------------------------------------------------

    def import_las_as_slices(self):
        """Entry point for the 'Import LAS → Slice' button."""
        from .gpr_utils import check_laspy
        from .gpr_las_volume import get_z_range_chunked
        from .gpr_las_slicer import slice_las_to_tifs, save_slicer_params, load_slicer_params

        result_laspy = check_laspy()
        if not result_laspy.get("ok"):
            QMessageBox.critical(
                self.dlg, "laspy non trovato",
                str(result_laspy.get("error") or "Errore laspy"),
            )
            return

        file_paths = self._pick_las_files_for_slicing()
        if not file_paths:
            return

        project_root = self._gpr_active_project_root()
        if not project_root:
            QMessageBox.warning(
                self.dlg, "Nessun progetto attivo",
                "Nessun progetto attivo.\n"
                "Apri il Project Manager e crea/apri un progetto prima di importare.",
            )
            return

        epsg = QgsProject.instance().crs().postgisSrid() or None
        loaded_ok = 0
        failed    = 0

        for file_path in file_paths:
            base = os.path.splitext(os.path.basename(file_path))[0]
            # sanitize for use as folder name
            safe_base = "".join(
                c if c.isalnum() or c in "_-" else "_" for c in base
            ).strip("_") or "las_slices"

            # Check for existing sidecar (re-slice scenario)
            candidate_dir = os.path.join(
                project_root, "timeslices_2d", safe_base
            )
            saved_params = load_slicer_params(candidate_dir)
            is_reslice   = saved_params is not None

            # Read Z range
            try:
                z_min_det, z_max_det = get_z_range_chunked(file_path)
            except Exception as e:
                QMessageBox.warning(
                    self.dlg, "Errore lettura Z",
                    f"Impossibile leggere il range Z:\n{file_path}\n\n{e}",
                )
                failed += 1
                continue

            # Config dialog
            params = self._ask_slice_params(
                z_min_det, z_max_det,
                default_group=safe_base,
                saved=saved_params,
                is_reslice=is_reslice,
            )
            if params is None:
                continue

            group_name = params["group_name"] or safe_base
            output_dir = os.path.join(
                project_root, "timeslices_2d", group_name
            )
            os.makedirs(output_dir, exist_ok=True)

            # Generate slices
            try:
                slices = slice_las_to_tifs(
                    las_path   = file_path,
                    output_dir = output_dir,
                    resolution = params["resolution"],
                    z_step     = params["z_step"],
                    z_min      = params["z_min"],
                    z_max      = params["z_max"],
                    radius     = params["radius"],
                    epsg       = epsg,
                )
            except Exception as e:
                QMessageBox.critical(
                    self.dlg, "Errore generazione slice", str(e)
                )
                failed += 1
                continue

            if not slices:
                QMessageBox.warning(
                    self.dlg, "Nessuna slice generata",
                    f"Nessun punto trovato nel range Z scelto:\n"
                    f"[{params['z_min']:.4f}, {params['z_max']:.4f}] m",
                )
                failed += 1
                continue

            # Save sidecar for future re-slice
            save_slicer_params(output_dir, {
                "source_las":  file_path,
                "group_name":  group_name,
                "z_min":       params["z_min"],
                "z_max":       params["z_max"],
                "z_step":      params["z_step"],
                "resolution":  params["resolution"],
                "radius":      params["radius"],
                "epsg":        epsg,
                "n_slices":    len(slices),
            })

            # Register in catalog (same as timeslice import)
            try:
                self._register_las_slices_in_catalog(
                    project_root, group_name, slices, epsg,
                    reslice=is_reslice,
                )
            except Exception as e:
                QMessageBox.warning(
                    self.dlg, "Errore registrazione catalogo", str(e)
                )
                # slices are on disk — continue anyway

            # Refresh UI — same call used after every timeslice import
            if hasattr(self, "populate_group_list"):
                try:
                    self.populate_group_list()
                except Exception:
                    pass

            if hasattr(self, "_notify_info"):
                action = "Re-slice" if is_reslice else "Import LAS→Slice"
                self._notify_info(
                    f"{action} OK: gruppo '{group_name}', "
                    f"{len(slices)} slice, "
                    f"dz={params['z_step']:.3f} m, "
                    f"res={params['resolution']:.3f} m, "
                    f"radius={params['radius']:.3f} m.",
                    duration=12,
                )
            loaded_ok += 1

        if hasattr(self, "_notify_info"):
            self._notify_info(
                f"Import LAS→Slice completato. OK: {loaded_ok}, falliti: {failed}.",
                duration=8,
            )
