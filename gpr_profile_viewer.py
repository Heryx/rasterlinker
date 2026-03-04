# -*- coding: utf-8 -*-
"""
GPR Profile Viewer
==================
QDialog con radargram matplotlib integrato.
Il cursore è collegato al canvas QGIS:
  - asse X (distanza): mostra RubberBand posizione sul canvas
  - asse Y (profondità): cambia timeslice visibile via dial del plugin
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QToolBar,
    QAction, QLabel, QComboBox, QSlider,
    QCheckBox, QDoubleSpinBox, QSpinBox,
    QFileDialog, QMessageBox, QSizePolicy,
    QGroupBox, QFormLayout, QPushButton,
)
from qgis.core import (
    QgsPointXY, QgsGeometry, QgsWkbTypes,
)
from qgis.gui import QgsRubberBand

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

from .gpr_ogpr_reader import read_ogpr, OgprProfile, OgprChannel
from .gpr_processing  import apply_pipeline, DEFAULT_PIPELINE


# ---------------------------------------------------------------------------
# Colormaps GPR
# ---------------------------------------------------------------------------
GPR_CMAPS = ["RdBu_r", "seismic", "gray", "bwr", "Greys_r"]
DEFAULT_CMAP = "RdBu_r"


# ---------------------------------------------------------------------------
# Viewer principale
# ---------------------------------------------------------------------------

class GprProfileViewer(QDialog):
    """
    Finestra viewer per profili GPR .ogpr.

    Parametri
    ---------
    iface  : QgisInterface  (per accedere al canvas)
    plugin : istanza del plugin principale (per accedere al dial)
    parent : widget padre
    """

    def __init__(self, iface, plugin=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.iface  = iface
        self.plugin = plugin
        self.setWindowTitle("GPR Profile Viewer")
        self.resize(1100, 600)

        self._profiles:   list[OgprProfile] = []
        self._prof_idx:   int = 0
        self._ch_idx:     int = 0
        self._raw_data:   Optional[np.ndarray] = None   # (n_samples, n_slices)
        self._disp_data:  Optional[np.ndarray] = None   # processato, normaliz.
        self._cursor_x:   Optional[float] = None        # distanza (m)
        self._cursor_z:   Optional[float] = None        # profondità (m)
        self._pipe_params: dict = {**DEFAULT_PIPELINE}

        # RubberBand su canvas: punto + linea profilo
        self._rb_point: Optional[QgsRubberBand] = None
        self._rb_line:  Optional[QgsRubberBand] = None

        # Throttle per l'aggiornamento canvas (evita lag)
        self._canvas_timer = QTimer(self)
        self._canvas_timer.setSingleShot(True)
        self._canvas_timer.setInterval(50)   # ms
        self._canvas_timer.timeout.connect(self._flush_canvas_update)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        # --- Toolbar ---
        tb = QToolBar()
        act_open = QAction("\U0001f4c2  Importa .ogpr", self)
        act_open.triggered.connect(self.import_files)
        tb.addAction(act_open)

        act_prev = QAction("\u25c4 Profilo", self)
        act_prev.triggered.connect(self._prev_profile)
        tb.addAction(act_prev)

        self._lbl_profile = QLabel("—")
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

        # --- Area centrale: radargram + controlli ---
        center = QHBoxLayout()

        # Radargram
        if HAS_MPL:
            self._fig   = Figure(figsize=(9, 4), tight_layout=True)
            self._ax    = self._fig.add_subplot(111)
            self._canvas_mpl = FigureCanvasQTAgg(self._fig)
            self._canvas_mpl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._canvas_mpl.mpl_connect("motion_notify_event",  self._on_mouse_move)
            self._canvas_mpl.mpl_connect("button_press_event",   self._on_mouse_press)
            self._canvas_mpl.mpl_connect("axes_leave_event",     self._on_axes_leave)
            center.addWidget(self._canvas_mpl, stretch=4)
            self._im       = None
            self._vline    = None   # cursore verticale
            self._hline    = None   # cursore orizzontale
        else:
            lbl = QLabel("matplotlib non trovato.\nInstalla: pip install matplotlib")
            lbl.setAlignment(Qt.AlignCenter)
            center.addWidget(lbl, stretch=4)

        # Pannello processing
        panel = self._build_processing_panel()
        center.addWidget(panel, stretch=1)

        root.addLayout(center)

        # --- Status bar ---
        self._lbl_status = QLabel("Importa un file .ogpr per iniziare.")
        root.addWidget(self._lbl_status)

    def _build_processing_panel(self) -> QGroupBox:
        grp = QGroupBox("Processing")
        fl  = QFormLayout(grp)

        self._chk_dewow     = QCheckBox()
        self._chk_dewow.setChecked(True)
        self._spin_dewow    = QSpinBox()
        self._spin_dewow.setRange(4, 256)
        self._spin_dewow.setValue(16)

        self._chk_bg        = QCheckBox()
        self._chk_bg.setChecked(True)

        self._chk_agc       = QCheckBox()
        self._chk_agc.setChecked(True)
        self._spin_agc      = QSpinBox()
        self._spin_agc.setRange(4, 256)
        self._spin_agc.setValue(32)

        self._chk_bp        = QCheckBox()
        self._chk_bp.setChecked(False)
        self._spin_bp_lo    = QDoubleSpinBox()
        self._spin_bp_lo.setRange(1, 3000)
        self._spin_bp_lo.setValue(100)
        self._spin_bp_hi    = QDoubleSpinBox()
        self._spin_bp_hi.setRange(1, 3000)
        self._spin_bp_hi.setValue(1200)

        fl.addRow("Dewow:",          self._chk_dewow)
        fl.addRow("  finestra:",     self._spin_dewow)
        fl.addRow("BG removal:",     self._chk_bg)
        fl.addRow("AGC gain:",       self._chk_agc)
        fl.addRow("  finestra:",     self._spin_agc)
        fl.addRow("Bandpass:",       self._chk_bp)
        fl.addRow("  low (MHz):",    self._spin_bp_lo)
        fl.addRow("  high (MHz):",   self._spin_bp_hi)

        btn = QPushButton("Applica")
        btn.clicked.connect(self._apply_processing)
        fl.addRow(btn)

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
            QMessageBox.warning(self, "Errori import",
                                "\n".join(errors))
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
        name = os.path.basename(prof.path)
        self._lbl_profile.setText(
            f"{self._prof_idx + 1}/{len(self._profiles)}  {name}"
        )
        # Aggiorna canali
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
        self._raw_data = ch.data.copy()
        self._apply_processing()

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def _apply_processing(self):
        if self._raw_data is None:
            return
        params = {
            "dewow":      self._chk_dewow.isChecked(),
            "dewow_win":  self._spin_dewow.value(),
            "bg_removal": self._chk_bg.isChecked(),
            "agc":        self._chk_agc.isChecked(),
            "agc_win":    self._spin_agc.value(),
            "bandpass":   self._chk_bp.isChecked(),
            "bp_low_mhz": self._spin_bp_lo.value(),
            "bp_high_mhz":self._spin_bp_hi.value(),
        }
        prof = self._profiles[self._prof_idx]
        self._disp_data = apply_pipeline(
            self._raw_data, params, dt_ns=prof.dt_ns
        )
        self._redraw()

    # ------------------------------------------------------------------
    # Disegno radargram
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
            interpolation="bilinear",
        )
        self._ax.set_xlabel("Distanza (m)")
        self._ax.set_ylabel("Profondit\u00e0 (m)")
        self._ax.set_title(
            f"{os.path.basename(prof.path)}  |  Ch {self._ch_idx}  "
            f"|  {prof.frequency_mhz:.0f} MHz"
        )
        # Ripristina linee cursore
        self._vline = self._ax.axvline(x=0, color="yellow", lw=1, visible=False)
        self._hline = self._ax.axhline(y=0, color="cyan",   lw=1, linestyle="--", visible=False)

        self._canvas_mpl.draw_idle()

    # ------------------------------------------------------------------
    # Cursore interattivo
    # ------------------------------------------------------------------

    def _on_mouse_move(self, event):
        if event.inaxes != self._ax or self._disp_data is None:
            return
        self._cursor_x = event.xdata   # distanza (m)
        self._cursor_z = event.ydata   # profondità (m)
        self._update_cursor_lines()
        self._update_status()
        self._canvas_timer.start()     # throttled canvas update

    def _on_mouse_press(self, event):
        """Click sinistro: aggiorna timeslice immediatamente."""
        if event.inaxes == self._ax and event.button == 1:
            self._cursor_z = event.ydata
            self._update_dial()

    def _on_axes_leave(self, event):
        if self._vline:
            self._vline.set_visible(False)
        if self._hline:
            self._hline.set_visible(False)
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
        east  = np.nan
        north = np.nan
        if (self._cursor_x is not None
                and len(ch.distances) > 1
                and self._cursor_x >= 0):
            idx_t = int(np.searchsorted(ch.distances, self._cursor_x))
            idx_t = min(idx_t, len(ch.distances) - 1)
            east  = ch.easting[idx_t]
            north = ch.northing[idx_t]
        z_str = f"{self._cursor_z:.3f} m" if self._cursor_z is not None else "—"
        x_str = f"{self._cursor_x:.2f} m" if self._cursor_x is not None else "—"
        self._lbl_status.setText(
            f"Dist: {x_str}  |  Profondit\u00e0: {z_str}  "
            f"|  E {east:.1f}  N {north:.1f}"
        )

    # ------------------------------------------------------------------
    # Bridge QGIS canvas
    # ------------------------------------------------------------------

    def _flush_canvas_update(self):
        """Chiamato dal timer: aggiorna RubberBand e dial."""
        self._update_rubber_band()
        self._update_dial()

    def _update_rubber_band(self):
        if (self._cursor_x is None or not self._profiles
                or self.iface is None):
            return
        prof = self._profiles[self._prof_idx]
        ch   = prof.channel(self._ch_idx)
        if len(ch.distances) < 2:
            return

        idx_t = int(np.searchsorted(ch.distances, self._cursor_x))
        idx_t = min(idx_t, len(ch.distances) - 1)
        east  = ch.easting[idx_t]
        north = ch.northing[idx_t]

        canvas = self.iface.mapCanvas()

        if self._rb_point is None:
            self._rb_point = QgsRubberBand(canvas, QgsWkbTypes.PointGeometry)
            self._rb_point.setColor(QColor(255, 220, 0))
            self._rb_point.setIconSize(12)
            self._rb_point.setWidth(3)

        self._rb_point.reset(QgsWkbTypes.PointGeometry)
        self._rb_point.addPoint(QgsPointXY(east, north), True)

    def _draw_profile_line_on_canvas(self):
        """Disegna la polyline del profilo corrente sul canvas QGIS."""
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
        """Mappa la profondità corrente sull'indice del dial del plugin."""
        if self._cursor_z is None or self.plugin is None:
            return
        try:
            dial = self.plugin.dlg.dial
        except AttributeError:
            return

        # Recupera le profondità del gruppo attivo dal catalogo
        try:
            from .project_catalog import load_catalog
            pr = (self.plugin.settings.value(
                self.plugin.settings_key_active_project, "", type=str) or "").strip()
            if not pr:
                return
            catalog  = load_catalog(pr)
            # Cerca il gruppo visibile attualmente
            group_id = getattr(self.plugin, "_active_group_id", None)
            if group_id is None:
                return
            grp = next(
                (g for g in catalog.get("raster_groups", [])
                 if g.get("id") == group_id), None
            )
            if grp is None:
                return
            ts_ids = grp.get("timeslice_ids", [])
            slices = [
                t for t in catalog.get("timeslices", [])
                if t.get("id") in ts_ids
            ]
            if not slices:
                return
            depths = np.array([
                (t.get("depth_from", 0) + t.get("depth_to", 0)) / 2.0
                for t in slices
            ])
            idx = int(np.argmin(np.abs(depths - self._cursor_z)))
            total = len(slices)
            # Mappa su range dial (0 .. dial.maximum())
            dial_val = int(round(idx / max(total - 1, 1) * dial.maximum()))
            if dial.value() != dial_val:
                dial.setValue(dial_val)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Timeslice creation (apre la finestra del plugin)
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
                "Funzione disponibile dal plugin principale — "
                "apri il pannello GPRLinker e usa Import → Da profili OGPR."
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
