# -*- coding: utf-8 -*-
"""
GPR Profile Viewer
==================
QMainWindow con radargram matplotlib integrato.
"""

from __future__ import annotations

import json
import os
import glob
import re
from typing import Optional

import numpy as np

from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal, QSettings
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog, QMainWindow, QVBoxLayout, QHBoxLayout, QToolBar,
    QAction, QLabel, QComboBox,
    QCheckBox, QDoubleSpinBox, QSpinBox,
    QSlider,
    QSplitter,
    QWidget,
    QInputDialog,
    QFileDialog, QMessageBox, QSizePolicy,
    QGroupBox, QFormLayout, QPushButton, QDialogButtonBox, QScrollArea, QDockWidget, QLineEdit,
)
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsWkbTypes,
    QgsRasterLayer,
)
from qgis.gui import QgsRubberBand

try:
    from qgis.PyQt import sip  # type: ignore
except Exception:
    try:
        import sip  # type: ignore
    except Exception:
        sip = None  # type: ignore

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

from .gpr_ogpr_reader import read_ogpr_cached, OgprProfile, OgprChannel
from .gpr_processing  import apply_pipeline, DEFAULT_PIPELINE, normalize_display


GPR_CMAPS    = [
    "RdBu_r",
    "seismic",
    "gray",
    "bwr",
    "Greys_r",
    "viridis",
    "plasma",
    "magma",
    "cividis",
    "turbo",
    "Spectral_r",
]
DEFAULT_CMAP = "RdBu_r"


class _RangeGainCurveDialog(QDialog):
    """Interactive breakpoint editor for range gain curve."""

    def __init__(self, points, max_points=16, on_curve_changed=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Range Gain Curve")
        self.resize(620, 420)
        self._max_points = int(max(2, min(32, max_points)))
        self._on_curve_changed = on_curve_changed
        self._drag_idx = None
        self._selected_idx = None
        self._did_drag = False
        self._points = self._sanitize(points)

        root = QVBoxLayout(self)

        if HAS_MPL:
            self._fig = Figure(figsize=(6.2, 3.2), tight_layout=True)
            self._ax = self._fig.add_subplot(111)
            self._canvas = FigureCanvasQTAgg(self._fig)
            root.addWidget(self._canvas, 1)
            self._canvas.mpl_connect("button_press_event", self._on_press)
            self._canvas.mpl_connect("button_release_event", self._on_release)
            self._canvas.mpl_connect("motion_notify_event", self._on_move)
        else:
            self._fig = None
            self._ax = None
            self._canvas = None
            lbl = QLabel("matplotlib non disponibile.")
            lbl.setAlignment(Qt.AlignCenter)
            root.addWidget(lbl, 1)

        help_lbl = QLabel(
            "X=Gain, Y=Depth (0 in alto) | Left click: seleziona/drag | Double-click: aggiungi | Right-click: elimina punto interno"
        )
        help_lbl.setWordWrap(True)
        root.addWidget(help_lbl)

        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("Step:"))
        self._spin_step = QDoubleSpinBox()
        self._spin_step.setRange(0.01, 1.0)
        self._spin_step.setSingleStep(0.01)
        self._spin_step.setValue(0.05)
        self._spin_step.setDecimals(2)
        step_row.addWidget(self._spin_step)
        self._btn_step_up = QPushButton("▲ Gain +")
        self._btn_step_down = QPushButton("▼ Gain -")
        self._btn_step_up.clicked.connect(lambda: self._move_selected(+float(self._spin_step.value())))
        self._btn_step_down.clicked.connect(lambda: self._move_selected(-float(self._spin_step.value())))
        step_row.addWidget(self._btn_step_up)
        step_row.addWidget(self._btn_step_down)
        step_row.addStretch(1)
        root.addLayout(step_row)

        btns_row = QHBoxLayout()
        self._btn_reset = QPushButton("Reset")
        self._btn_reset.clicked.connect(self._on_reset)
        btns_row.addWidget(self._btn_reset)
        btns_row.addStretch(1)
        root.addLayout(btns_row)

        dbb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        dbb.accepted.connect(self.accept)
        dbb.rejected.connect(self.reject)
        root.addWidget(dbb)

        self._redraw()

    def _sanitize(self, points):
        arr = np.asarray(points if points is not None else [], dtype=np.float64).reshape(-1, 2)
        clean = []
        for x, y in arr:
            if np.isfinite(x) and np.isfinite(y):
                clean.append((float(np.clip(x, 0.0, 1.0)), float(np.clip(y, 0.1, 80.0))))
        if not clean:
            clean = [(0.0, 1.0), (1.0, 6.0)]
        clean.sort(key=lambda p: p[0])
        # Force endpoints and uniqueness.
        x0, y0 = clean[0]
        x1, y1 = clean[-1]
        pts = [(0.0, float(y0))]
        for x, y in clean[1:-1]:
            if x <= 0.0 or x >= 1.0:
                continue
            if abs(x - pts[-1][0]) < 1e-6:
                continue
            pts.append((x, y))
        if abs(1.0 - pts[-1][0]) < 1e-6:
            pts[-1] = (1.0, float(y1))
        else:
            pts.append((1.0, float(y1)))
        if len(pts) > self._max_points:
            inner = pts[1:-1]
            take = max(0, self._max_points - 2)
            if len(inner) > take:
                idx = np.linspace(0, len(inner) - 1, take, dtype=np.int64)
                inner = [inner[i] for i in idx]
            pts = [pts[0]] + inner + [pts[-1]]
        return np.asarray(pts, dtype=np.float64)

    def points(self):
        return self._points.astype(np.float64, copy=True)

    def _emit_changed(self):
        if callable(self._on_curve_changed):
            try:
                self._on_curve_changed(self.points())
            except Exception:
                pass

    def _redraw(self):
        if not HAS_MPL or self._ax is None or self._canvas is None:
            return
        self._ax.clear()
        p = self._points
        # X=gain, Y=depth fraction (0 top, 1 bottom).
        self._ax.plot(p[:, 1], p[:, 0], color="#2d6ba3", lw=1.4)
        self._ax.scatter(p[:, 1], p[:, 0], s=40, color="#c0392b", zorder=3)
        x_max = float(max(10.0, np.nanmax(p[:, 1]) * 1.15))
        self._ax.set_xlim(0.0, x_max)
        self._ax.set_ylim(1.0, 0.0)
        self._ax.grid(True, ls=":", lw=0.5, alpha=0.7)
        self._ax.set_xlabel("Gain")
        self._ax.set_ylabel("Depth fraction")
        self._ax.set_title(f"Breakpoints: {len(p)}/{self._max_points}")
        if self._selected_idx is not None:
            try:
                i = int(self._selected_idx)
                if 0 <= i < len(p):
                    sel = p[i]
                    self._ax.scatter(
                        [float(sel[1])],
                        [float(sel[0])],
                        s=80,
                        color="#ffd000",
                        edgecolor="#1f1f1f",
                        linewidths=1.0,
                        zorder=5,
                    )
            except Exception:
                pass
        self._canvas.draw_idle()

    def _nearest_idx(self, x, y):
        p = self._points
        if p.shape[0] <= 0:
            return None, 1e9
        # x=gain, y=depth fraction
        x_span = max(1e-6, float(np.nanmax(p[:, 1]) - np.nanmin(p[:, 1])))
        dx = (p[:, 1] - float(x)) / x_span
        dy = (p[:, 0] - float(y))
        d = np.sqrt(dx * dx + dy * dy)
        idx = int(np.argmin(d))
        return idx, float(d[idx])

    def _on_press(self, event):
        if event is None or event.inaxes != self._ax or event.xdata is None or event.ydata is None:
            return
        gain = float(np.clip(event.xdata, 0.1, 80.0))
        depth = float(np.clip(event.ydata, 0.0, 1.0))
        idx, dist = self._nearest_idx(gain, depth)

        # Right click: remove internal point.
        if int(getattr(event, "button", 0) or 0) == 3:
            if idx is not None and dist < 0.05 and idx not in (0, len(self._points) - 1):
                self._points = np.delete(self._points, idx, axis=0)
                self._points = self._sanitize(self._points)
                self._selected_idx = None
                self._emit_changed()
                self._redraw()
            return

        # Double left click: add point.
        if bool(getattr(event, "dblclick", False)) and int(getattr(event, "button", 0) or 0) == 1:
            if len(self._points) < self._max_points and (idx is None or dist >= 0.02):
                self._points = np.vstack([self._points, np.asarray([[depth, gain]], dtype=np.float64)])
                self._points = self._sanitize(self._points)
                idx_new, _ = self._nearest_idx(gain, depth)
                self._selected_idx = idx_new
                self._emit_changed()
                self._redraw()
            return

        # Single left click: select nearest point and enable drag.
        if int(getattr(event, "button", 0) or 0) == 1 and idx is not None and dist < 0.08:
            self._selected_idx = int(idx)
            self._drag_idx = int(idx)
            self._did_drag = False
            self._redraw()

    def _on_move(self, event):
        if self._drag_idx is None:
            return
        if event is None or event.inaxes != self._ax or event.xdata is None or event.ydata is None:
            return
        i = int(self._drag_idx)
        gain = float(np.clip(event.xdata, 0.1, 80.0))
        depth = float(np.clip(event.ydata, 0.0, 1.0))
        p = self._points.copy()
        if i == 0:
            depth = 0.0
        elif i == (len(p) - 1):
            depth = 1.0
        else:
            lo = float(p[i - 1, 0] + 1e-4)
            hi = float(p[i + 1, 0] - 1e-4)
            if hi <= lo:
                depth = float(p[i, 0])
            else:
                depth = float(np.clip(depth, lo, hi))
        p[i, 0] = depth
        p[i, 1] = gain
        self._points = self._sanitize(p)
        self._did_drag = True
        self._emit_changed()
        self._redraw()

    def _on_release(self, _event):
        self._did_drag = False
        self._drag_idx = None

    def _move_selected(self, delta: float):
        if self._selected_idx is None:
            return
        i = int(self._selected_idx)
        p = self._points.copy()
        if i < 0 or i >= len(p):
            return
        p[i, 1] = float(np.clip(p[i, 1] + float(delta), 0.1, 80.0))
        self._points = self._sanitize(p)
        self._emit_changed()
        self._redraw()

    def _on_reset(self):
        self._points = np.asarray([(0.0, 1.0), (1.0, 6.0)], dtype=np.float64)
        self._selected_idx = None
        self._drag_idx = None
        self._emit_changed()
        self._redraw()


