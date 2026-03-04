# -*- coding: utf-8 -*-
"""
GprOgprVolumeMixin
==================
Fornisce il metodo import_ogpr_as_slices(profiles) al plugin principale.
Viene chiamato da GprProfileViewer tramite:
    self.plugin.import_ogpr_as_slices(self._profiles)

Flusso:
  1. Dialog parametri (canale/combinazione, z_min/max, z_step, risoluzione, radius, gruppo)
  2. slice_ogpr_to_tifs()  -> lista di dict {path, z_from, z_to, ...}
  3. _register_las_slices_in_catalog()  (riusa GprVolumeMixin)
  4. Aggiorna lista gruppi nel Project Manager
"""

from __future__ import annotations

import os

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox,
    QLabel, QDoubleSpinBox, QComboBox, QLineEdit, QGroupBox,
    QMessageBox,
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
    # Dialog parametri
    # ------------------------------------------------------------------

    def _ask_ogpr_slice_params(
        self,
        n_channels: int,
        depth_max_m: float,
        default_group: str = "gpr_slices",
        saved: dict | None = None,
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
            default_res   = saved.get("resolution",  default_res)
            default_step  = saved.get("z_step",       default_step)
            depth_max_m   = saved.get("z_max",        depth_max_m)

        default_radius = (
            saved.get("radius", default_res * 2 ** 0.5) if saved
            else default_res * 2 ** 0.5
        )

        # --- finestra ---
        dlg = QDialog(getattr(self, "dlg", None))
        dlg.setWindowTitle("OGPR \u2192 Timeslice")
        dlg.setMinimumWidth(400)
        root = QVBoxLayout(dlg)

        root.addWidget(QLabel(
            f"Profondita' massima rilevata: <b>{depth_max_m:.3f} m</b>  —  "
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

        # --- ComboBox canali ---
        cb_ch = QComboBox()
        cb_ch.addItem("\U0001f4e1  Tutti i canali — media (consigliato)",  (-1, "mean"))
        cb_ch.addItem("\U0001f4e1  Tutti i canali — massimo",              (-1, "max"))
        for ci in range(n_channels):
            cb_ch.addItem(f"Solo canale {ci}", (ci, "mean"))
        cb_ch.setToolTip(
            "'Tutti — media': combina l'ampiezza di ogni canale con la media.\n"
            "  Riduce il rumore, risultato piu' stabile.\n"
            "'Tutti — massimo': prende il canale con ampiezza maggiore per ogni traccia.\n"
            "  Evidenzia le anomalie piu' forti.\n"
            "'Solo canale N': un singolo canale (utile per analisi per polarizzazione)."
        )
        # ripristina selezione salvata
        if saved:
            prev_ch  = saved.get("channel", -1)
            prev_comb = saved.get("combine_method", "mean")
            for i in range(cb_ch.count()):
                d = cb_ch.itemData(i)
                if d == (prev_ch, prev_comb):
                    cb_ch.setCurrentIndex(i)
                    break

        sp_zmin   = _spin(0.0,   1000.0, 4, 0.01,   saved.get("z_min",   0.0)         if saved else 0.0)
        sp_zmax   = _spin(0.001, 1000.0, 4, 0.01,   saved.get("z_max",   depth_max_m) if saved else depth_max_m)
        sp_step   = _spin(0.001,  100.0, 4, 0.005,  default_step)
        sp_res    = _spin(0.001,  100.0, 4, 0.01,   default_res)
        sp_radius = _spin(0.001,  100.0, 4, 0.01,   default_radius)
        le_group  = QLineEdit(saved.get("group_name", default_group) if saved else default_group)
        le_group.setPlaceholderText("Nome gruppo catalogo")

        fl.addRow("Canali:",          cb_ch)
        fl.addRow("Z minimo (m):",    sp_zmin)
        fl.addRow("Z massimo (m):",   sp_zmax)
        fl.addRow("Step Z (m):",      sp_step)
        fl.addRow("Risoluzione XY:",  sp_res)
        fl.addRow("Radius IDW (m):",  sp_radius)
        fl.addRow("Nome gruppo:",      le_group)
        root.addWidget(grp)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
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
    # Entry point pubblico
    # ------------------------------------------------------------------

    def import_ogpr_as_slices(self, profiles: list) -> None:
        """
        Punto di ingresso chiamato da GprProfileViewer._open_slice_dialog().

        profiles: lista di OgprProfile gia' letti da read_ogpr()
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

        # --- candidato nome gruppo basato sui file sorgente ---
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
        saved = load_ogpr_slicer_params(candidate_dir)
        is_reslice = saved is not None

        depth_max  = max(p.depth_max_m for p in profiles)
        n_channels = max(p.n_channels  for p in profiles)

        params = self._ask_ogpr_slice_params(
            n_channels  = n_channels,
            depth_max_m = depth_max,
            default_group = default_group,
            saved = saved,
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

        try:
            slices = slice_ogpr_to_tifs(
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

        # --- salva parametri sidecar ---
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

        # --- registra nel catalogo (riusa GprVolumeMixin) ---
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

        # --- aggiorna UI ---
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
