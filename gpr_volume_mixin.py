# -*- coding: utf-8 -*-
"""
GPR LAS volume import – integra con il catalogo come timeslice.
Il dialog mostra un'anteprima delle colonne LAS per permettere
la selezione manuale dei campi R, G, B (o campo singolo).
"""

import os
import re

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QMessageBox, QFileDialog, QDialog, QVBoxLayout, QHBoxLayout,
    QFormLayout, QDoubleSpinBox, QLineEdit, QComboBox,
    QDialogButtonBox, QLabel, QTableWidget, QTableWidgetItem,
    QGroupBox, QRadioButton, QCheckBox,
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

    def _pick_csv_files_for_slicing(self):
        filt = "CSV files (*.csv *.txt *.tsv);;All files (*.*)"
        paths, _ = QFileDialog.getOpenFileNames(self.dlg, "Seleziona CSV", "", filt)
        return paths or []

    def _guess_depth_from_filename(self, path, fallback=0.0):
        base = os.path.splitext(os.path.basename(path or ""))[0]
        tokens = re.findall(r"-?\d+(?:[.,]\d+)?", base)
        if not tokens:
            return float(fallback)
        text = str(tokens[-1]).replace(",", ".")
        try:
            return float(text)
        except Exception:
            return float(fallback)

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
        hdr = f"{n_pts:,} punti  |  Point format {pf_id}" if n_pts > 0 else ""
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

    def _ask_csv_slice_params(
        self,
        file_paths,
        default_group="",
        saved=None,
    ):
        FILE_DEPTH_MODE = "__file_depth__"
        from .gpr_csv_slicer import (
            csv_separator_mode_label,
            diagnose_csv_fields_detailed,
            normalize_csv_separator_mode,
        )

        try:
            default_res = float((self.dlg.lineEditDistanceX.text() or "").strip())
        except Exception:
            default_res = 0.10
        try:
            default_step = float((self.dlg.lineEditDistanceY.text() or "").strip())
        except Exception:
            default_step = 0.05

        if saved:
            default_res = float(saved.get("resolution", default_res))
            default_step = float(saved.get("z_step", default_step))
        default_radius = (
            float(saved.get("radius", default_res * 2 ** 0.5))
            if saved else float(default_res * 2 ** 0.5)
        )

        prev_mode = saved.get("value_field", "rgb") if saved else "rgb"
        prev_x = saved.get("x_field", "") if saved else ""
        prev_y = saved.get("y_field", "") if saved else ""
        prev_z = saved.get("z_field", "") if saved else ""
        prev_r = saved.get("r_field", "red") if saved else "red"
        prev_g = saved.get("g_field", "green") if saved else "green"
        prev_b = saved.get("b_field", "blue") if saved else "blue"
        prev_s = saved.get("single_field", "intensity") if saved else "intensity"
        prev_depths = saved.get("depth_by_file", {}) if saved else {}
        saved_sep_mode = normalize_csv_separator_mode(
            (saved or {}).get("separator_mode", "auto")
        )

        z_min_def = float(saved.get("z_min", 0.0)) if saved and saved.get("z_min") is not None else 0.0
        z_max_def = float(saved.get("z_max", 1.0)) if saved and saved.get("z_max") is not None else 1.0

        dlg = QDialog(self.dlg)
        dlg.setWindowTitle("Configura slice CSV")
        dlg.setMinimumWidth(780)
        layout = QVBoxLayout(dlg)

        lbl_header = QLabel("")
        lbl_status = QLabel("")
        layout.addWidget(lbl_header)
        layout.addWidget(lbl_status)

        # Separator selector (with checkboxes)
        grp_sep = QGroupBox("Separatori CSV (spunta uno o piu separatori)")
        sep_l = QHBoxLayout(grp_sep)
        cb_sep_comma = QCheckBox("Comma (,)")
        cb_sep_semicolon = QCheckBox("Semicolon (;)")
        cb_sep_tab = QCheckBox("Tab")
        cb_sep_space = QCheckBox("Spazio")
        sep_l.addWidget(cb_sep_comma)
        sep_l.addWidget(cb_sep_semicolon)
        sep_l.addWidget(cb_sep_tab)
        sep_l.addWidget(cb_sep_space)
        layout.addWidget(grp_sep)

        # Preview table container
        grp_tbl = QGroupBox("Anteprima colonne")
        tbl_l = QVBoxLayout(grp_tbl)
        tbl = QTableWidget(0, 0)
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.NoSelection)
        tbl.setMaximumHeight(220)
        tbl_l.addWidget(tbl)
        layout.addWidget(grp_tbl)

        # Mapping group
        grp_map = QGroupBox("Mapping colonne")
        map_form = QFormLayout(grp_map)
        cb_x = QComboBox()
        cb_y = QComboBox()
        cb_z = QComboBox()
        map_form.addRow("Colonna X:", cb_x)
        map_form.addRow("Colonna Y:", cb_y)
        map_form.addRow("Colonna Z / Depth:", cb_z)
        layout.addWidget(grp_map)

        # Output mode
        grp_mode = QGroupBox("Modalita output")
        mode_l = QHBoxLayout(grp_mode)
        rb_rgb = QRadioButton("RGB (R,G,B)")
        rb_mono = QRadioButton("Banda singola")
        rb_rgb.setChecked(prev_mode == "rgb")
        rb_mono.setChecked(prev_mode != "rgb")
        mode_l.addWidget(rb_rgb)
        mode_l.addWidget(rb_mono)
        layout.addWidget(grp_mode)

        grp_rgb = QGroupBox("Campi RGB")
        rgb_form = QFormLayout(grp_rgb)
        cb_r = QComboBox()
        cb_g = QComboBox()
        cb_b = QComboBox()
        rgb_form.addRow("Banda R:", cb_r)
        rgb_form.addRow("Banda G:", cb_g)
        rgb_form.addRow("Banda B:", cb_b)
        layout.addWidget(grp_rgb)

        grp_single = QGroupBox("Campo singolo")
        single_form = QFormLayout(grp_single)
        cb_single = QComboBox()
        single_form.addRow("Campo valori:", cb_single)
        layout.addWidget(grp_single)

        grp_rgb.setVisible(rb_rgb.isChecked())
        grp_single.setVisible(rb_mono.isChecked())
        rb_rgb.toggled.connect(lambda on: (grp_rgb.setVisible(on), grp_single.setVisible(not on)))

        # Depth-per-file table (used when no Z column)
        grp_depth = QGroupBox("Depth per file")
        depth_l = QVBoxLayout(grp_depth)
        depth_tbl = QTableWidget(len(file_paths), 2)
        depth_tbl.setHorizontalHeaderLabels(["CSV", "Depth (m)"])
        depth_tbl.verticalHeader().setVisible(False)
        depth_tbl.setMaximumHeight(220)
        depth_spins = {}
        for ridx, path in enumerate(file_paths):
            base = os.path.basename(path)
            depth_tbl.setItem(ridx, 0, QTableWidgetItem(base))
            sp = QDoubleSpinBox(dlg)
            sp.setRange(-9999.0, 9999.0)
            sp.setDecimals(4)
            sp.setSingleStep(0.1)
            default_depth = self._guess_depth_from_filename(path, fallback=ridx * default_step)
            saved_depth = prev_depths.get(path, prev_depths.get(base, default_depth))
            try:
                sp.setValue(float(saved_depth))
            except Exception:
                sp.setValue(float(default_depth))
            depth_tbl.setCellWidget(ridx, 1, sp)
            depth_spins[path] = sp
        depth_tbl.resizeColumnsToContents()
        depth_l.addWidget(depth_tbl)
        layout.addWidget(grp_depth)

        # Numeric params
        grp_params = QGroupBox("Parametri griglia")
        form = QFormLayout(grp_params)

        def _spin(lo, hi, dec, step, val):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(dec)
            s.setSingleStep(step)
            s.setValue(val)
            return s

        sp_zmin = _spin(-9999, 9999, 4, 0.01, z_min_def)
        sp_zmax = _spin(-9999, 9999, 4, 0.01, z_max_def)
        sp_step = _spin(0.001, 1000, 4, 0.01, default_step)
        sp_res = _spin(0.001, 1000, 4, 0.01, default_res)
        sp_radius = _spin(0.001, 1000, 4, 0.01, default_radius)
        le_group = QLineEdit(saved.get("group_name", default_group) if saved else default_group)
        le_group.setPlaceholderText("Nome gruppo (cartella output)")

        form.addRow("Z minimo (m):", sp_zmin)
        form.addRow("Z massimo (m):", sp_zmax)
        form.addRow("Step Z / spessore (m):", sp_step)
        form.addRow("Risoluzione XY (m):", sp_res)
        form.addRow("Radius IDW (m):", sp_radius)
        form.addRow("Nome gruppo:", le_group)
        layout.addWidget(grp_params)

        diagnosis_state = {"dx": {}, "fields": []}

        def _mode_to_set(mode_key):
            key = normalize_csv_separator_mode(mode_key)
            mapping = {
                "comma": {","},
                "semicolon": {";"},
                "tab": {"\t"},
                "space": {" "},
                "comma_tab": {",", "\t"},
                "comma_space": {",", " "},
                "tab_space": {"\t", " "},
                "common_mixed": {",", ";", "\t", " "},
                "auto": {",", ";", "\t", " "},
            }
            return set(mapping.get(key, {",", ";", "\t", " "}))

        def _set_to_mode(chars):
            s = set(chars or [])
            if not s:
                return "auto"
            if s == {","}:
                return "comma"
            if s == {";"}:
                return "semicolon"
            if s == {"\t"}:
                return "tab"
            if s == {" "}:
                return "space"
            if s == {",", "\t"}:
                return "comma_tab"
            if s == {",", " "}:
                return "comma_space"
            if s == {"\t", " "}:
                return "tab_space"
            return "common_mixed"

        def _selected_separator_mode():
            chars = set()
            if cb_sep_comma.isChecked():
                chars.add(",")
            if cb_sep_semicolon.isChecked():
                chars.add(";")
            if cb_sep_tab.isChecked():
                chars.add("\t")
            if cb_sep_space.isChecked():
                chars.add(" ")
            return _set_to_mode(chars)

        def _set_separator_checks(mode_key):
            chars = _mode_to_set(mode_key)
            cb_sep_comma.blockSignals(True)
            cb_sep_semicolon.blockSignals(True)
            cb_sep_tab.blockSignals(True)
            cb_sep_space.blockSignals(True)
            cb_sep_comma.setChecked("," in chars)
            cb_sep_semicolon.setChecked(";" in chars)
            cb_sep_tab.setChecked("\t" in chars)
            cb_sep_space.setChecked(" " in chars)
            cb_sep_comma.blockSignals(False)
            cb_sep_semicolon.blockSignals(False)
            cb_sep_tab.blockSignals(False)
            cb_sep_space.blockSignals(False)

        def _populate_combo(combo, fields, preferred="", keep_current=True):
            current = combo.currentText().strip() if keep_current else ""
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(fields)
            target = ""
            if current and current in fields:
                target = current
            elif preferred and preferred in fields:
                target = preferred
            elif fields:
                target = fields[0]
            if target in fields:
                combo.setCurrentIndex(fields.index(target))
            combo.blockSignals(False)

        def _z_field_value():
            data = cb_z.currentData()
            if data in (None, "", FILE_DEPTH_MODE):
                return None
            return str(data)

        def _on_z_mode_changed():
            use_file_depth = _z_field_value() is None
            grp_depth.setVisible(use_file_depth)
            sp_zmin.setEnabled(not use_file_depth)
            sp_zmax.setEnabled(not use_file_depth)

        def _row_by_name(name):
            rows = diagnosis_state.get("dx", {}).get("rows", [])
            for row in rows:
                if str(row.get("name", "")) == str(name or ""):
                    return row
            return None

        def _refresh_z_range_from_selection():
            zf = _z_field_value()
            if not zf:
                return
            row = _row_by_name(zf)
            if row is None or not bool(row.get("has_data", False)):
                return
            try:
                sp_zmin.setValue(float(row.get("min", sp_zmin.value())))
                sp_zmax.setValue(float(row.get("max", sp_zmax.value())))
            except Exception:
                pass

        def _apply_diagnosis(dx, keep_current=True):
            rows = list(dx.get("rows", []) or [])
            fields = list(dx.get("all_field_names", []) or [])
            diagnosis_state["dx"] = dx
            diagnosis_state["fields"] = fields

            header_txt = (
                f"{len(file_paths)} file selezionati | campioni analizzati: {int(dx.get('n_points_sampled', 0))}"
            )
            lbl_header.setText(header_txt)
            lbl_status.setText(
                "Riconoscimento automatico: "
                + ("completo (X/Y/Z + valore)" if bool(dx.get("recognized_complete", False)) else "parziale, verifica mapping manuale")
                + f" | separatori: {csv_separator_mode_label(_selected_separator_mode())}"
            )

            n_preview = max((len(r.get("values", [])) for r in rows), default=0)
            cols = ["Campo"] + [f"Val {i+1}" for i in range(n_preview)] + ["Min", "Max"]
            tbl.setColumnCount(len(cols))
            tbl.setHorizontalHeaderLabels(cols)
            tbl.setRowCount(len(rows))
            for ridx, row in enumerate(rows):
                vals = list(row.get("values", []))
                items = [str(row.get("name", ""))]
                items.extend(str(vals[i]) if i < len(vals) else "" for i in range(n_preview))
                if bool(row.get("has_data", False)):
                    items.append(f"{float(row.get('min', 0.0)):.4f}")
                    items.append(f"{float(row.get('max', 0.0)):.4f}")
                else:
                    items.extend(["-", "-"])
                for cidx, txt in enumerate(items):
                    tbl.setItem(ridx, cidx, QTableWidgetItem(txt))
            tbl.resizeColumnsToContents()

            _populate_combo(cb_x, fields, preferred=(dx.get("suggested_x") or prev_x), keep_current=keep_current)
            _populate_combo(cb_y, fields, preferred=(dx.get("suggested_y") or prev_y), keep_current=keep_current)
            _populate_combo(cb_r, fields, preferred=(dx.get("suggested_r") or prev_r), keep_current=keep_current)
            _populate_combo(cb_g, fields, preferred=(dx.get("suggested_g") or prev_g), keep_current=keep_current)
            _populate_combo(cb_b, fields, preferred=(dx.get("suggested_b") or prev_b), keep_current=keep_current)
            _populate_combo(cb_single, fields, preferred=(dx.get("suggested_single") or prev_s), keep_current=keep_current)

            current_z = cb_z.currentData() if keep_current else None
            cb_z.blockSignals(True)
            cb_z.clear()
            cb_z.addItem("Depth per file (ogni CSV = una profondita)", FILE_DEPTH_MODE)
            for field in fields:
                cb_z.addItem(field, field)
            target_z = None
            if current_z and current_z != FILE_DEPTH_MODE and current_z in fields:
                target_z = current_z
            elif prev_z and prev_z in fields:
                target_z = prev_z
            elif dx.get("suggested_z") in fields:
                target_z = dx.get("suggested_z")
            if target_z and target_z in fields:
                cb_z.setCurrentIndex(1 + fields.index(target_z))
            else:
                cb_z.setCurrentIndex(0)
            cb_z.blockSignals(False)
            _on_z_mode_changed()
            _refresh_z_range_from_selection()

        def _refresh_from_separator_checks(keep_current=True):
            sep_mode = _selected_separator_mode()
            try:
                dx = diagnose_csv_fields_detailed(
                    file_paths,
                    separator_mode=sep_mode,
                )
            except Exception:
                dx = {}
            _apply_diagnosis(dx, keep_current=keep_current)

        for cb in (cb_sep_comma, cb_sep_semicolon, cb_sep_tab, cb_sep_space):
            cb.toggled.connect(lambda _on: _refresh_from_separator_checks(keep_current=True))

        cb_z.currentIndexChanged.connect(lambda _idx: (_on_z_mode_changed(), _refresh_z_range_from_selection()))

        _set_separator_checks(saved_sep_mode)
        _refresh_from_separator_checks(keep_current=False)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = btns.button(QDialogButtonBox.Ok)
        cancel_btn = btns.button(QDialogButtonBox.Cancel)
        layout.addWidget(btns)

        def _accept_if_valid():
            fields = diagnosis_state.get("fields", [])
            if not fields:
                QMessageBox.warning(self.dlg, "CSV", "Nessuna colonna rilevata con i separatori selezionati.")
                return
            if cb_x.count() == 0 or cb_y.count() == 0:
                QMessageBox.warning(self.dlg, "CSV", "Seleziona almeno le colonne X e Y.")
                return
            if rb_rgb.isChecked():
                if cb_r.count() == 0 or cb_g.count() == 0 or cb_b.count() == 0:
                    QMessageBox.warning(self.dlg, "CSV", "Seleziona le colonne R/G/B.")
                    return
            else:
                if cb_single.count() == 0:
                    QMessageBox.warning(self.dlg, "CSV", "Seleziona il campo singolo.")
                    return
            dlg.accept()

        if ok_btn is not None:
            ok_btn.clicked.connect(_accept_if_valid)
        if cancel_btn is not None:
            cancel_btn.clicked.connect(dlg.reject)
        else:
            btns.rejected.connect(dlg.reject)

        if dlg.exec_() != QDialog.Accepted:
            return None

        z_field = _z_field_value()
        use_rgb = rb_rgb.isChecked()
        depth_by_file = None
        if z_field is None:
            depth_by_file = {path: float(sp.value()) for path, sp in depth_spins.items()}

        return {
            "separator_mode": _selected_separator_mode(),
            "x_field": cb_x.currentText().strip(),
            "y_field": cb_y.currentText().strip(),
            "z_field": z_field,
            "value_field": "rgb" if use_rgb else cb_single.currentText().strip(),
            "single_field": cb_single.currentText().strip(),
            "r_field": cb_r.currentText().strip(),
            "g_field": cb_g.currentText().strip(),
            "b_field": cb_b.currentText().strip(),
            "depth_by_file": depth_by_file,
            "z_min": (sp_zmin.value() if z_field else None),
            "z_max": (sp_zmax.value() if z_field else None),
            "z_step": sp_step.value(),
            "resolution": sp_res.value(),
            "radius": sp_radius.value(),
            "group_name": le_group.text().strip() or default_group,
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

    def import_csv_as_slices(self):
        from .gpr_csv_slicer import (
            csv_separator_mode_label,
            load_csv_slicer_params,
            save_csv_slicer_params,
            slice_csv_to_tifs,
        )

        file_paths = self._pick_csv_files_for_slicing()
        if not file_paths:
            return

        project_root = self._gpr_active_project_root()
        if not project_root:
            QMessageBox.warning(
                self.dlg,
                "Nessun progetto attivo",
                "Apri un progetto nel Project Manager prima di importare.",
            )
            return

        base = os.path.splitext(os.path.basename(file_paths[0]))[0]
        safe_base = "".join(c if c.isalnum() or c in "_-" else "_" for c in base).strip("_") or "csv_slices"
        candidate_dir = os.path.join(project_root, "timeslices_2d", safe_base)
        saved_params = load_csv_slicer_params(candidate_dir)

        params = self._ask_csv_slice_params(
            file_paths=file_paths,
            default_group=safe_base,
            saved=saved_params,
        )
        if params is None:
            return
        separator_mode = str(params.get("separator_mode") or "auto")

        group_name = params.get("group_name") or safe_base
        output_dir = os.path.join(project_root, "timeslices_2d", group_name)
        os.makedirs(output_dir, exist_ok=True)
        is_reslice = load_csv_slicer_params(output_dir) is not None

        epsg = QgsProject.instance().crs().postgisSrid() or None
        if hasattr(self, "_notify_info"):
            mode_info = (
                f"RGB ({params['r_field']},{params['g_field']},{params['b_field']})"
                if params.get("value_field") == "rgb"
                else f"campo '{params.get('single_field', params.get('value_field', ''))}'"
            )
            depth_info = (
                f"Z da colonna '{params['z_field']}'"
                if params.get("z_field")
                else "Depth per file"
            )
            self._notify_info(
                (
                    f"CSV->Slice: {mode_info}, {depth_info}, "
                    f"sep={csv_separator_mode_label(separator_mode)}, "
                    f"dz={params['z_step']:.3f}m, res={params['resolution']:.3f}m..."
                ),
                duration=45,
            )

        try:
            slices = slice_csv_to_tifs(
                csv_paths=file_paths,
                output_dir=output_dir,
                resolution=float(params["resolution"]),
                z_step=float(params["z_step"]),
                z_min=params.get("z_min"),
                z_max=params.get("z_max"),
                radius=float(params["radius"]),
                epsg=epsg,
                x_field=params["x_field"],
                y_field=params["y_field"],
                z_field=params.get("z_field"),
                value_field=params["value_field"],
                single_field=params.get("single_field", params["value_field"]),
                r_field=params.get("r_field", "red"),
                g_field=params.get("g_field", "green"),
                b_field=params.get("b_field", "blue"),
                depth_by_file=params.get("depth_by_file"),
                separator_mode=separator_mode,
            )
        except Exception as e:
            QMessageBox.critical(self.dlg, "Errore generazione slice CSV", str(e))
            return

        if not slices:
            QMessageBox.warning(
                self.dlg,
                "Nessuna slice",
                "Nessuna slice generata dai CSV selezionati (controlla mapping e valori numerici).",
            )
            return

        save_csv_slicer_params(
            output_dir,
            {
                "source_csv_paths": file_paths,
                "group_name": group_name,
                "x_field": params["x_field"],
                "y_field": params["y_field"],
                "z_field": params.get("z_field"),
                "value_field": params["value_field"],
                "single_field": params.get("single_field"),
                "r_field": params.get("r_field"),
                "g_field": params.get("g_field"),
                "b_field": params.get("b_field"),
                "depth_by_file": params.get("depth_by_file"),
                "separator_mode": separator_mode,
                "z_min": params.get("z_min"),
                "z_max": params.get("z_max"),
                "z_step": params["z_step"],
                "resolution": params["resolution"],
                "radius": params["radius"],
                "epsg": epsg,
                "n_slices": len(slices),
            },
        )

        try:
            self._register_las_slices_in_catalog(
                project_root,
                group_name,
                slices,
                epsg,
                reslice=is_reslice,
            )
        except Exception as e:
            QMessageBox.warning(self.dlg, "Errore catalogo", str(e))

        if hasattr(self, "populate_group_list"):
            try:
                self.populate_group_list()
            except Exception:
                pass

        if hasattr(self, "_notify_info"):
            self._notify_info(
                f"Import CSV->Slice OK: '{group_name}', {len(slices)} slice.",
                duration=10,
            )
