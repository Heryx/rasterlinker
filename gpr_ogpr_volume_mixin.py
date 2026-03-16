# -*- coding: utf-8 -*-
"""
GprOgprVolumeMixin
==================
Fornisce il metodo import_ogpr_as_slices(profiles) al plugin principale.

Flusso:
  1. Dialog parametri (canale, z_min/max, z_step, res, radius, gruppo)
  2. compute_ogpr_slice_grids() -> grids in RAM (inviluppo Hilbert)
  3. _show_slice_preview()     -> dialog matplotlib con slider + colormap
  4. Se l'utente conferma: write_grids_to_tifs() + catalogo + refresh UI
"""

from __future__ import annotations

import os

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QDialogButtonBox,
    QLabel, QDoubleSpinBox, QComboBox, QLineEdit, QGroupBox,
    QMessageBox, QSizePolicy, QPushButton, QSlider,
)
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject


class GprOgprVolumeMixin:

    # ------------------------------------------------------------------
    # Dialog parametri
    # ------------------------------------------------------------------

    def _ask_ogpr_slice_params(
        self,
        n_channels: int,
        depth_max_m: float,
        default_group: str = "gpr_slices",
        saved: dict | None = None,
    ) -> dict | None:

        try:
            default_res = float((self.dlg.lineEditDistanceX.text() or "").strip())
        except (AttributeError, ValueError):
            default_res = 0.10
        try:
            default_step = float((self.dlg.lineEditDistanceY.text() or "").strip())
        except (AttributeError, ValueError):
            default_step = 0.05

        if saved:
            default_res  = saved.get("resolution", default_res)
            default_step = saved.get("z_step",      default_step)
            depth_max_m  = saved.get("z_max",       depth_max_m)

        default_radius = (
            saved.get("radius", default_res * 2 ** 0.5) if saved
            else default_res * 2 ** 0.5
        )

        dlg = QDialog(getattr(self, "dlg", None))
        dlg.setWindowTitle("OGPR \u2192 Timeslice")
        dlg.setMinimumWidth(420)
        root = QVBoxLayout(dlg)

        root.addWidget(QLabel(
            f"Profondita' massima rilevata: <b>{depth_max_m:.3f} m</b>  \u2014  "
            f"<b>{n_channels}</b> canal{'e' if n_channels == 1 else 'i'}"
        ))

        def _spin(lo, hi, dec, step, val):
            s = QDoubleSpinBox()
            s.setRange(lo, hi); s.setDecimals(dec)
            s.setSingleStep(step); s.setValue(val)
            return s

        grp = QGroupBox("Parametri griglia")
        fl  = QFormLayout(grp)

        cb_ch = QComboBox()
        cb_ch.addItem("\U0001f4e1  Tutti i canali \u2014 media (consigliato)", (-1, "mean"))
        cb_ch.addItem("\U0001f4e1  Tutti i canali \u2014 massimo",             (-1, "max"))
        for ci in range(n_channels):
            cb_ch.addItem(f"Solo canale {ci}", (ci, "mean"))
        cb_ch.setToolTip(
            "'Tutti \u2014 media': combina l'ampiezza di ogni canale con la media.\n"
            "'Tutti \u2014 massimo': prende il canale con ampiezza maggiore.\n"
            "'Solo canale N': un singolo canale (analisi per polarizzazione)."
        )
        if saved:
            prev_ch, prev_comb = saved.get("channel", -1), saved.get("combine_method", "mean")
            for i in range(cb_ch.count()):
                if cb_ch.itemData(i) == (prev_ch, prev_comb):
                    cb_ch.setCurrentIndex(i); break

        sp_zmin   = _spin(0.0,    1000.0, 4, 0.01,  saved.get("z_min",   0.0)         if saved else 0.0)
        sp_zmax   = _spin(0.001,  1000.0, 4, 0.01,  saved.get("z_max",   depth_max_m) if saved else depth_max_m)
        sp_step   = _spin(0.001,   100.0, 4, 0.005, default_step)
        sp_res    = _spin(0.001,   100.0, 4, 0.01,  default_res)
        sp_radius = _spin(0.001,   100.0, 4, 0.01,  default_radius)
        le_group  = QLineEdit(saved.get("group_name", default_group) if saved else default_group)
        le_group.setPlaceholderText("Nome gruppo catalogo")

        fl.addRow("Canali:",         cb_ch)
        fl.addRow("Z minimo (m):",   sp_zmin)
        fl.addRow("Z massimo (m):",  sp_zmax)
        fl.addRow("Step Z (m):",     sp_step)
        fl.addRow("Risoluzione XY:", sp_res)
        fl.addRow("Radius IDW (m):", sp_radius)
        fl.addRow("Nome gruppo:",     le_group)
        root.addWidget(grp)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        root.addWidget(btns)

        if dlg.exec_() != QDialog.Accepted:
            return None

        ch_val, comb_val = cb_ch.currentData()
        return {
            "channel":        ch_val,
            "combine_method": comb_val,
            "z_min":          sp_zmin.value(),
            "z_max":          sp_zmax.value(),
            "z_step":         sp_step.value(),
            "resolution":     sp_res.value(),
            "radius":         sp_radius.value(),
            "group_name":     le_group.text().strip() or default_group,
        }

    # ------------------------------------------------------------------
    # Anteprima interattiva
    # ------------------------------------------------------------------

    def _show_slice_preview(
        self,
        grids: list[dict],
        meta: dict,
    ) -> tuple[bool, str]:
        """
        Mostra un dialog matplotlib con slider profondita' e scelta colormap.
        Ritorna (confermato: bool, colormap_name: str).
        """
        try:
            import matplotlib  # type: ignore
            matplotlib.use("Qt5Agg")
            from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg  # type: ignore
            from matplotlib.figure import Figure  # type: ignore
        except ImportError:
            QMessageBox.warning(
                getattr(self, "dlg", None),
                "matplotlib non disponibile",
                "Installa matplotlib per l'anteprima:\n  pip install matplotlib"
            )
            # se matplotlib non c'e', chiedi comunque se procedere
            ans = QMessageBox.question(
                getattr(self, "dlg", None),
                "Procedi senza anteprima?",
                f"Generare comunque {len(grids)} slice?",
                QMessageBox.Yes | QMessageBox.No,
            )
            return (ans == QMessageBox.Yes), "gray"

        COLORMAPS = ["gray", "RdBu_r", "viridis", "plasma", "hot", "seismic", "jet"]

        dlg = QDialog(getattr(self, "dlg", None))
        dlg.setWindowTitle("Anteprima Timeslice")
        dlg.setMinimumSize(700, 560)
        root = QVBoxLayout(dlg)

        # --- matplotlib canvas ---
        fig    = Figure(figsize=(6, 4), tight_layout=True)
        canvas = FigureCanvasQTAgg(fig)
        canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(canvas)

        ax = fig.add_subplot(111)
        ax.set_aspect("equal")
        ax.axis("off")

        # calcola vmin/vmax globali (2-98 percentile su tutti i grids)
        all_vals = np.concatenate([
            item["grid"][np.isfinite(item["grid"])].ravel()
            for item in grids
        ])
        g_vmin = float(np.percentile(all_vals, 2))  if all_vals.size else 0.0
        g_vmax = float(np.percentile(all_vals, 98)) if all_vals.size else 1.0
        if g_vmax <= g_vmin:
            g_vmax = g_vmin + 1e-6

        im_handle = [None]

        def _render(idx, cmap):
            ax.clear()
            ax.axis("off")
            item  = grids[idx]
            grid  = item["grid"]
            masked = np.where(np.isfinite(grid), grid, np.nan)
            im = ax.imshow(
                np.flipud(masked),
                cmap=cmap,
                vmin=g_vmin, vmax=g_vmax,
                aspect="equal",
                interpolation="nearest",
            )
            ax.set_title(
                f"Slice {idx+1}/{len(grids)}  —  "
                f"z = {item['z_from']:.3f} \u2013 {item['z_to']:.3f} m",
                fontsize=10,
            )
            if im_handle[0] is None:
                fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
                im_handle[0] = im
            canvas.draw()

        # --- slider ---
        lbl_slice = QLabel()
        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(len(grids) - 1)
        slider.setValue(0)

        # --- colormap ---
        cb_cmap = QComboBox()
        cb_cmap.addItems(COLORMAPS)
        cb_cmap.setFixedWidth(120)

        def _update():
            idx  = slider.value()
            cmap = cb_cmap.currentText()
            lbl_slice.setText(f"Slice {idx+1} / {len(grids)}")
            _render(idx, cmap)

        slider.valueChanged.connect(lambda _: _update())
        cb_cmap.currentTextChanged.connect(lambda _: _update())

        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("Profondita':"))
        ctrl_row.addWidget(slider, stretch=1)
        ctrl_row.addWidget(lbl_slice)
        ctrl_row.addSpacing(16)
        ctrl_row.addWidget(QLabel("Colormap:"))
        ctrl_row.addWidget(cb_cmap)
        root.addLayout(ctrl_row)

        # --- bottoni ---
        btn_ok  = QPushButton("\u2705  Procedi con la generazione")
        btn_cancel = QPushButton("\u274c  Annulla")
        btn_ok.setDefault(True)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        root.addLayout(btn_row)

        confirmed = [False]
        btn_ok.clicked.connect(lambda: (confirmed.__setitem__(0, True), dlg.accept()))
        btn_cancel.clicked.connect(dlg.reject)

        _update()   # render prima slice
        dlg.exec_()

        return confirmed[0], cb_cmap.currentText()

    # ------------------------------------------------------------------
    # Entry point pubblico
    # ------------------------------------------------------------------

    def import_ogpr_as_slices(self, profiles: list) -> None:
        from .gpr_ogpr_slicer import (
            compute_ogpr_slice_grids,
            write_grids_to_tifs,
            save_ogpr_slicer_params,
            load_ogpr_slicer_params,
        )

        if not profiles:
            QMessageBox.information(
                getattr(self, "dlg", None), "Nessun profilo",
                "Importa almeno un file .ogpr prima di creare le timeslice.",
            )
            return

        project_root = self._gpr_active_project_root()
        if not project_root:
            QMessageBox.warning(
                getattr(self, "dlg", None), "Nessun progetto attivo",
                "Apri un progetto nel Project Manager prima di importare.",
            )
            return

        base_names = list({
            os.path.splitext(os.path.basename(p.path))[0] for p in profiles
        })
        default_group = base_names[0] if len(base_names) == 1 else "gpr_slices"
        candidate_dir = os.path.join(project_root, "timeslices_2d", default_group)
        saved      = load_ogpr_slicer_params(candidate_dir)
        is_reslice = saved is not None

        depth_max  = max(p.depth_max_m for p in profiles)
        n_channels = max(p.n_channels  for p in profiles)

        params = self._ask_ogpr_slice_params(
            n_channels=n_channels, depth_max_m=depth_max,
            default_group=default_group, saved=saved,
        )
        if params is None:
            return

        group_name = params["group_name"]
        output_dir = os.path.join(project_root, "timeslices_2d", group_name)
        epsg       = QgsProject.instance().crs().postgisSrid() or None

        ch_label = (
            f"tutti ({params['combine_method']})"
            if params["channel"] < 0
            else f"canale {params['channel']}"
        )

        if hasattr(self, "_notify_info"):
            self._notify_info(
                f"Calcolo grids OGPR: {len(profiles)} profili, {ch_label}, "
                f"dz={params['z_step']:.3f}m\u2026", duration=60,
            )

        # ---- 1. Calcola grids (Hilbert, IDW) ----
        try:
            grids, meta = compute_ogpr_slice_grids(
                profiles       = profiles,
                channel        = params["channel"],
                combine_method = params["combine_method"],
                resolution     = params["resolution"],
                z_step         = params["z_step"],
                z_min          = params["z_min"],
                z_max          = params["z_max"],
                radius         = params["radius"],
            )
        except Exception as exc:
            QMessageBox.critical(
                getattr(self, "dlg", None), "Errore calcolo grids", str(exc)
            )
            return

        if not grids:
            QMessageBox.warning(
                getattr(self, "dlg", None), "Nessuna slice prodotta",
                f"Nessun punto nel range Z [{params['z_min']:.4f}, "
                f"{params['z_max']:.4f}] m.\n"
                "Verifica che i profili abbiano coordinate valide.",
            )
            return

        # ---- 2. Anteprima interattiva ----
        confirmed, _cmap = self._show_slice_preview(grids, meta)
        if not confirmed:
            return

        # ---- 3. Scrivi GeoTIFF + QML ----
        os.makedirs(output_dir, exist_ok=True)
        if hasattr(self, "_notify_info"):
            self._notify_info(
                f"Scrittura {len(grids)} GeoTIFF\u2026", duration=60
            )
        try:
            slices = write_grids_to_tifs(grids, meta, output_dir, epsg)
        except Exception as exc:
            QMessageBox.critical(
                getattr(self, "dlg", None), "Errore scrittura GeoTIFF", str(exc)
            )
            return

        # ---- 4. Sidecar params ----
        save_ogpr_slicer_params(output_dir, {
            "source_profiles": [p.path for p in profiles],
            "group_name":      group_name,
            "channel":         params["channel"],
            "combine_method":  params["combine_method"],
            "z_min":           params["z_min"],
            "z_max":           params["z_max"],
            "z_step":          params["z_step"],
            "resolution":      params["resolution"],
            "radius":          params["radius"],
            "epsg":            epsg,
            "n_slices":        len(slices),
        })

        # ---- 5. Catalogo ----
        try:
            self._register_las_slices_in_catalog(
                project_root, group_name, slices, epsg, reslice=is_reslice
            )
        except Exception as exc:
            QMessageBox.warning(
                getattr(self, "dlg", None), "Errore catalogo", str(exc)
            )

        # ---- 6. Refresh UI ----
        if hasattr(self, "populate_group_list"):
            try:
                self.populate_group_list()
            except Exception:
                pass

        action = "Re-slice" if is_reslice else "Import OGPR\u2192Slice"
        msg = (
            f"{action} completato: '{group_name}', "
            f"{len(slices)} slice, {ch_label}, "
            f"dz={params['z_step']:.3f}m."
        )
        if hasattr(self, "_notify_info"):
            self._notify_info(msg, duration=12)
        else:
            QMessageBox.information(
                getattr(self, "dlg", None), "Slice completate", msg
            )
