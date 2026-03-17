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
    QMessageBox, QHBoxLayout,
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

        if saved:
            default_res   = saved.get("resolution",  default_res)
            default_step  = saved.get("z_step",       default_step)
            depth_max_m   = saved.get("z_max",        depth_max_m)

        default_radius = (
            saved.get("radius", default_res * 2 ** 0.5) if saved
            else default_res * 2 ** 0.5
        )

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

        btn_row = QHBoxLayout()
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
    # Entry point pubblico
    # ------------------------------------------------------------------

    def import_ogpr_as_slices(
        self,
        profiles: list,
        slice_params: dict | None = None,
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

        group_name = params["group_name"]
        output_dir = os.path.join(project_root, "timeslices_2d", group_name)
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

        def _on_task_done(done_task, ok):
            try:
                if not ok:
                    if bool(getattr(done_task, "cancelled", False)):
                        QMessageBox.information(
                            getattr(self, "dlg", None),
                            "OGPR -> Timeslice",
                            "Operazione annullata.",
                        )
                    else:
                        err = str(getattr(done_task, "error_message", "") or "Errore sconosciuto")
                        QMessageBox.critical(
                            getattr(self, "dlg", None),
                            "Errore OGPR -> Timeslice",
                            err,
                        )
                    return

                slices = list(getattr(done_task, "slices", []) or [])
                if bool(getattr(done_task, "no_grids", False)) or not slices:
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
                    "epsg":            epsg,
                    "n_slices":        len(slices),
                    "normalize_channels": bool(extra_slice_params.get("normalize_channels", False)),
                    "extraction_mode": str(extra_slice_params.get("extraction_mode", "las_like") or "las_like"),
                    "use_processing": bool(extra_slice_params.get("use_processing", False)),
                    "use_anisotropic_idw": bool(extra_slice_params.get("use_anisotropic_idw", False)),
                    "auto_radius": bool(extra_slice_params.get("auto_radius", False)),
                    "min_points": int(extra_slice_params.get("min_points", 1) or 1),
                    "fill_nodata": bool(extra_slice_params.get("fill_nodata", False)),
                    "smooth_sigma": float(extra_slice_params.get("smooth_sigma", 0.0) or 0.0),
                    "depth_radius_factor": float(extra_slice_params.get("depth_radius_factor", 0.6) or 0.0),
                    "balance_profiles": bool(extra_slice_params.get("balance_profiles", True)),
                }
                if extra_slice_params.get("amplitude_sigma") is not None:
                    sidecar_params["amplitude_sigma"] = extra_slice_params.get("amplitude_sigma")
                save_ogpr_slicer_params(output_dir, sidecar_params)

                # --- registra nel catalogo (riusa GprVolumeMixin) ---
                try:
                    self._register_las_slices_in_catalog(
                        project_root, group_name, slices, epsg, reslice=is_reslice
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

                # --- apri viewer 3D con il volume appena calcolato ---
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

                        meta_for_viewer.update(
                            {
                                "z_step": float(params["z_step"]),
                                "z_min": z_min_view,
                                "z_max": z_max_view,
                                "z_levels": [float(z) for z in z_levels] if z_levels else None,
                                "epsg": int(epsg) if epsg else None,
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
            finally:
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