class GprProfileViewer(QMainWindow):
    cursor_moved = pyqtSignal(float, float, float)  # easting, northing, depth

    def __init__(self, iface, plugin=None, parent=None):
        super().__init__(parent)
        self.iface  = iface
        self.plugin = plugin
        self.setWindowTitle("GPR Profile Viewer")
        self.resize(1200, 750)
        self.setDockNestingEnabled(True)

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
        self._processing_dock = None
        self._timeslice_dock = None
        self._processing_panel_widget = None
        self._timeslice_panel_widget = None
        self._cached_catalog = None
        self._cached_catalog_pr: Optional[str] = None
        self._cached_catalog_mtime: Optional[float] = None
        self._suppress_3d_depth_sync = False
        self._updating_range_gain_controls = False
        self._range_gain_breakpoints = np.asarray([(0.0, 1.0), (1.0, 6.0)], dtype=np.float64)
        self._hyper_apex_x: Optional[float] = None
        self._hyper_apex_depth: Optional[float] = None
        self._trim_start_traces = 0
        self._trim_end_traces = 0
        self._trace_source_indices: Optional[np.ndarray] = None
        self._bp_figure = None
        self._bp_ax = None
        self._bp_canvas = None
        self._wiggle_trace_idx: Optional[int] = None
        self._wiggle_depth_line = None
        self._fig_slice = None
        self._ax_slice = None
        self._canvas_slice = None
        self._slider_slice_depth = None
        self._slice_im = None
        self._slice_vline = None
        self._slice_hline = None
        self._slice_view_xlim: Optional[tuple[float, float]] = None
        self._slice_view_ylim: Optional[tuple[float, float]] = None
        self._slice_cache_path: Optional[str] = None
        self._slice_cache_mtime: Optional[float] = None
        self._slice_cache_arr: Optional[np.ndarray] = None
        self._slice_cache_extent: Optional[tuple[float, float, float, float]] = None
        self._slice_current_idx: Optional[int] = None
        self._slice_current_path: Optional[str] = None
        self._slice_current_extent: Optional[tuple[float, float, float, float]] = None
        self._slice_current_shape: Optional[tuple[int, int]] = None
        self._slice_current_cmap: str = ""
        self._slice_pan_anchor: Optional[tuple[float, float, tuple[float, float], tuple[float, float]]] = None
        self._slice_catalog: list[dict] = []
        self._slice_catalog_source_dir: str = ""
        self._slice_catalog_dz: float = 0.0
        self._updating_slice_nav: bool = False
        self._show_wiggle: bool = True
        self._ax_main_bounds_default = None
        self._ax_wiggle_bounds_default = None
        self._last_pipeline_params: dict = {}
        self._profile_view_initialized: bool = True
        self._allow_close: bool = False

        self._rb_point: Optional[QgsRubberBand] = None
        self._rb_line:  Optional[QgsRubberBand] = None
        self._rb_crosshair_h: Optional[QgsRubberBand] = None
        self._rb_crosshair_v: Optional[QgsRubberBand] = None
        self._crosshair_size_m: float = 5.0

        self._canvas_timer = QTimer(self)
        self._canvas_timer.setSingleShot(True)
        self._canvas_timer.setInterval(50)
        self._canvas_timer.timeout.connect(self._flush_canvas_update)
        self._updating_xpan = False

        self._build_ui()

    def _is_qt_alive(self, obj) -> bool:
        if obj is None:
            return False
        try:
            if sip is not None and hasattr(sip, "isdeleted") and sip.isdeleted(obj):
                return False
        except Exception:
            return False
        return True

    def _safe_set_text(self, widget, text: str):
        if not self._is_qt_alive(widget):
            return
        try:
            widget.setText(str(text))
        except Exception:
            pass

    def _safe_cb_clear(self, combo):
        if not self._is_qt_alive(combo):
            return
        try:
            combo.blockSignals(True)
            combo.clear()
        except Exception:
            pass
        finally:
            try:
                combo.blockSignals(False)
            except Exception:
                pass

    def _safe_draw_idle(self, canvas):
        if not self._is_qt_alive(canvas):
            return
        try:
            canvas.draw_idle()
        except Exception:
            pass

    def _reset_session_state(self):
        try:
            self._canvas_timer.stop()
        except Exception:
            pass

        self._profiles = []
        self._prof_idx = 0
        self._ch_idx = 0
        self._raw_data = None
        self._proc_data = None
        self._disp_data = None
        self._cursor_x = None
        self._cursor_z = None
        self._view_xlim = None
        self._view_ylim = None
        self._trace_source_indices = None
        self._trim_start_traces = 0
        self._trim_end_traces = 0
        self._hyper_apex_x = None
        self._hyper_apex_depth = None
        self._wiggle_trace_idx = None
        self._slice_pan_anchor = None

        self._slice_catalog = []
        self._slice_current_idx = None
        self._slice_current_path = None
        self._slice_current_extent = None
        self._slice_current_shape = None
        self._slice_current_cmap = ""
        self._slice_view_xlim = None
        self._slice_view_ylim = None
        self._slice_cache_path = None
        self._slice_cache_mtime = None
        self._slice_cache_arr = None
        self._slice_cache_extent = None

        self._safe_set_text(self._lbl_profile, "\u2014")
        self._safe_set_text(self._lbl_status, "Importa un file .ogpr per iniziare.")
        self._safe_set_text(self._lbl_xpan, "Full")
        self._safe_cb_clear(self._cb_channel)
        if self._is_qt_alive(getattr(self, "_xpan_slider", None)):
            try:
                self._xpan_slider.setEnabled(False)
                self._xpan_slider.setValue(0)
            except Exception:
                pass
        if self._is_qt_alive(getattr(self, "_slider_slice_depth", None)):
            try:
                self._slider_slice_depth.setRange(0, 0)
                self._slider_slice_depth.setValue(0)
                self._slider_slice_depth.setEnabled(False)
            except Exception:
                pass

        try:
            if self._ax is not None:
                self._ax.clear()
            if self._ax_wiggle is not None:
                self._ax_wiggle.clear()
            if self._canvas_mpl is not None:
                self._safe_draw_idle(self._canvas_mpl)
        except Exception:
            pass

        try:
            if self._ax_slice is not None:
                self._ax_slice.clear()
            if self._canvas_slice is not None:
                self._safe_draw_idle(self._canvas_slice)
        except Exception:
            pass

        self._update_slice_navigator()
        try:
            self._clear_timeslice_crosshair()
        except Exception:
            pass

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
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

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
        self._chk_real_aspect.toggled.connect(self._on_real_aspect_toggled)
        tb.addWidget(self._chk_real_aspect)
        tb.addWidget(QLabel(" VE:"))
        self._spin_vertical_exag = QDoubleSpinBox()
        self._spin_vertical_exag.setRange(0.5, 20.0)
        self._spin_vertical_exag.setSingleStep(0.5)
        self._spin_vertical_exag.setValue(5.0)
        self._spin_vertical_exag.setDecimals(1)
        self._spin_vertical_exag.setEnabled(bool(self._chk_real_aspect.isChecked()))
        self._spin_vertical_exag.setToolTip(
            "Vertical Exaggeration (VE): 1.0 = scala reale.\n"
            "Valori > 1 aumentano l'enfasi verticale del radargramma."
        )
        self._spin_vertical_exag.valueChanged.connect(lambda _v: self._redraw())
        tb.addWidget(self._spin_vertical_exag)

        self._chk_show_wiggle = QCheckBox("Mostra Wiggle")
        self._chk_show_wiggle.setChecked(True)
        self._chk_show_wiggle.toggled.connect(self._on_toggle_wiggle)
        tb.addWidget(self._chk_show_wiggle)

        act_reset_zoom = QAction("Reset Zoom", self)
        act_reset_zoom.setToolTip("Ripristina l'estensione completa del profilo.")
        act_reset_zoom.triggered.connect(self._reset_zoom)
        tb.addAction(act_reset_zoom)

        act_export = QAction("Export Radargram", self)
        act_export.setToolTip("Esporta il radargramma corrente in PNG/TIFF con DPI configurabile.")
        act_export.triggered.connect(self._export_radargram_image)
        tb.addAction(act_export)

        tb.addSeparator()
        act_processing = QAction("Processing", self)
        act_processing.setToolTip("Apri il pannello Processing in una finestra separata.")
        act_processing.triggered.connect(self._open_processing_window)
        tb.addAction(act_processing)

        act_timeslice = QAction("Timeslice", self)
        act_timeslice.setToolTip("Apri il pannello Timeslice in una finestra separata.")
        act_timeslice.triggered.connect(self._open_timeslice_window)
        tb.addAction(act_timeslice)

        act_view3d = QAction("3D Viewer", self)
        act_view3d.setToolTip("Apri il viewer 3D delle timeslice.")
        act_view3d.triggered.connect(self._open_3d_viewer)
        tb.addAction(act_view3d)

        root.addWidget(tb)

        center = QHBoxLayout()
        self._xpan_slider = None
        self._lbl_xpan = None

        if HAS_MPL:
            # Upper panel: active timeslice 2D view.
            self._fig_slice = Figure(figsize=(9, 3), tight_layout=True)
            self._ax_slice = self._fig_slice.add_subplot(111)
            self._canvas_slice = FigureCanvasQTAgg(self._fig_slice)
            self._canvas_slice.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._canvas_slice.mpl_connect("button_press_event", self._on_slice_press)
            self._canvas_slice.mpl_connect("button_release_event", self._on_slice_release)
            self._canvas_slice.mpl_connect("motion_notify_event", self._on_slice_mouse_move)
            self._canvas_slice.mpl_connect("scroll_event", self._on_slice_scroll_zoom)
            self._slider_slice_depth = QSlider(Qt.Vertical)
            self._slider_slice_depth.setRange(0, 0)
            self._slider_slice_depth.setValue(0)
            self._slider_slice_depth.setEnabled(False)
            self._slider_slice_depth.setInvertedAppearance(True)
            self._slider_slice_depth.setInvertedControls(True)
            self._slider_slice_depth.setToolTip(
                "Scorri tra le slice calcolate.\nSu = superficiale | Giu = profondo"
            )
            self._slider_slice_depth.valueChanged.connect(self._on_slice_depth_slider)
            slice_host = QWidget()
            slice_lay = QHBoxLayout(slice_host)
            slice_lay.setContentsMargins(0, 0, 0, 0)
            slice_lay.setSpacing(4)
            slice_lay.addWidget(self._canvas_slice, stretch=1)
            slice_lay.addWidget(self._slider_slice_depth, stretch=0)

            # Lower panel: radargram + wiggle + pan slider.
            self._fig = Figure(figsize=(9, 3), tight_layout=False)
            gs = self._fig.add_gridspec(
                1,
                2,
                width_ratios=[4.0, 0.6],
                wspace=0.02,
                left=0.07,
                right=0.98,
                top=0.94,
                bottom=0.11,
            )
            self._ax         = self._fig.add_subplot(gs[0, 0])
            self._ax_wiggle  = self._fig.add_subplot(gs[0, 1], sharey=self._ax)
            self._ax_wiggle.tick_params(axis="y", left=False, labelleft=False)
            self._ax_main_bounds_default = list(self._ax.get_position().bounds)
            self._ax_wiggle_bounds_default = list(self._ax_wiggle.get_position().bounds)
            self._canvas_mpl = FigureCanvasQTAgg(self._fig)
            self._canvas_mpl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._canvas_mpl.mpl_connect("motion_notify_event", self._on_mouse_move)
            self._canvas_mpl.mpl_connect("button_press_event",  self._on_mouse_press)
            self._canvas_mpl.mpl_connect("axes_leave_event",    self._on_axes_leave)
            self._canvas_mpl.mpl_connect("scroll_event",        self._on_scroll_zoom)

            left_host = QWidget()
            left_lay = QVBoxLayout(left_host)
            left_lay.setContentsMargins(0, 0, 0, 0)
            left_lay.setSpacing(4)
            left_lay.addWidget(self._canvas_mpl, stretch=1)

            pan_row = QWidget()
            pan_row_l = QHBoxLayout(pan_row)
            pan_row_l.setContentsMargins(4, 0, 4, 0)
            pan_row_l.setSpacing(6)
            pan_row_l.addWidget(QLabel("Scorri profilo:"))
            self._xpan_slider = QSlider(Qt.Horizontal)
            self._xpan_slider.setRange(0, 1000)
            self._xpan_slider.setValue(0)
            self._xpan_slider.setEnabled(False)
            self._xpan_slider.setToolTip(
                "Slider orizzontale sotto il profilo per navigare profili molto lunghi.\n"
                "Si attiva automaticamente quando lo zoom X e' < 100%."
            )
            self._xpan_slider.valueChanged.connect(self._on_xpan_slider_changed)
            pan_row_l.addWidget(self._xpan_slider, 1)
            self._lbl_xpan = QLabel("Full")
            self._lbl_xpan.setMinimumWidth(90)
            self._lbl_xpan.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            pan_row_l.addWidget(self._lbl_xpan, 0)
            left_lay.addWidget(pan_row, stretch=0)

            splitter = QSplitter(Qt.Vertical)
            splitter.addWidget(slice_host)
            splitter.addWidget(left_host)
            splitter.setStretchFactor(0, 2)
            splitter.setStretchFactor(1, 1)
            center.addWidget(splitter, stretch=4)
            self._im    = None
            self._vline = None
            self._hline = None
            self._wiggle_line = None
        else:
            lbl = QLabel("matplotlib non trovato.\nInstalla: pip install matplotlib")
            lbl.setAlignment(Qt.AlignCenter)
            center.addWidget(lbl, stretch=4)

        self._processing_panel_widget, self._timeslice_panel_widget = self._build_processing_panel()
        self._init_aux_windows()
        self._build_menu()
        root.addLayout(center)

        self._lbl_status = QLabel("Importa un file .ogpr per iniziare.")
        root.addWidget(self._lbl_status)

    def _dock_settings(self):
        st = getattr(self.plugin, "settings", None)
        if st is None:
            st = QSettings()
        return st

    @staticmethod
    def _dock_key(dock_id: str, suffix: str) -> str:
        return f"GeoSurveyStudio/gpr_profile_viewer/docks/{dock_id}/{suffix}"

    def _save_dock_state(self, dock: QDockWidget, dock_id: str):
        if dock is None:
            return
        settings = self._dock_settings()
        try:
            area = int(self.dockWidgetArea(dock))
        except Exception:
            area = int(Qt.RightDockWidgetArea)
        settings.setValue(self._dock_key(dock_id, "area"), area)
        settings.setValue(self._dock_key(dock_id, "floating"), bool(dock.isFloating()))
        settings.setValue(self._dock_key(dock_id, "visible"), bool(dock.isVisible()))
        try:
            settings.setValue(self._dock_key(dock_id, "geometry"), dock.saveGeometry())
        except Exception:
            pass

    def _restore_dock_state(self, dock: QDockWidget, dock_id: str, default_area=Qt.RightDockWidgetArea):
        if dock is None:
            return
        settings = self._dock_settings()
        area_val = settings.value(self._dock_key(dock_id, "area"), int(default_area), type=int)
        try:
            area = Qt.DockWidgetArea(int(area_val))
        except Exception:
            area = default_area
        try:
            self.addDockWidget(area, dock)
        except Exception:
            self.addDockWidget(default_area, dock)
        floating = bool(settings.value(self._dock_key(dock_id, "floating"), False, type=bool))
        try:
            dock.setFloating(floating)
        except Exception:
            pass
        geom = settings.value(self._dock_key(dock_id, "geometry"), None)
        if geom is not None:
            try:
                dock.restoreGeometry(geom)
            except Exception:
                pass
        visible = bool(settings.value(self._dock_key(dock_id, "visible"), True, type=bool))
        dock.setVisible(visible)

    def _create_aux_dock(self, title: str, object_name: str, content: QWidget):
        dock = QDockWidget(title, self)
        dock.setObjectName(object_name)
        dock.setAllowedAreas(
            Qt.LeftDockWidgetArea
            | Qt.RightDockWidgetArea
            | Qt.BottomDockWidgetArea
            | Qt.TopDockWidgetArea
        )
        dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        dock.setWidget(content)
        return dock

    def _init_aux_windows(self):
        created_proc = False
        created_slice = False
        if self._processing_dock is None and self._processing_panel_widget is not None:
            self._processing_dock = self._create_aux_dock(
                "GPR - Processing", "GprProcessingDock", self._processing_panel_widget
            )
            self._restore_dock_state(self._processing_dock, "processing", Qt.RightDockWidgetArea)
            created_proc = True
        if self._timeslice_dock is None and self._timeslice_panel_widget is not None:
            self._timeslice_dock = self._create_aux_dock(
                "GPR - Timeslice", "GprTimesliceDock", self._timeslice_panel_widget
            )
            self._restore_dock_state(self._timeslice_dock, "timeslice", Qt.RightDockWidgetArea)
            created_slice = True

        if (created_proc or created_slice) and self._processing_dock is not None and self._timeslice_dock is not None:
            try:
                self.tabifyDockWidget(self._processing_dock, self._timeslice_dock)
            except Exception:
                pass

    def _open_processing_window(self):
        if self._processing_dock is None:
            self._init_aux_windows()
        if self._processing_dock is None:
            return
        self._processing_dock.show()
        self._processing_dock.raise_()

    def _open_timeslice_window(self):
        if self._timeslice_dock is None:
            self._init_aux_windows()
        if self._timeslice_dock is None:
            return
        self._timeslice_dock.show()
        self._timeslice_dock.raise_()

    def _open_project_manager(self):
        if self.plugin is not None and hasattr(self.plugin, "open_project_manager"):
            try:
                self.plugin.open_project_manager()
                return
            except Exception:
                pass
        QMessageBox.information(
            self,
            "Project Manager",
            "Project Manager non disponibile in questo contesto.",
        )

    def _save_project_file(self):
        try:
            ok = bool(QgsProject.instance().write())
        except Exception:
            ok = False
        if ok:
            self._safe_set_text(self._lbl_status, "Progetto QGIS salvato.")
        else:
            QMessageBox.warning(
                self,
                "Salva progetto",
                "Impossibile salvare il progetto QGIS corrente.",
            )

    def _toggle_wiggle_panel(self, checked: bool):
        try:
            self._chk_show_wiggle.setChecked(bool(checked))
        except Exception:
            self._show_wiggle = bool(checked)
            self._apply_wiggle_visibility_layout()
            self._redraw()

    def _on_real_aspect_toggled(self, checked: bool):
        try:
            if hasattr(self, "_spin_vertical_exag") and self._spin_vertical_exag is not None:
                self._spin_vertical_exag.setEnabled(bool(checked))
        except Exception:
            pass
        self._redraw()

    def _build_menu(self):
        mb = self.menuBar()
        if mb is None:
            return
        try:
            mb.clear()
        except Exception:
            pass

        m_file = mb.addMenu("File")
        act_import = QAction("Importa .ogpr...", self)
        act_import.setShortcut("Ctrl+O")
        act_import.triggered.connect(self.import_files)
        m_file.addAction(act_import)

        act_open_pm = QAction("Apri Project Manager...", self)
        act_open_pm.setShortcut("Ctrl+Shift+O")
        act_open_pm.triggered.connect(self._open_project_manager)
        m_file.addAction(act_open_pm)

        act_save_proj = QAction("Salva progetto", self)
        act_save_proj.setShortcut("Ctrl+S")
        act_save_proj.triggered.connect(self._save_project_file)
        m_file.addAction(act_save_proj)

        m_file.addSeparator()
        act_export = QAction("Export Radargram...", self)
        act_export.triggered.connect(self._export_radargram_image)
        m_file.addAction(act_export)

        act_import_slice = QAction("Import Timeslices to Canvas", self)
        act_import_slice.triggered.connect(self._import_slices_to_canvas)
        m_file.addAction(act_import_slice)

        m_file.addSeparator()
        act_close = QAction("Chiudi", self)
        act_close.setShortcut("Ctrl+W")
        act_close.triggered.connect(self.close)
        m_file.addAction(act_close)

        m_view = mb.addMenu("View")
        if self._processing_dock is not None:
            act_proc = QAction("Processing", self)
            act_proc.setCheckable(True)
            act_proc.setChecked(bool(self._processing_dock.isVisible()))
            act_proc.toggled.connect(self._processing_dock.setVisible)
            self._processing_dock.visibilityChanged.connect(act_proc.setChecked)
            m_view.addAction(act_proc)

        if self._timeslice_dock is not None:
            act_slice = QAction("Timeslice", self)
            act_slice.setCheckable(True)
            act_slice.setChecked(bool(self._timeslice_dock.isVisible()))
            act_slice.toggled.connect(self._timeslice_dock.setVisible)
            self._timeslice_dock.visibilityChanged.connect(act_slice.setChecked)
            m_view.addAction(act_slice)

        act_wiggle = QAction("Wiggle", self)
        act_wiggle.setCheckable(True)
        act_wiggle.setChecked(bool(self._show_wiggle))
        act_wiggle.toggled.connect(self._toggle_wiggle_panel)
        m_view.addAction(act_wiggle)

        m_view.addSeparator()
        act_reset = QAction("Reset Zoom", self)
        act_reset.setShortcut("Ctrl+0")
        act_reset.triggered.connect(self._reset_zoom)
        m_view.addAction(act_reset)

        act_real_scale = QAction("Scala reale", self)
        act_real_scale.setCheckable(True)
        act_real_scale.setChecked(bool(self._chk_real_aspect.isChecked()))
        act_real_scale.toggled.connect(self._chk_real_aspect.setChecked)
        self._chk_real_aspect.toggled.connect(act_real_scale.setChecked)
        m_view.addAction(act_real_scale)

        m_proc = mb.addMenu("Processing")
        act_apply = QAction("Applica pipeline", self)
        act_apply.setShortcut("F5")
        act_apply.triggered.connect(self._apply_processing)
        m_proc.addAction(act_apply)

        act_gain = QAction("Aggiorna gain", self)
        act_gain.setShortcut("F6")
        act_gain.triggered.connect(self._apply_gain_only)
        m_proc.addAction(act_gain)

        act_save_preset = QAction("Salva preset gain/hyperbola...", self)
        act_save_preset.triggered.connect(self._save_gain_hyper_preset)
        m_proc.addAction(act_save_preset)

        act_load_preset = QAction("Carica preset gain/hyperbola...", self)
        act_load_preset.triggered.connect(self._load_gain_hyper_preset)
        m_proc.addAction(act_load_preset)

    def _apply_wiggle_visibility_layout(self):
        if not HAS_MPL or not hasattr(self, "_ax") or not hasattr(self, "_ax_wiggle"):
            return
        if self._ax is None or self._ax_wiggle is None:
            return
        if self._ax_main_bounds_default is None or self._ax_wiggle_bounds_default is None:
            try:
                self._ax_main_bounds_default = list(self._ax.get_position().bounds)
                self._ax_wiggle_bounds_default = list(self._ax_wiggle.get_position().bounds)
            except Exception:
                return

        main = list(self._ax_main_bounds_default)
        wig = list(self._ax_wiggle_bounds_default)
        if bool(getattr(self, "_show_wiggle", True)):
            self._ax_wiggle.set_visible(True)
            self._ax.set_position(main)
            self._ax_wiggle.set_position(wig)
        else:
            self._ax_wiggle.set_visible(False)
            right = float(wig[0] + wig[2])
            self._ax.set_position([float(main[0]), float(main[1]), right - float(main[0]), float(main[3])])

    def _on_toggle_wiggle(self, checked: bool):
        self._show_wiggle = bool(checked)
        self._apply_wiggle_visibility_layout()
        self._redraw()

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
            "Background Removal (GPR-SLICE section Background Removal, pag. 166).\n"
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
        self._spin_bg_sample_start.setValue(10)
        self._spin_bg_sample_start.setEnabled(False)
        self._spin_bg_sample_start.setToolTip(
            "Primo campione incluso nel BG removal (0 = dall'inizio).\n\n"
            "Imposta > 0 per ESCLUDERE i primi N campioni dalla sottrazione.\n"
            "Effetto: preserva il ground coupling / onda diretta nei primi\n"
            "campioni, permettendo all'AGC di amplificarli correttamente.\n"
            "Esempio: per 600 MHz con dt=0.117 ns, 0.5 m ~ 28 campioni."
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
        self._chk_bp.toggled.connect(self._on_bp_controls_changed)
        self._spin_bp_lo.valueChanged.connect(self._on_bp_spin_changed)
        self._spin_bp_hi.valueChanged.connect(self._on_bp_spin_changed)

        # Display
        self._spin_clip = QDoubleSpinBox()
        self._spin_clip.setRange(50.0, 99.9); self._spin_clip.setSingleStep(1.0)
        self._spin_clip.setValue(95.0)
        self._spin_clip.setToolTip("Percentile di clip in normalize_display.")

        self._spin_gain = QDoubleSpinBox()
        self._spin_gain.setRange(0.1, 20.0); self._spin_gain.setSingleStep(0.5)
        self._spin_gain.setValue(2.0)
        self._spin_gain.setToolTip("Moltiplicatore display post-normalize.")

        self._chk_flip_profile = QCheckBox()
        self._chk_flip_profile.setChecked(False)
        self._chk_flip_profile.setToolTip(
            "Inverti orizzontalmente il radargramma corrente (direzione tracce)."
        )
        self._chk_flip_profile.toggled.connect(lambda _v: self._reload_data())

        self._spin_trim_start = QSpinBox()
        self._spin_trim_start.setRange(0, 0)
        self._spin_trim_start.setValue(0)
        self._spin_trim_start.setToolTip("Numero tracce da rimuovere all'inizio del profilo.")
        self._spin_trim_end = QSpinBox()
        self._spin_trim_end.setRange(0, 0)
        self._spin_trim_end.setValue(0)
        self._spin_trim_end.setToolTip("Numero tracce da rimuovere alla fine del profilo.")
        self._btn_apply_trim = QPushButton("Apply Trim")
        self._btn_apply_trim.setToolTip("Applica il ritaglio tracce al profilo corrente.")
        self._btn_apply_trim.clicked.connect(self._reload_data)
        self._spin_trim_start.valueChanged.connect(self._on_trim_traces_changed)
        self._spin_trim_end.valueChanged.connect(self._on_trim_traces_changed)

        self._chk_range_gain = QCheckBox()
        self._chk_range_gain.setChecked(False)
        self._chk_range_gain.setToolTip(
            "Gain in funzione della profondita' per recuperare riflessioni profonde."
        )
        self._chk_range_gain_pre_agc = QCheckBox()
        self._chk_range_gain_pre_agc.setChecked(True)
        self._chk_range_gain_pre_agc.setEnabled(False)
        self._chk_range_gain_pre_agc.setToolTip(
            "Applica la curva di range gain prima dell'AGC.\n"
            "Utile per attenuare l'onda diretta superficiale prima della normalizzazione AGC."
        )
        self._spin_gain_surface = QDoubleSpinBox()
        self._spin_gain_surface.setRange(0.1, 20.0)
        self._spin_gain_surface.setSingleStep(0.1)
        self._spin_gain_surface.setValue(0.3)
        self._spin_gain_surface.setEnabled(False)
        self._spin_gain_deep = QDoubleSpinBox()
        self._spin_gain_deep.setRange(0.1, 60.0)
        self._spin_gain_deep.setSingleStep(0.5)
        self._spin_gain_deep.setValue(6.0)
        self._spin_gain_deep.setEnabled(False)
        self._cb_range_gain_curve = QComboBox()
        self._cb_range_gain_curve.addItem("Linear", "linear")
        self._cb_range_gain_curve.addItem("Power", "power")
        self._cb_range_gain_curve.addItem("Exponential", "exp")
        self._cb_range_gain_curve.addItem("Breakpoints", "breakpoints")
        self._cb_range_gain_curve.setCurrentIndex(1)
        self._cb_range_gain_curve.setEnabled(False)
        self._spin_range_gain_power = QDoubleSpinBox()
        self._spin_range_gain_power.setRange(0.2, 6.0)
        self._spin_range_gain_power.setSingleStep(0.1)
        self._spin_range_gain_power.setValue(1.8)
        self._spin_range_gain_power.setEnabled(False)
        self._btn_range_gain_curve = QPushButton("Edit curve...")
        self._btn_range_gain_curve.setEnabled(False)
        self._btn_range_gain_curve.clicked.connect(self._open_range_gain_curve_editor)

        def _toggle_range_gain(checked):
            mode = str(self._cb_range_gain_curve.currentData() or "power")
            use_breakpoints = checked and mode == "breakpoints"
            self._spin_gain_surface.setEnabled(checked)
            self._spin_gain_deep.setEnabled(checked)
            self._chk_range_gain_pre_agc.setEnabled(checked)
            self._cb_range_gain_curve.setEnabled(checked)
            self._spin_range_gain_power.setEnabled(checked and mode in {"power", "exp"})
            self._btn_range_gain_curve.setEnabled(use_breakpoints)
            self._apply_range_gain_change()

        self._chk_range_gain.toggled.connect(_toggle_range_gain)
        self._chk_range_gain_pre_agc.toggled.connect(lambda _v: self._apply_range_gain_change())
        self._spin_gain_surface.valueChanged.connect(self._on_range_gain_surface_changed)
        self._spin_gain_deep.valueChanged.connect(self._on_range_gain_deep_changed)
        self._spin_range_gain_power.valueChanged.connect(lambda _v: self._apply_range_gain_change())
        self._cb_range_gain_curve.currentIndexChanged.connect(self._on_range_gain_mode_changed)

        self._chk_hyperbola = QCheckBox()
        self._chk_hyperbola.setChecked(False)
        self._chk_hyperbola.setToolTip(
            "Overlay iperbole modello per stima RDP/velocita'."
        )
        self._spin_hyperbola_rdp = QDoubleSpinBox()
        self._spin_hyperbola_rdp.setRange(1.0, 40.0)
        self._spin_hyperbola_rdp.setSingleStep(0.1)
        self._spin_hyperbola_rdp.setValue(9.0)
        self._spin_hyperbola_rdp.setEnabled(False)
        self._lbl_hyperbola_vel = QLabel("v=n/a")
        self._btn_set_hyper_apex = QPushButton("Set apex from cursor")
        self._btn_set_hyper_apex.setEnabled(False)
        self._btn_hyper_auto_fit = QPushButton("Auto-fit RDP")
        self._btn_hyper_auto_fit.setEnabled(False)
        self._btn_clear_hyper = QPushButton("Clear apex")
        self._btn_clear_hyper.setEnabled(False)

        def _toggle_hyperbola_controls(checked):
            self._spin_hyperbola_rdp.setEnabled(checked)
            self._btn_set_hyper_apex.setEnabled(checked)
            self._btn_hyper_auto_fit.setEnabled(checked)
            self._btn_clear_hyper.setEnabled(checked)
            self._redraw()

        self._chk_hyperbola.toggled.connect(_toggle_hyperbola_controls)
        self._spin_hyperbola_rdp.valueChanged.connect(self._on_hyperbola_rdp_changed)
        self._btn_set_hyper_apex.clicked.connect(self._set_hyperbola_apex_from_cursor)
        self._btn_hyper_auto_fit.clicked.connect(self._auto_fit_hyperbola_rdp)
        self._btn_clear_hyper.clicked.connect(self._clear_hyperbola_apex)
        self._on_hyperbola_rdp_changed(self._spin_hyperbola_rdp.value())
        self._btn_save_gain_preset = QPushButton("Save Gain/Hyper Preset")
        self._btn_load_gain_preset = QPushButton("Load Gain/Hyper Preset")
        self._btn_save_gain_preset.clicked.connect(self._save_gain_hyper_preset)
        self._btn_load_gain_preset.clicked.connect(self._load_gain_hyper_preset)

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
        if HAS_MPL:
            self._bp_figure = Figure(figsize=(3.2, 1.8), tight_layout=True)
            self._bp_ax = self._bp_figure.add_subplot(111)
            self._bp_canvas = FigureCanvasQTAgg(self._bp_figure)
            self._bp_canvas.setMinimumHeight(170)
            self._bp_canvas.setToolTip(
                "Bandpass spectrum:\n"
                "click sinistro = low (MHz)\n"
                "click destro = high (MHz)\n"
                "click centrale = handle piu' vicino"
            )
            try:
                self._bp_canvas.mpl_connect("button_press_event", self._on_bp_hist_click)
            except Exception:
                pass
            fl.addRow("  spettro:", self._bp_canvas)
        else:
            self._bp_figure = None
            self._bp_ax = None
            self._bp_canvas = None
        fl.addRow("Trim start traces:",  self._spin_trim_start)
        fl.addRow("Trim end traces:",    self._spin_trim_end)
        fl.addRow("  apply trim:",       self._btn_apply_trim)
        fl.addRow("Flip profile X:",     self._chk_flip_profile)
        fl.addRow("Clip %:",             self._spin_clip)
        fl.addRow("Gain display:",       self._spin_gain)
        fl.addRow("Range gain:",         self._chk_range_gain)
        fl.addRow("  pre-AGC:",          self._chk_range_gain_pre_agc)
        fl.addRow("  gain superficie:",  self._spin_gain_surface)
        fl.addRow("  gain profondo:",    self._spin_gain_deep)
        fl.addRow("  curva:",            self._cb_range_gain_curve)
        fl.addRow("  power:",            self._spin_range_gain_power)
        fl.addRow("  breakpoints:",      self._btn_range_gain_curve)
        fl.addRow("Hyperbola fit:",      self._chk_hyperbola)
        fl.addRow("  RDP:",              self._spin_hyperbola_rdp)
        fl.addRow("  velocity:",         self._lbl_hyperbola_vel)
        fl.addRow("  apex:",             self._btn_set_hyper_apex)
        fl.addRow("  auto-fit:",         self._btn_hyper_auto_fit)
        fl.addRow("  clear apex:",       self._btn_clear_hyper)
        fl.addRow(self._btn_save_gain_preset)
        fl.addRow(self._btn_load_gain_preset)

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

        self._lbl_slice_profiles = QLabel("Nessun profilo caricato")
        self._le_slice_outdir = QLineEdit()
        self._le_slice_outdir.setPlaceholderText("Cartella output GeoTIFF timeslice")
        self._btn_slice_outdir = QPushButton("...")
        self._btn_slice_outdir.setToolTip("Seleziona cartella output per importazione slice.")
        self._btn_slice_outdir.clicked.connect(self._choose_slice_outdir)
        out_row = QWidget()
        out_lay = QHBoxLayout(out_row)
        out_lay.setContentsMargins(0, 0, 0, 0)
        out_lay.setSpacing(4)
        out_lay.addWidget(self._le_slice_outdir, 1)
        out_lay.addWidget(self._btn_slice_outdir, 0)

        self._spin_slice_thickness = QDoubleSpinBox()
        self._spin_slice_thickness.setRange(0.01, 2.0)
        self._spin_slice_thickness.setSingleStep(0.05)
        self._spin_slice_thickness.setDecimals(2)
        self._spin_slice_thickness.setValue(0.10)
        self._spin_slice_thickness.setSuffix(" m")
        self._spin_slice_thickness.setToolTip(
            "Spessore verticale di ogni timeslice (dz)."
        )
        self._chk_slice_thickness_locked = QCheckBox("Blocca spessore")
        self._chk_slice_thickness_locked.setChecked(False)
        self._chk_slice_thickness_locked.setToolTip(
            "Blocca il valore di spessore finche' non vuoi rigenerare le slice."
        )

        def _on_thickness_lock_toggled(locked: bool):
            self._spin_slice_thickness.setEnabled(not bool(locked))

        self._chk_slice_thickness_locked.toggled.connect(_on_thickness_lock_toggled)

        self._cb_slice_cmap = QComboBox()
        self._cb_slice_cmap.addItems(GPR_CMAPS)
        self._cb_slice_cmap.setCurrentText(DEFAULT_CMAP)
        self._cb_slice_cmap.setToolTip("Colormap dedicata alla vista timeslice (indipendente dal radargramma).")
        self._spin_slice_vmin_pct = QDoubleSpinBox()
        self._spin_slice_vmin_pct.setRange(0.0, 49.0)
        self._spin_slice_vmin_pct.setSingleStep(0.5)
        self._spin_slice_vmin_pct.setValue(2.0)
        self._spin_slice_vmin_pct.setToolTip("Percentile minimo per contrasto timeslice.")
        self._spin_slice_vmax_pct = QDoubleSpinBox()
        self._spin_slice_vmax_pct.setRange(51.0, 100.0)
        self._spin_slice_vmax_pct.setSingleStep(0.5)
        self._spin_slice_vmax_pct.setValue(98.0)
        self._spin_slice_vmax_pct.setToolTip("Percentile massimo per contrasto timeslice.")
        self._cb_slice_cmap.currentTextChanged.connect(lambda _v: self._redraw_slice_view(force=True))
        self._spin_slice_vmin_pct.valueChanged.connect(self._on_slice_clip_changed)
        self._spin_slice_vmax_pct.valueChanged.connect(self._on_slice_clip_changed)

        self._cb_slice_preset = QComboBox()
        self._cb_slice_preset.addItem("Base (default)", "base")
        self._cb_slice_preset.addItem("Qualita'", "quality")
        self._cb_slice_preset.addItem("Aggressivo", "aggressive")
        self._cb_slice_preset.setCurrentIndex(0)
        self._cb_slice_preset.setToolTip(
            "Preset rapido parametri timeslice.\n"
            "Base: stabile e veloce.\n"
            "Qualita': migliore continuita' e contrasto.\n"
            "Aggressivo: massimo recupero, piu' smoothing."
        )
        self._btn_slice_preset_apply = QPushButton("Apply preset")
        self._btn_slice_preset_apply.clicked.connect(self._apply_selected_timeslice_preset)

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
        self._chk_slice_use_processing.setChecked(True)
        self._chk_slice_use_processing.setToolTip(
            "Applica dewow/time-zero/bg/agc/bandpass prima della creazione slice.\n"
            "Disattivato = comportamento piu' vicino al LAS."
        )

        self._chk_slice_bg = QCheckBox()
        self._chk_slice_bg.setChecked(True)
        self._chk_slice_bg.setToolTip(
            "Background removal diretto prima dell'estrazione ampiezza.\n"
            "Se Processing pre-slice e' OFF, aiuta a rimuovere banding orizzontale."
        )
        self._cb_slice_bg_mode = QComboBox()
        self._cb_slice_bg_mode.addItem("line_by_line", "line_by_line")
        self._cb_slice_bg_mode.addItem("grid_by_grid", "grid_by_grid")
        self._cb_slice_bg_mode.setCurrentIndex(0)
        self._chk_slice_bg_auto = QCheckBox("Auto")
        self._chk_slice_bg_auto.setChecked(True)
        self._spin_slice_bg_window = QSpinBox()
        self._spin_slice_bg_window.setRange(8, 99000)
        self._spin_slice_bg_window.setValue(200)
        self._spin_slice_bg_window.setEnabled(False)
        self._spin_slice_bg_sample_start = QSpinBox()
        self._spin_slice_bg_sample_start.setRange(0, 9999)
        self._spin_slice_bg_sample_start.setValue(0)
        self._spin_slice_bg_sample_end = QSpinBox()
        self._spin_slice_bg_sample_end.setRange(0, 9999)
        self._spin_slice_bg_sample_end.setValue(0)

        def _toggle_slice_bg_auto(auto_checked):
            self._spin_slice_bg_window.setEnabled(bool(self._chk_slice_bg.isChecked()) and (not auto_checked))

        def _toggle_slice_bg(bg_checked):
            self._cb_slice_bg_mode.setEnabled(bg_checked)
            self._chk_slice_bg_auto.setEnabled(bg_checked)
            self._spin_slice_bg_sample_start.setEnabled(bg_checked)
            self._spin_slice_bg_sample_end.setEnabled(bg_checked)
            _toggle_slice_bg_auto(self._chk_slice_bg_auto.isChecked())

        self._chk_slice_bg.toggled.connect(_toggle_slice_bg)
        self._chk_slice_bg_auto.toggled.connect(_toggle_slice_bg_auto)
        _toggle_slice_bg(self._chk_slice_bg.isChecked())

        self._spin_slice_stack_n = QSpinBox()
        self._spin_slice_stack_n.setRange(1, 31)
        self._spin_slice_stack_n.setValue(1)
        self._spin_slice_stack_n.setToolTip(
            "Numero tracce per stacking laterale (1 = disattivato)."
        )
        self._cb_slice_stack_kernel = QComboBox()
        self._cb_slice_stack_kernel.addItem("Boxcar", "boxcar")
        self._cb_slice_stack_kernel.addItem("Triangolare", "triangular")
        self._cb_slice_stack_kernel.setCurrentIndex(0)

        self._cb_slice_flip_mode = QComboBox()
        self._cb_slice_flip_mode.addItem("None", "none")
        self._cb_slice_flip_mode.addItem("Flip all profiles", "all")
        self._cb_slice_flip_mode.addItem("Flip odd profiles", "odd")
        self._cb_slice_flip_mode.addItem("Flip even profiles", "even")
        self._cb_slice_flip_mode.setCurrentIndex(0)
        self._cb_slice_flip_mode.setToolTip(
            "Inverte la direzione tracce prima del calcolo timeslice.\n"
            "Utile per uniformare profili acquisiti avanti/indietro."
        )

        self._chk_slice_topographic = QCheckBox()
        self._chk_slice_topographic.setChecked(False)
        self._chk_slice_topographic.setToolTip(
            "Correzione topografica: allinea la finestra depth per quota traccia."
        )
        self._cb_slice_topo_ref_mode = QComboBox()
        self._cb_slice_topo_ref_mode.addItem("Median surface", "median")
        self._cb_slice_topo_ref_mode.addItem("Mean surface", "mean")
        self._cb_slice_topo_ref_mode.addItem("Min surface", "min")
        self._cb_slice_topo_ref_mode.addItem("Max surface", "max")
        self._cb_slice_topo_ref_mode.addItem("Custom elevation", "custom")
        self._cb_slice_topo_ref_mode.setCurrentIndex(0)
        self._cb_slice_topo_ref_mode.setEnabled(False)
        self._spin_slice_topo_ref_custom = QDoubleSpinBox()
        self._spin_slice_topo_ref_custom.setDecimals(3)
        self._spin_slice_topo_ref_custom.setRange(-10000.0, 100000.0)
        self._spin_slice_topo_ref_custom.setSingleStep(0.1)
        self._spin_slice_topo_ref_custom.setValue(0.0)
        self._spin_slice_topo_ref_custom.setEnabled(False)

        def _toggle_topo_controls():
            on = bool(self._chk_slice_topographic.isChecked())
            self._cb_slice_topo_ref_mode.setEnabled(on)
            is_custom = str(self._cb_slice_topo_ref_mode.currentData() or "") == "custom"
            self._spin_slice_topo_ref_custom.setEnabled(on and is_custom)

        self._chk_slice_topographic.toggled.connect(lambda _v: _toggle_topo_controls())
        self._cb_slice_topo_ref_mode.currentIndexChanged.connect(lambda _v: _toggle_topo_controls())
        _toggle_topo_controls()

        self._chk_normalize_ch = QCheckBox(); self._chk_normalize_ch.setChecked(False)
        self._chk_normalize_ch.setToolTip("Bilancia ampiezza inter-canale (LAS-like: normalmente OFF).")

        self._chk_amplitude_filter = QCheckBox(); self._chk_amplitude_filter.setChecked(False)
        self._spin_amplitude_sigma = QDoubleSpinBox()
        self._spin_amplitude_sigma.setRange(1.0, 10.0); self._spin_amplitude_sigma.setSingleStep(0.5)
        self._spin_amplitude_sigma.setValue(3.0); self._spin_amplitude_sigma.setEnabled(False)
        self._chk_amplitude_filter.toggled.connect(self._spin_amplitude_sigma.setEnabled)

        self._cb_slice_idw_mode = QComboBox()
        self._cb_slice_idw_mode.addItem("Fast (kNN)", "fast")
        self._cb_slice_idw_mode.addItem("Quality (radius)", "quality")
        self._cb_slice_idw_mode.setCurrentIndex(1)
        self._cb_slice_idw_mode.setToolTip(
            "Fast: molto piu' veloce su griglie grandi (kNN vettorizzato).\n"
            "Quality: ricerca entro raggio per cella (piu' lenta ma piu' fedele localmente)."
        )
        self._chk_slice_use_hilbert = QCheckBox()
        self._chk_slice_use_hilbert.setChecked(True)
        self._chk_slice_use_hilbert.setToolTip(
            "Usa l'envelope Hilbert come ampiezza di default per stabilizzare le timeslice."
        )
        self._spin_slice_overlap_pct = QSpinBox()
        self._spin_slice_overlap_pct.setRange(0, 90)
        self._spin_slice_overlap_pct.setSingleStep(5)
        self._spin_slice_overlap_pct.setValue(50)
        self._spin_slice_overlap_pct.setSuffix(" %")
        self._spin_slice_overlap_pct.setToolTip(
            "Overlap tra slice consecutive. 50% migliora la continuita' verticale."
        )
        self._spin_slice_blanking_m = QDoubleSpinBox()
        self._spin_slice_blanking_m.setRange(0.0, 100.0)
        self._spin_slice_blanking_m.setDecimals(3)
        self._spin_slice_blanking_m.setSingleStep(0.05)
        self._spin_slice_blanking_m.setValue(0.30)
        self._spin_slice_blanking_m.setSuffix(" m")
        self._spin_slice_blanking_m.setToolTip(
            "Celle oltre questa distanza dai dati reali diventano NoData (0=disattiva)."
        )
        self._spin_slice_idw_power = QSpinBox()
        self._spin_slice_idw_power.setRange(1, 4)
        self._spin_slice_idw_power.setValue(2)
        self._spin_slice_idw_power.setToolTip(
            "Esponente potenza IDW (p). 2 standard, 3-4 enfatizza dettagli locali."
        )

        self._chk_anisotropic_idw = QCheckBox(); self._chk_anisotropic_idw.setChecked(False)
        self._chk_auto_radius     = QCheckBox(); self._chk_auto_radius.setChecked(True)
        self._chk_fill_nodata     = QCheckBox(); self._chk_fill_nodata.setChecked(True)
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

        self._chk_slice_parallel_profiles = QCheckBox()
        self._chk_slice_parallel_profiles.setChecked(True)
        self._chk_slice_parallel_profiles.setToolTip(
            "Elabora i profili in parallelo durante la creazione timeslice/volume."
        )
        self._spin_slice_profile_workers = QSpinBox()
        self._spin_slice_profile_workers.setRange(0, max(0, int(os.cpu_count() or 32)))
        self._spin_slice_profile_workers.setValue(0)
        self._spin_slice_profile_workers.setToolTip(
            "Numero worker per profili (0 = automatico)."
        )
        self._spin_slice_profile_workers.setEnabled(True)

        def _toggle_profile_workers(checked):
            self._spin_slice_profile_workers.setEnabled(bool(checked))

        self._chk_slice_parallel_profiles.toggled.connect(_toggle_profile_workers)
        _toggle_profile_workers(self._chk_slice_parallel_profiles.isChecked())

        self._chk_smooth = QCheckBox(); self._chk_smooth.setChecked(True)
        self._spin_smooth_sigma = QDoubleSpinBox()
        self._spin_smooth_sigma.setRange(0.5, 10.0); self._spin_smooth_sigma.setSingleStep(0.1)
        self._spin_smooth_sigma.setValue(0.8); self._spin_smooth_sigma.setEnabled(True)
        self._chk_smooth.toggled.connect(self._spin_smooth_sigma.setEnabled)

        fl_slice.addRow("Profili:",           self._lbl_slice_profiles)
        fl_slice.addRow("Output folder:",     out_row)
        fl_slice.addRow("Spessore slice:",    self._spin_slice_thickness)
        fl_slice.addRow("  lock:",            self._chk_slice_thickness_locked)
        fl_slice.addRow("Preset:",            self._cb_slice_preset)
        fl_slice.addRow("  azione:",          self._btn_slice_preset_apply)
        fl_slice.addRow("Slice colormap:",    self._cb_slice_cmap)
        fl_slice.addRow("Slice clip min %:",  self._spin_slice_vmin_pct)
        fl_slice.addRow("Slice clip max %:",  self._spin_slice_vmax_pct)
        fl_slice.addRow("Estrazione:",         self._cb_slice_extraction)
        fl_slice.addRow("Usa Hilbert:",        self._chk_slice_use_hilbert)
        fl_slice.addRow("Processing pre-slice:", self._chk_slice_use_processing)
        fl_slice.addRow("BG pre-slice:",       self._chk_slice_bg)
        fl_slice.addRow("  modo BG:",          self._cb_slice_bg_mode)
        fl_slice.addRow("  auto BG:",          self._chk_slice_bg_auto)
        fl_slice.addRow("  finestra BG:",      self._spin_slice_bg_window)
        fl_slice.addRow("  BG da campione:",   self._spin_slice_bg_sample_start)
        fl_slice.addRow("  BG a campione:",    self._spin_slice_bg_sample_end)
        fl_slice.addRow("Trace stacking:",     self._spin_slice_stack_n)
        fl_slice.addRow("  kernel:",           self._cb_slice_stack_kernel)
        fl_slice.addRow("Flip pre-slice:",     self._cb_slice_flip_mode)
        fl_slice.addRow("Topographic corr.:",  self._chk_slice_topographic)
        fl_slice.addRow("  topo reference:",   self._cb_slice_topo_ref_mode)
        fl_slice.addRow("  custom elev:",      self._spin_slice_topo_ref_custom)
        fl_slice.addRow("Normalizza canali:",  self._chk_normalize_ch)
        fl_slice.addRow("Filtro ampiezza:",    self._chk_amplitude_filter)
        fl_slice.addRow("  sigma:",            self._spin_amplitude_sigma)
        fl_slice.addRow("IDW mode:",           self._cb_slice_idw_mode)
        fl_slice.addRow("Potenza IDW (p):",    self._spin_slice_idw_power)
        fl_slice.addRow("Overlap slice:",      self._spin_slice_overlap_pct)
        fl_slice.addRow("Blanking (m):",       self._spin_slice_blanking_m)
        fl_slice.addRow("IDW anisotropo:",     self._chk_anisotropic_idw)
        fl_slice.addRow("  raggio auto:",      self._chk_auto_radius)
        fl_slice.addRow("Bilancia profili:",   self._chk_slice_balance_profiles)
        fl_slice.addRow("Raggio vs profondita':", self._spin_slice_depth_radius_factor)
        fl_slice.addRow("  min points:",       self._spin_slice_min_points)
        fl_slice.addRow("Profili paralleli:",  self._chk_slice_parallel_profiles)
        fl_slice.addRow("  workers (0=auto):", self._spin_slice_profile_workers)
        fl_slice.addRow("Fill NoData:",        self._chk_fill_nodata)
        fl_slice.addRow("Smooth gaussiano:",   self._chk_smooth)
        fl_slice.addRow("  sigma:",            self._spin_smooth_sigma)

        btn_slice = QPushButton("Crea Timeslice...")
        btn_slice.clicked.connect(self._open_slice_dialog)
        fl_slice.addRow(btn_slice)

        self._lbl_slice_nav = QLabel("\u2014 nessuna slice \u2014")
        self._lbl_slice_nav.setAlignment(Qt.AlignCenter)
        self._lbl_slice_depth_top = QLabel("0.00 m")
        self._lbl_slice_depth_bottom = QLabel("\u2014")
        self._lbl_slice_depth_top.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._lbl_slice_depth_bottom.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        depth_row = QWidget()
        depth_layout = QHBoxLayout(depth_row)
        depth_layout.setContentsMargins(0, 0, 0, 0)
        depth_layout.setSpacing(8)
        depth_layout.addWidget(self._lbl_slice_depth_top)
        depth_layout.addStretch(1)
        depth_layout.addWidget(self._lbl_slice_depth_bottom)

        btn_slice_up = QPushButton("\u25b2 Slice su")
        btn_slice_up.clicked.connect(lambda: self._step_slice(-1))
        btn_slice_down = QPushButton("\u25bc Slice giu'")
        btn_slice_down.clicked.connect(lambda: self._step_slice(+1))
        nav_btn_row = QWidget()
        nav_btn_layout = QHBoxLayout(nav_btn_row)
        nav_btn_layout.setContentsMargins(0, 0, 0, 0)
        nav_btn_layout.setSpacing(4)
        nav_btn_layout.addWidget(btn_slice_up)
        nav_btn_layout.addWidget(btn_slice_down)

        fl_slice.addRow("Navigator:", self._lbl_slice_nav)
        fl_slice.addRow(depth_row)
        fl_slice.addRow(nav_btn_row)

        btn_slice_import = QPushButton("Import to Canvas")
        btn_slice_import.setToolTip(
            "Importa i GeoTIFF timeslice dalla cartella output nel gruppo 'GPR Timeslices'."
        )
        btn_slice_import.clicked.connect(self._import_slices_to_canvas)
        fl_slice.addRow(btn_slice_import)

        btn_view3d = QPushButton("Apri Viewer 3D...")
        btn_view3d.setToolTip(
            "Costruisce un volume 3D dalle timeslice interpolate e apre il viewer PyVista."
        )
        btn_view3d.clicked.connect(self._open_3d_viewer)
        fl_slice.addRow(btn_view3d)

        self._chk_timeslice_sync = QCheckBox()
        self._chk_timeslice_sync.setChecked(False)
        self._chk_timeslice_sync.setToolTip(
            "Sincronizza la timeslice visibile nel canvas QGIS con la profondita' "
            "del cursore sul radargramma e mostra un crosshair rosso X/Y."
        )
        self._spin_crosshair_size = QDoubleSpinBox()
        self._spin_crosshair_size.setRange(0.5, 100.0)
        self._spin_crosshair_size.setSingleStep(0.5)
        self._spin_crosshair_size.setValue(5.0)
        self._spin_crosshair_size.setEnabled(False)
        self._spin_crosshair_size.setToolTip("Dimensione del crosshair (metri, semi-lunghezza).")

        def _on_toggle_timeslice_sync(checked):
            self._spin_crosshair_size.setEnabled(bool(checked))
            if checked:
                self._canvas_timer.start()
            else:
                self._clear_timeslice_crosshair()

        self._chk_timeslice_sync.toggled.connect(_on_toggle_timeslice_sync)
        self._spin_crosshair_size.valueChanged.connect(
            lambda v: setattr(self, "_crosshair_size_m", float(v))
        )

        fl_slice.addRow("Sync timeslice canvas:", self._chk_timeslice_sync)
        fl_slice.addRow("  crosshair size (m):", self._spin_crosshair_size)

        proc_content = QWidget()
        proc_layout = QVBoxLayout(proc_content)
        proc_layout.setContentsMargins(0, 0, 0, 0)
        proc_layout.addWidget(grp)
        proc_layout.addStretch()

        processing_scroll = QScrollArea()
        processing_scroll.setWidgetResizable(True)
        processing_scroll.setWidget(proc_content)
        processing_scroll.setMinimumWidth(250)
        processing_scroll.setMaximumWidth(420)

        slice_content = QWidget()
        slice_layout = QVBoxLayout(slice_content)
        slice_layout.setContentsMargins(0, 0, 0, 0)
        slice_layout.addWidget(grp_slice)
        slice_layout.addStretch()

        timeslice_scroll = QScrollArea()
        timeslice_scroll.setWidgetResizable(True)
        timeslice_scroll.setWidget(slice_content)
        timeslice_scroll.setMinimumWidth(270)
        timeslice_scroll.setMaximumWidth(460)
        try:
            default_out = self._default_slice_output_dir()
            if default_out:
                self._le_slice_outdir.setText(default_out)
        except Exception:
            pass
        self._update_slice_profile_info()
        try:
            dz0 = float(self._spin_slice_thickness.value())
        except Exception:
            dz0 = 0.10
        self._refresh_slice_catalog(
            str(self._le_slice_outdir.text() or "").strip(),
            dz=dz0,
            reset_index=True,
            redraw=False,
        )
        return processing_scroll, timeslice_scroll

    def _current_dt_ns(self) -> float:
        if not self._profiles:
            return 0.117
        try:
            dt_ns = float(getattr(self._profiles[self._prof_idx], "dt_ns", 0.117) or 0.117)
        except Exception:
            dt_ns = 0.117
        if not np.isfinite(dt_ns) or dt_ns <= 0.0:
            dt_ns = 0.117
        return dt_ns

    def _current_bp_source_data(self) -> Optional[np.ndarray]:
        src = self._raw_data if self._raw_data is not None else self._proc_data
        if src is None:
            return None
        arr = np.asarray(src, dtype=np.float64)
        if arr.ndim != 2 or arr.size <= 0:
            return None
        if not np.isfinite(arr).any():
            return None
        return arr

    def _bandpass_limits(self) -> tuple[float, float]:
        lo = float(self._spin_bp_lo.value())
        hi = float(self._spin_bp_hi.value())
        if not np.isfinite(lo):
            lo = 1.0
        if not np.isfinite(hi):
            hi = lo + 1.0
        if hi <= lo:
            hi = lo + 1.0
        return lo, hi

    def _refresh_bp_histogram(self):
        if (not HAS_MPL) or self._bp_ax is None or self._bp_canvas is None:
            return
        ax = self._bp_ax
        ax.clear()

        arr = self._current_bp_source_data()
        if arr is None:
            ax.text(0.5, 0.5, "Nessun dato", ha="center", va="center", transform=ax.transAxes)
            ax.set_xticks([])
            ax.set_yticks([])
            self._safe_draw_idle(self._bp_canvas)
            return

        n_s, n_t = int(arr.shape[0]), int(arr.shape[1])
        if n_s <= 4 or n_t <= 0:
            ax.text(0.5, 0.5, "Dati insufficienti", ha="center", va="center", transform=ax.transAxes)
            ax.set_xticks([])
            ax.set_yticks([])
            self._safe_draw_idle(self._bp_canvas)
            return

        dt_s = self._current_dt_ns() * 1e-9
        if dt_s <= 0:
            dt_s = 0.117e-9

        # Limit trace count for responsiveness on long profiles.
        if n_t > 64:
            idx = np.linspace(0, n_t - 1, 64, dtype=np.int64)
            arr_fft = arr[:, idx]
        else:
            arr_fft = arr

        freqs_mhz = np.fft.rfftfreq(n_s, d=dt_s) / 1e6
        spec = np.mean(np.abs(np.fft.rfft(arr_fft, axis=0)), axis=1)
        if not np.isfinite(spec).any():
            spec = np.zeros_like(freqs_mhz)
        else:
            spec = np.nan_to_num(spec, nan=0.0, posinf=0.0, neginf=0.0)

        max_mhz = float(self._spin_bp_hi.maximum())
        finite_f = np.isfinite(freqs_mhz)
        valid = finite_f & (freqs_mhz >= 0.0) & (freqs_mhz <= max_mhz)
        if not valid.any():
            ax.text(0.5, 0.5, "Spettro non disponibile", ha="center", va="center", transform=ax.transAxes)
            ax.set_xticks([])
            ax.set_yticks([])
            self._safe_draw_idle(self._bp_canvas)
            return

        fx = freqs_mhz[valid]
        sy = spec[valid]
        ax.plot(fx, sy, color="#3d7ab5", lw=1.0)
        ax.fill_between(fx, sy, color="#3d7ab5", alpha=0.18)

        lo, hi = self._bandpass_limits()
        is_on = bool(self._chk_bp.isChecked())
        c_lo = "#e74c3c" if is_on else "#9aa0a6"
        c_hi = "#e74c3c" if is_on else "#9aa0a6"
        c_band = "#f39c12" if is_on else "#c8ccd2"
        ax.axvline(lo, color=c_lo, lw=1.4, ls="--")
        ax.axvline(hi, color=c_hi, lw=1.4, ls="--")
        ax.axvspan(lo, hi, color=c_band, alpha=0.12)

        ax.set_xlim(float(np.nanmin(fx)), float(np.nanmax(fx)))
        ax.set_xlabel("Freq (MHz)")
        ax.set_ylabel("Amp")
        ax.set_title("Bandpass")
        ax.grid(True, ls=":", lw=0.4, alpha=0.6)
        try:
            self._safe_draw_idle(self._bp_canvas)
        except Exception:
            pass

    def _on_bp_controls_changed(self, _checked: bool):
        self._refresh_bp_histogram()
        if self._raw_data is not None:
            self._apply_processing()

    def _on_bp_spin_changed(self, _value: float):
        lo = float(self._spin_bp_lo.value())
        hi = float(self._spin_bp_hi.value())
        if hi <= lo:
            if self.sender() is self._spin_bp_lo:
                hi = lo + 1.0
                self._spin_bp_hi.blockSignals(True)
                try:
                    self._spin_bp_hi.setValue(hi)
                finally:
                    self._spin_bp_hi.blockSignals(False)
            else:
                lo = hi - 1.0
                self._spin_bp_lo.blockSignals(True)
                try:
                    self._spin_bp_lo.setValue(max(float(self._spin_bp_lo.minimum()), lo))
                finally:
                    self._spin_bp_lo.blockSignals(False)
        self._refresh_bp_histogram()
        if self._raw_data is not None and bool(self._chk_bp.isChecked()):
            self._apply_processing()

    def _on_bp_hist_click(self, event):
        if event is None or event.xdata is None:
            return
        if self._bp_ax is None or event.inaxes != self._bp_ax:
            return

        freq = float(event.xdata)
        fmin = float(self._spin_bp_lo.minimum())
        fmax = float(self._spin_bp_hi.maximum())
        freq = float(np.clip(freq, fmin, fmax))
        lo, hi = self._bandpass_limits()
        btn = int(getattr(event, "button", 0) or 0)

        if btn == 1:
            lo = min(freq, hi - 1.0)
        elif btn == 3:
            hi = max(freq, lo + 1.0)
        elif btn == 2:
            if abs(freq - lo) <= abs(freq - hi):
                lo = min(freq, hi - 1.0)
            else:
                hi = max(freq, lo + 1.0)
        else:
            return

        self._spin_bp_lo.blockSignals(True)
        self._spin_bp_hi.blockSignals(True)
        try:
            self._spin_bp_lo.setValue(float(np.clip(lo, fmin, fmax)))
            self._spin_bp_hi.setValue(float(np.clip(hi, fmin, fmax)))
        finally:
            self._spin_bp_lo.blockSignals(False)
            self._spin_bp_hi.blockSignals(False)

        self._refresh_bp_histogram()
        if self._raw_data is not None:
            self._apply_processing()

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
        # Nuova sessione import: reset completo stato+UI per evitare riferimenti stale a widget Qt.
        self._reset_session_state()
        errors = []
        imported_warnings = []
        for p in paths:
            md5_failed = False
            try:
                prof = read_ogpr_cached(p, verify_md5=True)
                self._profiles.append(prof)
            except Exception as e:
                msg = str(e)
                if "MD5" in msg:
                    try:
                        prof = read_ogpr_cached(p, verify_md5=False)
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
        self._update_slice_profile_info()
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

    def keyPressEvent(self, event):
        key = int(event.key()) if event is not None else 0
        if key == int(Qt.Key_Up):
            self._step_slice(-1)
            event.accept()
            return
        if key == int(Qt.Key_Down):
            self._step_slice(+1)
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_channel_changed(self, idx):
        if idx >= 0:
            self._ch_idx = idx
            self._reload_data()

    def _update_trim_controls_for_channel(self, n_traces: int):
        n_t = max(0, int(n_traces))
        max_trim = max(0, n_t - 1)
        cur_start = int(np.clip(self._spin_trim_start.value(), 0, max_trim))
        cur_end = int(np.clip(self._spin_trim_end.value(), 0, max_trim))
        if cur_start + cur_end > max_trim:
            cur_end = max(0, max_trim - cur_start)
        self._spin_trim_start.blockSignals(True)
        self._spin_trim_end.blockSignals(True)
        try:
            self._spin_trim_start.setRange(0, max_trim)
            self._spin_trim_end.setRange(0, max_trim)
            self._spin_trim_start.setValue(cur_start)
            self._spin_trim_end.setValue(cur_end)
        finally:
            self._spin_trim_start.blockSignals(False)
            self._spin_trim_end.blockSignals(False)
        self._trim_start_traces = int(cur_start)
        self._trim_end_traces = int(cur_end)

    def _on_trim_traces_changed(self, _value):
        if not self._profiles:
            return
        start = int(self._spin_trim_start.value())
        end = int(self._spin_trim_end.value())
        max_trim = max(0, int(self._spin_trim_start.maximum()))
        if start + end > max_trim:
            if self.sender() is self._spin_trim_start:
                end = max(0, max_trim - start)
                self._spin_trim_end.blockSignals(True)
                try:
                    self._spin_trim_end.setValue(end)
                finally:
                    self._spin_trim_end.blockSignals(False)
            else:
                start = max(0, max_trim - end)
                self._spin_trim_start.blockSignals(True)
                try:
                    self._spin_trim_start.setValue(start)
                finally:
                    self._spin_trim_start.blockSignals(False)
        self._trim_start_traces = int(start)
        self._trim_end_traces = int(end)
        self._reload_data()

    def _load_current_profile(self):
        if not self._profiles:
            self._update_slice_profile_info()
            return
        prof = self._profiles[self._prof_idx]
        # Reset zoom when changing profile to avoid carrying tiny previous windows.
        self._view_xlim = None
        self._view_ylim = None
        self._profile_view_initialized = False
        self._update_slice_profile_info()
        self._safe_set_text(
            self._lbl_profile,
            f"{self._prof_idx + 1}/{len(self._profiles)}  "
            f"{os.path.basename(prof.path)}"
        )
        if not self._is_qt_alive(self._cb_channel):
            return
        self._cb_channel.blockSignals(True)
        self._safe_cb_clear(self._cb_channel)
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
        try:
            self._cb_channel.blockSignals(False)
        except Exception:
            pass
        try:
            self._update_trim_controls_for_channel(int(prof.channel(self._ch_idx).data.shape[1]))
        except Exception:
            self._update_trim_controls_for_channel(0)
        try:
            v_prof = self._profile_reference_velocity(prof)
            rdp_prof = float(np.clip(self._velocity_to_rdp(v_prof), 1.0, 40.0))
            self._spin_hyperbola_rdp.setValue(rdp_prof)
        except Exception:
            pass
        self._reload_data()
        if self._ch_idx != 0:
            self._safe_set_text(
                self._lbl_status,
                f"Canale auto-selezionato: Ch {self._ch_idx} (segnale migliore)."
            )
        self._draw_profile_line_on_canvas()

    def _reload_data(self):
        if not self._profiles:
            return
        if not self._is_qt_alive(self):
            return
        for w in (
            getattr(self, "_spin_trim_start", None),
            getattr(self, "_spin_trim_end", None),
            getattr(self, "_chk_flip_profile", None),
        ):
            if not self._is_qt_alive(w):
                return
        prof = self._profiles[self._prof_idx]
        ch   = prof.channel(self._ch_idx)
        full = ch.data.copy()
        n_t = int(full.shape[1]) if full.ndim == 2 else 0
        self._update_trim_controls_for_channel(n_t)
        start = int(np.clip(self._trim_start_traces, 0, max(0, n_t - 1)))
        end_trim = int(np.clip(self._trim_end_traces, 0, max(0, n_t - 1)))
        end = max(start + 1, n_t - end_trim)
        end = min(end, n_t)
        idx = np.arange(start, end, dtype=np.int64)
        if bool(getattr(self, "_chk_flip_profile", None) and self._chk_flip_profile.isChecked()):
            idx = idx[::-1]
        if idx.size <= 0:
            idx = np.arange(0, min(1, n_t), dtype=np.int64)
        if full.ndim == 2 and idx.size > 0:
            self._raw_data = full[:, idx].copy()
        else:
            self._raw_data = full.copy()
        self._trace_source_indices = idx.astype(np.int64, copy=False)
        self._proc_data = None
        self._hyper_apex_x = None
        self._hyper_apex_depth = None
        self._apply_processing()

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def _current_processing_params(self) -> dict:
        """Return current processing controls as pipeline params."""
        bg_auto = bool(self._chk_bg_auto.isChecked())
        bg_window = 0 if bg_auto else int(self._spin_bg_window.value())
        bp_lo, bp_hi = self._bandpass_limits()
        rg_points = self._sanitize_range_gain_breakpoints(self._range_gain_breakpoints)
        return {
            "dewow": bool(self._chk_dewow.isChecked()),
            "dewow_win": int(self._spin_dewow.value()),
            "timezero": bool(self._chk_timezero.isChecked()),
            "tz_method": str(self._cb_tz_method.currentText()),
            "tz_mode": str(self._cb_tz_mode.currentText()),
            "tz_threshold": float(self._spin_tz_threshold.value()),
            "tz_backup_nsamp": int(self._spin_tz_backup.value()),
            "bg_removal": bool(self._chk_bg.isChecked()),
            "bg_mode": str(self._cb_bg_mode.currentData() or "line_by_line"),
            "bg_window": int(bg_window),
            "bg_sample_start": int(self._spin_bg_sample_start.value()),
            "bg_sample_end": int(self._spin_bg_sample_end.value()),
            "agc": bool(self._chk_agc.isChecked()),
            "agc_win": int(self._spin_agc.value()),
            "pre_agc_gain": bool(
                self._chk_range_gain.isChecked()
                and hasattr(self, "_chk_range_gain_pre_agc")
                and self._chk_range_gain_pre_agc.isChecked()
            ),
            "pre_agc_surface_gain": float(self._spin_gain_surface.value()),
            "pre_agc_deep_gain": float(self._spin_gain_deep.value()),
            "pre_agc_curve": str(self._cb_range_gain_curve.currentData() or "power"),
            "pre_agc_power": float(self._spin_range_gain_power.value()),
            "pre_agc_breakpoints": [[float(x), float(y)] for x, y in rg_points.tolist()],
            "bandpass": bool(self._chk_bp.isChecked()),
            "bp_low_mhz": float(bp_lo),
            "bp_high_mhz": float(bp_hi),
            "clip_pct": float(self._spin_clip.value()),
        }

    def _apply_processing(self):
        if self._raw_data is None:
            return
        if not self._is_qt_alive(self):
            return
        for w in (
            getattr(self, "_chk_dewow", None),
            getattr(self, "_spin_dewow", None),
            getattr(self, "_chk_timezero", None),
            getattr(self, "_chk_bg", None),
            getattr(self, "_chk_agc", None),
            getattr(self, "_spin_agc", None),
        ):
            if not self._is_qt_alive(w):
                return
        params = self._current_processing_params()
        self._last_pipeline_params = dict(params)
        prof = self._profiles[self._prof_idx]
        try:
            self._proc_data = apply_pipeline(
                self._raw_data, params, dt_ns=prof.dt_ns, normalize_output=False
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
                self._proc_data = np.asarray(self._raw_data, dtype=np.float32)
                self._safe_set_text(
                    self._lbl_status,
                    "Processing inconcludente: visualizzazione fallback su dato grezzo."
                )
            except Exception:
                pass

        self._refresh_bp_histogram()
        self._apply_gain_only()

    def _sanitize_range_gain_breakpoints(self, points):
        arr = np.asarray(points if points is not None else [], dtype=np.float64).reshape(-1, 2)
        rows = []
        for x, y in arr:
            if np.isfinite(x) and np.isfinite(y):
                rows.append((float(np.clip(x, 0.0, 1.0)), float(np.clip(y, 0.1, 80.0))))
        if not rows:
            rows = [(0.0, float(self._spin_gain_surface.value())), (1.0, float(self._spin_gain_deep.value()))]
        rows.sort(key=lambda p: p[0])
        out = [(0.0, float(rows[0][1]))]
        for x, y in rows[1:-1]:
            if x <= 0.0 or x >= 1.0:
                continue
            if abs(x - out[-1][0]) < 1e-6:
                continue
            out.append((x, y))
        out.append((1.0, float(rows[-1][1])))
        max_pts = 16
        if len(out) > max_pts:
            inner = out[1:-1]
            take = max(0, max_pts - 2)
            idx = np.linspace(0, len(inner) - 1, take, dtype=np.int64) if take > 0 else np.zeros(0, dtype=np.int64)
            inner = [inner[i] for i in idx] if take > 0 else []
            out = [out[0]] + inner + [out[-1]]
        return np.asarray(out, dtype=np.float64)

    def _sync_range_gain_spins_from_breakpoints(self):
        if self._range_gain_breakpoints is None or self._range_gain_breakpoints.shape[0] < 2:
            return
        self._updating_range_gain_controls = True
        try:
            self._spin_gain_surface.setValue(float(self._range_gain_breakpoints[0, 1]))
            self._spin_gain_deep.setValue(float(self._range_gain_breakpoints[-1, 1]))
        finally:
            self._updating_range_gain_controls = False

    def _sync_range_gain_breakpoints_from_spins(self):
        if self._range_gain_breakpoints is None or self._range_gain_breakpoints.shape[0] < 2:
            self._range_gain_breakpoints = np.asarray(
                [
                    (0.0, float(self._spin_gain_surface.value())),
                    (1.0, float(self._spin_gain_deep.value())),
                ],
                dtype=np.float64,
            )
        else:
            self._range_gain_breakpoints[0, 0] = 0.0
            self._range_gain_breakpoints[-1, 0] = 1.0
            self._range_gain_breakpoints[0, 1] = float(self._spin_gain_surface.value())
            self._range_gain_breakpoints[-1, 1] = float(self._spin_gain_deep.value())
            self._range_gain_breakpoints = self._sanitize_range_gain_breakpoints(self._range_gain_breakpoints)

    def _range_gain_pre_agc_enabled(self) -> bool:
        return bool(
            getattr(self, "_chk_range_gain", None)
            and self._chk_range_gain.isChecked()
            and getattr(self, "_chk_range_gain_pre_agc", None)
            and self._chk_range_gain_pre_agc.isChecked()
        )

    def _apply_range_gain_change(self):
        if self._range_gain_pre_agc_enabled():
            self._apply_processing()
        else:
            self._apply_gain_only()

    def _on_range_gain_surface_changed(self, _value: float):
        if self._updating_range_gain_controls:
            return
        self._sync_range_gain_breakpoints_from_spins()
        self._apply_range_gain_change()

    def _on_range_gain_deep_changed(self, _value: float):
        if self._updating_range_gain_controls:
            return
        self._sync_range_gain_breakpoints_from_spins()
        self._apply_range_gain_change()

    def _on_range_gain_mode_changed(self, _idx: int):
        checked = bool(self._chk_range_gain.isChecked())
        mode = str(self._cb_range_gain_curve.currentData() or "power")
        self._spin_range_gain_power.setEnabled(checked and mode in {"power", "exp"})
        self._btn_range_gain_curve.setEnabled(checked and mode == "breakpoints")
        self._apply_range_gain_change()

    def _on_range_gain_curve_live_changed(self, points):
        self._range_gain_breakpoints = self._sanitize_range_gain_breakpoints(points)
        self._sync_range_gain_spins_from_breakpoints()
        if self._chk_range_gain.isChecked():
            self._apply_range_gain_change()

    def _open_range_gain_curve_editor(self):
        if not HAS_MPL:
            QMessageBox.information(self, "Range Gain", "Editor curva non disponibile (matplotlib mancante).")
            return
        original = np.asarray(self._range_gain_breakpoints, dtype=np.float64).copy()
        dlg = _RangeGainCurveDialog(
            points=original,
            max_points=16,
            on_curve_changed=self._on_range_gain_curve_live_changed,
            parent=self,
        )
        if dlg.exec_() == QDialog.Accepted:
            self._range_gain_breakpoints = self._sanitize_range_gain_breakpoints(dlg.points())
            self._sync_range_gain_spins_from_breakpoints()
            if self._chk_range_gain.isChecked():
                self._apply_range_gain_change()
            return
        # Revert previewed edits on cancel.
        self._range_gain_breakpoints = self._sanitize_range_gain_breakpoints(original)
        self._sync_range_gain_spins_from_breakpoints()
        if self._chk_range_gain.isChecked():
            self._apply_range_gain_change()

    @staticmethod
    def _rdp_to_velocity_m_s(rdp: float) -> float:
        c = 299792458.0
        rr = max(float(rdp), 1e-6)
        return float(c / np.sqrt(rr))

    @staticmethod
    def _velocity_to_rdp(v_m_s: float) -> float:
        c = 299792458.0
        v = max(float(v_m_s), 1e-9)
        return float((c / v) ** 2)

    def _profile_reference_velocity(self, prof) -> float:
        try:
            v = float(getattr(prof, "velocity_m_s", 0.0) or 0.0)
        except Exception:
            v = 0.0
        if not np.isfinite(v) or v <= 0:
            # Default medium equivalent ~RDP 9.
            v = self._rdp_to_velocity_m_s(9.0)
        return float(v)

    def _on_hyperbola_rdp_changed(self, value: float):
        v = self._rdp_to_velocity_m_s(float(value))
        self._safe_set_text(self._lbl_hyperbola_vel, f"v={v:.3e} m/s")
        if self._chk_hyperbola.isChecked():
            self._redraw()

    def _set_hyperbola_apex_from_cursor(self):
        if self._cursor_x is None or self._cursor_z is None:
            QMessageBox.information(self, "Hyperbola Fit", "Muovi il cursore sul radargramma e riprova.")
            return
        self._hyper_apex_x = float(self._cursor_x)
        self._hyper_apex_depth = float(self._cursor_z)
        if self._chk_hyperbola.isChecked():
            self._redraw()

    def _clear_hyperbola_apex(self):
        self._hyper_apex_x = None
        self._hyper_apex_depth = None
        if self._chk_hyperbola.isChecked():
            self._redraw()

    def _channel_trace_vector(self, values, n_t: int) -> np.ndarray:
        target = max(0, int(n_t))
        if target <= 0:
            return np.zeros(0, dtype=np.float64)
        arr = np.asarray(values if values is not None else [], dtype=np.float64)
        idx = self._trace_source_indices
        if (
            isinstance(idx, np.ndarray)
            and idx.ndim == 1
            and idx.size == target
            and arr.size > 0
        ):
            try:
                imin = int(np.nanmin(idx))
                imax = int(np.nanmax(idx))
            except Exception:
                imin, imax = 0, -1
            if imin >= 0 and imax < arr.size:
                try:
                    return np.asarray(arr[idx.astype(np.int64)], dtype=np.float64)
                except Exception:
                    pass
        if arr.size == target:
            return arr.astype(np.float64, copy=False)
        if arr.size <= 0:
            return np.full(target, np.nan, dtype=np.float64)
        if arr.size == 1:
            return np.full(target, float(arr[0]), dtype=np.float64)
        src = np.linspace(0.0, 1.0, arr.size, dtype=np.float64)
        dst = np.linspace(0.0, 1.0, target, dtype=np.float64)
        return np.interp(dst, src, arr).astype(np.float64)

    def _display_distance_axis(self, prof, n_traces: int) -> np.ndarray:
        n_t = max(1, int(n_traces))
        try:
            ch = prof.channel(self._ch_idx)
            distances = self._channel_trace_vector(getattr(ch, "distances", []), n_t)
        except Exception:
            distances = np.zeros(0, dtype=np.float64)
        if distances.size != n_t:
            if distances.size > 1:
                src = np.linspace(0.0, 1.0, distances.size, dtype=np.float64)
                dst = np.linspace(0.0, 1.0, n_t, dtype=np.float64)
                distances = np.interp(dst, src, distances).astype(np.float64)
            else:
                step = float(getattr(prof, "sampling_step_m", 0.1) or 0.1)
                step = max(step, 1e-6)
                distances = (np.arange(n_t, dtype=np.float64) * step).astype(np.float64)
        finite = np.isfinite(distances)
        if not finite.any():
            return np.linspace(0.0, float(max(1, n_t - 1)), n_t, dtype=np.float64)
        if not finite.all():
            idx = np.arange(distances.size, dtype=np.float64)
            ok = np.where(finite)[0].astype(np.float64)
            distances = np.interp(idx, ok, distances[finite]).astype(np.float64)
        d0 = float(distances[0]) if np.isfinite(distances[0]) else 0.0
        distances = distances - d0
        if distances.size > 1:
            # Enforce monotonic axis for noisy/raw cumulative distance vectors.
            distances = np.maximum.accumulate(distances)
        span = float(distances[-1] - distances[0]) if distances.size > 1 else 0.0
        if (not np.isfinite(span)) or span <= 1e-9:
            step = float(getattr(prof, "sampling_step_m", 0.1) or 0.1)
            step = max(step, 1e-6)
            distances = (np.arange(n_t, dtype=np.float64) * step).astype(np.float64)
        return distances

    def _auto_fit_hyperbola_rdp(self):
        if self._disp_data is None or not self._profiles:
            QMessageBox.information(self, "Hyperbola Fit", "Nessun profilo visualizzato.")
            return
        if self._hyper_apex_x is None or self._hyper_apex_depth is None:
            if self._cursor_x is not None and self._cursor_z is not None:
                self._set_hyperbola_apex_from_cursor()
            else:
                QMessageBox.information(
                    self,
                    "Hyperbola Fit",
                    "Imposta prima l'apice (Set apex from cursor o doppio click).",
                )
                return
        prof = self._profiles[self._prof_idx]
        arr = np.asarray(self._disp_data, dtype=np.float64)
        if arr.ndim != 2 or arr.size == 0:
            QMessageBox.information(self, "Hyperbola Fit", "Dati radargramma non disponibili.")
            return
        n_s, n_t = arr.shape
        depth_max = self._depth_max_display(prof)
        if not np.isfinite(depth_max) or depth_max <= 0:
            QMessageBox.information(self, "Hyperbola Fit", "Scala profondita' non valida.")
            return

        x0 = float(self._hyper_apex_x)
        z0 = float(self._hyper_apex_depth)
        if not (np.isfinite(x0) and np.isfinite(z0) and z0 >= 0.0):
            QMessageBox.information(self, "Hyperbola Fit", "Apice non valido.")
            return

        distances = self._display_distance_axis(prof, n_t)
        v_ref = self._profile_reference_velocity(prof)
        t0 = (2.0 * z0) / max(v_ref, 1e-9)
        if not np.isfinite(t0) or t0 < 0:
            QMessageBox.information(self, "Hyperbola Fit", "Tempo apice non valido.")
            return

        abs_arr = np.abs(arr)
        if not np.isfinite(abs_arr).any():
            QMessageBox.information(self, "Hyperbola Fit", "Segnale non valido per il fit.")
            return

        # Focus near apex: robust span around current profile.
        dist_span = float(np.nanmax(distances) - np.nanmin(distances)) if distances.size > 1 else 0.0
        focus_half = max(1.5, 0.30 * max(dist_span, 1.0))
        near = np.abs(distances - x0) <= focus_half
        if int(np.count_nonzero(near)) < 8:
            near = np.ones_like(distances, dtype=bool)

        corridor = max(1, int(round(0.006 * n_s)))
        candidates = np.linspace(1.0, 40.0, 157, dtype=np.float64)
        best_rdp = None
        best_score = -np.inf
        best_count = 0

        for rdp in candidates:
            v_fit = self._rdp_to_velocity_m_s(float(rdp))
            dx = distances - x0
            t = np.sqrt(np.maximum(0.0, t0 * t0 + (2.0 * dx / max(v_fit, 1e-9)) ** 2))
            z_disp = 0.5 * v_ref * t
            idx_f = (z_disp / max(depth_max, 1e-9)) * max(0, n_s - 1)
            valid = near & np.isfinite(idx_f) & (idx_f >= 0.0) & (idx_f <= (n_s - 1))
            count = int(np.count_nonzero(valid))
            if count < 12:
                continue
            jj = np.where(valid)[0]
            ii = np.rint(idx_f[valid]).astype(np.int64)
            # Corridor amplitude around modeled curve.
            if corridor > 0:
                vals = []
                for k in range(-corridor, corridor + 1):
                    si = np.clip(ii + k, 0, n_s - 1)
                    vals.append(abs_arr[si, jj])
                amp = np.max(np.vstack(vals), axis=0)
            else:
                amp = abs_arr[ii, jj]
            dxv = np.abs(distances[valid] - x0)
            span = max(1e-6, float(np.nanpercentile(dxv, 90))) if dxv.size > 0 else 1.0
            w = 1.0 / (1.0 + (dxv / span) ** 2)
            if not np.isfinite(amp).any():
                continue
            score = float(np.average(np.nan_to_num(amp, nan=0.0), weights=np.nan_to_num(w, nan=0.0)))
            if np.isfinite(score) and score > best_score:
                best_score = score
                best_rdp = float(rdp)
                best_count = count

        if best_rdp is None:
            QMessageBox.information(self, "Hyperbola Fit", "Impossibile stimare RDP con i dati correnti.")
            return

        self._spin_hyperbola_rdp.setValue(float(best_rdp))
        self._safe_set_text(
            self._lbl_status,
            f"Hyperbola auto-fit: RDP={best_rdp:.2f}  score={best_score:.4g}  traces={best_count}"
        )
        if self._chk_hyperbola.isChecked():
            self._redraw()

    def _collect_gain_hyper_preset(self) -> dict:
        points = self._sanitize_range_gain_breakpoints(self._range_gain_breakpoints)
        preset = {
            "format": "gpr_gain_hyper_preset",
            "version": 1,
            "display_gain": float(self._spin_gain.value()),
            "clip_pct": float(self._spin_clip.value()),
            "range_gain": {
                "enabled": bool(self._chk_range_gain.isChecked()),
                "pre_agc": bool(
                    hasattr(self, "_chk_range_gain_pre_agc")
                    and self._chk_range_gain_pre_agc.isChecked()
                ),
                "surface_gain": float(self._spin_gain_surface.value()),
                "deep_gain": float(self._spin_gain_deep.value()),
                "curve_mode": str(self._cb_range_gain_curve.currentData() or "power"),
                "power": float(self._spin_range_gain_power.value()),
                "breakpoints": [[float(x), float(y)] for x, y in points.tolist()],
            },
            "hyperbola": {
                "enabled": bool(self._chk_hyperbola.isChecked()),
                "rdp": float(self._spin_hyperbola_rdp.value()),
                "apex_x": (float(self._hyper_apex_x) if self._hyper_apex_x is not None else None),
                "apex_depth": (float(self._hyper_apex_depth) if self._hyper_apex_depth is not None else None),
            },
        }
        return preset

    def _apply_gain_hyper_preset(self, payload: dict):
        if not isinstance(payload, dict):
            raise ValueError("Preset non valido: struttura JSON non riconosciuta.")
        if payload.get("format") not in {"gpr_gain_hyper_preset", None}:
            raise ValueError("Preset non valido: campo format non supportato.")

        rg = payload.get("range_gain", {}) if isinstance(payload.get("range_gain"), dict) else {}
        hy = payload.get("hyperbola", {}) if isinstance(payload.get("hyperbola"), dict) else {}

        # Basic controls first.
        self._spin_gain.setValue(float(payload.get("display_gain", self._spin_gain.value())))
        self._spin_clip.setValue(float(payload.get("clip_pct", self._spin_clip.value())))

        self._updating_range_gain_controls = True
        try:
            self._spin_gain_surface.setValue(float(rg.get("surface_gain", self._spin_gain_surface.value())))
            self._spin_gain_deep.setValue(float(rg.get("deep_gain", self._spin_gain_deep.value())))
            self._spin_range_gain_power.setValue(float(rg.get("power", self._spin_range_gain_power.value())))
        finally:
            self._updating_range_gain_controls = False

        # Breakpoint curve.
        pts = rg.get("breakpoints")
        if isinstance(pts, list) and pts:
            self._range_gain_breakpoints = self._sanitize_range_gain_breakpoints(pts)
            self._sync_range_gain_spins_from_breakpoints()
        else:
            self._sync_range_gain_breakpoints_from_spins()

        mode = str(rg.get("curve_mode", self._cb_range_gain_curve.currentData() or "power")).strip().lower()
        mode_idx = 0
        for i in range(self._cb_range_gain_curve.count()):
            if str(self._cb_range_gain_curve.itemData(i) or "").strip().lower() == mode:
                mode_idx = i
                break
        self._cb_range_gain_curve.setCurrentIndex(mode_idx)
        if hasattr(self, "_chk_range_gain_pre_agc"):
            self._chk_range_gain_pre_agc.setChecked(bool(rg.get("pre_agc", self._chk_range_gain_pre_agc.isChecked())))
        self._chk_range_gain.setChecked(bool(rg.get("enabled", self._chk_range_gain.isChecked())))
        self._on_range_gain_mode_changed(self._cb_range_gain_curve.currentIndex())

        # Hyperbola.
        self._spin_hyperbola_rdp.setValue(float(hy.get("rdp", self._spin_hyperbola_rdp.value())))
        self._chk_hyperbola.setChecked(bool(hy.get("enabled", self._chk_hyperbola.isChecked())))
        apex_x = hy.get("apex_x")
        apex_d = hy.get("apex_depth")
        self._hyper_apex_x = float(apex_x) if apex_x is not None else None
        self._hyper_apex_depth = float(apex_d) if apex_d is not None else None

        self._apply_gain_only()
        if self._chk_hyperbola.isChecked():
            self._redraw()

    def _save_gain_hyper_preset(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Gain/Hyper Preset",
            "gpr_gain_hyper_preset.json",
            "JSON (*.json)",
        )
        if not path:
            return
        if not str(path).lower().endswith(".json"):
            path = f"{path}.json"
        payload = self._collect_gain_hyper_preset()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=True)
            self._safe_set_text(self._lbl_status, f"Preset salvato: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Preset", f"Errore salvataggio preset:\n{exc}")

    def _load_gain_hyper_preset(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Gain/Hyper Preset",
            "",
            "JSON (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self._apply_gain_hyper_preset(payload)
            self._safe_set_text(self._lbl_status, f"Preset caricato: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Load Preset", f"Errore caricamento preset:\n{exc}")

    def _draw_hyperbola_overlay_on_axis(self, ax, prof, dist_max: float, depth_max: float):
        if ax is None:
            return
        if not bool(getattr(self, "_chk_hyperbola", None) and self._chk_hyperbola.isChecked()):
            return
        if self._hyper_apex_x is None or self._hyper_apex_depth is None:
            return
        x0 = float(self._hyper_apex_x)
        z0_disp = float(self._hyper_apex_depth)
        if not (np.isfinite(x0) and np.isfinite(z0_disp) and np.isfinite(dist_max) and np.isfinite(depth_max)):
            return
        if z0_disp < 0 or z0_disp > depth_max:
            return

        v_ref = self._profile_reference_velocity(prof)
        rdp = float(self._spin_hyperbola_rdp.value())
        v_fit = self._rdp_to_velocity_m_s(rdp)
        # Apex sample time from current displayed depth scale.
        t0 = (2.0 * z0_disp) / max(v_ref, 1e-9)
        x = np.linspace(0.0, float(dist_max), 600, dtype=np.float64)
        dx = x - x0
        # t(x)=sqrt(t0^2 + (2*dx/v)^2), then mapped back to display-depth with v_ref.
        t = np.sqrt(np.maximum(0.0, t0 * t0 + (2.0 * dx / max(v_fit, 1e-9)) ** 2))
        z_disp = 0.5 * v_ref * t
        finite = np.isfinite(z_disp) & (z_disp >= 0.0) & (z_disp <= (depth_max * 1.02))
        if not finite.any():
            return
        ax.plot(x[finite], z_disp[finite], color="#ffd000", lw=1.5, alpha=0.95, zorder=7)
        ax.scatter([x0], [z0_disp], s=28, color="#ff8c00", edgecolor="#1f1f1f", linewidths=0.5, zorder=8)
        ax.text(
            x0,
            min(depth_max, z0_disp + 0.03 * max(depth_max, 1e-6)),
            f"RDP {rdp:.2f}",
            color="#ffd000",
            fontsize=8,
            ha="left",
            va="bottom",
            zorder=8,
        )

    def _draw_hyperbola_overlay(self, prof, dist_max: float, depth_max: float):
        self._draw_hyperbola_overlay_on_axis(self._ax, prof, dist_max, depth_max)

    def _build_depth_range_gain(self, n_samples: int) -> np.ndarray:
        n = max(1, int(n_samples))
        if not bool(getattr(self, "_chk_range_gain", None) and self._chk_range_gain.isChecked()):
            return np.ones((n, 1), dtype=np.float32)
        g0 = float(getattr(self, "_spin_gain_surface", None).value() if hasattr(self, "_spin_gain_surface") else 1.0)
        g1 = float(getattr(self, "_spin_gain_deep", None).value() if hasattr(self, "_spin_gain_deep") else 1.0)
        g0 = max(g0, 1e-6)
        g1 = max(g1, 1e-6)
        t = np.linspace(0.0, 1.0, n, dtype=np.float64)
        mode = str(
            getattr(self, "_cb_range_gain_curve", None).currentData()
            if hasattr(self, "_cb_range_gain_curve")
            else "power"
        )
        if mode == "breakpoints":
            pts = self._sanitize_range_gain_breakpoints(self._range_gain_breakpoints)
            x = np.asarray(pts[:, 0], dtype=np.float64)
            y = np.asarray(pts[:, 1], dtype=np.float64)
            g = np.interp(t, x, y)
        else:
            p = float(getattr(self, "_spin_range_gain_power", None).value() if hasattr(self, "_spin_range_gain_power") else 1.8)
            p = float(np.clip(p, 0.2, 8.0))
            if mode == "linear":
                g = g0 + (g1 - g0) * t
            elif mode == "exp":
                # Exponential interpolation in log domain keeps monotonic behavior.
                g = np.exp(np.log(g0) + (np.log(g1) - np.log(g0)) * t)
            else:
                g = g0 + (g1 - g0) * (t ** p)
        g = np.clip(g, 1e-6, 1e6).astype(np.float32)
        return g.reshape(-1, 1)

    def _compose_display_data(self) -> np.ndarray | None:
        if self._proc_data is None:
            return None
        proc = np.asarray(self._proc_data, dtype=np.float32)
        if proc.ndim != 2 or proc.size <= 0:
            return None

        clip_pct = float(self._spin_clip.value()) if hasattr(self, "_spin_clip") else 95.0
        # Normalize first, then apply manual gains so gain controls remain visible.
        base = normalize_display(proc, clip_pct=clip_pct).astype(np.float32, copy=False)
        pre_agc_applied = bool(getattr(self, "_last_pipeline_params", {}).get("pre_agc_gain", False))
        if pre_agc_applied:
            depth_gain = np.ones((int(base.shape[0]), 1), dtype=np.float32)
        else:
            depth_gain = self._build_depth_range_gain(int(base.shape[0]))
        gain = float(self._spin_gain.value()) if hasattr(self, "_spin_gain") else 1.0
        disp = (base * depth_gain * gain).astype(np.float32, copy=False)
        return np.clip(disp, -1.0, 1.0).astype(np.float32, copy=False)

    def _apply_gain_only(self):
        if self._proc_data is None:
            self._apply_processing()
            return
        self._disp_data = self._compose_display_data()
        if self._disp_data is None:
            return
        gain = float(self._spin_gain.value())
        disp_arr = np.asarray(self._disp_data, dtype=np.float64)
        finite = np.isfinite(disp_arr)
        if finite.any():
            disp_min = float(np.nanmin(disp_arr))
            disp_max = float(np.nanmax(disp_arr))
        else:
            disp_min = float("nan")
            disp_max = float("nan")
        self._safe_set_text(
            self._lbl_status,
            f"proc: min={self._proc_data.min():.3f}  "
            f"max={self._proc_data.max():.3f}  "
            f"disp: min={disp_min:.3f} max={disp_max:.3f}  "
            f"gain={gain:.1f}x  "
            f"range_gain={'on' if self._chk_range_gain.isChecked() else 'off'}  "
            f"pre_agc={'on' if self._range_gain_pre_agc_enabled() else 'off'}"
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
        # Explicit reset should restore full extent, not the initial short-window view.
        self._profile_view_initialized = True
        self._slice_view_xlim = None
        self._slice_view_ylim = None
        self._redraw()

    def _axis_pixel_size(self) -> tuple[float, float]:
        fig_w_px = fig_h_px = None
        try:
            if self._canvas_mpl is not None:
                fig_w_px, fig_h_px = self._canvas_mpl.get_width_height()
        except Exception:
            fig_w_px = fig_h_px = None
        if not (fig_w_px and fig_h_px):
            fig_w_px = float(self._fig.get_figwidth() * 100.0)
            fig_h_px = float(self._fig.get_figheight() * 100.0)
        bbox = self._ax.get_position()
        ax_w_px = max(1.0, float(fig_w_px) * float(bbox.width))
        ax_h_px = max(1.0, float(fig_h_px) * float(bbox.height))
        return float(ax_w_px), float(ax_h_px)

    def _compute_initial_xview(
        self,
        dist_max: float,
        depth_max: float,
        aspect_factor: float,
    ) -> tuple[float, float]:
        if not (np.isfinite(dist_max) and np.isfinite(depth_max)):
            return (0.0, 1.0)
        if dist_max <= 0.0 or depth_max <= 0.0:
            return (0.0, float(max(1.0, dist_max)))
        ax_w_px, ax_h_px = self._axis_pixel_size()
        if ax_h_px <= 1e-6:
            return (0.0, float(dist_max))
        af = float(np.clip(aspect_factor, 1e-6, 1e6))
        ideal_dist_window = float((ax_w_px * depth_max) / (ax_h_px * af))
        ideal_dist_window = float(np.clip(ideal_dist_window, 1.0, dist_max))
        # Keep full extent for short profiles; constrain only when profile is much longer.
        if dist_max <= (ideal_dist_window * 1.05):
            return (0.0, float(dist_max))
        return (0.0, float(ideal_dist_window))

    def _full_x_limits(self) -> tuple[float, float]:
        if self._disp_data is None or not self._profiles:
            return 0.0, 1.0
        prof = self._profiles[self._prof_idx]
        dist_arr = self._display_distance_axis(prof, int(self._disp_data.shape[1]))
        if dist_arr.size and np.isfinite(dist_arr).any():
            x_full_min = float(np.nanmin(dist_arr))
            x_full_max = float(np.nanmax(dist_arr))
        else:
            x_full_min = 0.0
            x_full_max = float(max(1.0, prof.sampling_step_m * max(self._disp_data.shape[1] - 1, 1)))
        if not (np.isfinite(x_full_min) and np.isfinite(x_full_max)) or x_full_max <= x_full_min:
            x_full_min, x_full_max = 0.0, 1.0
        return x_full_min, x_full_max

    def _sync_xpan_slider(self):
        if not hasattr(self, "_xpan_slider") or self._xpan_slider is None:
            return
        x_full_min, x_full_max = self._full_x_limits()
        full_w = float(max(x_full_max - x_full_min, 1e-9))
        x_view = self._view_xlim if self._view_xlim is not None else (x_full_min, x_full_max)
        x_view = self._clamp_axis_limits(x_view[0], x_view[1], x_full_min, x_full_max)
        view_w = float(max(abs(x_view[1] - x_view[0]), 1e-9))

        can_pan = view_w < (full_w - 1e-6)
        self._updating_xpan = True
        try:
            self._xpan_slider.setEnabled(can_pan)
            if not can_pan:
                self._xpan_slider.setValue(0)
                if self._lbl_xpan is not None:
                    self._safe_set_text(self._lbl_xpan, "Full")
                return
            movable = full_w - view_w
            ratio = (float(min(x_view)) - x_full_min) / max(movable, 1e-9)
            ratio = float(np.clip(ratio, 0.0, 1.0))
            self._xpan_slider.setValue(int(round(ratio * 1000.0)))
            if self._lbl_xpan is not None:
                self._safe_set_text(self._lbl_xpan, f"{min(x_view):.1f}-{max(x_view):.1f} m")
        finally:
            self._updating_xpan = False

    def _on_xpan_slider_changed(self, value: int):
        if bool(getattr(self, "_updating_xpan", False)):
            return
        if self._disp_data is None or self._ax is None:
            return
        x_full_min, x_full_max = self._full_x_limits()
        full_w = float(max(x_full_max - x_full_min, 1e-9))
        x_view = self._view_xlim if self._view_xlim is not None else (x_full_min, x_full_max)
        x_view = self._clamp_axis_limits(x_view[0], x_view[1], x_full_min, x_full_max)
        view_w = float(max(abs(x_view[1] - x_view[0]), 1e-9))
        movable = full_w - view_w
        if movable <= 1e-9:
            self._sync_xpan_slider()
            return
        ratio = float(np.clip(float(value) / 1000.0, 0.0, 1.0))
        lo = x_full_min + ratio * movable
        hi = lo + view_w
        self._view_xlim = (lo, hi)
        self._ax.set_xlim(*self._view_xlim)
        if self._canvas_mpl is not None:
            self._safe_draw_idle(self._canvas_mpl)
        self._sync_xpan_slider()

    def _export_radargram_image(self):
        if not HAS_MPL or self._disp_data is None or not self._profiles:
            QMessageBox.information(self, "Export Radargram", "Nessun radargramma da esportare.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Radargram",
            "radargram_export.png",
            "PNG (*.png);;TIFF (*.tif *.tiff)",
        )
        if not path:
            return
        dpi, ok = QInputDialog.getInt(
            self,
            "Export Radargram",
            "DPI:",
            300,
            72,
            2400,
            10,
        )
        if not ok:
            return
        prof = self._profiles[self._prof_idx]
        arr = np.asarray(self._disp_data, dtype=np.float64)
        if arr.ndim != 2 or arr.size <= 0:
            QMessageBox.information(self, "Export Radargram", "Dati radargramma non validi.")
            return
        n_s, n_t = arr.shape
        dist_arr = self._display_distance_axis(prof, n_t)
        if dist_arr.size > 0 and np.isfinite(dist_arr).any():
            dist_max = float(np.nanmax(dist_arr))
        else:
            dist_max = float(max(1.0, getattr(prof, "sampling_step_m", 0.1) * max(n_t - 1, 1)))
        depth_max = self._depth_max_display(prof)
        if not np.isfinite(depth_max) or depth_max <= 0:
            depth_max = float(max(1, n_s))

        finite = arr[np.isfinite(arr)]
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
        cmap = self._cb_cmap.currentText()
        interpolation_mode = str(self._cb_interp.currentData() or "bilinear")
        real_aspect = bool(self._chk_real_aspect.isChecked())
        vertical_exag = 1.0
        if hasattr(self, "_spin_vertical_exag") and self._spin_vertical_exag is not None:
            try:
                vertical_exag = float(self._spin_vertical_exag.value())
            except Exception:
                vertical_exag = 1.0
        vertical_exag = float(np.clip(vertical_exag, 0.5, 20.0))

        fig = Figure(figsize=(12.0, 5.5), tight_layout=True)
        ax = fig.add_subplot(111)
        aspect_export = "auto"
        if real_aspect and dist_max > 1e-9 and depth_max > 1e-9:
            # VE=1.0 preserves metric scale; VE>1 increases vertical readability.
            aspect_export = float(vertical_exag)
        ax.imshow(
            self._disp_data,
            aspect=aspect_export,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
            extent=[0, dist_max, depth_max, 0],
            interpolation=interpolation_mode,
            resample=True,
        )
        ax.set_xlim(0.0, dist_max)
        ax.set_ylim(depth_max, 0.0)
        ax.set_xlabel("Distanza (m)")
        ax.set_ylabel("Profondita' (m)")
        ax.set_title(
            f"{os.path.basename(prof.path)}  |  Ch {self._ch_idx}  |  {float(getattr(prof, 'frequency_mhz', 0.0)):.0f} MHz"
        )
        self._draw_hyperbola_overlay_on_axis(ax, prof, dist_max, depth_max)

        ext = os.path.splitext(path)[1].strip().lower()
        if ext not in {".png", ".tif", ".tiff"}:
            path = f"{path}.png"
        try:
            fig.savefig(path, dpi=int(dpi), bbox_inches="tight", facecolor="white")
            self._safe_set_text(self._lbl_status, f"Radargram export: {path}  dpi={int(dpi)}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Radargram", f"Errore export:\n{exc}")

    # ------------------------------------------------------------------
    # Disegno
    # ------------------------------------------------------------------

    def _redraw(self):
        if not HAS_MPL or self._disp_data is None:
            return
        prof = self._profiles[self._prof_idx]
        ch   = prof.channel(self._ch_idx)
        cmap = self._cb_cmap.currentText()

        dist_arr = self._display_distance_axis(prof, int(self._disp_data.shape[1]))
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
        # Keep fixed display limits so gain controls affect what the user sees.
        vmin, vmax = -1.0, 1.0
        real_aspect = bool(
            getattr(self, "_chk_real_aspect", None)
            and self._chk_real_aspect.isChecked()
        )
        interpolation_mode = "bilinear"
        if hasattr(self, "_cb_interp") and self._cb_interp is not None:
            interpolation_mode = str(self._cb_interp.currentData() or "bilinear")
        ve = 1.0
        if hasattr(self, "_spin_vertical_exag") and self._spin_vertical_exag is not None:
            try:
                ve = float(self._spin_vertical_exag.value())
            except Exception:
                ve = 1.0
        ve = float(np.clip(ve, 0.5, 20.0))
        self._im = self._ax.imshow(
            self._disp_data,
            # Mantieni il riempimento del pannello; la scala reale e' gestita sotto con set_aspect+datalim.
            aspect="auto",
            cmap=cmap,
            vmin=vmin, vmax=vmax,
            extent=[0, dist_max, depth_max, 0],
            interpolation=interpolation_mode,
            resample=True,
        )
        x_full = (0.0, dist_max)
        y_full = (depth_max, 0.0)
        if self._view_xlim is None:
            if not bool(getattr(self, "_profile_view_initialized", True)):
                init_af = ve if real_aspect else 1.0
                self._view_xlim = self._compute_initial_xview(dist_max, depth_max, init_af)
            else:
                self._view_xlim = x_full
        x_view = self._view_xlim
        y_view = self._view_ylim if self._view_ylim is not None else y_full
        x_view = self._clamp_axis_limits(x_view[0], x_view[1], x_full[0], x_full[1])
        y_view = self._clamp_axis_limits(y_view[0], y_view[1], 0.0, depth_max)
        self._view_xlim = x_view
        self._view_ylim = y_view
        self._ax.set_xlim(*x_view)
        self._ax.set_ylim(*y_view)
        if real_aspect and dist_max > 1e-9 and depth_max > 1e-9:
            # VE=1.0 keeps metric proportions, VE>1.0 increases vertical emphasis.
            self._ax.set_aspect(float(np.clip(ve, 0.5, 20.0)), adjustable="box")
        else:
            # In auto mode avoid excessive horizontal stretch on short profiles.
            ax_w_px, ax_h_px = self._axis_pixel_size()
            x_range = max(1e-9, float(abs(x_view[1] - x_view[0])))
            y_range = max(1e-9, float(abs(y_view[0] - y_view[1])))
            px_per_m_x = ax_w_px / x_range
            px_per_m_y = ax_h_px / y_range
            max_ratio = 4.0
            if px_per_m_x > (px_per_m_y * max_ratio):
                ratio = float((y_range / x_range) / max_ratio)
                self._ax.set_aspect(float(np.clip(ratio, 1e-4, 1e4)), adjustable="box")
            else:
                self._ax.set_aspect("auto", adjustable="box")
        self._ax.set_xlabel("Distanza (m)")
        self._ax.set_ylabel("Profondit\u00e0 (m)")
        self._ax.set_title(
            f"{os.path.basename(prof.path)}  |  "
            f"Ch {self._ch_idx}  |  "
            f"{prof.frequency_mhz:.0f} MHz"
        )
        self._draw_hyperbola_overlay(prof, dist_max, depth_max)
        self._vline = self._ax.axvline(x=0, color="yellow", lw=1, visible=False)
        self._hline = self._ax.axhline(y=0, color="cyan",   lw=1, linestyle="--", visible=False)
        self._apply_wiggle_visibility_layout()
        self._sync_xpan_slider()
        self._update_wiggle_plot(draw=False, force=True)
        self._profile_view_initialized = True
        self._safe_draw_idle(self._canvas_mpl)
        self._redraw_slice_view()

    def _depth_max_display(self, prof) -> float:
        if self._disp_data is not None:
            n_out = int(self._disp_data.shape[0])
        elif self._proc_data is not None:
            n_out = int(self._proc_data.shape[0])
        elif self._raw_data is not None:
            n_out = int(self._raw_data.shape[0])
        else:
            n_out = int(getattr(prof, "n_samples", 0) or 0)
        n_orig = int(getattr(prof, "n_samples", 0) or 0)
        depth_base = float(getattr(prof, "depth_max_m", 0.0) or 0.0)
        depth_max = depth_base * (n_out / n_orig) if n_orig > 0 else depth_base
        if not np.isfinite(depth_max) or depth_max <= 0:
            depth_max = max(1.0, float(n_out))
        return float(depth_max)

    def _cursor_metrics(self):
        if (
            not self._profiles
            or self._disp_data is None
            or self._cursor_x is None
            or self._cursor_z is None
        ):
            return None
        try:
            prof = self._profiles[self._prof_idx]
            ch = prof.channel(self._ch_idx)
        except Exception:
            return None

        n_tr = int(self._disp_data.shape[1])
        distances = self._channel_trace_vector(getattr(ch, "distances", []), n_tr)
        if distances.size != n_tr:
            step = float(getattr(prof, "sampling_step_m", 0.1) or 0.1)
            distances = (np.arange(n_tr, dtype=np.float64) * max(step, 1e-6)).astype(np.float64)
        if distances.size <= 0:
            return None

        depth_max = self._depth_max_display(prof)
        idx_t = int(np.searchsorted(distances, float(self._cursor_x)))
        idx_t = int(np.clip(idx_t, 0, max(0, n_tr - 1)))
        n_s = int(self._disp_data.shape[0])
        idx_s = int(np.rint((float(self._cursor_z) / max(depth_max, 1e-9)) * max(0, n_s - 1)))
        idx_s = int(np.clip(idx_s, 0, max(0, n_s - 1)))

        amp = None
        try:
            a = float(self._disp_data[idx_s, idx_t])
            amp = a if np.isfinite(a) else None
        except Exception:
            amp = None

        east = north = None
        try:
            e_vec = self._channel_trace_vector(getattr(ch, "easting", []), n_tr)
            n_vec = self._channel_trace_vector(getattr(ch, "northing", []), n_tr)
            if idx_t < e_vec.size:
                east = float(e_vec[idx_t])
            if idx_t < n_vec.size:
                north = float(n_vec[idx_t])
            if east is not None and not np.isfinite(east):
                east = None
            if north is not None and not np.isfinite(north):
                north = None
        except Exception:
            east = north = None

        return {
            "idx_trace": int(idx_t),
            "idx_sample": int(idx_s),
            "distance_m": float(distances[idx_t]),
            "depth_m": float(self._cursor_z),
            "time_ns": float(idx_s * float(getattr(prof, "dt_ns", 0.0) or 0.0)),
            "amplitude": amp,
            "east": east,
            "north": north,
            "depth_max_m": depth_max,
        }

    def _update_wiggle_plot(self, draw: bool = True, force: bool = False):
        if not HAS_MPL or not hasattr(self, "_ax_wiggle"):
            return
        axw = self._ax_wiggle
        if not bool(getattr(self, "_show_wiggle", True)):
            try:
                axw.set_visible(False)
            except Exception:
                pass
            return
        else:
            try:
                axw.set_visible(True)
            except Exception:
                pass
        if self._disp_data is None or not self._profiles:
            axw.clear()
            axw.set_xticks([])
            axw.set_yticks([])
            self._wiggle_trace_idx = None
            self._wiggle_depth_line = None
            if draw:
                self._safe_draw_idle(self._canvas_mpl)
            return

        metrics = self._cursor_metrics()
        if metrics is None:
            idx_t = int(np.clip(self._disp_data.shape[1] // 2, 0, max(0, self._disp_data.shape[1] - 1)))
            idx_s = 0
            depth_max = self._depth_max_display(self._profiles[self._prof_idx])
        else:
            idx_t = int(metrics["idx_trace"])
            idx_s = int(metrics["idx_sample"])
            depth_max = float(metrics["depth_max_m"])

        trace = np.asarray(self._disp_data[:, idx_t], dtype=np.float64)
        if trace.size <= 0:
            axw.clear()
            axw.set_xticks([])
            axw.set_yticks([])
            self._wiggle_trace_idx = None
            self._wiggle_depth_line = None
            if draw:
                self._safe_draw_idle(self._canvas_mpl)
            return

        depth_axis = np.linspace(0.0, float(depth_max), trace.size, dtype=np.float64)
        can_incremental = (
            (not bool(force))
            and self._wiggle_trace_idx is not None
            and int(self._wiggle_trace_idx) == int(idx_t)
            and self._wiggle_depth_line is not None
        )

        if can_incremental:
            if 0 <= idx_s < depth_axis.size:
                y = float(depth_axis[idx_s])
                try:
                    self._wiggle_depth_line.set_ydata([y, y])
                except Exception:
                    can_incremental = False
            if can_incremental and draw:
                self._safe_draw_idle(self._canvas_mpl)
            if can_incremental:
                return

        # Full redraw only when trace index or data changes.
        axw.clear()
        finite = np.isfinite(trace)
        if finite.any():
            vmax = float(np.nanpercentile(np.abs(trace[finite]), 98.0))
            if not np.isfinite(vmax) or vmax <= 1e-9:
                vmax = float(np.nanmax(np.abs(trace[finite])))
            if not np.isfinite(vmax) or vmax <= 1e-9:
                vmax = 1.0
            trn = np.clip(trace / vmax, -1.25, 1.25)
        else:
            trn = np.zeros_like(trace)
        # Leggero smoothing visivo del wiggle per ridurre seghettature ad alta frequenza.
        if trn.size >= 5:
            kernel = np.asarray([1.0, 2.0, 3.0, 2.0, 1.0], dtype=np.float64)
            kernel /= float(np.sum(kernel))
            tr_plot = np.convolve(trn, kernel, mode="same")
        else:
            tr_plot = trn
        # Compressione dolce delle code: evita "spike" visivi dominanti.
        tr_plot = np.tanh(tr_plot * 1.15)

        axw.plot(tr_plot, depth_axis, color="#e0e0e0", lw=0.7, antialiased=True)
        axw.fill_betweenx(
            depth_axis, 0.0, tr_plot, where=tr_plot >= 0.0, color="#ef5350", alpha=0.45
        )
        axw.fill_betweenx(
            depth_axis, 0.0, tr_plot, where=tr_plot < 0.0, color="#42a5f5", alpha=0.45
        )
        self._wiggle_depth_line = None
        if 0 <= idx_s < depth_axis.size:
            self._wiggle_depth_line = axw.axhline(
                float(depth_axis[idx_s]),
                color="goldenrod",
                lw=0.8,
                ls="--",
            )
        axw.set_ylim(float(depth_max), 0.0)
        axw.set_xlim(-1.2, 1.2)
        axw.set_title(f"T{idx_t}", fontsize=7, pad=2)
        axw.set_xlabel("")
        axw.set_ylabel("")
        axw.set_xticks([])
        axw.set_yticks([])
        axw.grid(False)
        axw.axvline(0.0, color="#555555", lw=0.5)
        axw.set_facecolor("#1a1a1a")
        for spine in axw.spines.values():
            spine.set_edgecolor("#333333")
            spine.set_linewidth(0.5)
        self._wiggle_trace_idx = int(idx_t)
        if draw:
            self._safe_draw_idle(self._canvas_mpl)

    # ------------------------------------------------------------------
    # Cursore
    # ------------------------------------------------------------------

    def _on_mouse_move(self, event):
        if event.inaxes != self._ax or self._disp_data is None:
            return
        self._cursor_x = event.xdata
        self._cursor_z = event.ydata
        self._update_cursor_lines()
        self._update_wiggle_plot(draw=False)
        self._update_status()
        self._canvas_timer.start()

    def _on_mouse_press(self, event):
        if event.inaxes == self._ax and event.button == 1:
            self._cursor_x = event.xdata
            self._cursor_z = event.ydata
            self._update_wiggle_plot(draw=False)
            if bool(getattr(event, "dblclick", False)) and bool(self._chk_hyperbola.isChecked()):
                self._set_hyperbola_apex_from_cursor()
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
        self._sync_xpan_slider()
        self._safe_draw_idle(self._canvas_mpl)

    def _on_axes_leave(self, event):
        if self._vline: self._vline.set_visible(False)
        if self._hline: self._hline.set_visible(False)
        self._safe_draw_idle(self._canvas_mpl)

    def _update_cursor_lines(self):
        if self._vline and self._cursor_x is not None:
            self._vline.set_xdata([self._cursor_x])
            self._vline.set_visible(True)
        if self._hline and self._cursor_z is not None:
            self._hline.set_ydata([self._cursor_z])
            self._hline.set_visible(True)
        self._safe_draw_idle(self._canvas_mpl)

    def _update_status(self):
        if not self._profiles:
            return
        m = self._cursor_metrics()
        if not m:
            z_str = f"{self._cursor_z:.3f} m" if self._cursor_z is not None else "\u2014"
            x_str = f"{self._cursor_x:.2f} m" if self._cursor_x is not None else "\u2014"
            self._safe_set_text(
                self._lbl_status,
                f"Dist: {x_str}  |  Profondita': {z_str}"
            )
            return
        amp = m.get("amplitude")
        amp_str = f"{float(amp):.6g}" if amp is not None else "n/a"
        east = m.get("east")
        north = m.get("north")
        east_str = f"{float(east):.2f}" if east is not None else "n/a"
        north_str = f"{float(north):.2f}" if north is not None else "n/a"
        self._safe_set_text(
            self._lbl_status,
            f"Trace={int(m['idx_trace'])}  Sample={int(m['idx_sample'])}  "
            f"Amp={amp_str}  Time={float(m['time_ns']):.3f} ns  "
            f"Depth={float(m['depth_m']):.3f} m  Dist={float(m['distance_m']):.3f} m  "
            f"E {east_str}  N {north_str}"
        )

    def _cursor_world_position(self):
        m = self._cursor_metrics()
        if not m:
            return None, None, None
        east = m.get("east")
        north = m.get("north")
        depth = float(m.get("depth_m", np.nan))
        if east is None or north is None:
            return None, None, None
        if not (np.isfinite(float(east)) and np.isfinite(float(north)) and np.isfinite(depth)):
            return None, None, None
        return float(east), float(north), float(depth)

    def _emit_cursor_moved(self):
        east, north, depth = self._cursor_world_position()
        if east is None or north is None or depth is None:
            return
        try:
            self.cursor_moved.emit(float(east), float(north), float(depth))
        except Exception:
            pass
        if not self._suppress_3d_depth_sync and self._gpr_3d_viewer is not None:
            try:
                self._gpr_3d_viewer.set_depth_from_external(float(depth))
            except Exception:
                pass

    def _on_3d_depth_changed(self, depth: float):
        if not np.isfinite(depth):
            return
        if self._profiles:
            try:
                prof = self._profiles[self._prof_idx]
                n_out = int(self._disp_data.shape[0]) if self._disp_data is not None else int(prof.n_samples)
                n_orig = int(prof.n_samples) if int(prof.n_samples) > 0 else max(1, n_out)
                depth_base = float(prof.depth_max_m) if np.isfinite(prof.depth_max_m) else 0.0
                depth_max = depth_base * (n_out / n_orig) if n_orig > 0 else depth_base
                if not np.isfinite(depth_max) or depth_max <= 0:
                    depth_max = max(1.0, float(n_out))
                depth = float(np.clip(float(depth), 0.0, depth_max))
            except Exception:
                depth = float(depth)
        else:
            depth = float(depth)

        self._suppress_3d_depth_sync = True
        try:
            self._cursor_z = depth
            self._update_cursor_lines()
            self._update_wiggle_plot(draw=False)
            self._update_status()
            self._update_dial()
            self._emit_cursor_moved()
            self._canvas_timer.start()
        finally:
            self._suppress_3d_depth_sync = False

    # ------------------------------------------------------------------
    # Bridge QGIS
    # ------------------------------------------------------------------

    def _flush_canvas_update(self):
        if not self._is_qt_alive(self):
            return
        try:
            self._update_rubber_band()
            self._update_dial()
            self._update_timeslice_visibility_in_canvas()
            self._update_timeslice_crosshair()
            self._emit_cursor_moved()
            if self._is_qt_alive(getattr(self, "_canvas_slice", None)):
                self._redraw_slice_view()
        except RuntimeError:
            pass

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
        m = self._cursor_metrics()
        if not m:
            return
        east = m.get("east")
        north = m.get("north")
        if east is None or north is None:
            return
        prof = self._profiles[self._prof_idx]
        canvas = self.iface.mapCanvas()
        if self._rb_point is None:
            self._rb_point = QgsRubberBand(canvas, QgsWkbTypes.PointGeometry)
            self._rb_point.setColor(QColor(255, 220, 0))
        self._rb_point.setIconSize(12)
        self._rb_point.setWidth(3)
        self._rb_point.setVisible(True)
        self._rb_point.reset(QgsWkbTypes.PointGeometry)
        pt_canvas = self._to_canvas_point(prof, float(east), float(north))
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
        if self._disp_data is not None and np.asarray(self._disp_data).ndim == 2:
            n_t = int(self._disp_data.shape[1])
        else:
            try:
                n_t = int(np.asarray(ch.data).shape[1])
            except Exception:
                n_t = 0
        e_vec = self._channel_trace_vector(getattr(ch, "easting", []), n_t)
        n_vec = self._channel_trace_vector(getattr(ch, "northing", []), n_t)
        finite = np.isfinite(e_vec) & np.isfinite(n_vec)
        if int(np.count_nonzero(finite)) < 2:
            return
        e_use = e_vec[finite]
        n_use = n_vec[finite]
        for i in range(len(e_use)):
            pt_canvas = self._to_canvas_point(prof, e_use[i], n_use[i])
            self._rb_line.addPoint(
                pt_canvas,
                i == len(e_use) - 1
            )

    def _clear_timeslice_crosshair(self):
        for rb in (self._rb_crosshair_h, self._rb_crosshair_v):
            if rb is not None:
                try:
                    rb.setVisible(False)
                except Exception:
                    pass
                try:
                    rb.reset(QgsWkbTypes.LineGeometry)
                except Exception:
                    pass

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

    def _active_group_timeslices_context(self):
        if self.plugin is None:
            return None, None
        settings = getattr(self.plugin, "settings", None)
        settings_key = getattr(self.plugin, "settings_key_active_project", None)
        if settings is None or not settings_key:
            return None, None
        try:
            pr = (settings.value(settings_key, "", type=str) or "").strip()
        except Exception:
            pr = ""
        if not pr:
            return None, None
        catalog = self._get_cached_catalog(pr)
        if not isinstance(catalog, dict):
            return None, None

        groups = [g for g in catalog.get("raster_groups", []) if isinstance(g, dict)]
        if not groups:
            return None, None

        active_group = None
        active_group_id = str(getattr(self.plugin, "_active_group_id", "") or "").strip()
        if active_group_id:
            active_group = next((g for g in groups if str(g.get("id") or "").strip() == active_group_id), None)

        if active_group is None:
            dlg = getattr(self.plugin, "dlg", None)
            glw = getattr(dlg, "groupListWidget", None)
            current_item = None
            try:
                current_item = glw.currentItem() if glw is not None else None
                if current_item is None and glw is not None:
                    selected_items = list(glw.selectedItems() or [])
                    current_item = selected_items[0] if selected_items else None
            except Exception:
                current_item = None
            if current_item is not None:
                gid = str(current_item.data(Qt.UserRole) or "").strip()
                gname = str(current_item.text() or "").strip()
                if gid:
                    active_group = next((g for g in groups if str(g.get("id") or "").strip() == gid), None)
                if active_group is None and gname:
                    active_group = next(
                        (g for g in groups if str(g.get("name") or "").strip().lower() == gname.lower()),
                        None,
                    )

        if active_group is None and hasattr(self.plugin, "_selected_group_names"):
            try:
                selected_names = list(getattr(self.plugin, "_selected_group_names")() or [])
            except Exception:
                selected_names = []
            for name in selected_names:
                key = str(name or "").strip().lower()
                if not key:
                    continue
                active_group = next(
                    (g for g in groups if str(g.get("name") or "").strip().lower() == key),
                    None,
                )
                if active_group is not None:
                    break

        if active_group is None:
            active_group = next((g for g in groups if g.get("timeslice_ids")), None)
        if active_group is None:
            return None, None

        tids = [str(tid).strip() for tid in (active_group.get("timeslice_ids") or []) if str(tid).strip()]
        if not tids:
            return active_group, []
        ts_by_id = {
            str(t.get("id") or "").strip(): t
            for t in catalog.get("timeslices", [])
            if isinstance(t, dict) and str(t.get("id") or "").strip()
        }
        slices = [ts_by_id[tid] for tid in tids if tid in ts_by_id]
        if not slices:
            tids_set = set(tids)
            slices = [
                t for t in catalog.get("timeslices", [])
                if isinstance(t, dict) and str(t.get("id") or "").strip() in tids_set
            ]
        return active_group, slices

    def _nearest_timeslice_index(self, slices, depth_m: float):
        if not slices:
            return None
        centers = []
        for t in slices:
            try:
                d0 = float(t.get("depth_from", 0.0) or 0.0)
                d1 = float(t.get("depth_to", d0) or d0)
            except Exception:
                d0 = 0.0
                d1 = 0.0
            centers.append((d0 + d1) * 0.5)
        depth_arr = np.asarray(centers, dtype=np.float64)
        if depth_arr.size <= 0 or not np.isfinite(depth_arr).any():
            return None
        try:
            target = float(depth_m)
        except Exception:
            return None
        if not np.isfinite(target):
            return None
        return int(np.nanargmin(np.abs(depth_arr - target)))

    def _active_project_root(self) -> str:
        if self.plugin is None:
            return ""
        settings = getattr(self.plugin, "settings", None)
        settings_key = getattr(self.plugin, "settings_key_active_project", None)
        if settings is None or not settings_key:
            return ""
        try:
            return (settings.value(settings_key, "", type=str) or "").strip()
        except Exception:
            return ""

    def _resolve_timeslice_raster_path(self, ts: dict) -> str:
        if not isinstance(ts, dict):
            return ""
        project_root = self._active_project_root()
        candidates = []
        for key in ("raster_path", "project_path", "path", "file_path", "source_path"):
            raw = str(ts.get(key) or "").strip()
            if raw:
                candidates.append(raw)
        for raw in candidates:
            p = raw
            if not os.path.isabs(p) and project_root:
                p = os.path.normpath(os.path.join(project_root, p))
            p = os.path.normpath(p)
            if os.path.exists(p):
                return p
        if candidates:
            fallback = candidates[0]
            if not os.path.isabs(fallback) and project_root:
                fallback = os.path.normpath(os.path.join(project_root, fallback))
            return os.path.normpath(fallback)
        return ""

    def _load_slice_raster_cached(self, raster_path: str):
        p = str(raster_path or "").strip()
        if not p:
            return None, None
        try:
            mtime = float(os.path.getmtime(p))
        except Exception:
            mtime = None

        cache_hit = (
            self._slice_cache_path == p
            and self._slice_cache_arr is not None
            and self._slice_cache_extent is not None
            and self._slice_cache_mtime == mtime
        )
        if cache_hit:
            return self._slice_cache_arr, self._slice_cache_extent

        from osgeo import gdal

        ds = gdal.Open(p)
        if ds is None:
            return None, None
        band = ds.GetRasterBand(1)
        if band is None:
            ds = None
            return None, None
        arr = band.ReadAsArray()
        if arr is None:
            ds = None
            return None, None
        arr = np.asarray(arr, dtype=np.float32)
        nodata = band.GetNoDataValue()
        if nodata is not None and np.isfinite(float(nodata)):
            arr[arr == float(nodata)] = np.nan
        arr[~np.isfinite(arr)] = np.nan

        gt = ds.GetGeoTransform(can_return_null=True)
        if gt:
            xmin = float(gt[0])
            xmax = float(gt[0] + gt[1] * ds.RasterXSize)
            ymax = float(gt[3])
            ymin = float(gt[3] + gt[5] * ds.RasterYSize)
        else:
            xmin, xmax = 0.0, float(arr.shape[1])
            ymin, ymax = 0.0, float(arr.shape[0])
        ds = None

        extent = (xmin, xmax, ymin, ymax)
        self._slice_cache_path = p
        self._slice_cache_mtime = mtime
        self._slice_cache_arr = arr
        self._slice_cache_extent = extent
        return arr, extent

    def _slice_world_to_pixel(self, east: float, north: float) -> tuple[float, float] | tuple[None, None]:
        if self._slice_current_extent is None:
            return None, None
        xmin, xmax, ymin, ymax = [float(v) for v in self._slice_current_extent]
        if not (np.isfinite(xmin) and np.isfinite(xmax) and np.isfinite(ymin) and np.isfinite(ymax)):
            return None, None
        # Ora la slice e' visualizzata direttamente in coordinate geografiche (extent su imshow),
        # quindi il crosshair puo' usare world-coordinates senza conversione in pixel.
        if abs(float(xmax - xmin)) < 1e-12 or abs(float(ymax - ymin)) < 1e-12:
            return None, None
        ex = float(east)
        ny = float(north)
        if not (np.isfinite(ex) and np.isfinite(ny)):
            return None, None
        return ex, ny

    def _update_slice_crosshair_overlay(self, draw: bool = True) -> bool:
        if self._ax_slice is None or self._slice_im is None:
            return False
        m = self._cursor_metrics()
        if not m:
            return False
        east = m.get("east")
        north = m.get("north")
        try:
            east_f = float(east) if east is not None else np.nan
            north_f = float(north) if north is not None else np.nan
        except Exception:
            return False
        if not (np.isfinite(east_f) and np.isfinite(north_f)):
            return False
        xw, yw = self._slice_world_to_pixel(east_f, north_f)
        if xw is None or yw is None:
            return False
        if self._slice_vline is None:
            self._slice_vline = self._ax_slice.axvline(xw, color="yellow", lw=1.0, alpha=0.85)
        else:
            self._slice_vline.set_xdata([xw, xw])
        if self._slice_hline is None:
            self._slice_hline = self._ax_slice.axhline(yw, color="cyan", lw=1.0, ls="--", alpha=0.8)
        else:
            self._slice_hline.set_ydata([yw, yw])
        if draw and self._canvas_slice is not None:
            self._safe_draw_idle(self._canvas_slice)
        return True

    def _on_slice_clip_changed(self, _value):
        if not hasattr(self, "_spin_slice_vmin_pct") or not hasattr(self, "_spin_slice_vmax_pct"):
            return
        lo = float(self._spin_slice_vmin_pct.value())
        hi = float(self._spin_slice_vmax_pct.value())
        if hi <= lo + 0.1:
            sender = self.sender()
            if sender is self._spin_slice_vmin_pct:
                hi = min(100.0, lo + 0.1)
                self._spin_slice_vmax_pct.blockSignals(True)
                try:
                    self._spin_slice_vmax_pct.setValue(hi)
                finally:
                    self._spin_slice_vmax_pct.blockSignals(False)
            else:
                lo = max(0.0, hi - 0.1)
                self._spin_slice_vmin_pct.blockSignals(True)
                try:
                    self._spin_slice_vmin_pct.setValue(lo)
                finally:
                    self._spin_slice_vmin_pct.blockSignals(False)
        self._redraw_slice_view(force=True)

    def _redraw_slice_view(self, force: bool = False):
        if not HAS_MPL or self._ax_slice is None or self._canvas_slice is None:
            return

        local_catalog = list(getattr(self, "_slice_catalog", []) or [])
        using_local_catalog = len(local_catalog) > 0
        slices = local_catalog if using_local_catalog else []

        if not slices:
            self._ax_slice.clear()
            self._slice_im = None
            self._slice_vline = None
            self._slice_hline = None
            self._slice_current_idx = None
            self._slice_current_path = None
            self._slice_current_extent = None
            self._slice_current_shape = None
            self._slice_current_cmap = ""
            self._ax_slice.text(
                0.5,
                0.5,
                "Nessuna timeslice calcolata.\nUsa 'Crea Timeslice...' nel pannello Timeslice.",
                transform=self._ax_slice.transAxes,
                ha="center",
                va="center",
                fontsize=9,
                color="#666666",
            )
            self._ax_slice.set_title("Timeslice locale")
            self._ax_slice.set_xticks([])
            self._ax_slice.set_yticks([])
            self._safe_draw_idle(self._canvas_slice)
            return

        idx = 0 if self._slice_current_idx is None else int(self._slice_current_idx)
        idx = int(np.clip(idx, 0, len(slices) - 1))
        ts = dict(slices[idx] or {})
        raster_path = str(ts.get("path") or "").strip()
        cmap = (
            self._cb_slice_cmap.currentText()
            if hasattr(self, "_cb_slice_cmap") and self._cb_slice_cmap is not None
            else (self._cb_cmap.currentText() if hasattr(self, "_cb_cmap") else DEFAULT_CMAP)
        )

        if not raster_path:
            self._ax_slice.clear()
            self._slice_im = None
            self._ax_slice.set_title(f"Slice {idx} - percorso raster non disponibile")
            self._ax_slice.set_xticks([])
            self._ax_slice.set_yticks([])
            self._safe_draw_idle(self._canvas_slice)
            return
        if not os.path.exists(raster_path):
            self._ax_slice.clear()
            self._slice_im = None
            self._ax_slice.set_title(f"Slice {idx} - file non trovato")
            self._ax_slice.set_xticks([])
            self._ax_slice.set_yticks([])
            self._safe_draw_idle(self._canvas_slice)
            return

        needs_full_redraw = bool(
            force
            or self._slice_im is None
            or self._slice_current_idx != idx
            or str(self._slice_current_path or "") != str(raster_path)
        )

        if needs_full_redraw:
            try:
                arr, extent = self._load_slice_raster_cached(raster_path)
            except Exception as exc:
                self._ax_slice.clear()
                self._slice_im = None
                self._ax_slice.set_title(f"Errore lettura raster: {exc}")
                self._ax_slice.set_xticks([])
                self._ax_slice.set_yticks([])
                self._safe_draw_idle(self._canvas_slice)
                return
            if arr is None or extent is None:
                self._ax_slice.clear()
                self._slice_im = None
                self._ax_slice.set_title("Errore lettura raster")
                self._ax_slice.set_xticks([])
                self._ax_slice.set_yticks([])
                self._safe_draw_idle(self._canvas_slice)
                return

            finite = arr[np.isfinite(arr)]
            p_lo = 2.0
            p_hi = 98.0
            if hasattr(self, "_spin_slice_vmin_pct") and self._spin_slice_vmin_pct is not None:
                try:
                    p_lo = float(self._spin_slice_vmin_pct.value())
                except Exception:
                    p_lo = 2.0
            if hasattr(self, "_spin_slice_vmax_pct") and self._spin_slice_vmax_pct is not None:
                try:
                    p_hi = float(self._spin_slice_vmax_pct.value())
                except Exception:
                    p_hi = 98.0
            if p_hi <= p_lo + 0.1:
                p_hi = min(100.0, p_lo + 0.1)
            if finite.size > 0:
                vmin = float(np.nanpercentile(finite, p_lo))
                vmax = float(np.nanpercentile(finite, p_hi))
                if (not np.isfinite(vmin)) or (not np.isfinite(vmax)) or (vmax <= vmin):
                    vmin = float(np.nanmin(finite))
                    vmax = float(np.nanmax(finite))
            else:
                vmin, vmax = 0.0, 1.0
            if not np.isfinite(vmin):
                vmin = 0.0
            if not np.isfinite(vmax) or vmax <= vmin:
                vmax = vmin + 1e-6

            self._ax_slice.clear()
            self._slice_vline = None
            self._slice_hline = None
            arr_masked = np.ma.masked_invalid(arr)
            slice_cmap = cmap
            try:
                from matplotlib import colormaps

                cm_obj = colormaps.get_cmap(str(cmap)).copy()
                cm_obj.set_bad(color="#e6e6e6", alpha=1.0)
                slice_cmap = cm_obj
            except Exception:
                slice_cmap = cmap
            self._slice_im = self._ax_slice.imshow(
                arr_masked,
                cmap=slice_cmap,
                vmin=vmin,
                vmax=vmax,
                extent=[float(extent[0]), float(extent[1]), float(extent[2]), float(extent[3])],
                origin="upper",
                aspect="equal",
                interpolation="bilinear",
            )

            n_rows, n_cols = int(arr.shape[0]), int(arr.shape[1])
            xmin, xmax, ymin, ymax = [float(v) for v in extent]
            x_full = (min(xmin, xmax), max(xmin, xmax))
            y_full = (min(ymin, ymax), max(ymin, ymax))
            if self._slice_view_xlim is None:
                self._slice_view_xlim = x_full
            if self._slice_view_ylim is None:
                self._slice_view_ylim = y_full
            self._slice_view_xlim = self._clamp_axis_limits(
                self._slice_view_xlim[0], self._slice_view_xlim[1], x_full[0], x_full[1]
            )
            self._slice_view_ylim = self._clamp_axis_limits(
                self._slice_view_ylim[0], self._slice_view_ylim[1], y_full[0], y_full[1]
            )
            self._ax_slice.set_xlim(*self._slice_view_xlim)
            self._ax_slice.set_ylim(*self._slice_view_ylim)
            self._ax_slice.set_aspect("equal", adjustable="datalim")

            self._ax_slice.grid(False)
            self._ax_slice.set_xticks([])
            self._ax_slice.set_yticks([])
            self._ax_slice.set_xlabel("")
            self._ax_slice.set_ylabel("")
            self._ax_slice.set_facecolor("#e6e6e6")
            for spine in self._ax_slice.spines.values():
                spine.set_visible(False)

            try:
                d0 = float(ts.get("z_top", 0.0) or 0.0)
                d1 = float(ts.get("z_bot", d0) or d0)
            except Exception:
                d0 = 0.0
                d1 = 0.0
            source_name = os.path.basename(str(self._slice_catalog_source_dir or "")) or "local"
            title_left = f"{source_name} | "
            base = os.path.basename(raster_path)
            self._ax_slice.set_title(f"{title_left}Timeslice {d0:.2f}-{d1:.2f} m  |  {base}")

            self._slice_current_idx = idx
            self._slice_current_path = raster_path
            self._slice_current_extent = tuple(float(v) for v in extent)
            self._slice_current_shape = (n_rows, n_cols)
            self._slice_current_cmap = str(cmap)
        else:
            if self._slice_im is not None and str(self._slice_current_cmap) != str(cmap):
                cm_to_set = cmap
                try:
                    from matplotlib import colormaps

                    cm_obj = colormaps.get_cmap(str(cmap)).copy()
                    cm_obj.set_bad(color="#e6e6e6", alpha=1.0)
                    cm_to_set = cm_obj
                except Exception:
                    cm_to_set = cmap
                self._slice_im.set_cmap(cm_to_set)
                self._slice_current_cmap = str(cmap)

        changed = self._update_slice_crosshair_overlay(draw=False)
        if changed or needs_full_redraw:
            self._safe_draw_idle(self._canvas_slice)

    def _on_slice_press(self, event):
        if event is None or event.inaxes != self._ax_slice:
            return
        if int(getattr(event, "button", 0) or 0) != 1:
            return
        if self._slice_im is None or event.xdata is None or event.ydata is None:
            return
        try:
            xlim0 = tuple(self._ax_slice.get_xlim())
            ylim0 = tuple(self._ax_slice.get_ylim())
            self._slice_pan_anchor = (
                float(event.xdata),
                float(event.ydata),
                (float(xlim0[0]), float(xlim0[1])),
                (float(ylim0[0]), float(ylim0[1])),
            )
        except Exception:
            self._slice_pan_anchor = None

    def _on_slice_release(self, _event):
        self._slice_pan_anchor = None

    def _on_slice_mouse_move(self, event):
        if event is None or event.inaxes != self._ax_slice:
            return
        if event.xdata is None or event.ydata is None:
            return
        if self._slice_pan_anchor is not None and self._slice_im is not None:
            try:
                px, py, xlim0, ylim0 = self._slice_pan_anchor
                dx = float(px) - float(event.xdata)
                dy = float(py) - float(event.ydata)
                nx0 = float(xlim0[0]) + dx
                nx1 = float(xlim0[1]) + dx
                ny0 = float(ylim0[0]) + dy
                ny1 = float(ylim0[1]) + dy
                ex0, ex1, ey0, ey1 = self._slice_im.get_extent()
                x_min, x_max = float(min(ex0, ex1)), float(max(ex0, ex1))
                y_min, y_max = float(min(ey0, ey1)), float(max(ey0, ey1))
                self._slice_view_xlim = self._clamp_axis_limits(nx0, nx1, x_min, x_max)
                self._slice_view_ylim = self._clamp_axis_limits(ny0, ny1, y_min, y_max)
                self._ax_slice.set_xlim(*self._slice_view_xlim)
                self._ax_slice.set_ylim(*self._slice_view_ylim)
                self._safe_draw_idle(self._canvas_slice)
            except Exception:
                pass
            return
        try:
            self._safe_set_text(
                self._lbl_status,
                f"Slice cursor: x {float(event.xdata):.1f}  y {float(event.ydata):.1f}"
            )
        except Exception:
            pass

    def _on_slice_scroll_zoom(self, event):
        if event is None or event.inaxes != self._ax_slice or self._slice_im is None:
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

        x0, x1 = self._ax_slice.get_xlim()
        y0, y1 = self._ax_slice.get_ylim()
        x = float(event.xdata)
        y = float(event.ydata)

        nx0 = x - (x - x0) * scale
        nx1 = x + (x1 - x) * scale
        ny0 = y - (y - y0) * scale
        ny1 = y + (y1 - y) * scale

        ex0, ex1, ey0, ey1 = self._slice_im.get_extent()
        x_min, x_max = float(min(ex0, ex1)), float(max(ex0, ex1))
        y_min, y_max = float(min(ey0, ey1)), float(max(ey0, ey1))

        self._slice_view_xlim = self._clamp_axis_limits(nx0, nx1, x_min, x_max)
        self._slice_view_ylim = self._clamp_axis_limits(ny0, ny1, y_min, y_max)
        self._ax_slice.set_xlim(*self._slice_view_xlim)
        self._ax_slice.set_ylim(*self._slice_view_ylim)
        self._safe_draw_idle(self._canvas_slice)

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
            slices = list(getattr(self, "_slice_catalog", []) or [])
            if not slices:
                return
            centers = []
            for i, s in enumerate(slices):
                try:
                    zc = s.get("z_center", None)
                    if zc is None:
                        z0 = float(s.get("z_top", 0.0) or 0.0)
                        z1 = float(s.get("z_bot", z0) or z0)
                        zc = 0.5 * (z0 + z1)
                    centers.append((i, float(zc)))
                except Exception:
                    continue
            if not centers:
                idx = int(self._slice_current_idx if self._slice_current_idx is not None else 0)
                idx = int(np.clip(idx, 0, len(slices) - 1))
            else:
                target = float(self._cursor_z)
                idx = min(centers, key=lambda p: abs(p[1] - target))[0]
            dial_val = int(np.clip(idx, 0, int(dial.maximum())))
            if dial.value() != dial_val:
                dial.setValue(dial_val)
        except Exception:
            pass

    def _update_timeslice_visibility_in_canvas(self):
        if not bool(getattr(self, "_chk_timeslice_sync", None) and self._chk_timeslice_sync.isChecked()):
            return
        if self._cursor_z is None or self.plugin is None:
            return

        slices = list(getattr(self, "_slice_catalog", []) or [])
        if not slices:
            return

        centers = []
        for i, s in enumerate(slices):
            try:
                zc = s.get("z_center", None)
                if zc is None:
                    z0 = float(s.get("z_top", 0.0) or 0.0)
                    z1 = float(s.get("z_bot", z0) or z0)
                    zc = 0.5 * (z0 + z1)
                centers.append((i, float(zc)))
            except Exception:
                continue
        if not centers:
            return

        target = float(self._cursor_z)
        idx = min(centers, key=lambda p: abs(p[1] - target))[0]

        try:
            root = QgsProject.instance().layerTreeRoot()
            qgis_group = root.findGroup("GPR Timeslices")
            if qgis_group is None:
                return

            raster_nodes = []
            for child in qgis_group.children():
                if not hasattr(child, "setItemVisibilityChecked"):
                    continue
                layer = child.layer() if hasattr(child, "layer") else None
                if layer is None:
                    continue
                if not hasattr(layer, "bandCount"):
                    continue
                raster_nodes.append(child)
            if not raster_nodes:
                return
            index = int(np.clip(idx, 0, len(raster_nodes) - 1))
            for i, node in enumerate(raster_nodes):
                node.setItemVisibilityChecked(i == index)
        except Exception:
            pass

    def _update_timeslice_crosshair(self):
        if not bool(getattr(self, "_chk_timeslice_sync", None) and self._chk_timeslice_sync.isChecked()):
            self._clear_timeslice_crosshair()
            return
        if self.iface is None or not self._profiles:
            self._clear_timeslice_crosshair()
            return
        m = self._cursor_metrics()
        if not m:
            self._clear_timeslice_crosshair()
            return
        east = m.get("east")
        north = m.get("north")
        if east is None or north is None:
            self._clear_timeslice_crosshair()
            return
        try:
            east_f = float(east)
            north_f = float(north)
        except Exception:
            self._clear_timeslice_crosshair()
            return
        if not (np.isfinite(east_f) and np.isfinite(north_f)):
            self._clear_timeslice_crosshair()
            return
        prof = self._profiles[self._prof_idx]
        canvas = self.iface.mapCanvas()
        if canvas is None:
            self._clear_timeslice_crosshair()
            return
        try:
            size = float(getattr(self, "_spin_crosshair_size").value())
        except Exception:
            size = float(getattr(self, "_crosshair_size_m", 5.0) or 5.0)
        size = float(np.clip(size, 0.5, 1000.0))

        if self._rb_crosshair_h is None:
            self._rb_crosshair_h = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
            self._rb_crosshair_h.setColor(QColor(255, 50, 50))
            self._rb_crosshair_h.setWidth(2)
        if self._rb_crosshair_v is None:
            self._rb_crosshair_v = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
            self._rb_crosshair_v.setColor(QColor(255, 50, 50))
            self._rb_crosshair_v.setWidth(2)

        try:
            self._rb_crosshair_h.reset(QgsWkbTypes.LineGeometry)
            p_left = self._to_canvas_point(prof, east_f - size, north_f)
            p_right = self._to_canvas_point(prof, east_f + size, north_f)
            self._rb_crosshair_h.addPoint(p_left, False)
            self._rb_crosshair_h.addPoint(p_right, True)
            self._rb_crosshair_h.setVisible(True)
        except Exception:
            pass
        try:
            self._rb_crosshair_v.reset(QgsWkbTypes.LineGeometry)
            p_bottom = self._to_canvas_point(prof, east_f, north_f - size)
            p_top = self._to_canvas_point(prof, east_f, north_f + size)
            self._rb_crosshair_v.addPoint(p_bottom, False)
            self._rb_crosshair_v.addPoint(p_top, True)
            self._rb_crosshair_v.setVisible(True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Timeslice
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_slice_depth_label(label: str) -> Optional[float]:
        tok = str(label or "").strip().lower()
        if not tok:
            return None
        sign = -1.0 if tok.startswith("m") else 1.0
        if tok.startswith("m"):
            tok = tok[1:]
        if "_" in tok:
            tok = tok.replace("_", ".", 1)
        try:
            return sign * float(tok)
        except Exception:
            return None

    def _scan_slice_catalog(self, outdir: str, dz: float) -> list[dict]:
        base = str(outdir or "").strip()
        if not base or not os.path.isdir(base):
            return []

        paths = sorted(glob.glob(os.path.join(base, "slice_*.tif")))
        if not paths:
            paths = sorted(glob.glob(os.path.join(base, "slice_*.tiff")))
        if not paths:
            return []

        catalog = []
        centers = []
        for fallback_idx, path in enumerate(paths):
            name = os.path.basename(path)
            idx = fallback_idx
            z_center = None
            m = re.search(r"^slice_(\d+)_z([A-Za-z0-9_]+)\.tiff?$", name, flags=re.IGNORECASE)
            if m:
                try:
                    idx = int(m.group(1))
                except Exception:
                    idx = fallback_idx
                z_center = self._parse_slice_depth_label(m.group(2))
            if z_center is not None and np.isfinite(z_center):
                centers.append(float(z_center))
            catalog.append(
                {
                    "path": os.path.normpath(path),
                    "index": int(idx),
                    "z_center": float(z_center) if z_center is not None else None,
                    "name": os.path.splitext(name)[0],
                }
            )

        catalog.sort(key=lambda item: (int(item.get("index", 0)), str(item.get("path", ""))))

        dz_use = float(dz) if np.isfinite(dz) and float(dz) > 0.0 else 0.0
        if dz_use <= 0.0:
            z_vals = [
                float(item["z_center"])
                for item in catalog
                if item.get("z_center") is not None and np.isfinite(float(item.get("z_center")))
            ]
            if len(z_vals) >= 2:
                z_vals = sorted(z_vals)
                diffs = np.diff(np.asarray(z_vals, dtype=np.float64))
                diffs = diffs[np.isfinite(diffs) & (diffs > 1e-9)]
                if diffs.size > 0:
                    dz_use = float(np.nanmedian(diffs))
        if dz_use <= 0.0:
            dz_use = 0.10

        for i, item in enumerate(catalog):
            zc = item.get("z_center")
            if zc is None or not np.isfinite(float(zc)):
                z_top = float(i) * dz_use
                z_center = z_top + 0.5 * dz_use
            else:
                z_center = float(zc)
                z_top = z_center - 0.5 * dz_use
            item["z_top"] = float(z_top)
            item["z_bot"] = float(z_top + dz_use)
            item["z_center"] = float(z_center)
        return catalog

    def _refresh_slice_catalog(
        self,
        outdir: str,
        dz: float,
        reset_index: bool = False,
        redraw: bool = True,
    ):
        base = os.path.normpath(str(outdir or "").strip()) if str(outdir or "").strip() else ""
        catalog = self._scan_slice_catalog(base, dz)
        self._slice_catalog = list(catalog or [])
        self._slice_catalog_source_dir = base
        try:
            self._slice_catalog_dz = float(dz) if np.isfinite(float(dz)) else 0.0
        except Exception:
            self._slice_catalog_dz = 0.0
        if not self._slice_catalog:
            self._slice_current_idx = None
        else:
            if reset_index or self._slice_current_idx is None:
                self._slice_current_idx = 0
            else:
                self._slice_current_idx = int(
                    np.clip(int(self._slice_current_idx), 0, len(self._slice_catalog) - 1)
                )
        self._update_slice_navigator()
        if redraw:
            self._slice_view_xlim = None
            self._slice_view_ylim = None
            self._redraw_slice_view(force=True)

    def _on_slice_depth_slider(self, value: int):
        if self._updating_slice_nav:
            return
        if not self._slice_catalog:
            return
        idx = int(np.clip(int(value), 0, len(self._slice_catalog) - 1))
        if self._slice_current_idx == idx:
            return
        self._slice_current_idx = idx
        try:
            center = float(self._slice_catalog[idx].get("z_center"))
            if np.isfinite(center):
                self._cursor_z = center
        except Exception:
            pass
        self._update_slice_navigator()
        self._redraw_slice_view(force=True)
        self._update_timeslice_visibility_in_canvas()

    def _step_slice(self, delta: int):
        if not self._slice_catalog:
            return
        curr = int(self._slice_current_idx if self._slice_current_idx is not None else 0)
        nxt = int(np.clip(curr + int(delta), 0, len(self._slice_catalog) - 1))
        if self._slider_slice_depth is not None:
            self._slider_slice_depth.setValue(nxt)
        else:
            self._on_slice_depth_slider(nxt)

    def _update_slice_navigator(self):
        has_slider = hasattr(self, "_slider_slice_depth") and self._slider_slice_depth is not None
        if (not has_slider) or (not hasattr(self, "_lbl_slice_nav")):
            return
        if not self._slice_catalog:
            self._updating_slice_nav = True
            try:
                self._slider_slice_depth.setRange(0, 0)
                self._slider_slice_depth.setValue(0)
                self._slider_slice_depth.setEnabled(False)
            finally:
                self._updating_slice_nav = False
            self._safe_set_text(self._lbl_slice_nav, "\u2014 nessuna slice \u2014")
            if hasattr(self, "_lbl_slice_depth_top"):
                self._safe_set_text(self._lbl_slice_depth_top, "0.00 m")
            if hasattr(self, "_lbl_slice_depth_bottom"):
                self._safe_set_text(self._lbl_slice_depth_bottom, "\u2014")
            return

        idx = int(self._slice_current_idx if self._slice_current_idx is not None else 0)
        idx = int(np.clip(idx, 0, len(self._slice_catalog) - 1))
        self._slice_current_idx = idx
        n = int(len(self._slice_catalog))
        entry = self._slice_catalog[idx]
        z_top = float(entry.get("z_top", 0.0) or 0.0)
        z_bot = float(entry.get("z_bot", z_top) or z_top)

        self._updating_slice_nav = True
        try:
            self._slider_slice_depth.setRange(0, n - 1)
            self._slider_slice_depth.setValue(idx)
            self._slider_slice_depth.setEnabled(True)
        finally:
            self._updating_slice_nav = False

        self._safe_set_text(self._lbl_slice_nav, f"Slice {idx + 1}/{n}  |  {z_top:.2f}-{z_bot:.2f} m")
        if hasattr(self, "_lbl_slice_depth_top"):
            self._safe_set_text(self._lbl_slice_depth_top, "0.00 m")
        if hasattr(self, "_lbl_slice_depth_bottom"):
            z_max = float(self._slice_catalog[-1].get("z_bot", z_bot) or z_bot)
            self._safe_set_text(self._lbl_slice_depth_bottom, f"{z_max:.2f} m")

    def _on_timeslice_build_finished(self, ok: bool, payload: dict | None = None):
        if not bool(ok):
            return
        data = dict(payload or {})
        outdir = str(data.get("output_dir") or "").strip()
        if not outdir:
            outdir = str(getattr(self, "_le_slice_outdir", None).text() if hasattr(self, "_le_slice_outdir") else "").strip()
        if outdir and hasattr(self, "_le_slice_outdir") and self._le_slice_outdir is not None:
            try:
                self._le_slice_outdir.setText(os.path.normpath(outdir))
            except Exception:
                pass
        dz = data.get("z_step")
        if dz is None:
            try:
                dz = float(self._spin_slice_thickness.value())
            except Exception:
                dz = 0.10
        try:
            dz_f = float(dz)
        except Exception:
            dz_f = 0.10
        if hasattr(self, "_chk_slice_thickness_locked") and self._chk_slice_thickness_locked is not None:
            self._chk_slice_thickness_locked.setChecked(True)
        self._refresh_slice_catalog(outdir, dz=dz_f, reset_index=True, redraw=True)

    def _default_slice_output_dir(self) -> str:
        project_root = self._active_project_root()
        if project_root and os.path.isdir(project_root):
            return os.path.normpath(os.path.join(project_root, "timeslices_2d"))
        return ""

    def _choose_slice_outdir(self):
        start = str(getattr(self, "_le_slice_outdir", None).text() if hasattr(self, "_le_slice_outdir") else "").strip()
        if not start:
            start = self._default_slice_output_dir() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Output folder Timeslice", start)
        if folder:
            try:
                norm = os.path.normpath(folder)
                self._le_slice_outdir.setText(norm)
                try:
                    dz = float(self._spin_slice_thickness.value())
                except Exception:
                    dz = 0.10
                self._refresh_slice_catalog(norm, dz=dz, reset_index=True, redraw=True)
            except Exception:
                pass

    def _update_slice_profile_info(self):
        if not hasattr(self, "_lbl_slice_profiles") or self._lbl_slice_profiles is None:
            return
        n_prof = int(len(self._profiles or []))
        if n_prof <= 0:
            self._safe_set_text(self._lbl_slice_profiles, "Nessun profilo caricato")
            return
        total_traces = 0
        for prof in self._profiles:
            try:
                if int(getattr(prof, "n_channels", 0) or 0) <= 0:
                    continue
                total_traces += int(prof.channel(0).data.shape[1])
            except Exception:
                continue
        self._safe_set_text(self._lbl_slice_profiles, f"{n_prof} profili  |  ~{total_traces} tracce")

    def _import_slices_to_canvas(self):
        outdir = str(getattr(self, "_le_slice_outdir", None).text() if hasattr(self, "_le_slice_outdir") else "").strip()
        if not outdir:
            QMessageBox.warning(self, "Import Timeslice", "Seleziona prima la cartella output.")
            return
        if not os.path.isdir(outdir):
            QMessageBox.warning(self, "Import Timeslice", "Cartella output non valida.")
            return

        tifs = sorted(glob.glob(os.path.join(outdir, "*.tif"))) + sorted(glob.glob(os.path.join(outdir, "*.tiff")))
        if not tifs:
            QMessageBox.information(self, "Import Timeslice", "Nessun GeoTIFF trovato nella cartella output.")
            return

        root = QgsProject.instance().layerTreeRoot()
        existing = root.findGroup("GPR Timeslices")
        if existing is not None:
            try:
                root.removeChildNode(existing)
            except Exception:
                pass
        group = root.addGroup("GPR Timeslices")

        added = 0
        for tif in tifs:
            name = os.path.splitext(os.path.basename(tif))[0]
            rl = QgsRasterLayer(tif, name)
            if not rl.isValid():
                continue
            QgsProject.instance().addMapLayer(rl, False)
            group.addLayer(rl)
            added += 1

        for i, child in enumerate(group.children()):
            try:
                child.setItemVisibilityChecked(i == 0)
            except Exception:
                pass

        if self.iface is not None and self.iface.mapCanvas() is not None:
            try:
                self.iface.mapCanvas().refresh()
            except Exception:
                pass
        self._safe_set_text(
            self._lbl_status,
            f"Import completato: {added} layer nel gruppo 'GPR Timeslices'."
        )
        if added <= 0:
            QMessageBox.warning(self, "Import Timeslice", "Nessun layer valido importato.")
        else:
            try:
                dz = float(self._spin_slice_thickness.value())
            except Exception:
                dz = 0.10
            self._refresh_slice_catalog(outdir, dz=dz, reset_index=False, redraw=True)

    @staticmethod
    def _set_combo_to_data(combo: QComboBox, value) -> None:
        if combo is None:
            return
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _apply_selected_timeslice_preset(self):
        key = str(self._cb_slice_preset.currentData() or "base").strip().lower()
        self._apply_timeslice_preset(key)

    def _apply_timeslice_preset(self, preset_key: str):
        key = str(preset_key or "base").strip().lower()
        if key not in {"base", "quality", "aggressive"}:
            key = "base"

        if key == "quality":
            cfg = {
                "extraction_mode": "envelope",
                "use_hilbert": True,
                "use_processing": True,
                "pre_slice_bg_removal": True,
                "pre_slice_bg_mode": "line_by_line",
                "pre_slice_bg_auto": True,
                "pre_slice_bg_window": 200,
                "pre_slice_bg_sample_start": 10,
                "pre_slice_bg_sample_end": 0,
                "stack_n": 3,
                "stack_kernel": "triangular",
                "flip_traces_mode": "none",
                "topographic_correction": False,
                "topo_reference_mode": "median",
                "topo_reference_elevation": 0.0,
                "normalize_channels": False,
                "amplitude_filter": True,
                "amplitude_sigma": 3.0,
                "idw_mode": "quality",
                "idw_power": 2,
                "overlap_fraction": 0.5,
                "blanking_distance": 0.30,
                "use_anisotropic_idw": True,
                "auto_radius": True,
                "balance_profiles": True,
                "depth_radius_factor": 0.8,
                "min_points": 2,
                "parallel_profiles": True,
                "profile_workers": 0,
                "fill_nodata": True,
                "smooth": True,
                "smooth_sigma": 1.0,
            }
            lbl = "Preset timeslice: Qualita' applicato."
        elif key == "aggressive":
            cfg = {
                "extraction_mode": "envelope",
                "use_hilbert": True,
                "use_processing": True,
                "pre_slice_bg_removal": True,
                "pre_slice_bg_mode": "grid_by_grid",
                "pre_slice_bg_auto": False,
                "pre_slice_bg_window": 300,
                "pre_slice_bg_sample_start": 10,
                "pre_slice_bg_sample_end": 0,
                "stack_n": 5,
                "stack_kernel": "triangular",
                "flip_traces_mode": "none",
                "topographic_correction": False,
                "topo_reference_mode": "median",
                "topo_reference_elevation": 0.0,
                "normalize_channels": False,
                "amplitude_filter": True,
                "amplitude_sigma": 2.5,
                "idw_mode": "quality",
                "idw_power": 3,
                "overlap_fraction": 0.5,
                "blanking_distance": 0.30,
                "use_anisotropic_idw": True,
                "auto_radius": True,
                "balance_profiles": True,
                "depth_radius_factor": 1.0,
                "min_points": 3,
                "parallel_profiles": True,
                "profile_workers": 0,
                "fill_nodata": True,
                "smooth": True,
                "smooth_sigma": 1.5,
            }
            lbl = "Preset timeslice: Aggressivo applicato."
        else:
            cfg = {
                "extraction_mode": "las_like",
                "use_hilbert": True,
                "use_processing": True,
                "pre_slice_bg_removal": True,
                "pre_slice_bg_mode": "line_by_line",
                "pre_slice_bg_auto": True,
                "pre_slice_bg_window": 200,
                "pre_slice_bg_sample_start": 10,
                "pre_slice_bg_sample_end": 0,
                "stack_n": 1,
                "stack_kernel": "boxcar",
                "flip_traces_mode": "none",
                "topographic_correction": False,
                "topo_reference_mode": "median",
                "topo_reference_elevation": 0.0,
                "normalize_channels": False,
                "amplitude_filter": False,
                "amplitude_sigma": 3.0,
                "idw_mode": "quality",
                "idw_power": 2,
                "overlap_fraction": 0.5,
                "blanking_distance": 0.30,
                "use_anisotropic_idw": False,
                "auto_radius": True,
                "balance_profiles": True,
                "depth_radius_factor": 0.6,
                "min_points": 1,
                "parallel_profiles": True,
                "profile_workers": 0,
                "fill_nodata": True,
                "smooth": True,
                "smooth_sigma": 0.8,
            }
            lbl = "Preset timeslice: Base applicato."

        self._set_combo_to_data(self._cb_slice_extraction, cfg["extraction_mode"])
        self._chk_slice_use_hilbert.setChecked(bool(cfg.get("use_hilbert", True)))
        self._chk_slice_use_processing.setChecked(bool(cfg["use_processing"]))
        self._chk_slice_bg.setChecked(bool(cfg["pre_slice_bg_removal"]))
        self._set_combo_to_data(self._cb_slice_bg_mode, cfg["pre_slice_bg_mode"])
        self._chk_slice_bg_auto.setChecked(bool(cfg["pre_slice_bg_auto"]))
        self._spin_slice_bg_window.setValue(int(cfg["pre_slice_bg_window"]))
        self._spin_slice_bg_sample_start.setValue(int(cfg["pre_slice_bg_sample_start"]))
        self._spin_slice_bg_sample_end.setValue(int(cfg["pre_slice_bg_sample_end"]))
        self._spin_slice_stack_n.setValue(int(cfg["stack_n"]))
        self._set_combo_to_data(self._cb_slice_stack_kernel, cfg["stack_kernel"])
        self._set_combo_to_data(self._cb_slice_flip_mode, cfg["flip_traces_mode"])
        self._chk_slice_topographic.setChecked(bool(cfg["topographic_correction"]))
        self._set_combo_to_data(self._cb_slice_topo_ref_mode, cfg["topo_reference_mode"])
        self._spin_slice_topo_ref_custom.setValue(float(cfg["topo_reference_elevation"]))
        self._chk_normalize_ch.setChecked(bool(cfg["normalize_channels"]))
        self._chk_amplitude_filter.setChecked(bool(cfg["amplitude_filter"]))
        self._spin_amplitude_sigma.setValue(float(cfg["amplitude_sigma"]))
        self._set_combo_to_data(self._cb_slice_idw_mode, cfg["idw_mode"])
        self._spin_slice_idw_power.setValue(int(cfg.get("idw_power", 2)))
        self._spin_slice_overlap_pct.setValue(int(round(float(cfg.get("overlap_fraction", 0.5)) * 100.0)))
        self._spin_slice_blanking_m.setValue(float(cfg.get("blanking_distance", 0.30)))
        self._chk_anisotropic_idw.setChecked(bool(cfg["use_anisotropic_idw"]))
        self._chk_auto_radius.setChecked(bool(cfg["auto_radius"]))
        self._chk_slice_balance_profiles.setChecked(bool(cfg["balance_profiles"]))
        self._spin_slice_depth_radius_factor.setValue(float(cfg["depth_radius_factor"]))
        self._spin_slice_min_points.setValue(int(cfg["min_points"]))
        self._chk_slice_parallel_profiles.setChecked(bool(cfg["parallel_profiles"]))
        self._spin_slice_profile_workers.setValue(int(cfg["profile_workers"]))
        self._chk_fill_nodata.setChecked(bool(cfg["fill_nodata"]))
        self._chk_smooth.setChecked(bool(cfg["smooth"]))
        self._spin_smooth_sigma.setValue(float(cfg["smooth_sigma"]))

        # Sync dependent controls if state has not emitted toggles.
        self._spin_slice_bg_window.setEnabled(
            bool(self._chk_slice_bg.isChecked()) and (not bool(self._chk_slice_bg_auto.isChecked()))
        )
        self._cb_slice_bg_mode.setEnabled(bool(self._chk_slice_bg.isChecked()))
        self._chk_slice_bg_auto.setEnabled(bool(self._chk_slice_bg.isChecked()))
        self._spin_slice_bg_sample_start.setEnabled(bool(self._chk_slice_bg.isChecked()))
        self._spin_slice_bg_sample_end.setEnabled(bool(self._chk_slice_bg.isChecked()))
        topo_on = bool(self._chk_slice_topographic.isChecked())
        self._cb_slice_topo_ref_mode.setEnabled(topo_on)
        self._spin_slice_topo_ref_custom.setEnabled(
            topo_on and (str(self._cb_slice_topo_ref_mode.currentData() or "") == "custom")
        )
        self._spin_amplitude_sigma.setEnabled(bool(self._chk_amplitude_filter.isChecked()))
        self._spin_smooth_sigma.setEnabled(bool(self._chk_smooth.isChecked()))
        self._spin_slice_profile_workers.setEnabled(bool(self._chk_slice_parallel_profiles.isChecked()))
        self._safe_set_text(self._lbl_status, lbl)

    def get_slice_params(self) -> dict:
        slice_bg_auto = bool(self._chk_slice_bg_auto.isChecked())
        topo_mode = str(self._cb_slice_topo_ref_mode.currentData() or "median")
        topo_custom = None
        if topo_mode == "custom":
            topo_custom = float(self._spin_slice_topo_ref_custom.value())
        proc_params = dict(self._current_processing_params() or {})
        # For timeslice pre-processing, avoid surface over-saturation with conservative defaults
        # when the panel is still using broad generic values.
        if bool(self._chk_slice_use_processing.isChecked()):
            try:
                if int(proc_params.get("bg_sample_start", 0) or 0) <= 0:
                    proc_params["bg_sample_start"] = 10
            except Exception:
                proc_params["bg_sample_start"] = 10
            try:
                agc_on = bool(proc_params.get("agc", False))
                agc_win = int(proc_params.get("agc_win", 0) or 0)
                if agc_on and agc_win >= 96:
                    proc_params["agc_win"] = 32
            except Exception:
                pass
        return {
            "normalize_channels":  self._chk_normalize_ch.isChecked(),
            "extraction_mode":     str(self._cb_slice_extraction.currentData() or "las_like"),
            "use_hilbert":         self._chk_slice_use_hilbert.isChecked(),
            "use_processing":      self._chk_slice_use_processing.isChecked(),
            "pre_slice_bg_removal": self._chk_slice_bg.isChecked(),
            "pre_slice_bg_mode": str(self._cb_slice_bg_mode.currentData() or "line_by_line"),
            "pre_slice_bg_window": (0 if slice_bg_auto else int(self._spin_slice_bg_window.value())),
            "pre_slice_bg_sample_start": int(self._spin_slice_bg_sample_start.value()),
            "pre_slice_bg_sample_end": int(self._spin_slice_bg_sample_end.value()),
            "stack_n": int(self._spin_slice_stack_n.value()),
            "stack_kernel": str(self._cb_slice_stack_kernel.currentData() or "boxcar"),
            "flip_traces_mode": str(self._cb_slice_flip_mode.currentData() or "none"),
            "pipeline_params": proc_params,
            "topographic_correction": bool(self._chk_slice_topographic.isChecked()),
            "topo_reference_mode": topo_mode,
            "topo_reference_elevation": topo_custom,
            "amplitude_sigma":     (
                float(self._spin_amplitude_sigma.value())
                if self._chk_amplitude_filter.isChecked() else None
            ),
            "idw_mode":            str(self._cb_slice_idw_mode.currentData() or "quality"),
            "idw_power":           int(self._spin_slice_idw_power.value()),
            "overlap_fraction":    float(self._spin_slice_overlap_pct.value()) / 100.0,
            "blanking_distance":   float(self._spin_slice_blanking_m.value()),
            "use_anisotropic_idw": self._chk_anisotropic_idw.isChecked(),
            "auto_radius":         self._chk_auto_radius.isChecked(),
            "balance_profiles":    self._chk_slice_balance_profiles.isChecked(),
            "depth_radius_factor": float(self._spin_slice_depth_radius_factor.value()),
            "min_points":          int(self._spin_slice_min_points.value()),
            "parallel_profiles":   self._chk_slice_parallel_profiles.isChecked(),
            "profile_workers":     int(self._spin_slice_profile_workers.value()),
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
        try:
            dz = float(self._spin_slice_thickness.value())
        except Exception:
            dz = 0.10
        if (not np.isfinite(dz)) or dz <= 0.0:
            QMessageBox.warning(self, "Timeslice", "Spessore slice non valido.")
            return
        slice_params = self.get_slice_params()
        slice_params["z_step_override"] = float(dz)
        slice_params["thickness_m"] = float(dz)
        outdir = str(getattr(self, "_le_slice_outdir", None).text() if hasattr(self, "_le_slice_outdir") else "").strip()
        if outdir:
            slice_params["output_dir"] = outdir
        if self.plugin and hasattr(self.plugin, "import_ogpr_as_slices"):
            try:
                self.plugin.import_ogpr_as_slices(
                    self._profiles,
                    slice_params=slice_params,
                    completion_callback=self._on_timeslice_build_finished,
                )
            except TypeError:
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
                pipeline_params=extra.get("pipeline_params"),
                normalize_channels=bool(extra.get("normalize_channels", False)),
                extraction_mode=str(extra.get("extraction_mode", "las_like") or "las_like"),
                use_hilbert=bool(extra.get("use_hilbert", True)),
                use_processing=bool(extra.get("use_processing", False)),
                amplitude_sigma=extra.get("amplitude_sigma"),
                use_anisotropic_idw=bool(extra.get("use_anisotropic_idw", False)),
                idw_mode=str(extra.get("idw_mode", "quality") or "quality"),
                idw_power=float(extra.get("idw_power", 2.0) or 2.0),
                auto_radius=bool(extra.get("auto_radius", True)),
                min_points=int(extra.get("min_points", 1) or 1),
                fill_nodata=bool(extra.get("fill_nodata", True)),
                overlap_fraction=float(extra.get("overlap_fraction", 0.5) or 0.0),
                blanking_distance=float(extra.get("blanking_distance", 0.0) or 0.0),
                smooth_sigma=float(extra.get("smooth_sigma", 0.8) or 0.0),
                depth_radius_factor=float(extra.get("depth_radius_factor", 0.6) or 0.0),
                balance_profiles=bool(extra.get("balance_profiles", True)),
                pre_slice_bg_removal=bool(extra.get("pre_slice_bg_removal", False)),
                pre_slice_bg_mode=str(extra.get("pre_slice_bg_mode", "line_by_line") or "line_by_line"),
                pre_slice_bg_window=int(extra.get("pre_slice_bg_window", 0) or 0),
                pre_slice_bg_sample_start=int(extra.get("pre_slice_bg_sample_start", 0) or 0),
                pre_slice_bg_sample_end=int(extra.get("pre_slice_bg_sample_end", 0) or 0),
                stack_n=int(extra.get("stack_n", 1) or 1),
                stack_kernel=str(extra.get("stack_kernel", "boxcar") or "boxcar"),
                flip_traces_mode=str(extra.get("flip_traces_mode", "none") or "none"),
                topographic_correction=bool(extra.get("topographic_correction", False)),
                topo_reference_mode=str(extra.get("topo_reference_mode", "median") or "median"),
                topo_reference_elevation=extra.get("topo_reference_elevation"),
                parallel_profiles=bool(extra.get("parallel_profiles", True)),
                profile_workers=int(extra.get("profile_workers", 0) or 0),
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
            "channel": int(params["channel"]),
            "use_processing": bool(extra.get("use_processing", False)),
            "extraction_mode": str(extra.get("extraction_mode", "las_like") or "las_like"),
            "pipeline_params": dict(extra.get("pipeline_params") or {}),
        })

        if self._gpr_3d_viewer is not None:
            try:
                self.cursor_moved.disconnect(self._gpr_3d_viewer.update_cursor_position)
            except Exception:
                pass
            try:
                self._gpr_3d_viewer.depth_changed.disconnect(self._on_3d_depth_changed)
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
            import traceback
            tb = traceback.format_exc(limit=8)
            QMessageBox.warning(
                self,
                "Viewer 3D",
                f"Impossibile aprire il viewer 3D:\n{exc}\n\n{tb}",
            )
            return

        self._gpr_3d_viewer = viewer
        try:
            self.cursor_moved.connect(viewer.update_cursor_position)
        except Exception:
            pass
        try:
            viewer.depth_changed.connect(self._on_3d_depth_changed)
        except Exception:
            pass
        self._emit_cursor_moved()
        viewer.show()
        viewer.raise_()
        viewer.activateWindow()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def request_close(self):
        """Force real close/destruction (used by plugin unload)."""
        self._allow_close = True
        try:
            self.close()
        finally:
            self._allow_close = False

    def _close_3d_viewer(self):
        if self._gpr_3d_viewer is None:
            return
        try:
            self.cursor_moved.disconnect(self._gpr_3d_viewer.update_cursor_position)
        except Exception:
            pass
        try:
            self._gpr_3d_viewer.depth_changed.disconnect(self._on_3d_depth_changed)
        except Exception:
            pass
        try:
            self._gpr_3d_viewer.close()
        except Exception:
            pass
        self._gpr_3d_viewer = None

    def closeEvent(self, event):
        # Default UX: closing hides the window to preserve child widgets/docks.
        # This avoids stale Python refs to deleted Qt C++ objects on next reopen.
        if not bool(getattr(self, "_allow_close", False)):
            try:
                if self._processing_dock is not None:
                    self._save_dock_state(self._processing_dock, "processing")
                if self._timeslice_dock is not None:
                    self._save_dock_state(self._timeslice_dock, "timeslice")
            except Exception:
                pass
            try:
                self._close_3d_viewer()
            except Exception:
                pass
            self.hide()
            event.ignore()
            return

        # Real close path (plugin unload).
        self._close_3d_viewer()
        if self._processing_dock is not None:
            try:
                self._save_dock_state(self._processing_dock, "processing")
            except Exception:
                pass
            try:
                self.removeDockWidget(self._processing_dock)
            except Exception:
                pass
            try:
                self._processing_dock.deleteLater()
            except Exception:
                pass
            self._processing_dock = None
        if self._timeslice_dock is not None:
            try:
                self._save_dock_state(self._timeslice_dock, "timeslice")
            except Exception:
                pass
            try:
                self.removeDockWidget(self._timeslice_dock)
            except Exception:
                pass
            try:
                self._timeslice_dock.deleteLater()
            except Exception:
                pass
            self._timeslice_dock = None
        for rb in (self._rb_point, self._rb_line, self._rb_crosshair_h, self._rb_crosshair_v):
            if rb is not None:
                try:
                    rb.reset()
                except Exception:
                    pass
        super().closeEvent(event)


