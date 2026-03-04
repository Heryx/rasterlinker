# -*- coding: utf-8 -*-
"""
GPR Profile Viewer
==================
QDialog con radargram matplotlib integrato.
  - asse X (distanza): mostra RubberBand posizione sul canvas
  - asse Y (profondita'): cambia timeslice visibile via dial del plugin
  - Gain display: moltiplicatore post-normalize per boost contrasto
  - Clip %: percentile di clipping per normalize_display
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QToolBar,
    QAction, QLabel, QComboBox,
    QCheckBox, QDoubleSpinBox, QSpinBox,
    QFileDialog, QMessageBox, QSizePolicy,
    QGroupBox, QFormLayout, QPushButton,
)
from qgis.core import (
    QgsPointXY, QgsWkbTypes,
)
from qgis.gui import QgsRubberBand

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

from .gpr_ogpr_reader import read_ogpr, OgprProfile, OgprChannel
from .gpr_processing  import apply_pipeline, DEFAULT_PIPELINE


GPR_CMAPS   = ["RdBu_r", "seismic", "gray", "bwr", "Greys_r"]
DEFAULT_CMAP = "RdBu_r"


class GprProfileViewer(QDialog):

    def __init__(self, iface, plugin=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.iface  = iface
        self.plugin = plugin
        self.setWindowTitle("GPR Profile Viewer")
        self.resize(1200, 650)

        self._profiles:    list[OgprProfile] = []
        self._prof_idx:    int = 0
        self._ch_idx:      int = 0
        self._raw_data:    Optional[np.ndarray] = None
        self._proc_data:   Optional[np.ndarray] = None   # dopo pipeline
        self._disp_data:   Optional[np.ndarray] = None   # dopo gain
        self._cursor_x:    Optional[float] = None
        self._cursor_z:    Optional[float] = None

        self._rb_point: Optional[QgsRubberBand] = None
        self._rb_line:  Optional[QgsRubberBand] = None

        self._canvas_timer = QTimer(self)
        self._canvas_timer.setSingleShot(True)
        self._canvas_timer.setInterval(50)
        self._canvas_timer.timeout.connect(self._flush_canvas_update)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        # Toolbar
        tb = QToolBar()
        act_open = QAction("\U0001f4c2  Importa .ogpr", self)
        act_open.triggered.connect(self.import_files)
        tb.addAction(act_open)

        act_prev = QAction("\u25c4 Profilo", self)
        act_prev.triggered.connect(self._prev_profile)
        tb.addAction(act_prev)

        self._lbl_profile = QLabel("\u2014")
        self._lbl_profile.setMinimumWidth(200)
        tb.addWidget(self._lbl_profile)

        act_next = QAction("Profilo \u25ba", self)
        act_next.triggered.connect(self._next_profile)
        tb.addAction(act_next)

        tb.addSeparator()
        tb.addWidget(QLabel(" Canale:"))
        self._cb_channel = QComboBox()
        self._cb_channel.setMinimumWidth(80)
        self._cb_channel.currentIndexChanged.connect(self._on_channel_changed)
        tb.addWidget(self._cb_channel)

        tb.addSeparator()
        tb.addWidget(QLabel(" Colormap:"))
        self._cb_cmap = QComboBox()
        self._cb_cmap.addItems(GPR_CMAPS)
        self._cb_cmap.setCurrentText(DEFAULT_CMAP)
        self._cb_cmap.currentTextChanged.connect(self._redraw)
        tb.addWidget(self._cb_cmap)

        root.addWidget(tb)

        center = QHBoxLayout()

        if HAS_MPL:
            self._fig        = Figure(figsize=(9, 4), tight_layout=True)
            self._ax         = self._fig.add_subplot(111)
            self._canvas_mpl = FigureCanvasQTAgg(self._fig)
            self._canvas_mpl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._canvas_mpl.mpl_connect("motion_notify_event", self._on_mouse_move)
            self._canvas_mpl.mpl_connect("button_press_event",  self._on_mouse_press)
            self._canvas_mpl.mpl_connect("axes_leave_event",    self._on_axes_leave)
            center.addWidget(self._canvas_mpl, stretch=4)
            self._im    = None
            self._vline = None
            self._hline = None
        else:
            lbl = QLabel("matplotlib non trovato.\nInstalla: pip install matplotlib")
            lbl.setAlignment(Qt.AlignCenter)
            center.addWidget(lbl, stretch=4)

        panel = self._build_processing_panel()
        center.addWidget(panel, stretch=1)
        root.addLayout(center)

        self._lbl_status = QLabel("Importa un file .ogpr per iniziare.")
        root.addWidget(self._lbl_status)

    def _build_processing_panel(self) -> QGroupBox:
        grp = QGroupBox("Processing")
        fl  = QFormLayout(grp)

        # --- Filtri ---
        self._chk_dewow  = QCheckBox(); self._chk_dewow.setChecked(True)
        self._spin_dewow = QSpinBox();  self._spin_dewow.setRange(4, 256); self._spin_dewow.setValue(16)

        self._chk_bg  = QCheckBox(); self._chk_bg.setChecked(True)

        self._chk_agc  = QCheckBox(); self._chk_agc.setChecked(True)
        self._spin_agc = QSpinBox();  self._spin_agc.setRange(8, 512); self._spin_agc.setValue(32)

        self._chk_bp   = QCheckBox(); self._chk_bp.setChecked(False)
        self._spin_bp_lo = QDoubleSpinBox(); self._spin_bp_lo.setRange(1, 3000); self._spin_bp_lo.setValue(200)
        self._spin_bp_hi = QDoubleSpinBox(); self._spin_bp_hi.setRange(1, 3000); self._spin_bp_hi.setValue(1200)

        # --- Display ---
        self._spin_clip = QDoubleSpinBox()
        self._spin_clip.setRange(50.0, 99.9)
        self._spin_clip.setSingleStep(1.0)
        self._spin_clip.setValue(95.0)
        self._spin_clip.setToolTip(
            "Percentile di clip in normalize_display.\n"
            "Valori bassi (es. 80%) = piu' contrasto sulle riflessioni deboli.\n"
            "Valori alti (es. 99%) = gamma piu' lineare."
        )

        self._spin_gain = QDoubleSpinBox()
        self._spin_gain.setRange(0.1, 20.0)
        self._spin_gain.setSingleStep(0.5)
        self._spin_gain.setValue(2.0)
        self._spin_gain.setToolTip(
            "Moltiplicatore display post-normalize.\n"
            "Aumenta per far emergere le iperboli deboli.\n"
            "I valori vengono clippati a [-1, 1] prima del display."
        )

        fl.addRow("Dewow:",       self._chk_dewow)
        fl.addRow("  finestra:",  self._spin_dewow)
        fl.addRow("BG removal:",  self._chk_bg)
        fl.addRow("AGC gain:",    self._chk_agc)
        fl.addRow("  finestra:",  self._spin_agc)
        fl.addRow("Bandpass:",    self._chk_bp)
        fl.addRow("  low (MHz):", self._spin_bp_lo)
        fl.addRow("  high (MHz):",self._spin_bp_hi)
        fl.addRow("Clip %:",      self._spin_clip)
        fl.addRow("Gain display:",self._spin_gain)

        btn_apply = QPushButton("Applica")
        btn_apply.clicked.connect(self._apply_processing)
        fl.addRow(btn_apply)

        # Gain-only: ricalcola display senza riprocessare
        btn_gain = QPushButton("Aggiorna gain")
        btn_gain.setToolTip("Applica solo Clip% e Gain senza rieseguire i filtri (piu' veloce).")
        btn_gain.clicked.connect(self._apply_gain_only)
        fl.addRow(btn_gain)

        btn_slice = QPushButton("\U0001f5fa  Crea Timeslice\u2026")
        btn_slice.clicked.connect(self._open_slice_dialog)
        fl.addRow(btn_slice)

        return grp

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Importa profili GPR", "",
            "OGPR files (*.ogpr);;All files (*)",
        )
        if not paths:
            return
        errors = []
        for p in paths:
            try:
                prof = read_ogpr(p)
                self._profiles.append(prof)
            except Exception as e:
                errors.append(f"{os.path.basename(p)}: {e}")
        if errors:
            QMessageBox.warning(self, "Errori import", "\n".join(errors))
        if self._profiles:
            self._prof_idx = len(self._profiles) - 1
            self._load_current_profile()

    # ------------------------------------------------------------------
    # Navigazione
    # ------------------------------------------------------------------

    def _prev_profile(self):
        if self._profiles:
            self._prof_idx = max(0, self._prof_idx - 1)
            self._load_current_profile()

    def _next_profile(self):
        if self._profiles:
            self._prof_idx = min(len(self._profiles) - 1, self._prof_idx + 1)
            self._load_current_profile()

    def _on_channel_changed(self, idx):
        if idx >= 0:
            self._ch_idx = idx
            self._reload_data()

    def _load_current_profile(self):
        if not self._profiles:
            return
        prof = self._profiles[self._prof_idx]
        self._lbl_profile.setText(
            f"{self._prof_idx + 1}/{len(self._profiles)}  "
            f"{os.path.basename(prof.path)}"
        )
        self._cb_channel.blockSignals(True)
        self._cb_channel.clear()
        for i in range(prof.n_channels):
            self._cb_channel.addItem(f"Ch {i}")
        self._cb_channel.blockSignals(False)
        self._ch_idx = 0
        self._cb_channel.setCurrentIndex(0)
        self._reload_data()
        self._draw_profile_line_on_canvas()

    def _reload_data(self):
        if not self._profiles:
            return
        prof = self._profiles[self._prof_idx]
        ch   = prof.channel(self._ch_idx)
        self._raw_data  = ch.data.copy()
        self._proc_data = None
        self._apply_processing()

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def _apply_processing(self):
        """Esegue la pipeline completa e ridisegna."""
        if self._raw_data is None:
            return
        params = {
            "dewow":       self._chk_dewow.isChecked(),
            "dewow_win":   self._spin_dewow.value(),
            "bg_removal":  self._chk_bg.isChecked(),
            "agc":         self._chk_agc.isChecked(),
            "agc_win":     self._spin_agc.value(),
            "bandpass":    self._chk_bp.isChecked(),
            "bp_low_mhz":  self._spin_bp_lo.value(),
            "bp_high_mhz": self._spin_bp_hi.value(),
            "clip_pct":    self._spin_clip.value(),
        }
        prof = self._profiles[self._prof_idx]
        try:
            self._proc_data = apply_pipeline(
                self._raw_data, params, dt_ns=prof.dt_ns
            )
        except Exception as e:
            QMessageBox.critical(self, "Errore processing", str(e))
            return
        self._apply_gain_only()

    def _apply_gain_only(self):
        """Applica solo il gain display senza riprocessare (piu' veloce)."""
        if self._proc_data is None:
            self._apply_processing()
            return
        gain = float(self._spin_gain.value())
        self._disp_data = np.clip(
            self._proc_data * gain, -1.0, 1.0
        ).astype(np.float32)
        self._lbl_status.setText(
            f"proc: min={self._proc_data.min():.3f}  "
            f"max={self._proc_data.max():.3f}  "
            f"gain={gain:.1f}x"
        )
        self._redraw()

    # ------------------------------------------------------------------
    # Disegno
    # ------------------------------------------------------------------

    def _redraw(self):
        if not HAS_MPL or self._disp_data is None:
            return
        prof = self._profiles[self._prof_idx]
        ch   = prof.channel(self._ch_idx)
        cmap = self._cb_cmap.currentText()

        dist_max  = float(ch.distances[-1]) if len(ch.distances) else 1.0
        depth_max = prof.depth_max_m

        self._ax.clear()
        self._im = self._ax.imshow(
            self._disp_data,
            aspect="auto",
            cmap=cmap,
            vmin=-1, vmax=1,
            extent=[0, dist_max, depth_max, 0],
            interpolation="nearest",       # nessuna sfocatura
        )
        self._ax.set_xlabel("Distanza (m)")
        self._ax.set_ylabel("Profondit\u00e0 (m)")
        self._ax.set_title(
            f"{os.path.basename(prof.path)}  |  "
            f"Ch {self._ch_idx}  |  "
            f"{prof.frequency_mhz:.0f} MHz"
        )
        self._vline = self._ax.axvline(x=0, color="yellow", lw=1, visible=False)
        self._hline = self._ax.axhline(y=0, color="cyan",   lw=1, linestyle="--", visible=False)
        self._canvas_mpl.draw_idle()

    # ------------------------------------------------------------------
    # Cursore
    # ------------------------------------------------------------------

    def _on_mouse_move(self, event):
        if event.inaxes != self._ax or self._disp_data is None:
            return
        self._cursor_x = event.xdata
        self._cursor_z = event.ydata
        self._update_cursor_lines()
        self._update_status()
        self._canvas_timer.start()

    def _on_mouse_press(self, event):
        if event.inaxes == self._ax and event.button == 1:
            self._cursor_z = event.ydata
            self._update_dial()

    def _on_axes_leave(self, event):
        if self._vline: self._vline.set_visible(False)
        if self._hline: self._hline.set_visible(False)
        self._canvas_mpl.draw_idle()

    def _update_cursor_lines(self):
        if self._vline and self._cursor_x is not None:
            self._vline.set_xdata([self._cursor_x])
            self._vline.set_visible(True)
        if self._hline and self._cursor_z is not None:
            self._hline.set_ydata([self._cursor_z])
            self._hline.set_visible(True)
        self._canvas_mpl.draw_idle()

    def _update_status(self):
        if not self._profiles:
            return
        prof  = self._profiles[self._prof_idx]
        ch    = prof.channel(self._ch_idx)
        east = north = np.nan
        if (self._cursor_x is not None
                and len(ch.distances) > 1
                and self._cursor_x >= 0):
            idx_t = int(np.searchsorted(ch.distances, self._cursor_x))
            idx_t = min(idx_t, len(ch.distances) - 1)
            east  = ch.easting[idx_t]
            north = ch.northing[idx_t]
        z_str = f"{self._cursor_z:.3f} m" if self._cursor_z is not None else "\u2014"
        x_str = f"{self._cursor_x:.2f} m" if self._cursor_x is not None else "\u2014"
        self._lbl_status.setText(
            f"Dist: {x_str}  |  Profondit\u00e0: {z_str}  "
            f"|  E {east:.1f}  N {north:.1f}"
        )

    # ------------------------------------------------------------------
    # Bridge QGIS canvas
    # ------------------------------------------------------------------

    def _flush_canvas_update(self):
        self._update_rubber_band()
        self._update_dial()

    def _update_rubber_band(self):
        if self._cursor_x is None or not self._profiles or self.iface is None:
            return
        prof = self._profiles[self._prof_idx]
        ch   = prof.channel(self._ch_idx)
        if len(ch.distances) < 2:
            return
        idx_t = min(
            int(np.searchsorted(ch.distances, self._cursor_x)),
            len(ch.distances) - 1
        )
        canvas = self.iface.mapCanvas()
        if self._rb_point is None:
            self._rb_point = QgsRubberBand(canvas, QgsWkbTypes.PointGeometry)
            self._rb_point.setColor(QColor(255, 220, 0))
            self._rb_point.setIconSize(12)
            self._rb_point.setWidth(3)
        self._rb_point.reset(QgsWkbTypes.PointGeometry)
        self._rb_point.addPoint(
            QgsPointXY(ch.easting[idx_t], ch.northing[idx_t]), True
        )

    def _draw_profile_line_on_canvas(self):
        if not self._profiles or self.iface is None:
            return
        prof   = self._profiles[self._prof_idx]
        ch     = prof.channel(self._ch_idx)
        canvas = self.iface.mapCanvas()
        if self._rb_line is None:
            self._rb_line = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
            self._rb_line.setColor(QColor(255, 165, 0))
            self._rb_line.setWidth(2)
        else:
            self._rb_line.reset(QgsWkbTypes.LineGeometry)
        for i in range(len(ch.easting)):
            self._rb_line.addPoint(
                QgsPointXY(ch.easting[i], ch.northing[i]),
                i == len(ch.easting) - 1
            )

    def _update_dial(self):
        if self._cursor_z is None or self.plugin is None:
            return
        try:
            dial = self.plugin.dlg.dial
        except AttributeError:
            return
        try:
            from .project_catalog import load_catalog
            pr = (self.plugin.settings.value(
                self.plugin.settings_key_active_project, "", type=str) or "").strip()
            if not pr:
                return
            catalog  = load_catalog(pr)
            group_id = getattr(self.plugin, "_active_group_id", None)
            if group_id is None:
                return
            grp = next(
                (g for g in catalog.get("raster_groups", [])
                 if g.get("id") == group_id), None
            )
            if grp is None:
                return
            slices = [
                t for t in catalog.get("timeslices", [])
                if t.get("id") in grp.get("timeslice_ids", [])
            ]
            if not slices:
                return
            depths = np.array([
                (t.get("depth_from", 0) + t.get("depth_to", 0)) / 2.0
                for t in slices
            ])
            idx = int(np.argmin(np.abs(depths - self._cursor_z)))
            dial_val = int(round(idx / max(len(slices) - 1, 1) * dial.maximum()))
            if dial.value() != dial_val:
                dial.setValue(dial_val)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Timeslice
    # ------------------------------------------------------------------

    def _open_slice_dialog(self):
        if not self._profiles:
            QMessageBox.information(self, "Nessun profilo",
                                    "Importa almeno un file .ogpr prima.")
            return
        if self.plugin and hasattr(self.plugin, "import_ogpr_as_slices"):
            self.plugin.import_ogpr_as_slices(self._profiles)
        else:
            QMessageBox.information(
                self, "Timeslice",
                "Funzione disponibile dal plugin principale."
            )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        for rb in (self._rb_point, self._rb_line):
            if rb is not None:
                try:
                    rb.reset()
                except Exception:
                    pass
        super().closeEvent(event)
