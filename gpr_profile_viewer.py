# -*- coding: utf-8 -*-
"""
GPR Profile Viewer
==================
QDialog con radargram matplotlib integrato.
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np

from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QToolBar,
    QAction, QLabel, QComboBox,
    QCheckBox, QDoubleSpinBox, QSpinBox,
    QFileDialog, QMessageBox, QSizePolicy,
    QGroupBox, QFormLayout, QPushButton,
)
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsWkbTypes,
)
from qgis.gui import QgsRubberBand

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

from .gpr_ogpr_reader import read_ogpr, OgprProfile, OgprChannel
from .gpr_processing  import apply_pipeline, DEFAULT_PIPELINE, normalize_display


GPR_CMAPS    = ["RdBu_r", "seismic", "gray", "bwr", "Greys_r"]
DEFAULT_CMAP = "RdBu_r"


class GprProfileViewer(QDialog):
    cursor_moved = pyqtSignal(float, float, float)  # easting, northing, depth

    def __init__(self, iface, plugin=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.iface  = iface
        self.plugin = plugin
        self.setWindowTitle("GPR Profile Viewer")
        self.resize(1200, 750)

        self._profiles:    list[OgprProfile] = []
        self._prof_idx:    int = 0
        self._ch_idx:      int = 0
        self._raw_data:    Optional[np.ndarray] = None
        self._proc_data:   Optional[np.ndarray] = None
        self._disp_data:   Optional[np.ndarray] = None
        self._cursor_x:    Optional[float] = None
        self._cursor_z:    Optional[float] = None
        self._view_xlim:   Optional[tuple[float, float]] = None
        self._view_ylim:   Optional[tuple[float, float]] = None
        self._gpr_3d_viewer = None
        self._cached_catalog = None
        self._cached_catalog_pr: Optional[str] = None
        self._cached_catalog_mtime: Optional[float] = None

        self._rb_point: Optional[QgsRubberBand] = None
        self._rb_line:  Optional[QgsRubberBand] = None

        self._canvas_timer = QTimer(self)
        self._canvas_timer.setSingleShot(True)
        self._canvas_timer.setInterval(50)
        self._canvas_timer.timeout.connect(self._flush_canvas_update)

        self._build_ui()

    def _channel_signal_score(self, data: np.ndarray) -> float:
        arr = np.asarray(data, dtype=np.float64)
        if arr.size == 0:
            return -1.0
        # Sampling leggero per evitare costi elevati su profili lunghi.
        if arr.size > 800_000:
            step = max(1, arr.size // 200_000)
            arr = arr.ravel()[::step]
        finite = np.isfinite(arr)
        if not finite.any():
            return -1.0
        arr = arr[finite]
        if arr.size == 0:
            return -1.0
        nz_ratio = float(np.count_nonzero(arr)) / float(arr.size)
        std = float(np.std(arr))
        amp = float(np.nanpercentile(np.abs(arr), 95)) if arr.size else 0.0
        return nz_ratio * 10.0 + std + amp * 1e-3

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

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

        tb.addSeparator()
        tb.addWidget(QLabel(" Render:"))
        self._cb_interp = QComboBox()
        self._cb_interp.addItem("Sharp", "nearest")
        self._cb_interp.addItem("Smooth", "bilinear")
        self._cb_interp.addItem("Fine", "bicubic")
        self._cb_interp.setCurrentIndex(1)  # Smooth by default
        self._cb_interp.setToolTip(
            "Sharp: pixel netti.\n"
            "Smooth: riduce effetto pixel rettangolari.\n"
            "Fine: resa piu' continua (piu' morbida)."
        )
        self._cb_interp.currentIndexChanged.connect(self._redraw)
        tb.addWidget(self._cb_interp)

        tb.addSeparator()
        self._chk_real_aspect = QCheckBox("Scala reale")
        self._chk_real_aspect.setChecked(True)
        self._chk_real_aspect.setToolTip(
            "Mantiene proporzioni metriche reali distanza/profondita'.\n"
            "Disattiva per adattare il profilo alla finestra."
        )
        self._chk_real_aspect.toggled.connect(self._redraw)
        tb.addWidget(self._chk_real_aspect)

        act_reset_zoom = QAction("Reset Zoom", self)
        act_reset_zoom.setToolTip("Ripristina l'estensione completa del profilo.")
        act_reset_zoom.triggered.connect(self._reset_zoom)
        tb.addAction(act_reset_zoom)

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
            self._canvas_mpl.mpl_connect("scroll_event",        self._on_scroll_zoom)
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

    def _build_processing_panel(self):
        grp = QGroupBox("Processing")
        fl  = QFormLayout(grp)

        # Dewow
        self._chk_dewow  = QCheckBox(); self._chk_dewow.setChecked(True)
        self._spin_dewow = QSpinBox();  self._spin_dewow.setRange(4, 256); self._spin_dewow.setValue(16)

        # Time-zero
        self._chk_timezero = QCheckBox()
        self._chk_timezero.setChecked(False)
        self._chk_timezero.setToolTip(
            "Tronca i campioni pre-onda diretta scan-by-scan o line-by-line.\n"
            "NON usa shift circolare: il radargram viene fisicamente troncato."
        )
        self._cb_tz_method = QComboBox()
        self._cb_tz_method.addItems(["peak", "threshold", "zero_crossing"])
        self._cb_tz_method.setCurrentText("peak")
        self._cb_tz_method.setEnabled(False)
        self._cb_tz_mode = QComboBox()
        self._cb_tz_mode.addItems(["line_by_line", "scan_by_scan"])
        self._cb_tz_mode.setCurrentText("line_by_line")
        self._cb_tz_mode.setEnabled(False)
        self._spin_tz_threshold = QDoubleSpinBox()
        self._spin_tz_threshold.setRange(0.05, 0.95); self._spin_tz_threshold.setSingleStep(0.05)
        self._spin_tz_threshold.setValue(0.20); self._spin_tz_threshold.setEnabled(False)
        self._spin_tz_backup = QSpinBox()
        self._spin_tz_backup.setRange(0, 32); self._spin_tz_backup.setValue(4)
        self._spin_tz_backup.setEnabled(False)

        def _toggle_tz(checked):
            for w in (self._cb_tz_method, self._cb_tz_mode,
                      self._spin_tz_threshold, self._spin_tz_backup):
                w.setEnabled(checked)
        self._chk_timezero.toggled.connect(_toggle_tz)

        # ------------------------------------------------------------------
        # BG removal
        # ------------------------------------------------------------------
        self._chk_bg = QCheckBox()
        self._chk_bg.setChecked(True)
        self._chk_bg.setToolTip(
            "Background Removal (GPR-SLICE §Background Removal, pag. 166).\n"
            "Sottrae la traccia media per eliminare banding orizzontale.\n\n"
            "ATTENZIONE: rimuove anche riflessi reali paralleli al profilo."
        )

        self._cb_bg_mode = QComboBox()
        self._cb_bg_mode.addItem("line_by_line", "line_by_line")
        self._cb_bg_mode.addItem("grid_by_grid", "grid_by_grid")
        self._cb_bg_mode.setCurrentIndex(0)
        self._cb_bg_mode.setEnabled(False)
        self._cb_bg_mode.setToolTip(
            "line_by_line : media calcolata su ogni singolo radargram.\n"
            "grid_by_grid : media calcolata su tutti i radargram del grid\n"
            "               (due passate: pre-BG + sottrazione globale)."
        )

        self._chk_bg_auto = QCheckBox("Auto")
        self._chk_bg_auto.setChecked(True)
        self._chk_bg_auto.setEnabled(False)
        self._chk_bg_auto.setToolTip(
            "Auto: sottrae la media dell'intero profilo (filter_length=99000).\n"
            "Disabilita per usare una finestra scorrevole personalizzata."
        )

        self._spin_bg_window = QSpinBox()
        self._spin_bg_window.setRange(8, 99000)
        self._spin_bg_window.setValue(200)
        self._spin_bg_window.setEnabled(False)
        self._spin_bg_window.setToolTip(
            "Lunghezza del filtro in tracce (media scorrevole).\n"
            "Attivo solo se Auto e' disabilitato."
        )

        # Finestra temporale
        self._spin_bg_sample_start = QSpinBox()
        self._spin_bg_sample_start.setRange(0, 9999)
        self._spin_bg_sample_start.setValue(0)
        self._spin_bg_sample_start.setEnabled(False)
        self._spin_bg_sample_start.setToolTip(
            "Primo campione incluso nel BG removal (0 = dall'inizio).\n\n"
            "Imposta > 0 per ESCLUDERE i primi N campioni dalla sottrazione.\n"
            "Effetto: preserva il ground coupling / onda diretta nei primi\n"
            "campioni, permettendo all'AGC di amplificarli correttamente.\n"
            "Esempio: per 600 MHz con dt=0.117 ns, 0.5 m ≈ 28 campioni."
        )

        self._spin_bg_sample_end = QSpinBox()
        self._spin_bg_sample_end.setRange(0, 9999)
        self._spin_bg_sample_end.setValue(0)
        self._spin_bg_sample_end.setEnabled(False)
        self._spin_bg_sample_end.setToolTip(
            "Ultimo campione escluso dal BG removal (0 = fine traccia).\n"
            "Usare per limitare la rimozione a una zona di profondita' specifica."
        )

        def _toggle_bg_auto(auto_checked):
            self._spin_bg_window.setEnabled(not auto_checked)

        def _toggle_bg(bg_checked):
            self._cb_bg_mode.setEnabled(bg_checked)
            self._chk_bg_auto.setEnabled(bg_checked)
            self._spin_bg_sample_start.setEnabled(bg_checked)
            self._spin_bg_sample_end.setEnabled(bg_checked)
            if bg_checked:
                _toggle_bg_auto(self._chk_bg_auto.isChecked())
            else:
                self._spin_bg_window.setEnabled(False)

        self._chk_bg.toggled.connect(_toggle_bg)
        self._chk_bg_auto.toggled.connect(_toggle_bg_auto)

        # AGC
        self._chk_agc  = QCheckBox(); self._chk_agc.setChecked(True)
        self._spin_agc = QSpinBox();  self._spin_agc.setRange(8, 512); self._spin_agc.setValue(128)

        # Bandpass
        self._chk_bp     = QCheckBox(); self._chk_bp.setChecked(False)
        self._spin_bp_lo = QDoubleSpinBox(); self._spin_bp_lo.setRange(1, 3000); self._spin_bp_lo.setValue(200)
        self._spin_bp_hi = QDoubleSpinBox(); self._spin_bp_hi.setRange(1, 3000); self._spin_bp_hi.setValue(1200)

        # Display
        self._spin_clip = QDoubleSpinBox()
        self._spin_clip.setRange(50.0, 99.9); self._spin_clip.setSingleStep(1.0)
        self._spin_clip.setValue(95.0)
        self._spin_clip.setToolTip("Percentile di clip in normalize_display.")

        self._spin_gain = QDoubleSpinBox()
        self._spin_gain.setRange(0.1, 20.0); self._spin_gain.setSingleStep(0.5)
        self._spin_gain.setValue(2.0)
        self._spin_gain.setToolTip("Moltiplicatore display post-normalize.")

        fl.addRow("Dewow:",              self._chk_dewow)
        fl.addRow("  finestra:",         self._spin_dewow)
        fl.addRow("Time-zero:",          self._chk_timezero)
        fl.addRow("  metodo:",           self._cb_tz_method)
        fl.addRow("  mode:",             self._cb_tz_mode)
        fl.addRow("  soglia:",           self._spin_tz_threshold)
        fl.addRow("  backup N:",         self._spin_tz_backup)
        fl.addRow("BG removal:",         self._chk_bg)
        fl.addRow("  modo:",             self._cb_bg_mode)
        fl.addRow("  auto:",             self._chk_bg_auto)
        fl.addRow("  finestra:",         self._spin_bg_window)
        fl.addRow("  da campione:",      self._spin_bg_sample_start)
        fl.addRow("  a campione:",       self._spin_bg_sample_end)
        fl.addRow("AGC gain:",           self._chk_agc)
        fl.addRow("  finestra:",         self._spin_agc)
        fl.addRow("Bandpass:",           self._chk_bp)
        fl.addRow("  low (MHz):",        self._spin_bp_lo)
        fl.addRow("  high (MHz):",       self._spin_bp_hi)
        fl.addRow("Clip %:",             self._spin_clip)
        fl.addRow("Gain display:",       self._spin_gain)

        btn_apply = QPushButton("Applica")
        btn_apply.clicked.connect(self._apply_processing)
        fl.addRow(btn_apply)

        btn_gain = QPushButton("Aggiorna gain")
        btn_gain.setToolTip("Applica solo Clip% e Gain senza rieseguire i filtri.")
        btn_gain.clicked.connect(self._apply_gain_only)
        fl.addRow(btn_gain)

        # Timeslice
        grp_slice = QGroupBox("Timeslice")
        fl_slice  = QFormLayout(grp_slice)

        self._cb_slice_extraction = QComboBox()
        self._cb_slice_extraction.addItem("LAS-like (abs)", "las_like")
        self._cb_slice_extraction.addItem("Envelope (Hilbert)", "envelope")
        self._cb_slice_extraction.addItem("Signed amplitude", "signed")
        self._cb_slice_extraction.setCurrentIndex(0)
        self._cb_slice_extraction.setToolTip(
            "Metodo di estrazione ampiezza per creare le timeslice.\n"
            "LAS-like usa abs(dato) e si comporta in modo piu' simile al flusso LAS."
        )

        self._chk_slice_use_processing = QCheckBox()
        self._chk_slice_use_processing.setChecked(False)
        self._chk_slice_use_processing.setToolTip(
            "Applica dewow/time-zero/bg/agc/bandpass prima della creazione slice.\n"
            "Disattivato = comportamento piu' vicino al LAS."
        )

        self._chk_normalize_ch = QCheckBox(); self._chk_normalize_ch.setChecked(False)
        self._chk_normalize_ch.setToolTip("Bilancia ampiezza inter-canale (LAS-like: normalmente OFF).")

        self._chk_amplitude_filter = QCheckBox(); self._chk_amplitude_filter.setChecked(False)
        self._spin_amplitude_sigma = QDoubleSpinBox()
        self._spin_amplitude_sigma.setRange(1.0, 10.0); self._spin_amplitude_sigma.setSingleStep(0.5)
        self._spin_amplitude_sigma.setValue(3.0); self._spin_amplitude_sigma.setEnabled(False)
        self._chk_amplitude_filter.toggled.connect(self._spin_amplitude_sigma.setEnabled)

        self._chk_anisotropic_idw = QCheckBox(); self._chk_anisotropic_idw.setChecked(False)
        self._chk_auto_radius     = QCheckBox(); self._chk_auto_radius.setChecked(False)
        self._chk_fill_nodata     = QCheckBox(); self._chk_fill_nodata.setChecked(False)
        self._chk_slice_balance_profiles = QCheckBox(); self._chk_slice_balance_profiles.setChecked(True)
        self._chk_slice_balance_profiles.setToolTip(
            "Bilancia l'ampiezza media tra profili prima dell'IDW per ridurre le strisce."
        )
        self._spin_slice_depth_radius_factor = QDoubleSpinBox()
        self._spin_slice_depth_radius_factor.setRange(0.0, 2.0)
        self._spin_slice_depth_radius_factor.setSingleStep(0.1)
        self._spin_slice_depth_radius_factor.setValue(0.6)
        self._spin_slice_depth_radius_factor.setToolTip(
            "Aumenta progressivamente il raggio IDW con la profondita'. 0 = disattivo."
        )
        self._spin_slice_min_points = QSpinBox()
        self._spin_slice_min_points.setRange(1, 12)
        self._spin_slice_min_points.setValue(1)
        self._spin_slice_min_points.setToolTip(
            "Punti minimi per stimare una cella IDW (LAS default = 1)."
        )

        self._chk_smooth = QCheckBox(); self._chk_smooth.setChecked(False)
        self._spin_smooth_sigma = QDoubleSpinBox()
        self._spin_smooth_sigma.setRange(0.5, 10.0); self._spin_smooth_sigma.setSingleStep(0.5)
        self._spin_smooth_sigma.setValue(1.0); self._spin_smooth_sigma.setEnabled(False)
        self._chk_smooth.toggled.connect(self._spin_smooth_sigma.setEnabled)

        fl_slice.addRow("Estrazione:",         self._cb_slice_extraction)
        fl_slice.addRow("Processing pre-slice:", self._chk_slice_use_processing)
        fl_slice.addRow("Normalizza canali:",  self._chk_normalize_ch)
        fl_slice.addRow("Filtro ampiezza:",    self._chk_amplitude_filter)
        fl_slice.addRow("  sigma:",            self._spin_amplitude_sigma)
        fl_slice.addRow("IDW anisotropo:",     self._chk_anisotropic_idw)
        fl_slice.addRow("  raggio auto:",      self._chk_auto_radius)
        fl_slice.addRow("Bilancia profili:",   self._chk_slice_balance_profiles)
        fl_slice.addRow("Raggio vs profondita':", self._spin_slice_depth_radius_factor)
        fl_slice.addRow("  min points:",       self._spin_slice_min_points)
        fl_slice.addRow("Fill NoData:",        self._chk_fill_nodata)
        fl_slice.addRow("Smooth gaussiano:",   self._chk_smooth)
        fl_slice.addRow("  sigma:",            self._spin_smooth_sigma)

        btn_slice = QPushButton("\U0001f5fa  Crea Timeslice\u2026")
        btn_slice.clicked.connect(self._open_slice_dialog)
        fl_slice.addRow(btn_slice)

        btn_view3d = QPushButton("Apri Viewer 3D...")
        btn_view3d.setToolTip(
            "Costruisce un volume 3D dalle timeslice interpolate e apre il viewer PyVista."
        )
        btn_view3d.clicked.connect(self._open_3d_viewer)
        fl_slice.addRow(btn_view3d)

        from qgis.PyQt.QtWidgets import QScrollArea, QWidget
        scroll_content = QWidget()
        scroll_layout  = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.addWidget(grp)
        scroll_layout.addWidget(grp_slice)
        scroll_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(scroll_content)
        scroll.setMinimumWidth(230)
        scroll.setMaximumWidth(300)
        return scroll

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
        imported_warnings = []
        for p in paths:
            md5_failed = False
            try:
                prof = read_ogpr(p, verify_md5=True)
                self._profiles.append(prof)
            except Exception as e:
                msg = str(e)
                if "MD5" in msg:
                    try:
                        prof = read_ogpr(p, verify_md5=False)
                        self._profiles.append(prof)
                        md5_failed = True
                        imported_warnings.append(
                            f"{os.path.basename(p)}: verifica MD5 fallita, importato comunque "
                            "(possibile schema MD5 non standard o file alterato)."
                        )
                    except Exception as e2:
                        errors.append(f"{os.path.basename(p)}: {e2}")
                        continue
                else:
                    errors.append(f"{os.path.basename(p)}: {msg}")
                    continue
            for w in list(getattr(prof, "parse_warnings", []) or []):
                # Evita doppioni: il warning MD5 esteso e' gia' mostrato sopra.
                if md5_failed and "MD5 mismatch" in str(w):
                    continue
                imported_warnings.append(f"{os.path.basename(p)}: {w}")
        if errors:
            QMessageBox.warning(self, "Errori import", "\n".join(errors))
        if imported_warnings:
            dedup = list(dict.fromkeys(imported_warnings))
            QMessageBox.information(
                self,
                "Warning import OGPR",
                "\n".join(dedup[:12]) + (
                    f"\n... altri {len(dedup) - 12} warning"
                    if len(dedup) > 12
                    else ""
                ),
            )
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

        # Seleziona automaticamente il canale con segnale migliore.
        best_idx = 0
        best_score = -1.0
        for i in range(prof.n_channels):
            try:
                score = self._channel_signal_score(prof.channel(i).data)
            except Exception:
                score = -1.0
            if score > best_score:
                best_score = score
                best_idx = i
        self._ch_idx = int(best_idx)
        self._cb_channel.setCurrentIndex(self._ch_idx)
        self._cb_channel.blockSignals(False)
        self._reload_data()
        if self._ch_idx != 0:
            self._lbl_status.setText(
                f"Canale auto-selezionato: Ch {self._ch_idx} (segnale migliore)."
            )
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
        if self._raw_data is None:
            return
        bg_auto   = self._chk_bg_auto.isChecked()
        bg_window = 0 if bg_auto else int(self._spin_bg_window.value())

        params = {
            "dewow":            self._chk_dewow.isChecked(),
            "dewow_win":        self._spin_dewow.value(),
            "timezero":         self._chk_timezero.isChecked(),
            "tz_method":        self._cb_tz_method.currentText(),
            "tz_mode":          self._cb_tz_mode.currentText(),
            "tz_threshold":     self._spin_tz_threshold.value(),
            "tz_backup_nsamp":  self._spin_tz_backup.value(),
            "bg_removal":       self._chk_bg.isChecked(),
            "bg_mode":          self._cb_bg_mode.currentData() or "line_by_line",
            "bg_window":        bg_window,
            "bg_sample_start":  self._spin_bg_sample_start.value(),
            "bg_sample_end":    self._spin_bg_sample_end.value(),
            "agc":              self._chk_agc.isChecked(),
            "agc_win":          self._spin_agc.value(),
            "bandpass":         self._chk_bp.isChecked(),
            "bp_low_mhz":       self._spin_bp_lo.value(),
            "bp_high_mhz":      self._spin_bp_hi.value(),
            "clip_pct":         self._spin_clip.value(),
        }
        prof = self._profiles[self._prof_idx]
        try:
            self._proc_data = apply_pipeline(
                self._raw_data, params, dt_ns=prof.dt_ns
            )
        except Exception as e:
            QMessageBox.critical(self, "Errore processing", str(e))
            return

        # Fallback robusto: evita profilo "vuoto" quando il risultato e' quasi nullo o non finito.
        proc = np.asarray(self._proc_data)
        finite = np.isfinite(proc)
        finite_ratio = float(finite.mean()) if proc.size else 0.0
        spread = float(np.nanmax(proc) - np.nanmin(proc)) if finite.any() else 0.0
        if finite_ratio < 0.5 or spread < 1e-6:
            try:
                self._proc_data = normalize_display(
                    np.asarray(self._raw_data, dtype=np.float32),
                    clip_pct=float(self._spin_clip.value()),
                )
                self._lbl_status.setText(
                    "Processing inconcludente: visualizzazione fallback su dato grezzo normalizzato."
                )
            except Exception:
                pass

        self._apply_gain_only()

    def _apply_gain_only(self):
        if self._proc_data is None:
            self._apply_processing()
            return
        gain = float(self._spin_gain.value())
        self._disp_data = (self._proc_data * gain).astype(np.float32, copy=False)
        disp_arr = np.asarray(self._disp_data, dtype=np.float64)
        finite = np.isfinite(disp_arr)
        if finite.any():
            disp_min = float(np.nanmin(disp_arr))
            disp_max = float(np.nanmax(disp_arr))
        else:
            disp_min = float("nan")
            disp_max = float("nan")
        self._lbl_status.setText(
            f"proc: min={self._proc_data.min():.3f}  "
            f"max={self._proc_data.max():.3f}  "
            f"disp: min={disp_min:.3f} max={disp_max:.3f}  "
            f"gain={gain:.1f}x"
        )
        self._redraw()

    @staticmethod
    def _clamp_axis_limits(
        lim0: float, lim1: float, axis_min: float, axis_max: float, min_span_ratio: float = 1e-3
    ) -> tuple[float, float]:
        if not (np.isfinite(lim0) and np.isfinite(lim1)):
            return axis_min, axis_max
        span_total = max(axis_max - axis_min, 1e-12)
        forward = lim1 >= lim0
        lo = min(lim0, lim1)
        hi = max(lim0, lim1)
        min_span = span_total * min_span_ratio
        if (hi - lo) < min_span:
            c = 0.5 * (lo + hi)
            lo = c - 0.5 * min_span
            hi = c + 0.5 * min_span
        if lo < axis_min:
            shift = axis_min - lo
            lo += shift
            hi += shift
        if hi > axis_max:
            shift = hi - axis_max
            lo -= shift
            hi -= shift
        lo = max(axis_min, lo)
        hi = min(axis_max, hi)
        return (lo, hi) if forward else (hi, lo)

    def _reset_zoom(self):
        self._view_xlim = None
        self._view_ylim = None
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

        dist_arr = np.asarray(ch.distances, dtype=np.float64)
        if dist_arr.size and np.isfinite(dist_arr).any():
            dist_max = float(np.nanmax(dist_arr))
        else:
            dist_max = float(max(1.0, prof.sampling_step_m * max(self._disp_data.shape[1] - 1, 1)))
        if not np.isfinite(dist_max) or dist_max <= 0:
            dist_max = float(max(1.0, prof.sampling_step_m * max(self._disp_data.shape[1] - 1, 1)))

        n_out     = self._disp_data.shape[0]
        n_orig    = prof.n_samples
        depth_base = float(prof.depth_max_m) if np.isfinite(prof.depth_max_m) else 0.0
        depth_max = depth_base * (n_out / n_orig) if n_orig > 0 else depth_base
        if not np.isfinite(depth_max) or depth_max <= 0:
            depth_max = max(1.0, float(n_out))

        self._ax.clear()
        disp = np.asarray(self._disp_data, dtype=np.float64)
        finite = disp[np.isfinite(disp)]
        if finite.size:
            vmin = float(np.percentile(finite, 1.0))
            vmax = float(np.percentile(finite, 99.0))
            if (not np.isfinite(vmin)) or (not np.isfinite(vmax)) or (vmax - vmin < 1e-6):
                vmin = float(np.nanmin(finite))
                vmax = float(np.nanmax(finite))
            if (not np.isfinite(vmin)) or (not np.isfinite(vmax)) or (vmax - vmin < 1e-6):
                vmin, vmax = -1.0, 1.0
        else:
            vmin, vmax = -1.0, 1.0
        real_aspect = bool(
            getattr(self, "_chk_real_aspect", None)
            and self._chk_real_aspect.isChecked()
        )
        interpolation_mode = "bilinear"
        if hasattr(self, "_cb_interp") and self._cb_interp is not None:
            interpolation_mode = str(self._cb_interp.currentData() or "bilinear")
        self._im = self._ax.imshow(
            self._disp_data,
            aspect="equal" if real_aspect else "auto",
            cmap=cmap,
            vmin=vmin, vmax=vmax,
            extent=[0, dist_max, depth_max, 0],
            interpolation=interpolation_mode,
            resample=True,
        )
        x_full = (0.0, dist_max)
        y_full = (depth_max, 0.0)
        x_view = self._view_xlim if self._view_xlim is not None else x_full
        y_view = self._view_ylim if self._view_ylim is not None else y_full
        x_view = self._clamp_axis_limits(x_view[0], x_view[1], x_full[0], x_full[1])
        y_view = self._clamp_axis_limits(y_view[0], y_view[1], 0.0, depth_max)
        self._view_xlim = x_view
        self._view_ylim = y_view
        self._ax.set_xlim(*x_view)
        self._ax.set_ylim(*y_view)
        self._ax.set_aspect("equal" if real_aspect else "auto", adjustable="box")
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
            self._emit_cursor_moved()

    def _on_scroll_zoom(self, event):
        if event.inaxes != self._ax or self._im is None:
            return
        if event.xdata is None or event.ydata is None:
            return

        step = float(getattr(event, "step", 0.0) or 0.0)
        btn = str(getattr(event, "button", "") or "").lower()
        if step > 0 or btn == "up":
            scale = 1.0 / 1.2
        elif step < 0 or btn == "down":
            scale = 1.2
        else:
            return

        x0, x1 = self._ax.get_xlim()
        y0, y1 = self._ax.get_ylim()
        x = float(event.xdata)
        y = float(event.ydata)

        nx0 = x - (x - x0) * scale
        nx1 = x + (x1 - x) * scale
        ny0 = y - (y - y0) * scale
        ny1 = y + (y1 - y) * scale

        ex0, ex1, ey0, ey1 = self._im.get_extent()
        x_min, x_max = float(min(ex0, ex1)), float(max(ex0, ex1))
        y_min, y_max = float(min(ey0, ey1)), float(max(ey0, ey1))

        self._view_xlim = self._clamp_axis_limits(nx0, nx1, x_min, x_max)
        self._view_ylim = self._clamp_axis_limits(ny0, ny1, y_min, y_max)

        self._ax.set_xlim(*self._view_xlim)
        self._ax.set_ylim(*self._view_ylim)
        self._canvas_mpl.draw_idle()

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
        east, north, _ = self._cursor_world_position()
        east = float(east) if east is not None else np.nan
        north = float(north) if north is not None else np.nan
        z_str = f"{self._cursor_z:.3f} m" if self._cursor_z is not None else "\u2014"
        x_str = f"{self._cursor_x:.2f} m" if self._cursor_x is not None else "\u2014"
        self._lbl_status.setText(
            f"Dist: {x_str}  |  Profondit\u00e0: {z_str}  "
            f"|  E {east:.1f}  N {north:.1f}"
        )

    def _cursor_world_position(self):
        if (
            not self._profiles
            or self._cursor_x is None
            or self._cursor_z is None
        ):
            return None, None, None
        try:
            prof = self._profiles[self._prof_idx]
            ch = prof.channel(self._ch_idx)
        except Exception:
            return None, None, None
        if len(ch.distances) < 1:
            return None, None, None
        try:
            idx_t = int(np.searchsorted(ch.distances, self._cursor_x))
            idx_t = min(max(idx_t, 0), len(ch.distances) - 1)
            east = float(ch.easting[idx_t])
            north = float(ch.northing[idx_t])
            depth = float(self._cursor_z)
        except Exception:
            return None, None, None
        if not (np.isfinite(east) and np.isfinite(north) and np.isfinite(depth)):
            return None, None, None
        return east, north, depth

    def _emit_cursor_moved(self):
        east, north, depth = self._cursor_world_position()
        if east is None or north is None or depth is None:
            return
        try:
            self.cursor_moved.emit(float(east), float(north), float(depth))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Bridge QGIS
    # ------------------------------------------------------------------

    def _flush_canvas_update(self):
        self._update_rubber_band()
        self._update_dial()
        self._emit_cursor_moved()

    def _to_canvas_point(self, prof, east: float, north: float) -> QgsPointXY:
        pt = QgsPointXY(float(east), float(north))
        if self.iface is None:
            return pt
        canvas = self.iface.mapCanvas()
        if canvas is None:
            return pt
        try:
            epsg = int(getattr(prof, "epsg", 0) or 0)
        except Exception:
            epsg = 0
        if epsg <= 0:
            # Fallback: prova CRS progetto QGIS quando l'EPSG profilo manca.
            try:
                src = QgsProject.instance().crs()
                dst = canvas.mapSettings().destinationCrs()
                if src.isValid() and dst.isValid() and src != dst:
                    tr = QgsCoordinateTransform(src, dst, QgsProject.instance())
                    return tr.transform(pt)
            except Exception:
                pass
            return pt
        try:
            src = QgsCoordinateReferenceSystem.fromEpsgId(epsg)
            dst = canvas.mapSettings().destinationCrs()
            if not src.isValid() or not dst.isValid() or src == dst:
                return pt
            tr = QgsCoordinateTransform(src, dst, QgsProject.instance())
            return tr.transform(pt)
        except Exception:
            return pt

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
        self._rb_point.setVisible(True)
        self._rb_point.reset(QgsWkbTypes.PointGeometry)
        pt_canvas = self._to_canvas_point(prof, ch.easting[idx_t], ch.northing[idx_t])
        self._rb_point.addPoint(
            pt_canvas, True
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
            self._rb_line.setVisible(True)
        else:
            self._rb_line.reset(QgsWkbTypes.LineGeometry)
        for i in range(len(ch.easting)):
            pt_canvas = self._to_canvas_point(prof, ch.easting[i], ch.northing[i])
            self._rb_line.addPoint(
                pt_canvas,
                i == len(ch.easting) - 1
            )

    def _get_cached_catalog(self, project_root: str):
        if not project_root:
            return None
        cat_path = os.path.join(project_root, "metadata", "project_catalog.json")
        try:
            mtime = float(os.path.getmtime(cat_path))
        except Exception:
            mtime = None
        needs_reload = (
            self._cached_catalog is None
            or self._cached_catalog_pr != project_root
            or self._cached_catalog_mtime != mtime
        )
        if needs_reload:
            from .project_catalog import load_catalog

            self._cached_catalog = load_catalog(project_root)
            self._cached_catalog_pr = project_root
            self._cached_catalog_mtime = mtime
        return self._cached_catalog

    def _update_dial(self):
        if self._cursor_z is None or self.plugin is None:
            return
        settings = getattr(self.plugin, "settings", None)
        settings_key = getattr(self.plugin, "settings_key_active_project", None)
        if settings is None or not settings_key:
            return
        dlg = getattr(self.plugin, "dlg", None)
        if dlg is None:
            return
        dial = getattr(dlg, "Dial", None) or getattr(dlg, "dial", None)
        if dial is None:
            return
        try:
            pr = (settings.value(settings_key, "", type=str) or "").strip()
            if not pr:
                return
            catalog = self._get_cached_catalog(pr)
            if not isinstance(catalog, dict):
                return
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

    def get_slice_params(self) -> dict:
        return {
            "normalize_channels":  self._chk_normalize_ch.isChecked(),
            "extraction_mode":     str(self._cb_slice_extraction.currentData() or "las_like"),
            "use_processing":      self._chk_slice_use_processing.isChecked(),
            "amplitude_sigma":     (
                float(self._spin_amplitude_sigma.value())
                if self._chk_amplitude_filter.isChecked() else None
            ),
            "use_anisotropic_idw": self._chk_anisotropic_idw.isChecked(),
            "auto_radius":         self._chk_auto_radius.isChecked(),
            "balance_profiles":    self._chk_slice_balance_profiles.isChecked(),
            "depth_radius_factor": float(self._spin_slice_depth_radius_factor.value()),
            "min_points":          int(self._spin_slice_min_points.value()),
            "fill_nodata":         self._chk_fill_nodata.isChecked(),
            "smooth_sigma":        (
                float(self._spin_smooth_sigma.value())
                if self._chk_smooth.isChecked() else 0.0
            ),
        }

    def _open_slice_dialog(self):
        if not self._profiles:
            QMessageBox.information(self, "Nessun profilo",
                                    "Importa almeno un file .ogpr prima.")
            return
        slice_params = self.get_slice_params()
        if self.plugin and hasattr(self.plugin, "import_ogpr_as_slices"):
            self.plugin.import_ogpr_as_slices(self._profiles, slice_params=slice_params)
        else:
            QMessageBox.information(
                self, "Timeslice",
                "Funzione disponibile dal plugin principale."
            )

    def _open_3d_viewer(self):
        if not self._profiles:
            QMessageBox.information(
                self,
                "Nessun profilo",
                "Importa almeno un file .ogpr prima di aprire il viewer 3D.",
            )
            return

        try:
            from .gpr_3d_viewer_dialog import Gpr3dViewerDialog
            from .gpr_ogpr_slicer import compute_ogpr_slice_grids
            from .gpr_volume_3d import build_3d_volume
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Viewer 3D",
                f"Impossibile inizializzare il viewer 3D:\n{exc}",
            )
            return

        depth_max = max(float(getattr(p, "depth_max_m", 0.0) or 0.0) for p in self._profiles)
        n_channels = max(int(getattr(p, "n_channels", 1) or 1) for p in self._profiles)

        params = None
        if self.plugin is not None and hasattr(self.plugin, "_ask_ogpr_slice_params"):
            try:
                params = self.plugin._ask_ogpr_slice_params(
                    profiles=self._profiles,
                    n_channels=n_channels,
                    depth_max_m=depth_max,
                    default_group="gpr_volume3d_preview",
                    saved=None,
                    slice_params=self.get_slice_params(),
                )
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Viewer 3D",
                    f"Errore nel dialog parametri 3D:\n{exc}",
                )
                return
            if params is None:
                return
        else:
            # Fallback sicuro quando il plugin principale non e' disponibile.
            params = {
                "channel": int(self._ch_idx),
                "combine_method": "mean",
                "z_min": 0.0,
                "z_max": max(depth_max, 0.1),
                "z_step": 0.05,
                "resolution": 0.10,
                "radius": 0.10 * (2.0 ** 0.5),
                "group_name": "gpr_volume3d_preview",
            }

        extra = self.get_slice_params()
        try:
            grids, meta = compute_ogpr_slice_grids(
                profiles=self._profiles,
                channel=int(params["channel"]),
                combine_method=str(params["combine_method"]),
                resolution=float(params["resolution"]),
                z_step=float(params["z_step"]),
                z_min=float(params["z_min"]),
                z_max=float(params["z_max"]),
                radius=float(params["radius"]),
                normalize_channels=bool(extra.get("normalize_channels", False)),
                extraction_mode=str(extra.get("extraction_mode", "las_like") or "las_like"),
                use_processing=bool(extra.get("use_processing", False)),
                amplitude_sigma=extra.get("amplitude_sigma"),
                use_anisotropic_idw=bool(extra.get("use_anisotropic_idw", False)),
                auto_radius=bool(extra.get("auto_radius", False)),
                min_points=int(extra.get("min_points", 1) or 1),
                fill_nodata=bool(extra.get("fill_nodata", False)),
                smooth_sigma=float(extra.get("smooth_sigma", 0.0) or 0.0),
                depth_radius_factor=float(extra.get("depth_radius_factor", 0.6) or 0.0),
                balance_profiles=bool(extra.get("balance_profiles", True)),
                emit_diagnostics=True,
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Viewer 3D",
                f"Errore durante il calcolo volume 3D:\n{exc}",
            )
            return

        if not grids:
            QMessageBox.warning(
                self,
                "Viewer 3D",
                "Nessuna slice disponibile per costruire il volume 3D.",
            )
            return

        try:
            volume = build_3d_volume(grids, meta)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Viewer 3D",
                f"Impossibile costruire il volume 3D:\n{exc}",
            )
            return

        z_levels = []
        for g in grids:
            try:
                z_levels.append(float(g.get("z_lev")))
            except Exception:
                continue
        z_levels = [z for z in z_levels if np.isfinite(z)]
        z_min_view = float(min(z_levels)) if z_levels else float(params["z_min"])
        z_max_view = float(max(z_levels)) if z_levels else float(params["z_max"])

        meta_3d = dict(meta or {})
        meta_3d.update({
            "n_z": int(volume.shape[0]),
            "n_y": int(volume.shape[1]),
            "n_x": int(volume.shape[2]),
            "z_step": float(params["z_step"]),
            "z_min": z_min_view,
            "z_max": z_max_view,
            "z_levels": [float(z) for z in z_levels] if z_levels else None,
        })

        if self._gpr_3d_viewer is not None:
            try:
                self.cursor_moved.disconnect(self._gpr_3d_viewer.update_cursor_position)
            except Exception:
                pass
            try:
                self._gpr_3d_viewer.close()
            except Exception:
                pass
            self._gpr_3d_viewer = None

        try:
            viewer = Gpr3dViewerDialog(
                volume=volume,
                meta=meta_3d,
                profiles=self._profiles,
                grids=grids,
                parent=self,
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Viewer 3D",
                f"Impossibile aprire il viewer 3D:\n{exc}",
            )
            return

        self._gpr_3d_viewer = viewer
        try:
            self.cursor_moved.connect(viewer.update_cursor_position)
        except Exception:
            pass
        self._emit_cursor_moved()
        viewer.show()
        viewer.raise_()
        viewer.activateWindow()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if self._gpr_3d_viewer is not None:
            try:
                self.cursor_moved.disconnect(self._gpr_3d_viewer.update_cursor_position)
            except Exception:
                pass
            try:
                self._gpr_3d_viewer.close()
            except Exception:
                pass
            self._gpr_3d_viewer = None
        for rb in (self._rb_point, self._rb_line):
            if rb is not None:
                try:
                    rb.reset()
                except Exception:
                    pass
        super().closeEvent(event)
