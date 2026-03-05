# -*- coding: utf-8 -*-
"""
GprOgprVolumeMixin
==================
Fornisce il metodo import_ogpr_as_slices(profiles, slice_params) al plugin principale.
Viene chiamato da GprProfileViewer tramite:
    self.plugin.import_ogpr_as_slices(self._profiles, slice_params=...)

Flusso:
  1. Dialog parametri (canale/combinazione, z_min/max, z_step, risoluzione, radius, gruppo)
     con tasto Anteprima per verificare radius/risoluzione senza scrivere file.
  2. slice_ogpr_to_tifs()  -> lista di dict {path, z_from, z_to, ...}
  3. _register_las_slices_in_catalog()  (riusa GprVolumeMixin)
  4. Aggiorna lista gruppi nel Project Manager
"""

from __future__ import annotations

import os

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout,
    QLabel, QDoubleSpinBox, QComboBox, QLineEdit, QGroupBox,
    QMessageBox, QPushButton, QHBoxLayout, QSizePolicy,
    QDialogButtonBox,
)
from qgis.core import QgsProject


class GprOgprVolumeMixin:
    """
    Aggiungere questa classe alla lista di ereditarieta' del plugin principale
    dopo GprVolumeMixin:

        class GeoSurveyStudioPlugin(
            ...,
            GprVolumeMixin,
            GprOgprVolumeMixin,
            ...
        ):
    """

    # ------------------------------------------------------------------
    # Dialog parametri + anteprima
    # ------------------------------------------------------------------

    def _ask_ogpr_slice_params(
        self,
        profiles: list,
        n_channels: int,
        depth_max_m: float,
        default_group: str = "gpr_slices",
        saved: dict | None = None,
        slice_params: dict | None = None,
    ) -> dict | None:
        """Mostra dialog di configurazione slice OGPR. Ritorna None se annullato."""

        try:
            default_res = float(
                (self.dlg.lineEditDistanceX.text() or "").strip()
            )
        except (AttributeError, ValueError):
            default_res = 0.10
        try:
            default_step = float(
                (self.dlg.lineEditDistanceY.text() or "").strip()
            )
        except (AttributeError, ValueError):
            default_step = 0.05

        if saved:
            default_res  = saved.get("resolution", default_res)
            default_step = saved.get("z_step",     default_step)
            depth_max_m  = saved.get("z_max",      depth_max_m)

        default_radius = (
            saved.get("radius", default_res * 2 ** 0.5) if saved
            else default_res * 2 ** 0.5
        )

        # ------------------------------------------------------------------
        # Finestra
        # ------------------------------------------------------------------
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
            s.setRange(lo, hi)
            s.setDecimals(dec)
            s.setSingleStep(step)
            s.setValue(val)
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
            "'Solo canale N': un singolo canale."
        )
        if saved:
            prev_ch   = saved.get("channel", -1)
            prev_comb = saved.get("combine_method", "mean")
            for i in range(cb_ch.count()):
                d = cb_ch.itemData(i)
                if d == (prev_ch, prev_comb):
                    cb_ch.setCurrentIndex(i)
                    break

        sp_zmin   = _spin(0.0,   1000.0, 4, 0.01,  saved.get("z_min",   0.0)         if saved else 0.0)
        sp_zmax   = _spin(0.001, 1000.0, 4, 0.01,  saved.get("z_max",   depth_max_m) if saved else depth_max_m)
        sp_step   = _spin(0.001,  100.0, 4, 0.005, default_step)
        sp_res    = _spin(0.001,  100.0, 4, 0.01,  default_res)
        sp_radius = _spin(0.001,  100.0, 4, 0.01,  default_radius)
        le_group  = QLineEdit(saved.get("group_name", default_group) if saved else default_group)
        le_group.setPlaceholderText("Nome gruppo catalogo")

        sp_radius.setToolTip(
            "Raggio IDW in metri.\n"
            "Deve essere >= meta' della spaziatura inter-linea.\n"
            "Usa il tasto \"Anteprima\" per verificare prima del run completo."
        )

        fl.addRow("Canali:",         cb_ch)
        fl.addRow("Z minimo (m):",   sp_zmin)
        fl.addRow("Z massimo (m):",  sp_zmax)
        fl.addRow("Step Z (m):",     sp_step)
        fl.addRow("Risoluzione XY:", sp_res)
        fl.addRow("Radius IDW (m):", sp_radius)
        fl.addRow("Nome gruppo:",    le_group)
        root.addWidget(grp)

        # ------------------------------------------------------------------
        # Tasto Anteprima
        # ------------------------------------------------------------------
        def _on_preview():
            from .gpr_ogpr_slicer import compute_preview_slice

            ch_val, comb_val = cb_ch.currentData()
            sp = slice_params or {}
            z_c = (sp_zmin.value() + sp_zmax.value()) / 2.0

            try:
                result = compute_preview_slice(
                    profiles        = profiles,
                    z_center        = z_c,
                    channel         = ch_val,
                    combine_method  = comb_val,
                    resolution      = sp_res.value(),
                    z_step          = sp_step.value(),
                    radius          = sp_radius.value(),
                    normalize_channels  = sp.get("normalize_channels", True),
                    amplitude_sigma     = sp.get("amplitude_sigma"),
                    use_anisotropic_idw = sp.get("use_anisotropic_idw", False),
                    auto_radius         = sp.get("auto_radius", False),
                    fill_nodata         = sp.get("fill_nodata", False),
                    smooth_sigma        = sp.get("smooth_sigma", 0.0),
                )
            except Exception as exc:
                QMessageBox.critical(dlg, "Errore anteprima", str(exc))
                return

            if result is None:
                QMessageBox.warning(
                    dlg, "Anteprima vuota",
                    f"Nessun punto nel range Z [{sp_zmin.value():.4f}, {sp_zmax.value():.4f}] m.\n"
                    "Verifica Z min/max e Step Z."
                )
                return

            self._show_preview_slice(result, parent=dlg)

        btn_preview = QPushButton("\U0001f50d  Anteprima")
        btn_preview.setToolTip(
            "Calcola e visualizza una singola slice al livello Z centrale\n"
            "senza scrivere file su disco.\n\n"
            "Utile per verificare Radius IDW e Risoluzione XY prima del run completo.\n"
            "Se meno del 30% delle celle e' valido, il radius e' troppo piccolo."
        )
        btn_preview.clicked.connect(_on_preview)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)

        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_preview)
        btn_row.addStretch()
        btn_row.addWidget(btns)
        root.addLayout(btn_row)

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
    # Popup matplotlib anteprima
    # ------------------------------------------------------------------

    def _show_preview_slice(self, result: dict, parent=None) -> None:
        """
        Mostra la slice di anteprima in un QDialog non-modale con matplotlib.
        Riporta:
          - imshow della griglia con colorbar
          - percentuale celle valide (indicatore qualita' del radius)
          - warning rosso se fill_pct < 30% (radius troppo piccolo)
        """
        try:
            from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError:
            QMessageBox.warning(
                parent or getattr(self, "dlg", None),
                "matplotlib non trovato",
                "Installa matplotlib per usare l'anteprima:\n"
                "  pip install matplotlib",
            )
            return

        grid       = result["grid"]
        x_min      = result["x_min"]
        y_min      = result["y_min"]
        resolution = result["resolution"]
        z_from     = result["z_from"]
        z_to       = result["z_to"]
        n_pts      = result["n_pts"]
        n_valid    = result["n_valid_cells"]
        n_total    = result["n_total_cells"]
        radius     = result["radius"]
        fill_pct   = result["fill_pct"]

        n_y, n_x = grid.shape
        x_max    = x_min + n_x * resolution
        y_max    = y_min + n_y * resolution

        prev_dlg = QDialog(parent or getattr(self, "dlg", None))
        prev_dlg.setWindowTitle(
            f"Anteprima  z = [{z_from:.3f} \u2013 {z_to:.3f}] m"
        )
        prev_dlg.resize(720, 560)
        layout = QVBoxLayout(prev_dlg)

        fig = Figure(figsize=(8, 5), tight_layout=True)
        ax  = fig.add_subplot(111)

        im = ax.imshow(
            grid,
            origin="lower",
            extent=[x_min, x_max, y_min, y_max],
            cmap="RdBu_r",
            aspect="equal",
            interpolation="nearest",
        )
        fig.colorbar(im, ax=ax, label="Ampiezza norm.")
        ax.set_xlabel("Easting (m)")
        ax.set_ylabel("Northing (m)")
        ax.set_title(
            f"z = [{z_from:.3f} \u2013 {z_to:.3f}] m  |  "
            f"n_pts={n_pts}  radius={radius:.3f} m  |  "
            f"celle valide = {fill_pct:.0f}%",
            fontsize=9,
        )

        canvas = FigureCanvasQTAgg(fig)
        canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(canvas)

        # Info bar
        lbl_info = QLabel(
            f"Grid {n_x}\u00d7{n_y}  \u2014  res = {resolution:.4f} m  \u2014  "
            f"{n_valid}/{n_total} celle valide ({fill_pct:.0f}%)  \u2014  "
            f"radius = {radius:.3f} m"
        )
        layout.addWidget(lbl_info)

        # Warning se il radius e' chiaramente troppo piccolo
        if fill_pct < 30.0:
            lbl_warn = QLabel(
                "\u26a0\ufe0f  Meno del 30% delle celle e' stato interpolato.\n"
                "Aumenta Radius IDW (almeno pari alla spaziatura inter-linea) "
                "e/o disabilita 'raggio auto'."
            )
            lbl_warn.setStyleSheet(
                "color: #cc0000; font-weight: bold; "
                "background: #fff3cd; padding: 4px; border-radius: 3px;"
            )
            lbl_warn.setWordWrap(True)
            layout.addWidget(lbl_warn)
        elif fill_pct < 60.0:
            lbl_warn = QLabel(
                "\u26a0  Circa il 30-60% delle celle e' valido.\n"
                "Considera di aumentare Radius IDW o attivare Fill NoData."
            )
            lbl_warn.setStyleSheet(
                "color: #856404; font-weight: bold; "
                "background: #fff3cd; padding: 4px; border-radius: 3px;"
            )
            lbl_warn.setWordWrap(True)
            layout.addWidget(lbl_warn)

        btn_close = QPushButton("Chiudi")
        btn_close.clicked.connect(prev_dlg.close)
        layout.addWidget(btn_close)

        # Non-modale: l'utente puo' tornare al dialog principale
        # e cambiare radius, poi cliccare di nuovo Anteprima
        prev_dlg.setModal(False)
        prev_dlg.show()
        canvas.draw()

        # Mantiene il riferimento per evitare garbage collection
        self._preview_dlg = prev_dlg

    # ------------------------------------------------------------------
    # Entry point pubblico
    # ------------------------------------------------------------------

    def import_ogpr_as_slices(
        self,
        profiles: list,
        slice_params: dict | None = None,
    ) -> None:
        """
        Punto di ingresso chiamato da GprProfileViewer._open_slice_dialog().

        Parameters
        ----------
        profiles : list[OgprProfile]
        slice_params : dict | None
            Parametri opzionali di filtraggio/interpolazione dalla GUI viewer.
        """
        from .gpr_ogpr_slicer import (
            slice_ogpr_to_tifs,
            save_ogpr_slicer_params,
            load_ogpr_slicer_params,
        )

        if not profiles:
            QMessageBox.information(
                getattr(self, "dlg", None),
                "Nessun profilo",
                "Importa almeno un file .ogpr prima di creare le timeslice.",
            )
            return

        project_root = self._gpr_active_project_root()
        if not project_root:
            QMessageBox.warning(
                getattr(self, "dlg", None),
                "Nessun progetto attivo",
                "Apri un progetto nel Project Manager prima di importare.",
            )
            return

        base_names = list({
            os.path.splitext(os.path.basename(p.path))[0]
            for p in profiles
        })
        default_group = (
            base_names[0] if len(base_names) == 1
            else "gpr_slices"
        )
        candidate_dir = os.path.join(
            project_root, "timeslices_2d", default_group
        )
        saved      = load_ogpr_slicer_params(candidate_dir)
        is_reslice = saved is not None

        depth_max  = max(p.depth_max_m for p in profiles)
        n_channels = max(p.n_channels  for p in profiles)

        params = self._ask_ogpr_slice_params(
            profiles      = profiles,
            n_channels    = n_channels,
            depth_max_m   = depth_max,
            default_group = default_group,
            saved         = saved,
            slice_params  = slice_params,
        )
        if params is None:
            return

        group_name = params["group_name"]
        output_dir = os.path.join(project_root, "timeslices_2d", group_name)
        os.makedirs(output_dir, exist_ok=True)

        epsg = QgsProject.instance().crs().postgisSrid() or None

        ch_label = (
            f"tutti ({params['combine_method']})"
            if params["channel"] < 0
            else f"canale {params['channel']}"
        )
        if hasattr(self, "_notify_info"):
            self._notify_info(
                f"Generazione slice OGPR: {len(profiles)} profili, "
                f"{ch_label}, "
                f"dz={params['z_step']:.3f}m, res={params['resolution']:.3f}m\u2026",
                duration=60,
            )

        sp = slice_params or {}
        slicer_kwargs = dict(
            profiles       = profiles,
            output_dir     = output_dir,
            channel        = params["channel"],
            combine_method = params["combine_method"],
            resolution     = params["resolution"],
            z_step         = params["z_step"],
            z_min          = params["z_min"],
            z_max          = params["z_max"],
            radius         = params["radius"],
            epsg           = epsg,
        )
        _optional_keys = (
            "normalize_channels",
            "amplitude_sigma",
            "use_anisotropic_idw",
            "auto_radius",
            "fill_nodata",
            "smooth_sigma",
        )
        for k in _optional_keys:
            if k in sp:
                slicer_kwargs[k] = sp[k]

        try:
            slices = slice_ogpr_to_tifs(**slicer_kwargs)
        except Exception as exc:
            QMessageBox.critical(
                getattr(self, "dlg", None),
                "Errore generazione slice",
                str(exc),
            )
            return

        if not slices:
            QMessageBox.warning(
                getattr(self, "dlg", None),
                "Nessuna slice prodotta",
                f"Nessun punto nel range Z [{params['z_min']:.4f}, "
                f"{params['z_max']:.4f}] m.\n"
                "Verifica che i profili abbiano coordinate valide.",
            )
            return

        save_ogpr_slicer_params(output_dir, {
            "source_profiles":  [p.path for p in profiles],
            "group_name":       group_name,
            "channel":          params["channel"],
            "combine_method":   params["combine_method"],
            "z_min":            params["z_min"],
            "z_max":            params["z_max"],
            "z_step":           params["z_step"],
            "resolution":       params["resolution"],
            "radius":           params["radius"],
            "epsg":             epsg,
            "n_slices":         len(slices),
            "slice_params":     sp,
        })

        try:
            self._register_las_slices_in_catalog(
                project_root, group_name, slices, epsg, reslice=is_reslice
            )
        except Exception as exc:
            QMessageBox.warning(
                getattr(self, "dlg", None),
                "Errore catalogo",
                str(exc),
            )

        if hasattr(self, "populate_group_list"):
            try:
                self.populate_group_list()
            except Exception:
                pass

        action = "Re-slice" if is_reslice else "Import OGPR\u2192Slice"
        msg    = (
            f"{action} completato: '{group_name}', "
            f"{len(slices)} slice, "
            f"{ch_label}, "
            f"dz={params['z_step']:.3f}m."
        )
        if hasattr(self, "_notify_info"):
            self._notify_info(msg, duration=12)
        else:
            QMessageBox.information(
                getattr(self, "dlg", None), "Slice completate", msg
            )
