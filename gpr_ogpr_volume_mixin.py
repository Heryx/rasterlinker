# -*- coding: utf-8 -*-
"""
GprOgprVolumeMixin
==================
Fornisce il metodo import_ogpr_as_slices(profiles) al plugin principale.
Viene chiamato da GprProfileViewer tramite:
    self.plugin.import_ogpr_as_slices(self._profiles)

Flusso:
  1. Dialog parametri (canale/combinazione, z_min/max, z_step, risoluzione, radius, gruppo)
  2. compute_ogpr_slice_grids() + write_grids_to_tifs()
  3. _register_las_slices_in_catalog()  (riusa GprVolumeMixin)
  4. Aggiorna lista gruppi nel Project Manager
"""

from __future__ import annotations

import os
from collections import Counter

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox,
    QLabel, QDoubleSpinBox, QComboBox, QLineEdit, QGroupBox,
    QMessageBox, QHBoxLayout, QToolButton, QWidget, QSizePolicy,
    QCheckBox, QSpinBox,
)
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject
from .project_catalog import parse_depth_from_filename

_DEPTH_PARSE_CONF_THRESHOLD = 0.8


class GprOgprVolumeMixin:

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

        try:
            default_res = float((self.dlg.lineEditDistanceX.text() or "").strip())
        except (AttributeError, ValueError):
            default_res = 0.10
        try:
            default_step = float((self.dlg.lineEditDistanceY.text() or "").strip())
        except (AttributeError, ValueError):
            default_step = 0.05

        forced_z_step = None
        try:
            forced_z_step = float((slice_params or {}).get("z_step_override"))
            if not (forced_z_step > 0.0):
                forced_z_step = None
        except Exception:
            forced_z_step = None
        if forced_z_step is None:
            try:
                forced_z_step = float((slice_params or {}).get("thickness_m"))
                if not (forced_z_step > 0.0):
                    forced_z_step = None
            except Exception:
                forced_z_step = None

        if saved:
            default_res   = saved.get("resolution",  default_res)
            default_step  = saved.get("z_step",       default_step)
            depth_max_m   = saved.get("z_max",        depth_max_m)
        if forced_z_step is not None:
            default_step = float(forced_z_step)

        default_radius = (
            saved.get("radius", default_res * 2 ** 0.5) if saved
            else default_res * 2 ** 0.5
        )
        adv_defaults = dict(saved or {})
        adv_defaults.update(slice_params or {})

        # Attempt to infer z_min/z_max from filenames (cascading parser)
        try:
            # profiles expected to have .path attribute
            candidates = []
            for p in (profiles or []):
                fp = getattr(p, "path", None) or getattr(p, "file", None) or None
                if not fp:
                    continue
                parsed = parse_depth_from_filename(fp)
                candidates.append((parsed.get("confidence", 0.0), parsed, fp))
            # pick best candidate
            if candidates:
                best = max(candidates, key=lambda x: x[0])
                conf, parsed, src = best
                if (
                    conf >= _DEPTH_PARSE_CONF_THRESHOLD
                    and parsed.get("depth_from") is not None
                    and parsed.get("depth_to") is not None
                ):
                    default_zmin = parsed.get("depth_from")
                    default_zmax = parsed.get("depth_to")
                else:
                    default_zmin = 0.0
                    default_zmax = depth_max_m
            else:
                default_zmin = 0.0
                default_zmax = depth_max_m
        except Exception:
            default_zmin = 0.0
            default_zmax = depth_max_m

        # --- finestra ---
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
            "'Tutti — media': combina l'ampiezza di ogni canale con la media.\n"
            "  Riduce il rumore, risultato piu' stabile.\n"
            "'Tutti — massimo': prende il canale con ampiezza maggiore per ogni traccia.\n"
            "  Evidenzia le anomalie piu' forti.\n"
            "'Solo canale N': un singolo canale (utile per analisi per polarizzazione)."
        )
        if saved:
            prev_ch  = saved.get("channel", -1)
            prev_comb = saved.get("combine_method", "mean")
            for i in range(cb_ch.count()):
                if cb_ch.itemData(i) == (prev_ch, prev_comb):
                    cb_ch.setCurrentIndex(i); break

        sp_zmin   = _spin(0.0,   1000.0, 4, 0.01,   saved.get("z_min",   default_zmin) if saved else default_zmin)
        sp_zmax   = _spin(0.001, 1000.0, 4, 0.01,   saved.get("z_max",   default_zmax) if saved else default_zmax)
        sp_step   = _spin(0.001,  100.0, 4, 0.005,  default_step)
        sp_res    = _spin(0.001,  100.0, 4, 0.01,   default_res)
        sp_radius = _spin(0.001,  100.0, 4, 0.01,   default_radius)
        le_group  = QLineEdit(saved.get("group_name", default_group) if saved else default_group)
        le_group.setPlaceholderText("Nome gruppo catalogo")
        if forced_z_step is not None:
            sp_step.setValue(float(forced_z_step))
            sp_step.setEnabled(False)
            sp_step.setToolTip("Valore fissato dal pannello Timeslice del Profile Viewer.")

        sp_zmin.setToolTip("Profondita' iniziale da analizzare (metri).")
        sp_zmax.setToolTip("Profondita' finale da analizzare (metri).")
        sp_res.setToolTip(
            "Dimensione pixel in XY del raster output.\n"
            "Valori tipici: 0.05-0.10 m."
        )
        sp_radius.setToolTip(
            "Raggio IDW massimo in metri.\n"
            "Se Auto radius e' attivo nelle opzioni avanzate, puo' essere stimato dai dati."
        )
        le_group.setToolTip("Nome del gruppo catalogo per i raster generati.")

        fl.addRow("Canali:",          cb_ch)
        fl.addRow("Z minimo (m):",    sp_zmin)
        fl.addRow("Z massimo (m):",   sp_zmax)
        fl.addRow("Step Z (m):",      sp_step)
        fl.addRow("Risoluzione XY:",  sp_res)
        fl.addRow("Radius IDW (m):",  sp_radius)
        fl.addRow("Nome gruppo:",      le_group)
        root.addWidget(grp)

        def _make_collapsible_section(title: str, content_widget: QWidget):
            btn = QToolButton()
            btn.setText(f"\u25b6  {title}")
            btn.setCheckable(True)
            btn.setChecked(False)
            btn.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            content_widget.setVisible(False)

            def _toggle(checked: bool):
                content_widget.setVisible(bool(checked))
                btn.setText(f"{'\u25bc' if checked else '\u25b6'}  {title}")

            btn.toggled.connect(_toggle)
            return btn, content_widget

        # Advanced parameters: collapsed by default.
        adv_widget = QWidget(dlg)
        adv_layout = QFormLayout(adv_widget)

        cb_extraction = QComboBox()
        cb_extraction.addItem("LAS-like (abs)", "las_like")
        cb_extraction.addItem("Envelope (Hilbert)", "envelope")
        cb_extraction.addItem("Signed amplitude", "signed")
        _ex_mode = str(adv_defaults.get("extraction_mode", "las_like") or "las_like")
        for i in range(cb_extraction.count()):
            if cb_extraction.itemData(i) == _ex_mode:
                cb_extraction.setCurrentIndex(i)
                break
        cb_extraction.setToolTip("Metodo di estrazione ampiezza prima dell'interpolazione.")

        cb_idw_mode = QComboBox()
        cb_idw_mode.addItem("Quality (radius)", "quality")
        cb_idw_mode.addItem("Fast (kNN)", "fast")
        _idw_mode = str(adv_defaults.get("idw_mode", "quality") or "quality")
        for i in range(cb_idw_mode.count()):
            if cb_idw_mode.itemData(i) == _idw_mode:
                cb_idw_mode.setCurrentIndex(i)
                break
        cb_idw_mode.setToolTip("Quality: piu' fedele; Fast: piu' veloce su griglie grandi.")

        sp_idw_power = QSpinBox()
        sp_idw_power.setRange(1, 4)
        sp_idw_power.setValue(int(adv_defaults.get("idw_power", 2) or 2))
        sp_idw_power.setToolTip("Esponente IDW (p). 2 standard, >2 enfatizza vicinanza.")

        sp_min_points = QSpinBox()
        sp_min_points.setRange(1, 12)
        sp_min_points.setValue(int(adv_defaults.get("min_points", 1) or 1))
        sp_min_points.setToolTip("Numero minimo di punti per stimare una cella.")

        sp_overlap = QSpinBox()
        sp_overlap.setRange(0, 90)
        sp_overlap.setSingleStep(5)
        sp_overlap.setSuffix(" %")
        sp_overlap.setValue(int(round(float(adv_defaults.get("overlap_fraction", 0.5) or 0.0) * 100.0)))
        sp_overlap.setToolTip("Overlap verticale tra slice consecutive.")

        sp_blanking = QDoubleSpinBox()
        sp_blanking.setRange(0.0, 100.0)
        sp_blanking.setDecimals(3)
        sp_blanking.setSingleStep(0.05)
        sp_blanking.setValue(float(adv_defaults.get("blanking_distance", 0.0) or 0.0))
        sp_blanking.setToolTip("Distanza massima dai dati reali; oltre questa soglia la cella e' NoData.")

        chk_use_hilbert = QCheckBox()
        chk_use_hilbert.setChecked(bool(adv_defaults.get("use_hilbert", True)))
        chk_use_hilbert.setToolTip("Usa envelope Hilbert come default per ampiezza.")

        chk_auto_radius = QCheckBox()
        chk_auto_radius.setChecked(bool(adv_defaults.get("auto_radius", True)))
        chk_auto_radius.setToolTip("Stima automaticamente il raggio IDW dai dati.")

        chk_aniso = QCheckBox()
        chk_aniso.setChecked(bool(adv_defaults.get("use_anisotropic_idw", False)))
        chk_aniso.setToolTip("Attiva distanza anisotropa lungo/tra profili.")

        chk_fill_nodata = QCheckBox()
        chk_fill_nodata.setChecked(bool(adv_defaults.get("fill_nodata", True)))
        chk_fill_nodata.setToolTip("Riempie celle vuote entro distanza limite.")

        chk_balance = QCheckBox()
        chk_balance.setChecked(bool(adv_defaults.get("balance_profiles", True)))
        chk_balance.setToolTip("Bilancia ampiezza media tra profili diversi.")

        sp_smooth = QDoubleSpinBox()
        sp_smooth.setRange(0.0, 10.0)
        sp_smooth.setDecimals(2)
        sp_smooth.setSingleStep(0.1)
        sp_smooth.setValue(float(adv_defaults.get("smooth_sigma", 0.8) or 0.0))
        sp_smooth.setToolTip("Sigma smoothing gaussiano post-IDW (0=off).")

        adv_layout.addRow("Estrazione:", cb_extraction)
        adv_layout.addRow("IDW mode:", cb_idw_mode)
        adv_layout.addRow("IDW power:", sp_idw_power)
        adv_layout.addRow("Min punti:", sp_min_points)
        adv_layout.addRow("Overlap slice:", sp_overlap)
        adv_layout.addRow("Blanking (m):", sp_blanking)
        adv_layout.addRow("Usa Hilbert:", chk_use_hilbert)
        adv_layout.addRow("Auto radius:", chk_auto_radius)
        adv_layout.addRow("IDW anisotropo:", chk_aniso)
        adv_layout.addRow("Fill nodata:", chk_fill_nodata)
        adv_layout.addRow("Smoothing sigma:", sp_smooth)
        adv_layout.addRow("Balance profili:", chk_balance)

        btn_adv, adv_widget = _make_collapsible_section("Opzioni avanzate", adv_widget)
        root.addWidget(btn_adv)
        root.addWidget(adv_widget)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btns)
        root.addLayout(btn_row)

        if dlg.exec_() != QDialog.Accepted:
            return None

        ch_val, comb_val = cb_ch.currentData()
        z_step_value = sp_step.value()
        if forced_z_step is not None:
            z_step_value = float(forced_z_step)
        return {
            "channel":        ch_val,
            "combine_method": comb_val,
            "z_min":          sp_zmin.value(),
            "z_max":          sp_zmax.value(),
            "z_step":         z_step_value,
            "resolution":     sp_res.value(),
            "radius":         sp_radius.value(),
            "group_name":     le_group.text().strip() or default_group,
            "advanced": {
                "extraction_mode": str(cb_extraction.currentData() or "las_like"),
                "idw_mode": str(cb_idw_mode.currentData() or "quality"),
                "idw_power": int(sp_idw_power.value()),
                "min_points": int(sp_min_points.value()),
                "overlap_fraction": float(sp_overlap.value()) / 100.0,
                "blanking_distance": float(sp_blanking.value()),
                "use_hilbert": bool(chk_use_hilbert.isChecked()),
                "auto_radius": bool(chk_auto_radius.isChecked()),
                "use_anisotropic_idw": bool(chk_aniso.isChecked()),
                "fill_nodata": bool(chk_fill_nodata.isChecked()),
                "smooth_sigma": float(sp_smooth.value()),
                "balance_profiles": bool(chk_balance.isChecked()),
            },
        }

    # ------------------------------------------------------------------
    # Entry point pubblico
    # ------------------------------------------------------------------

    def import_ogpr_as_slices(
        self,
        profiles: list,
        slice_params: dict | None = None,
        completion_callback=None,
    ) -> None:
        """
        Punto di ingresso chiamato da GprProfileViewer._open_slice_dialog().

        profiles: lista di OgprProfile gia' letti da read_ogpr()
        """
        from .gpr_ogpr_slicer import (
            load_ogpr_slicer_params,
            save_ogpr_slicer_params,
        )
        from .background_tasks import OgprSliceBuildTask, start_task_with_progress_dialog

        if not profiles:
            QMessageBox.information(
                getattr(self, "dlg", None), "Nessun profilo",
                "Importa almeno un file .ogpr prima di creare le timeslice.",
            )
            return
        if bool(getattr(self, "_ogpr_slice_task_active", False)):
            QMessageBox.information(
                getattr(self, "dlg", None),
                "Generazione in corso",
                "Una generazione OGPR->Timeslice e' gia' in esecuzione.",
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
            profiles=profiles,
            n_channels  = n_channels,
            depth_max_m = depth_max,
            default_group = default_group,
            saved = saved,
            slice_params=slice_params,
        )
        if params is None:
            return
        params = dict(params or {})
        dialog_extra_slice_params = dict(params.pop("advanced", {}) or {})

        group_name = params["group_name"]
        output_dir = os.path.join(project_root, "timeslices_2d", group_name)
        out_override = str((slice_params or {}).get("output_dir") or "").strip()
        if out_override:
            if not os.path.isabs(out_override):
                out_override = os.path.normpath(os.path.join(project_root, out_override))
            output_dir = out_override
        epsg = QgsProject.instance().crs().postgisSrid() or None
        if not epsg:
            epsg_vals = []
            for p in profiles:
                try:
                    v = int(getattr(p, "epsg", 0) or 0)
                except Exception:
                    v = 0
                if v > 0:
                    epsg_vals.append(v)
            if epsg_vals:
                epsg = int(Counter(epsg_vals).most_common(1)[0][0])
                if hasattr(self, "_notify_info"):
                    self._notify_info(
                        f"CRS progetto non impostato: uso EPSG:{epsg} dai profili OGPR.",
                        duration=8,
                    )

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

        # Parametri avanzati: priorita' ai controlli live del viewer; fallback al sidecar.
        extra_slice_params = dict(saved or {})
        extra_slice_params.update(slice_params or {})
        extra_slice_params.update(dialog_extra_slice_params)

        def _on_task_done(done_task, ok):
            cb_ok = False
            cb_payload = {
                "ok": bool(ok),
                "output_dir": output_dir,
                "group_name": group_name,
                "z_step": float(params.get("z_step", 0.0) or 0.0),
                "z_min": float(params.get("z_min", 0.0) or 0.0),
                "z_max": float(params.get("z_max", 0.0) or 0.0),
                "slices": [],
                "error": "",
            }
            try:
                if not ok:
                    if bool(getattr(done_task, "cancelled", False)):
                        cb_payload["error"] = "Operazione annullata."
                        QMessageBox.information(
                            getattr(self, "dlg", None),
                            "OGPR -> Timeslice",
                            "Operazione annullata.",
                        )
                    else:
                        err = str(getattr(done_task, "error_message", "") or "Errore sconosciuto")
                        cb_payload["error"] = err
                        QMessageBox.critical(
                            getattr(self, "dlg", None),
                            "Errore OGPR -> Timeslice",
                            err,
                        )
                    return

                slices = list(getattr(done_task, "slices", []) or [])
                task_meta = dict(getattr(done_task, "meta", {}) or {})
                epsg_written = task_meta.get("epsg_written", epsg)
                georef_warning = str(task_meta.get("georef_warning", "") or "").strip()
                timing_s = task_meta.get("timing_s") if isinstance(task_meta.get("timing_s"), dict) else {}
                task_timing_s = (
                    task_meta.get("task_timing_s")
                    if isinstance(task_meta.get("task_timing_s"), dict)
                    else {}
                )
                if georef_warning:
                    try:
                        self.iface.messageBar().pushWarning("GeoSurvey Studio", georef_warning)
                    except Exception:
                        QMessageBox.warning(
                            getattr(self, "dlg", None),
                            "Georeferenziazione OGPR",
                            georef_warning,
                        )
                if bool(getattr(done_task, "no_grids", False)) or not slices:
                    cb_payload["error"] = (
                        f"Nessun punto nel range Z [{params['z_min']:.4f}, {params['z_max']:.4f}] m."
                    )
                    QMessageBox.warning(
                        getattr(self, "dlg", None), "Nessuna slice prodotta",
                        f"Nessun punto nel range Z [{params['z_min']:.4f}, "
                        f"{params['z_max']:.4f}] m.\n"
                        "Verifica che i profili abbiano coordinate valide.",
                    )
                    return

                # --- salva parametri sidecar ---
                sidecar_params = {
                    "source_profiles": [p.path for p in profiles],
                    "group_name":      group_name,
                    "channel":         params["channel"],
                    "combine_method":  params["combine_method"],
                    "z_min":           params["z_min"],
                    "z_max":           params["z_max"],
                    "z_step":          params["z_step"],
                    "resolution":      params["resolution"],
                    "radius":          params["radius"],
                    "epsg":            epsg_written,
                    "n_slices":        len(slices),
                    "normalize_channels": bool(extra_slice_params.get("normalize_channels", False)),
                    "extraction_mode": str(extra_slice_params.get("extraction_mode", "las_like") or "las_like"),
                    "use_hilbert": bool(extra_slice_params.get("use_hilbert", True)),
                    "use_processing": bool(extra_slice_params.get("use_processing", False)),
                    "pre_slice_bg_removal": bool(extra_slice_params.get("pre_slice_bg_removal", False)),
                    "pre_slice_bg_mode": str(extra_slice_params.get("pre_slice_bg_mode", "line_by_line") or "line_by_line"),
                    "pre_slice_bg_window": int(extra_slice_params.get("pre_slice_bg_window", 0) or 0),
                    "pre_slice_bg_sample_start": int(extra_slice_params.get("pre_slice_bg_sample_start", 0) or 0),
                    "pre_slice_bg_sample_end": int(extra_slice_params.get("pre_slice_bg_sample_end", 0) or 0),
                    "stack_n": int(extra_slice_params.get("stack_n", 1) or 1),
                    "stack_kernel": str(extra_slice_params.get("stack_kernel", "boxcar") or "boxcar"),
                    "flip_traces_mode": str(extra_slice_params.get("flip_traces_mode", "none") or "none"),
                    "pipeline_params": dict(extra_slice_params.get("pipeline_params") or {}),
                    "topographic_correction": bool(extra_slice_params.get("topographic_correction", False)),
                    "topo_reference_mode": str(extra_slice_params.get("topo_reference_mode", "median") or "median"),
                    "topo_reference_elevation": extra_slice_params.get("topo_reference_elevation"),
                    "use_anisotropic_idw": bool(extra_slice_params.get("use_anisotropic_idw", False)),
                    "idw_mode": str(extra_slice_params.get("idw_mode", "quality") or "quality"),
                    "idw_power": int(extra_slice_params.get("idw_power", 2) or 2),
                    "overlap_fraction": float(extra_slice_params.get("overlap_fraction", 0.5) or 0.0),
                    "blanking_distance": float(extra_slice_params.get("blanking_distance", 0.0) or 0.0),
                    "parallel_profiles": bool(extra_slice_params.get("parallel_profiles", True)),
                    "profile_workers": int(extra_slice_params.get("profile_workers", 0) or 0),
                    "auto_radius": bool(extra_slice_params.get("auto_radius", True)),
                    "min_points": int(extra_slice_params.get("min_points", 1) or 1),
                    "fill_nodata": bool(extra_slice_params.get("fill_nodata", True)),
                    "smooth_sigma": float(extra_slice_params.get("smooth_sigma", 0.8) or 0.0),
                    "depth_radius_factor": float(extra_slice_params.get("depth_radius_factor", 0.6) or 0.0),
                    "balance_profiles": bool(extra_slice_params.get("balance_profiles", True)),
                    "open_3d_on_complete": bool(extra_slice_params.get("open_3d_on_complete", True)),
                }
                if timing_s:
                    sidecar_params["timing_s"] = dict(timing_s)
                if task_timing_s:
                    sidecar_params["task_timing_s"] = dict(task_timing_s)
                if extra_slice_params.get("amplitude_sigma") is not None:
                    sidecar_params["amplitude_sigma"] = extra_slice_params.get("amplitude_sigma")
                save_ogpr_slicer_params(output_dir, sidecar_params)

                # --- registra nel catalogo (riusa GprVolumeMixin) ---
                try:
                    self._register_las_slices_in_catalog(
                        project_root, group_name, slices, epsg_written, reslice=is_reslice
                    )
                except Exception as exc:
                    QMessageBox.warning(
                        getattr(self, "dlg", None), "Errore catalogo", str(exc)
                    )

                # --- aggiorna UI ---
                if hasattr(self, "populate_group_list"):
                    try:
                        self.populate_group_list()
                    except Exception:
                        pass

                # --- apri viewer 3D con il volume appena calcolato (opzionale) ---
                if bool(extra_slice_params.get("open_3d_on_complete", True)):
                    try:
                        grids_for_viewer = list(getattr(done_task, "grids", []) or [])
                        meta_for_viewer = dict(getattr(done_task, "meta", {}) or {})
                        if grids_for_viewer and meta_for_viewer:
                            from .gpr_volume_3d import build_3d_volume
                            from .gpr_3d_viewer_dialog import Gpr3dViewerDialog

                            z_levels = []
                            for g in grids_for_viewer:
                                try:
                                    z_levels.append(float(g.get("z_lev")))
                                except Exception:
                                    continue
                            z_levels = [z for z in z_levels if z == z]
                            z_min_view = float(min(z_levels)) if z_levels else float(params["z_min"])
                            z_max_view = float(max(z_levels)) if z_levels else float(params["z_max"])

                            if "x_min" not in meta_for_viewer or "y_min" not in meta_for_viewer:
                                x_vals = []
                                y_vals = []
                                for prof in profiles:
                                    try:
                                        ch0 = prof.channel(0)
                                    except Exception:
                                        continue
                                    for x in list(getattr(ch0, "easting", []) or []):
                                        try:
                                            xf = float(x)
                                        except Exception:
                                            continue
                                        if xf == xf:
                                            x_vals.append(xf)
                                    for y in list(getattr(ch0, "northing", []) or []):
                                        try:
                                            yf = float(y)
                                        except Exception:
                                            continue
                                        if yf == yf:
                                            y_vals.append(yf)
                                if x_vals and "x_min" not in meta_for_viewer:
                                    meta_for_viewer["x_min"] = float(min(x_vals))
                                if y_vals and "y_min" not in meta_for_viewer:
                                    meta_for_viewer["y_min"] = float(min(y_vals))

                            meta_for_viewer.update(
                                {
                                    "z_step": float(params["z_step"]),
                                    "z_min": z_min_view,
                                    "z_max": z_max_view,
                                    "z_levels": [float(z) for z in z_levels] if z_levels else None,
                                    "epsg": int(epsg_written) if epsg_written else None,
                                    "channel": int(params.get("channel", 0) or 0),
                                    "use_processing": bool(extra_slice_params.get("use_processing", False)),
                                    "extraction_mode": str(extra_slice_params.get("extraction_mode", "las_like") or "las_like"),
                                    "use_hilbert": bool(extra_slice_params.get("use_hilbert", True)),
                                    "overlap_fraction": float(extra_slice_params.get("overlap_fraction", 0.5) or 0.0),
                                    "blanking_distance": float(extra_slice_params.get("blanking_distance", 0.0) or 0.0),
                                    "idw_power": int(extra_slice_params.get("idw_power", 2) or 2),
                                    "pipeline_params": dict(extra_slice_params.get("pipeline_params") or {}),
                                }
                            )

                            volume = build_3d_volume(grids_for_viewer, meta_for_viewer)
                            old_viewer = getattr(self, "_ogpr_3d_viewer_dialog", None)
                            if old_viewer is not None:
                                try:
                                    old_viewer.close()
                                except Exception:
                                    pass
                            viewer = Gpr3dViewerDialog(
                                volume=volume,
                                meta=meta_for_viewer,
                                profiles=profiles,
                                grids=grids_for_viewer,
                                parent=getattr(self, "dlg", None),
                            )
                            self._ogpr_3d_viewer_dialog = viewer
                            viewer.show()
                            viewer.raise_()
                            viewer.activateWindow()
                    except Exception as exc:
                        if hasattr(self, "_notify_info"):
                            self._notify_info(
                                f"Slice create: viewer 3D non aperto ({exc}).",
                                duration=10,
                            )

                action = "Re-slice" if is_reslice else "Import OGPR->Slice"
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
                cb_ok = True
                cb_payload.update(
                    {
                        "ok": True,
                        "slices": list(slices or []),
                        "epsg": epsg_written,
                        "meta": dict(task_meta or {}),
                    }
                )
                if hasattr(self, "_notify_info") and task_timing_s:
                    try:
                        self._notify_info(
                            "Timing OGPR->Slice: "
                            f"tot={float(task_timing_s.get('total', 0.0)):.2f}s, "
                            f"compute={float(task_timing_s.get('compute', 0.0)):.2f}s, "
                            f"write={float(task_timing_s.get('write', 0.0)):.2f}s.",
                            duration=12,
                        )
                    except Exception:
                        pass
            finally:
                if callable(completion_callback):
                    try:
                        completion_callback(bool(cb_ok), dict(cb_payload))
                    except Exception:
                        pass
                self._ogpr_slice_task_active = False

        build_task = OgprSliceBuildTask(
            profiles=profiles,
            params=params,
            extra_slice_params=extra_slice_params,
            output_dir=output_dir,
            epsg=epsg,
        )
        self._ogpr_slice_task_active = True
        start_task_with_progress_dialog(
            build_task,
            getattr(self, "dlg", None),
            "Generazione slice OGPR in corso...",
            "OGPR -> Timeslice",
            on_finished=_on_task_done,
        )
        if hasattr(self, "_notify_info"):
            self._notify_info("OGPR->Slice avviato in background.", duration=8)
