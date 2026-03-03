# -*- coding: utf-8 -*-
"""
GPR LAS volume import – integra con il catalogo esattamente come le timeslice.
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
    QComboBox,
    QDialogButtonBox,
    QLabel,
)
from qgis.core import QgsProject


class GprVolumeMixin:

    # ------------------------------------------------------------------
    # Internal helpers
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
        return [
            os.path.join(volumes_dir, n)
            for n in sorted(os.listdir(volumes_dir))
            if n.lower().endswith((".las", ".laz"))
        ]

    # ------------------------------------------------------------------
    # Smart file picker
    # ------------------------------------------------------------------

    def _pick_las_files_for_slicing(self) -> list:
        project_root = self._gpr_active_project_root()
        existing     = self._gpr_volumes_in_project(project_root)
        volumes_dir  = os.path.join(project_root, "volumes_3d") if project_root else ""

        if not project_root or not os.path.isdir(volumes_dir):
            paths, _ = QFileDialog.getOpenFileNames(
                self.dlg, "Seleziona file LAS/LAZ", "",
                "Point Cloud (*.copc.laz *.las *.laz)",
            )
            return paths or []

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
        btn_new = msg.addButton("\U0001f4e5  Importa file nuovo",      QMessageBox.ActionRole)
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
    # Configuration dialog
    # ------------------------------------------------------------------

    def _ask_slice_params(
        self,
        z_min_det: float,
        z_max_det: float,
        default_group: str = "",
        saved: dict | None = None,
        is_reslice: bool = False,
        field_info: dict | None = None,   # from diagnose_las_fields()
    ) -> dict | None:
        try:
            default_res  = float((self.dlg.lineEditDistanceX.text() or "").strip())
        except ValueError:
            default_res  = 0.10
        try:
            default_step = float((self.dlg.lineEditDistanceY.text() or "").strip())
        except ValueError:
            default_step = 0.05

        if saved:
            z_min_det    = saved.get("z_min",       z_min_det)
            z_max_det    = saved.get("z_max",       z_max_det)
            default_res  = saved.get("resolution",  default_res)
            default_step = saved.get("z_step",      default_step)
        default_radius = saved.get("radius", default_res * 2 ** 0.5) if saved else default_res * 2 ** 0.5

        # --- build field list from diagnosis ---
        fi = field_info or {}
        available_fields = fi.get("available", [])   # [(name, min, max, has_data)]
        suggested_field  = fi.get("suggested", "intensity")
        if saved and saved.get("value_field"):
            suggested_field = saved["value_field"]

        # Build display labels: "intensity  [0 – 255]  ✓" style
        field_names   = []
        field_labels  = []
        for name, mn, mx, has_data in available_fields:
            label = f"{name}   [{mn:.0f} – {mx:.0f}]"
            if has_data:
                label = "\u2713 " + label
            else:
                label = "\u2610 " + label
            field_names.append(name)
            field_labels.append(label)

        # Always include intensity even if not in diagnosis
        if "intensity" not in field_names:
            field_names.insert(0, "intensity")
            field_labels.insert(0, "\u2610 intensity   [?]")

        # --- build dialog ---
        dlg = QDialog(self.dlg)
        dlg.setWindowTitle("Re-slice: modifica parametri" if is_reslice else "Configura slice LAS")
        dlg.setMinimumWidth(400)
        layout = QVBoxLayout(dlg)

        # Header info
        n_points = fi.get("n_points", 0)
        pf_id    = fi.get("point_format", -1)
        info_text = (
            ("Parametri precedenti caricati.\n" if is_reslice else "") +
            f"Range Z rilevato: [{z_min_det:.4f}, {z_max_det:.4f}] m"
        )
        if n_points > 0:
            info_text += f"\nPunti: {n_points:,}   |   Point format: {pf_id}"
        info_lbl = QLabel(info_text)
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

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

        # Field selector
        cb_field = QComboBox()
        for lbl in field_labels:
            cb_field.addItem(lbl)
        # Pre-select suggested
        try:
            cb_field.setCurrentIndex(field_names.index(suggested_field))
        except ValueError:
            pass

        le_group = QLineEdit()
        le_group.setText(saved.get("group_name", default_group) if saved else default_group)
        le_group.setPlaceholderText("Nome del gruppo (cartella output)")

        form.addRow("Campo valori:",       cb_field)
        form.addRow("Z minimo (m):",       sp_zmin)
        form.addRow("Z massimo (m):",      sp_zmax)
        form.addRow("Step Z — dz (m):",    sp_step)
        form.addRow("Risoluzione XY (m):", sp_res)
        form.addRow("Radius IDW (m):",      sp_radius)
        form.addRow("Nome gruppo:",          le_group)
        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec_() != QDialog.Accepted:
            return None

        chosen_idx   = cb_field.currentIndex()
        chosen_field = field_names[chosen_idx] if 0 <= chosen_idx < len(field_names) else "intensity"

        return {
            "z_min":       sp_zmin.value(),
            "z_max":       sp_zmax.value(),
            "z_step":      sp_step.value(),
            "resolution":  sp_res.value(),
            "radius":      sp_radius.value(),
            "group_name":  le_group.text().strip() or default_group,
            "value_field": chosen_field,
        }

    # ------------------------------------------------------------------
    # Catalog registration
    # ------------------------------------------------------------------

    def _register_las_slices_in_catalog(
        self,
        project_root: str,
        group_name: str,
        slices: list,
        epsg: int | None,
        reslice: bool = False,
    ) -> str:
        from .project_catalog import (
            load_catalog, save_catalog,
            create_raster_group, register_timeslices_batch,
            assign_timeslices_to_group, utc_now_iso,
        )

        if reslice:
            catalog = load_catalog(project_root)
            grp = next(
                (g for g in catalog.get("raster_groups", [])
                 if (g.get("name") or "").strip().lower() == group_name.strip().lower()),
                None,
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
        now      = utc_now_iso()
        crs_auth = f"EPSG:{epsg}" if epsg else None

        records = [
            {
                "id":           f"timeslice_{now}_{sl['index']:04d}",
                "name":         sl["name"],
                "project_path": sl["path"],
                "depth_from":   sl["z_from"],
                "depth_to":     sl["z_to"],
                "unit":         "m",
                "crs":          crs_auth,
                "z_source":     "las_volume",
                "imported_at":  now,
            }
            for sl in slices
        ]

        register_timeslices_batch(project_root, records)
        assign_timeslices_to_group(project_root, group_id, [r["id"] for r in records])
        return group_id

    # ------------------------------------------------------------------
    # Public: main import action
    # ------------------------------------------------------------------

    def import_las_as_slices(self):
        """Entry point for the 'Import LAS \u2192 Slice' button."""
        from .gpr_utils import check_laspy
        from .gpr_las_volume import get_z_range_chunked
        from .gpr_las_slicer import (
            slice_las_to_tifs, save_slicer_params, load_slicer_params,
            diagnose_las_fields,
        )

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
                "Apri il Project Manager e crea/apri un progetto prima di importare.",
            )
            return

        epsg      = QgsProject.instance().crs().postgisSrid() or None
        loaded_ok = 0
        failed    = 0

        for file_path in file_paths:
            base = os.path.splitext(os.path.basename(file_path))[0]
            safe_base = "".join(
                c if c.isalnum() or c in "_-" else "_" for c in base
            ).strip("_") or "las_slices"

            # Check for sidecar (re-slice)
            candidate_dir = os.path.join(project_root, "timeslices_2d", safe_base)
            saved_params  = load_slicer_params(candidate_dir)
            is_reslice    = saved_params is not None

            # Step 1: quick field diagnosis (reads only first 30k points)
            if hasattr(self, "_notify_info"):
                self._notify_info("Analisi campi del file LAS in corso\u2026", duration=5)
            try:
                field_info = diagnose_las_fields(file_path)
            except Exception:
                field_info = {}

            # Step 2: read Z range
            try:
                z_min_det, z_max_det = get_z_range_chunked(file_path)
            except Exception as e:
                QMessageBox.warning(
                    self.dlg, "Errore lettura Z",
                    f"Impossibile leggere il range Z:\n{file_path}\n\n{e}",
                )
                failed += 1
                continue

            # Step 3: config dialog (with field selector)
            params = self._ask_slice_params(
                z_min_det, z_max_det,
                default_group=safe_base,
                saved=saved_params,
                is_reslice=is_reslice,
                field_info=field_info,
            )
            if params is None:
                continue

            group_name = params["group_name"] or safe_base
            output_dir = os.path.join(project_root, "timeslices_2d", group_name)
            os.makedirs(output_dir, exist_ok=True)

            # Step 4: generate slice TIFs
            if hasattr(self, "_notify_info"):
                self._notify_info(
                    f"Generazione slice in corso\u2026 "
                    f"campo='{params['value_field']}', "
                    f"dz={params['z_step']:.3f} m, "
                    f"res={params['resolution']:.3f} m",
                    duration=30,
                )
            try:
                slices = slice_las_to_tifs(
                    las_path    = file_path,
                    output_dir  = output_dir,
                    resolution  = params["resolution"],
                    z_step      = params["z_step"],
                    z_min       = params["z_min"],
                    z_max       = params["z_max"],
                    radius      = params["radius"],
                    epsg        = epsg,
                    value_field = params["value_field"],
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
                    f"Nessun punto nel range Z scelto:\n"
                    f"[{params['z_min']:.4f}, {params['z_max']:.4f}] m",
                )
                failed += 1
                continue

            # Step 5: save sidecar
            save_slicer_params(output_dir, {
                "source_las":  file_path,
                "group_name":  group_name,
                "z_min":       params["z_min"],
                "z_max":       params["z_max"],
                "z_step":      params["z_step"],
                "resolution":  params["resolution"],
                "radius":      params["radius"],
                "value_field": params["value_field"],
                "epsg":        epsg,
                "n_slices":    len(slices),
            })

            # Step 6: register in catalog (= timeslice logic)
            try:
                self._register_las_slices_in_catalog(
                    project_root, group_name, slices, epsg,
                    reslice=is_reslice,
                )
            except Exception as e:
                QMessageBox.warning(
                    self.dlg, "Errore registrazione catalogo", str(e)
                )

            # Step 7: refresh UI
            if hasattr(self, "populate_group_list"):
                try:
                    self.populate_group_list()
                except Exception:
                    pass

            action = "Re-slice" if is_reslice else "Import LAS\u2192Slice"
            if hasattr(self, "_notify_info"):
                self._notify_info(
                    f"{action} OK: '{group_name}', "
                    f"{len(slices)} slice, "
                    f"campo='{params['value_field']}', "
                    f"dz={params['z_step']:.3f} m, res={params['resolution']:.3f} m.",
                    duration=12,
                )
            loaded_ok += 1

        if hasattr(self, "_notify_info"):
            self._notify_info(
                f"Import LAS\u2192Slice completato. OK: {loaded_ok}, falliti: {failed}.",
                duration=8,
            )
