# -*- coding: utf-8 -*-
"""
GPR LAS volume import – integra con il catalogo come timeslice.
Il dialog mostra un'anteprima delle colonne LAS per permettere
la selezione manuale dei campi R, G, B (o campo singolo).
"""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QMessageBox, QFileDialog, QDialog, QVBoxLayout, QHBoxLayout,
    QFormLayout, QDoubleSpinBox, QLineEdit, QComboBox,
    QDialogButtonBox, QLabel, QTableWidget, QTableWidgetItem,
    QGroupBox, QRadioButton,
)
from qgis.core import QgsProject


class GprVolumeMixin:

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _gpr_active_project_root(self) -> str:
        try:
            return (self.settings.value(
                self.settings_key_active_project, "", type=str) or "").strip()
        except Exception:
            return ""

    def _gpr_volumes_in_project(self, project_root):
        if not project_root:
            return []
        vd = os.path.join(project_root, "volumes_3d")
        if not os.path.isdir(vd):
            return []
        return [
            os.path.join(vd, n) for n in sorted(os.listdir(vd))
            if n.lower().endswith((".las", ".laz"))
        ]

    # ------------------------------------------------------------------
    # File picker
    # ------------------------------------------------------------------

    def _pick_las_files_for_slicing(self):
        pr    = self._gpr_active_project_root()
        exist = self._gpr_volumes_in_project(pr)
        vd    = os.path.join(pr, "volumes_3d") if pr else ""
        filt  = "Point Cloud (*.copc.laz *.las *.laz)"

        if not pr or not os.path.isdir(vd):
            paths, _ = QFileDialog.getOpenFileNames(self.dlg, "Seleziona LAS/LAZ", "", filt)
            return paths or []

        if not exist:
            ans = QMessageBox.question(
                self.dlg, "Nessun volume",
                f"Nessun LAS/LAZ in:\n{vd}\n\nSeleziona comunque?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return []
            paths, _ = QFileDialog.getOpenFileNames(self.dlg, "Seleziona LAS/LAZ", "", filt)
            return paths or []

        n       = len(exist)
        preview = "\n".join(f"  \u2022 {os.path.basename(p)}" for p in exist[:8])
        if n > 8:
            preview += f"\n  \u2026 e altri {n - 8}"
        msg = QMessageBox(self.dlg)
        msg.setWindowTitle("Volumi nel progetto")
        msg.setText(f"Trovati {n} volume/i in:\n{vd}\n\n{preview}\n\nUsa esistente o importa nuovo?")
        msg.setIcon(QMessageBox.Question)
        b_ex  = msg.addButton("\U0001f4c2  Usa dal progetto", QMessageBox.AcceptRole)
        b_new = msg.addButton("\U0001f4e5  Importa nuovo",    QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Cancel)
        msg.exec_()
        if msg.clickedButton() == b_ex:
            paths, _ = QFileDialog.getOpenFileNames(self.dlg, "Seleziona volume", vd, filt)
            return paths or []
        if msg.clickedButton() == b_new:
            paths, _ = QFileDialog.getOpenFileNames(self.dlg, "Seleziona LAS/LAZ", "", filt)
            return paths or []
        return []

    # ------------------------------------------------------------------
    # Configuration dialog  (with field preview table)
    # ------------------------------------------------------------------

    def _ask_slice_params(
        self,
        z_min_det, z_max_det,
        default_group="",
        saved=None,
        is_reslice=False,
        diagnosis=None,
    ):
        try:
            default_res  = float((self.dlg.lineEditDistanceX.text() or "").strip())
        except ValueError:
            default_res  = 0.10
        try:
            default_step = float((self.dlg.lineEditDistanceY.text() or "").strip())
        except ValueError:
            default_step = 0.05

        if saved:
            z_min_det    = saved.get("z_min",      z_min_det)
            z_max_det    = saved.get("z_max",      z_max_det)
            default_res  = saved.get("resolution", default_res)
            default_step = saved.get("z_step",     default_step)
        default_radius = (
            saved.get("radius", default_res * 2 ** 0.5) if saved
            else default_res * 2 ** 0.5
        )

        dx = diagnosis or {}
        rows        = dx.get("rows", [])
        all_fields  = [r["name"] for r in rows] or ["intensity", "red", "green", "blue"]
        n_pts       = dx.get("n_points", 0)
        pf_id       = dx.get("point_format", -1)
        n_sample    = len(rows[0]["values"]) if rows else 0

        # Restore previously saved fields
        prev_mode = saved.get("value_field", "rgb") if saved else "rgb"
        prev_r    = saved.get("r_field", dx.get("suggested_r",      "red"))    if saved else dx.get("suggested_r",      "red")
        prev_g    = saved.get("g_field", dx.get("suggested_g",      "green"))  if saved else dx.get("suggested_g",      "green")
        prev_b    = saved.get("b_field", dx.get("suggested_b",      "blue"))   if saved else dx.get("suggested_b",      "blue")
        prev_s    = saved.get("single_field", dx.get("suggested_single", "intensity")) if saved else dx.get("suggested_single", "intensity")

        # ---- Dialog ----
        dlg = QDialog(self.dlg)
        dlg.setWindowTitle("Re-slice" if is_reslice else "Configura slice LAS")
        dlg.setMinimumWidth(640)
        layout = QVBoxLayout(dlg)

        # Header
        hdr = f"{n_points:,} punti  |  Point format {pf_id}" if n_pts > 0 else ""
        if is_reslice:
            hdr = "Parametri precedenti caricati.  " + hdr
        if hdr:
            layout.addWidget(QLabel(hdr))

        # ---- Preview table ----
        if rows:
            grp_tbl = QGroupBox(f"Anteprima campi (prime {n_sample} righe)")
            tbl_lay = QVBoxLayout(grp_tbl)

            col_labels = ["Campo"] + [f"Riga {i+1}" for i in range(n_sample)] + ["Min", "Max"]
            tbl = QTableWidget(len(rows), len(col_labels))
            tbl.setHorizontalHeaderLabels(col_labels)
            tbl.verticalHeader().setVisible(False)
            tbl.setEditTriggers(QTableWidget.NoEditTriggers)
            tbl.setSelectionMode(QTableWidget.NoSelection)
            tbl.setMaximumHeight(200)

            GREEN_BG = QColor(200, 240, 200)
            GRAY_BG  = QColor(240, 240, 240)

            for row_idx, row in enumerate(rows):
                bg = GREEN_BG if row["has_data"] else GRAY_BG
                items = ([row["name"]]
                         + [str(v) for v in row["values"]]
                         + [f"{row['min']:.2f}", f"{row['max']:.2f}"])
                for col_idx, text in enumerate(items):
                    item = QTableWidgetItem(text)
                    item.setBackground(bg)
                    tbl.setItem(row_idx, col_idx, item)

            tbl.resizeColumnsToContents()
            tbl_lay.addWidget(tbl)
            layout.addWidget(grp_tbl)
        else:
            layout.addWidget(QLabel(
                "\u26a0 Anteprima non disponibile — seleziona i campi manualmente."
            ))

        # ---- Output mode ----
        grp_mode = QGroupBox("Modalit\u00e0 output")
        mode_lay = QHBoxLayout(grp_mode)
        rb_rgb  = QRadioButton("\U0001f3a8 RGB — 3 bande (rosso, verde, blu)")
        rb_mono = QRadioButton("Banda singola (un campo)")
        rb_rgb.setChecked(prev_mode == "rgb")
        rb_mono.setChecked(prev_mode != "rgb")
        mode_lay.addWidget(rb_rgb)
        mode_lay.addWidget(rb_mono)
        layout.addWidget(grp_mode)

        def _combo(default_field):
            cb = QComboBox()
            cb.addItems(all_fields)
            idx = all_fields.index(default_field) if default_field in all_fields else 0
            cb.setCurrentIndex(idx)
            return cb

        # ---- RGB field selectors ----
        grp_rgb = QGroupBox("Campi RGB")
        rgb_form = QFormLayout(grp_rgb)
        cb_r = _combo(prev_r)
        cb_g = _combo(prev_g)
        cb_b = _combo(prev_b)
        rgb_form.addRow("Banda R (rosso):",  cb_r)
        rgb_form.addRow("Banda G (verde):",  cb_g)
        rgb_form.addRow("Banda B (blu):",    cb_b)
        layout.addWidget(grp_rgb)

        # ---- Single field selector ----
        grp_single = QGroupBox("Campo singolo")
        single_form = QFormLayout(grp_single)
        cb_single = _combo(prev_s)
        single_form.addRow("Campo valori:", cb_single)
        layout.addWidget(grp_single)

        # Show/hide based on mode
        grp_rgb.setVisible(rb_rgb.isChecked())
        grp_single.setVisible(rb_mono.isChecked())
        rb_rgb.toggled.connect(lambda on: (
            grp_rgb.setVisible(on), grp_single.setVisible(not on)
        ))

        # ---- Numeric params ----
        grp_params = QGroupBox("Parametri griglia")
        form = QFormLayout(grp_params)

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
        le_group  = QLineEdit(saved.get("group_name", default_group) if saved else default_group)
        le_group.setPlaceholderText("Nome gruppo (cartella output)")

        form.addRow("Z minimo (m):",       sp_zmin)
        form.addRow("Z massimo (m):",      sp_zmax)
        form.addRow("Step Z (m):",         sp_step)
        form.addRow("Risoluzione XY (m):", sp_res)
        form.addRow("Radius IDW (m):",     sp_radius)
        form.addRow("Nome gruppo:",         le_group)
        layout.addWidget(grp_params)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec_() != QDialog.Accepted:
            return None

        use_rgb = rb_rgb.isChecked()
        return {
            "value_field":  "rgb" if use_rgb else all_fields[cb_single.currentIndex()],
            "r_field":      all_fields[cb_r.currentIndex()],
            "g_field":      all_fields[cb_g.currentIndex()],
            "b_field":      all_fields[cb_b.currentIndex()],
            "single_field": all_fields[cb_single.currentIndex()],
            "z_min":        sp_zmin.value(),
            "z_max":        sp_zmax.value(),
            "z_step":       sp_step.value(),
            "resolution":   sp_res.value(),
            "radius":       sp_radius.value(),
            "group_name":   le_group.text().strip() or default_group,
        }

    # ------------------------------------------------------------------
    # Catalog registration
    # ------------------------------------------------------------------

    def _register_las_slices_in_catalog(
        self, project_root, group_name, slices, epsg, reslice=False
    ):
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
                old = set(grp.get("timeslice_ids", []))
                catalog["timeslices"] = [
                    t for t in catalog.get("timeslices", []) if t.get("id") not in old
                ]
                grp["timeslice_ids"] = []
                save_catalog(project_root, catalog)

        grp_rec, _ = create_raster_group(project_root, group_name)
        gid  = grp_rec["id"]
        now  = utc_now_iso()
        crs  = f"EPSG:{epsg}" if epsg else None

        records = [
            {
                "id":           f"timeslice_{now}_{sl['index']:04d}",
                "name":         sl["name"],
                "project_path": sl["path"],
                "depth_from":   sl["z_from"],
                "depth_to":     sl["z_to"],
                "unit":         "m",
                "crs":          crs,
                "z_source":     "las_volume",
                "imported_at":  now,
            }
            for sl in slices
        ]
        register_timeslices_batch(project_root, records)
        assign_timeslices_to_group(project_root, gid, [r["id"] for r in records])
        return gid

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def import_las_as_slices(self):
        from .gpr_utils import check_laspy
        from .gpr_las_volume import get_z_range_chunked
        from .gpr_las_slicer import (
            slice_las_to_tifs, save_slicer_params, load_slicer_params,
            diagnose_las_fields_detailed,
        )

        if not check_laspy().get("ok"):
            QMessageBox.critical(self.dlg, "laspy non trovato",
                                 "Installa laspy: pip install laspy[lazrs]")
            return

        file_paths = self._pick_las_files_for_slicing()
        if not file_paths:
            return

        project_root = self._gpr_active_project_root()
        if not project_root:
            QMessageBox.warning(self.dlg, "Nessun progetto attivo",
                                "Apri un progetto nel Project Manager prima di importare.")
            return

        epsg      = QgsProject.instance().crs().postgisSrid() or None
        loaded_ok = 0
        failed    = 0

        for file_path in file_paths:
            base      = os.path.splitext(os.path.basename(file_path))[0]
            safe_base = "".join(
                c if c.isalnum() or c in "_-" else "_" for c in base
            ).strip("_") or "las_slices"

            candidate_dir = os.path.join(project_root, "timeslices_2d", safe_base)
            saved_params  = load_slicer_params(candidate_dir)
            is_reslice    = saved_params is not None

            # Detailed diagnosis with value preview
            if hasattr(self, "_notify_info"):
                self._notify_info("Analisi campi LAS in corso\u2026", duration=8)
            try:
                diagnosis = diagnose_las_fields_detailed(file_path)
            except Exception:
                diagnosis = {}

            try:
                z_min_det, z_max_det = get_z_range_chunked(file_path)
            except Exception as e:
                QMessageBox.warning(self.dlg, "Errore lettura Z", str(e))
                failed += 1
                continue

            params = self._ask_slice_params(
                z_min_det, z_max_det,
                default_group=safe_base,
                saved=saved_params,
                is_reslice=is_reslice,
                diagnosis=diagnosis,
            )
            if params is None:
                continue

            group_name = params["group_name"] or safe_base
            output_dir = os.path.join(project_root, "timeslices_2d", group_name)
            os.makedirs(output_dir, exist_ok=True)

            if hasattr(self, "_notify_info"):
                mode_str = (f"RGB ({params['r_field']},{params['g_field']},{params['b_field']})"
                            if params["value_field"] == "rgb"
                            else f"campo '{params['value_field']}'")
                self._notify_info(
                    f"Generazione slice: {mode_str}, "
                    f"dz={params['z_step']:.3f}m, res={params['resolution']:.3f}m\u2026",
                    duration=60,
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
                    r_field     = params["r_field"],
                    g_field     = params["g_field"],
                    b_field     = params["b_field"],
                )
            except Exception as e:
                QMessageBox.critical(self.dlg, "Errore generazione slice", str(e))
                failed += 1
                continue

            if not slices:
                QMessageBox.warning(
                    self.dlg, "Nessuna slice",
                    f"Nessun punto nel range Z [{params['z_min']:.4f}, {params['z_max']:.4f}] m",
                )
                failed += 1
                continue

            save_slicer_params(output_dir, {
                "source_las":   file_path,
                "group_name":   group_name,
                "z_min":        params["z_min"],
                "z_max":        params["z_max"],
                "z_step":       params["z_step"],
                "resolution":   params["resolution"],
                "radius":       params["radius"],
                "value_field":  params["value_field"],
                "r_field":      params["r_field"],
                "g_field":      params["g_field"],
                "b_field":      params["b_field"],
                "single_field": params["single_field"],
                "epsg":         epsg,
                "n_slices":     len(slices),
            })

            try:
                self._register_las_slices_in_catalog(
                    project_root, group_name, slices, epsg, reslice=is_reslice
                )
            except Exception as e:
                QMessageBox.warning(self.dlg, "Errore catalogo", str(e))

            if hasattr(self, "populate_group_list"):
                try:
                    self.populate_group_list()
                except Exception:
                    pass

            mode_info = (f"3 bande RGB ({params['r_field']},{params['g_field']},{params['b_field']})"
                         if params["value_field"] == "rgb"
                         else f"campo '{params['value_field']}'")
            if hasattr(self, "_notify_info"):
                action = "Re-slice" if is_reslice else "Import LAS\u2192Slice"
                self._notify_info(
                    f"{action} OK: '{group_name}', {len(slices)} slice, {mode_info}.",
                    duration=12,
                )
            loaded_ok += 1

        if hasattr(self, "_notify_info"):
            self._notify_info(
                f"Completato. OK: {loaded_ok}, falliti: {failed}.", duration=8
            )
