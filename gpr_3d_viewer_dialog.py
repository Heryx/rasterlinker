# -*- coding: utf-8 -*-
"""Standalone 3D viewer dialog for OGPR volumes."""

from __future__ import annotations

import importlib
import os
import sys
import numpy as np

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
    from matplotlib.figure import Figure

    _HAS_MPL = True
except Exception:  # pragma: no cover - optional dependency
    FigureCanvasQTAgg = None
    Figure = None
    _HAS_MPL = False

pv = None
QtInteractor = None
_HAS_PYVISTA = False
_HAS_QTINTERACTOR = False
_PYVISTA_IMPORT_ERROR = None
_QTINTERACTOR_IMPORT_ERROR = None
_PYVISTA_BOOTSTRAP_DIAG = ""
_PYVISTA_BOOTSTRAPPED = False


def _candidate_site_packages() -> list[str]:
    """Best-effort site-packages candidates for OSGeo4W/QGIS runtime."""
    ver_tag = f"Python{sys.version_info.major}{sys.version_info.minor}"
    roots = set()
    for key in ("OSGEO4W_ROOT", "QGIS_PREFIX_PATH"):
        val = (os.environ.get(key) or "").strip()
        if val:
            roots.add(val)
    roots.update(
        {
            sys.prefix,
            sys.exec_prefix,
            sys.base_prefix,
            os.path.dirname(sys.executable),
        }
    )

    candidates = []
    for root in list(roots):
        if not root:
            continue
        root = os.path.abspath(root)
        parent = os.path.dirname(root)
        candidates.extend(
            [
                os.path.join(root, "Lib", "site-packages"),
                os.path.join(root, "apps", ver_tag, "Lib", "site-packages"),
                os.path.join(parent, "apps", ver_tag, "Lib", "site-packages"),
            ]
        )
    out = []
    seen = set()
    for c in candidates:
        c = os.path.abspath(c)
        if c in seen:
            continue
        seen.add(c)
        if os.path.isdir(c):
            out.append(c)
    return out


def _bootstrap_pyvista_runtime() -> bool:
    """Import pyvista/pyvistaqt, trying path fallback and storing diagnostics."""
    global pv, QtInteractor
    global _HAS_PYVISTA, _HAS_QTINTERACTOR
    global _PYVISTA_IMPORT_ERROR, _QTINTERACTOR_IMPORT_ERROR
    global _PYVISTA_BOOTSTRAP_DIAG, _PYVISTA_BOOTSTRAPPED

    if _PYVISTA_BOOTSTRAPPED:
        return bool(_HAS_PYVISTA and _HAS_QTINTERACTOR)

    attempted_paths = []

    def _try_import():
        try:
            mod = importlib.import_module("pyvista")
        except Exception as exc:
            return False, ("pyvista", exc)
        try:
            # Force QtPy to use PyQt5 under QGIS runtime.
            if not os.environ.get("QT_API"):
                os.environ["QT_API"] = "pyqt5"
            mod_qt = importlib.import_module("pyvistaqt")
            qt_interactor = getattr(mod_qt, "QtInteractor", None)
            if qt_interactor is None:
                raise ImportError("pyvistaqt imported but QtInteractor is missing")
        except Exception as exc:
            return False, ("pyvistaqt", exc, mod)
        return True, (mod, qt_interactor)

    ok, result = _try_import()
    if not ok:
        for p in _candidate_site_packages():
            if p not in sys.path:
                sys.path.append(p)
                attempted_paths.append(p)
        ok, result = _try_import()

    if ok:
        pv, qt_interactor = result
        QtInteractor = qt_interactor
        _HAS_PYVISTA = True
        _HAS_QTINTERACTOR = True
        _PYVISTA_IMPORT_ERROR = None
        _QTINTERACTOR_IMPORT_ERROR = None
        _PYVISTA_BOOTSTRAPPED = True
        _PYVISTA_BOOTSTRAP_DIAG = (
            f"Python={sys.executable}\n"
            f"sys.prefix={sys.prefix}\n"
            f"added_site_packages={attempted_paths}"
        )
        return True

    _HAS_PYVISTA = False
    _HAS_QTINTERACTOR = False
    if isinstance(result, tuple) and len(result) >= 2 and result[0] == "pyvistaqt":
        _QTINTERACTOR_IMPORT_ERROR = result[1]
        _PYVISTA_IMPORT_ERROR = None
    else:
        _PYVISTA_IMPORT_ERROR = result[1] if isinstance(result, tuple) and len(result) >= 2 else result
    _PYVISTA_BOOTSTRAPPED = True
    _PYVISTA_BOOTSTRAP_DIAG = (
        f"Python={sys.executable}\n"
        f"sys.prefix={sys.prefix}\n"
        f"added_site_packages={attempted_paths}\n"
        f"sys.path_tail={sys.path[-6:]}"
    )
    return False


class Gpr3dViewerDialog(QDialog):
    """Interactive 3D volume viewer based on pyvistaqt in a QDialog."""
    depth_changed = pyqtSignal(float)

    def __init__(self, volume, meta, profiles=None, grids=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("GPR 3D Volume Viewer")
        self.resize(1280, 800)

        _bootstrap_pyvista_runtime()
        if not _HAS_PYVISTA:
            raise ImportError(
                "pyvista non disponibile nel runtime QGIS.\n"
                "Dettaglio: "
                f"{_PYVISTA_IMPORT_ERROR}\n\n"
                "Installa nel Python usato da QGIS (non in un env diverso):\n"
                "python -m pip install pyvista pyvistaqt\n\n"
                f"{_PYVISTA_BOOTSTRAP_DIAG}"
            ) from _PYVISTA_IMPORT_ERROR
        if not _HAS_QTINTERACTOR:
            raise ImportError(
                "pyvistaqt / QtInteractor non disponibile nel runtime QGIS.\n"
                "Dettaglio: "
                f"{_QTINTERACTOR_IMPORT_ERROR}\n\n"
                "Installa nel Python usato da QGIS:\n"
                "python -m pip install pyvista pyvistaqt\n\n"
                f"{_PYVISTA_BOOTSTRAP_DIAG}"
            ) from _QTINTERACTOR_IMPORT_ERROR

        self._volume = np.asarray(volume, dtype=np.float32)
        if self._volume.ndim != 3:
            raise ValueError("volume must be 3D with shape (n_z, n_y, n_x)")

        self._meta = dict(meta or {})
        self._profiles = list(profiles or [])
        self._grids = list(grids or [])

        self._volume_grid = None
        self._spatial = {}
        self._z_axis = np.zeros(0, dtype=np.float32)
        self._data_min = 0.0
        self._data_max = 1.0
        self._amp_min = 0.0
        self._amp_max = 1.0
        self._clip_min = 0.0
        self._clip_max = 1.0
        self._hist_values = np.zeros(0, dtype=np.float32)
        self._finite_voxels = 0
        self._total_voxels = 0
        self._updating_controls = False
        self._render_in_progress = False
        self._suppress_depth_emit = False
        self._pick_enabled = False
        self._disp_origin_x = 0.0
        self._disp_origin_y = 0.0
        self._section_cmap = "RdBu_r"
        self._profile_color_mode = "solid_yellow"
        self._hist_figure = None
        self._hist_ax = None
        self._hist_canvas = None

        self._volume_actor_name = "volume_actor"
        self._volume_outline_name = "volume_outline"
        self._section_actor_name = "section_actor"
        self._iso_actor_name = "iso_actor"
        self._cursor_slice_name = "cursor_slice"
        self._cursor_marker_name = "cursor_marker"
        self._profile_name_prefix = "profile_line_"
        self._profile_curtain_prefix = "profile_curtain_"
        self._profile_actor_count = 0
        self._profile_curtain_cache = {}

        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal, self)
        root.addWidget(splitter)

        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._plotter = QtInteractor(left)
        if hasattr(self._plotter, "interactor"):
            left_layout.addWidget(self._plotter.interactor)
        else:
            left_layout.addWidget(self._plotter)

        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 6, 6, 6)

        form = QFormLayout()

        self._section_combo = QComboBox()
        self._section_combo.addItem("C-scan (horizontal)", "cscan")
        self._section_combo.addItem("B-scan inline (Y)", "inline")
        self._section_combo.addItem("B-scan crossline (X)", "crossline")
        self._section_combo.currentIndexChanged.connect(self._on_section_mode_changed)
        form.addRow("Section:", self._section_combo)

        slice_row = QWidget()
        slice_row_l = QHBoxLayout(slice_row)
        slice_row_l.setContentsMargins(0, 0, 0, 0)
        self._slice_slider = QSlider(Qt.Horizontal)
        self._slice_slider.setTracking(True)
        self._slice_slider.valueChanged.connect(self._on_slice_changed)
        self._slice_spin = QSpinBox()
        self._slice_spin.setRange(0, 0)
        self._slice_spin.setMinimumWidth(72)
        self._slice_spin.valueChanged.connect(self._on_slice_spin_changed)
        self._lbl_slice = QLabel("-")
        self._lbl_slice.setMinimumWidth(160)
        slice_row_l.addWidget(self._slice_slider, 1)
        slice_row_l.addWidget(self._slice_spin, 0)
        slice_row_l.addWidget(self._lbl_slice, 0)
        form.addRow("Slice index:", slice_row)

        self._cmap_combo = QComboBox()
        self._cmap_combo.addItems(
            [
                "RdBu_r",
                "seismic",
                "gray",
                "viridis",
                "plasma",
                "magma",
                "turbo",
                "Spectral_r",
            ]
        )
        self._cmap_combo.setCurrentText(self._section_cmap)
        self._cmap_combo.currentTextChanged.connect(self._on_cmap_changed)
        form.addRow("Timeslice cmap:", self._cmap_combo)

        self._profile_color_combo = QComboBox()
        self._profile_color_combo.addItem("Solid Yellow", "solid_yellow")
        self._profile_color_combo.addItem("Solid Cyan", "solid_cyan")
        self._profile_color_combo.addItem("Solid White", "solid_white")
        self._profile_color_combo.addItem("Palette Turbo", "palette_turbo")
        self._profile_color_combo.addItem("Palette Viridis", "palette_viridis")
        self._profile_color_combo.addItem("Palette Plasma", "palette_plasma")
        self._profile_color_combo.currentIndexChanged.connect(self._on_profile_color_changed)
        form.addRow("Profiles color:", self._profile_color_combo)

        clip_row = QWidget()
        clip_row_l = QHBoxLayout(clip_row)
        clip_row_l.setContentsMargins(0, 0, 0, 0)
        self._clip_min_spin = QDoubleSpinBox()
        self._clip_min_spin.setDecimals(6)
        self._clip_min_spin.setSingleStep(0.01)
        self._clip_min_spin.valueChanged.connect(self._on_clip_spin_changed)
        self._clip_max_spin = QDoubleSpinBox()
        self._clip_max_spin.setDecimals(6)
        self._clip_max_spin.setSingleStep(0.01)
        self._clip_max_spin.valueChanged.connect(self._on_clip_spin_changed)
        self._btn_clip_auto = QPushButton("Auto")
        self._btn_clip_auto.clicked.connect(self._on_clip_auto)
        clip_row_l.addWidget(self._clip_min_spin, 1)
        clip_row_l.addWidget(self._clip_max_spin, 1)
        clip_row_l.addWidget(self._btn_clip_auto, 0)
        form.addRow("Amplitude clip:", clip_row)

        thr_row = QWidget()
        thr_row_l = QHBoxLayout(thr_row)
        thr_row_l.setContentsMargins(0, 0, 0, 0)
        self._threshold_slider = QSlider(Qt.Horizontal)
        self._threshold_slider.setTracking(False)
        self._threshold_slider.setRange(0, 1000)
        self._threshold_slider.valueChanged.connect(self._on_threshold_slider_changed)
        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setDecimals(5)
        self._threshold_spin.setSingleStep(0.01)
        self._threshold_spin.valueChanged.connect(self._on_threshold_spin_changed)
        thr_row_l.addWidget(self._threshold_slider, 1)
        thr_row_l.addWidget(self._threshold_spin, 0)
        form.addRow("Amplitude threshold:", thr_row)

        self._threshold_mode_combo = QComboBox()
        self._threshold_mode_combo.addItem("Above", "above")
        self._threshold_mode_combo.addItem("Absolute", "abs")
        self._threshold_mode_combo.addItem("Below", "below")
        self._threshold_mode_combo.currentIndexChanged.connect(self._on_threshold_mode_changed)
        form.addRow("Threshold mode:", self._threshold_mode_combo)

        self._iso_extract_combo = QComboBox()
        self._iso_extract_combo.addItem("Surface only", "surface")
        self._iso_extract_combo.addItem("All voxels", "all")
        self._iso_extract_combo.currentIndexChanged.connect(self._on_iso_extract_mode_changed)
        form.addRow("Iso extraction:", self._iso_extract_combo)

        self._opacity_spin = QDoubleSpinBox()
        self._opacity_spin.setDecimals(2)
        self._opacity_spin.setRange(0.05, 1.00)
        self._opacity_spin.setSingleStep(0.05)
        self._opacity_spin.setValue(0.85)
        self._opacity_spin.valueChanged.connect(self._on_opacity_changed)
        form.addRow("Volume opacity:", self._opacity_spin)

        self._z_scale_spin = QDoubleSpinBox()
        self._z_scale_spin.setDecimals(2)
        self._z_scale_spin.setRange(0.25, 20.0)
        self._z_scale_spin.setSingleStep(0.25)
        self._z_scale_spin.setValue(1.0)
        self._z_scale_spin.valueChanged.connect(self._on_z_scale_changed)
        form.addRow("Z exaggeration:", self._z_scale_spin)

        self._chk_show_volume = QCheckBox()
        self._chk_show_volume.setChecked(False)
        self._chk_show_volume.toggled.connect(self._on_show_volume_toggled)
        form.addRow("Show volume:", self._chk_show_volume)

        self._chk_show_iso = QCheckBox()
        self._chk_show_iso.setChecked(False)
        self._chk_show_iso.toggled.connect(self._on_show_iso_toggled)
        form.addRow("Show isosurface:", self._chk_show_iso)

        self._chk_show_profiles = QCheckBox()
        self._chk_show_profiles.setChecked(True)
        self._chk_show_profiles.toggled.connect(self._on_show_profiles_toggled)
        form.addRow("Show profiles:", self._chk_show_profiles)

        self._chk_parallel_proj = QCheckBox()
        self._chk_parallel_proj.setChecked(True)
        self._chk_parallel_proj.toggled.connect(self._on_projection_toggled)
        form.addRow("Parallel view:", self._chk_parallel_proj)

        right_layout.addLayout(form)
        if _HAS_MPL:
            self._hist_figure = Figure(figsize=(3.2, 1.6), tight_layout=True)
            self._hist_ax = self._hist_figure.add_subplot(111)
            self._hist_canvas = FigureCanvasQTAgg(self._hist_figure)
            self._hist_canvas.setMinimumHeight(165)
            self._hist_canvas.setToolTip(
                "Taglio ampiezza:\n"
                "click sinistro = imposta clip minimo\n"
                "click destro = imposta clip massimo"
            )
            try:
                self._hist_canvas.mpl_connect("button_press_event", self._on_hist_click)
            except Exception:
                pass
            right_layout.addWidget(self._hist_canvas)
        else:
            lbl_hist = QLabel("Istogramma non disponibile (matplotlib mancante).")
            lbl_hist.setWordWrap(True)
            right_layout.addWidget(lbl_hist)

        btn_row = QHBoxLayout()
        self._btn_export_las = QPushButton("Export LAS")
        self._btn_export_las.clicked.connect(self._on_export_las)
        self._btn_export_vti = QPushButton("Export .vti")
        self._btn_export_vti.clicked.connect(self._on_export_vti)
        self._btn_export_npz = QPushButton("Export .npz")
        self._btn_export_npz.clicked.connect(self._on_export_npz)
        self._btn_load = QPushButton("Load .vti/.npz")
        self._btn_load.clicked.connect(self._on_load_volume)
        self._btn_reset_camera = QPushButton("Reset Camera")
        self._btn_reset_camera.clicked.connect(self._on_reset_camera)
        btn_row.addWidget(self._btn_export_las)
        btn_row.addWidget(self._btn_export_vti)
        btn_row.addWidget(self._btn_export_npz)
        right_layout.addLayout(btn_row)

        cam_row = QHBoxLayout()
        self._btn_view_top = QPushButton("Top")
        self._btn_view_inline = QPushButton("Inline")
        self._btn_view_cross = QPushButton("Crossline")
        self._btn_view_top.clicked.connect(lambda: self._apply_camera_for_mode("cscan"))
        self._btn_view_inline.clicked.connect(lambda: self._apply_camera_for_mode("inline"))
        self._btn_view_cross.clicked.connect(lambda: self._apply_camera_for_mode("crossline"))
        cam_row.addWidget(self._btn_view_top)
        cam_row.addWidget(self._btn_view_inline)
        cam_row.addWidget(self._btn_view_cross)
        right_layout.addLayout(cam_row)

        right_layout.addWidget(self._btn_reset_camera)
        right_layout.addWidget(self._btn_load)

        self._lbl_cursor = QLabel("Cursor: -")
        self._lbl_cursor.setWordWrap(True)
        right_layout.addWidget(self._lbl_cursor)

        self._lbl_pick = QLabel("Pick: click in 3D view to inspect voxel")
        self._lbl_pick.setWordWrap(True)
        right_layout.addWidget(self._lbl_pick)

        self._status = QLabel("Volume loaded")
        self._status.setWordWrap(True)
        right_layout.addWidget(self._status)
        right_layout.addStretch(1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([980, 300])

        self._replace_volume(self._volume, self._meta, reset_camera=True)

    def _grid_spatial_info(self, vol: np.ndarray, meta: dict) -> dict:
        n_z, n_y, n_x = [int(v) for v in vol.shape]
        res = float(meta.get("resolution", 1.0) or 1.0)
        if not np.isfinite(res) or res <= 0:
            res = 1.0
        z_step = float(meta.get("z_step", meta.get("z_resolution", res)) or res)
        if not np.isfinite(z_step) or z_step <= 0:
            z_step = max(res, 1e-6)
        x_min = float(meta.get("x_min", 0.0) or 0.0)
        y_min = float(meta.get("y_min", 0.0) or 0.0)
        z_min = float(meta.get("z_min", 0.0) or 0.0)
        z_max = float(meta.get("z_max", z_min + z_step * max(n_z - 1, 1)) or 0.0)
        if z_max < z_min:
            z_min, z_max = z_max, z_min
        return {
            "n_x": n_x,
            "n_y": n_y,
            "n_z": n_z,
            "res": res,
            "z_step": z_step,
            "x_min": x_min,
            "y_min": y_min,
            "z_min": z_min,
            "z_max": z_max,
        }

    def _depth_axis(self, info: dict, meta: dict) -> np.ndarray:
        n_z = int(info["n_z"])
        z_levels = meta.get("z_levels")
        if isinstance(z_levels, (list, tuple, np.ndarray)):
            try:
                arr = np.asarray(z_levels, dtype=np.float32)
                if arr.size == n_z and np.isfinite(arr).all():
                    return arr
            except Exception:
                pass
        if n_z <= 1:
            return np.asarray([float(info["z_min"])], dtype=np.float32)
        return np.linspace(
            float(info["z_min"]),
            float(info["z_max"]),
            n_z,
            dtype=np.float32,
        )

    def _depth_at_index(self, idx: int) -> float:
        if self._z_axis.size <= 0:
            return 0.0
        i = int(np.clip(idx, 0, self._z_axis.size - 1))
        return float(self._z_axis[i])

    def _x_at_index(self, idx: int) -> float:
        n_x = int(self._spatial.get("n_x", 1))
        i = int(np.clip(idx, 0, max(0, n_x - 1)))
        return i * float(self._spatial.get("res", 1.0))

    def _y_at_index(self, idx: int) -> float:
        n_y = int(self._spatial.get("n_y", 1))
        i = int(np.clip(idx, 0, max(0, n_y - 1)))
        return i * float(self._spatial.get("res", 1.0))

    def _center_point_3d(self) -> tuple[float, float, float]:
        x = self._x_at_index(max(0, int(self._spatial.get("n_x", 1) // 2)))
        y = self._y_at_index(max(0, int(self._spatial.get("n_y", 1) // 2)))
        z = -self._depth_at_index(max(0, int(self._spatial.get("n_z", 1) // 2)))
        return x, y, z

    def _world_to_local_xy(self, east: float, north: float) -> tuple[float, float]:
        return float(east) - float(self._disp_origin_x), float(north) - float(self._disp_origin_y)

    def _local_to_world_xy(self, x_local: float, y_local: float) -> tuple[float, float]:
        return float(x_local) + float(self._disp_origin_x), float(y_local) + float(self._disp_origin_y)

    def _slider_to_threshold(self, slider_value: int) -> float:
        if self._amp_max <= self._amp_min:
            return float(self._amp_min)
        ratio = float(np.clip(slider_value, 0, 1000)) / 1000.0
        return float(self._amp_min + ratio * (self._amp_max - self._amp_min))

    def _threshold_to_slider(self, threshold: float) -> int:
        if self._amp_max <= self._amp_min:
            return 0
        ratio = (float(threshold) - self._amp_min) / (self._amp_max - self._amp_min)
        return int(round(float(np.clip(ratio, 0.0, 1.0)) * 1000.0))

    def _effective_clim(self) -> tuple[float, float]:
        lo = float(self._clip_min)
        hi = float(self._clip_max)
        if not np.isfinite(lo):
            lo = float(self._amp_min)
        if not np.isfinite(hi):
            hi = float(self._amp_max)
        if hi <= lo:
            hi = lo + 1e-6
        return lo, hi

    def _set_clip_controls(self):
        lo_bound = float(self._data_min)
        hi_bound = float(self._data_max)
        if not (np.isfinite(lo_bound) and np.isfinite(hi_bound) and hi_bound > lo_bound):
            lo_bound, hi_bound = float(self._amp_min), float(self._amp_max)
        if hi_bound <= lo_bound:
            hi_bound = lo_bound + 1e-6

        self._updating_controls = True
        try:
            self._clip_min_spin.setRange(lo_bound, hi_bound)
            self._clip_max_spin.setRange(lo_bound, hi_bound)
            self._clip_min_spin.setValue(float(np.clip(self._clip_min, lo_bound, hi_bound)))
            self._clip_max_spin.setValue(float(np.clip(self._clip_max, lo_bound, hi_bound)))
        finally:
            self._updating_controls = False
        self._clip_min = float(self._clip_min_spin.value())
        self._clip_max = float(self._clip_max_spin.value())

    def _refresh_histogram(self):
        if (not _HAS_MPL) or self._hist_ax is None or self._hist_canvas is None:
            return
        ax = self._hist_ax
        ax.clear()
        vals = np.asarray(self._hist_values, dtype=np.float64)
        if vals.size > 0:
            finite = vals[np.isfinite(vals)]
        else:
            finite = np.zeros(0, dtype=np.float64)
        if finite.size <= 0:
            ax.text(0.5, 0.5, "No amplitude data", ha="center", va="center", transform=ax.transAxes)
            ax.set_xticks([])
            ax.set_yticks([])
        else:
            bins = int(np.clip(np.sqrt(float(finite.size)), 40, 120))
            hist, edges = np.histogram(finite, bins=bins)
            centers = 0.5 * (edges[:-1] + edges[1:])
            ax.plot(centers, hist, color="#3d7ab5", lw=1.2)
            ax.fill_between(centers, hist, color="#3d7ab5", alpha=0.18)
            lo, hi = self._effective_clim()
            ax.axvline(lo, color="#f39c12", lw=1.5, label="clip min")
            ax.axvline(hi, color="#d63031", lw=1.5, label="clip max")
            ax.set_xlim(float(edges[0]), float(edges[-1]))
            ax.set_title("Amplitude clip")
            ax.set_xlabel("Amplitude")
            ax.set_ylabel("Count")
        try:
            self._hist_canvas.draw_idle()
        except Exception:
            pass

    def _on_hist_click(self, event):
        if event is None or event.xdata is None:
            return
        if self._hist_ax is None or event.inaxes != self._hist_ax:
            return
        x = float(event.xdata)
        lo_bound = float(self._clip_min_spin.minimum())
        hi_bound = float(self._clip_max_spin.maximum())
        if int(getattr(event, "button", 0) or 0) == 1:
            x = float(np.clip(x, lo_bound, self._clip_max - 1e-6))
            self._clip_min_spin.setValue(x)
        elif int(getattr(event, "button", 0) or 0) == 3:
            x = float(np.clip(x, self._clip_min + 1e-6, hi_bound))
            self._clip_max_spin.setValue(x)

    def _on_clip_auto(self):
        self._updating_controls = True
        try:
            self._clip_min_spin.setValue(float(self._amp_min))
            self._clip_max_spin.setValue(float(self._amp_max))
        finally:
            self._updating_controls = False
        self._clip_min = float(self._clip_min_spin.value())
        self._clip_max = float(self._clip_max_spin.value())
        self._refresh_histogram()
        if not self._update_volume_actor_clim_fast():
            self._draw_volume_actor()
        self._refresh_section_plane()
        self._refresh_isosurface()
        self._render_now()

    def _on_clip_spin_changed(self, _value: float):
        if self._updating_controls:
            return
        lo = float(self._clip_min_spin.value())
        hi = float(self._clip_max_spin.value())
        if hi <= lo:
            if self.sender() is self._clip_min_spin:
                hi = lo + 1e-6
                self._updating_controls = True
                try:
                    self._clip_max_spin.setValue(hi)
                finally:
                    self._updating_controls = False
            else:
                lo = hi - 1e-6
                self._updating_controls = True
                try:
                    self._clip_min_spin.setValue(lo)
                finally:
                    self._updating_controls = False
        self._clip_min = float(self._clip_min_spin.value())
        self._clip_max = float(self._clip_max_spin.value())
        self._refresh_histogram()
        if not self._update_volume_actor_clim_fast():
            self._draw_volume_actor()
        self._refresh_section_plane()
        self._refresh_isosurface()
        self._render_now()

    def _on_cmap_changed(self, text: str):
        self._section_cmap = str(text or "RdBu_r")
        self._draw_volume_actor()
        self._refresh_section_plane()
        self._refresh_isosurface()
        self._render_now()

    def _on_profile_color_changed(self, _idx: int):
        self._profile_color_mode = str(self._profile_color_combo.currentData() or "solid_yellow")
        self._load_profiles(self._profiles)
        self._render_now()

    def _palette_color(self, palette_name: str, ratio: float) -> tuple[float, float, float]:
        r = float(np.clip(ratio, 0.0, 1.0))
        try:
            import matplotlib.cm as cm  # pragma: no cover - optional dependency

            rgba = cm.get_cmap(palette_name)(r)
            return float(rgba[0]), float(rgba[1]), float(rgba[2])
        except Exception:
            # Fallback gradient if matplotlib colormap is unavailable.
            return r, 1.0 - abs(2.0 * r - 1.0), 1.0 - r

    def _profile_line_color(self, idx: int, total: int):
        mode = str(self._profile_color_mode or "solid_yellow")
        if mode == "solid_cyan":
            return "cyan"
        if mode == "solid_white":
            return "white"
        if mode == "palette_turbo":
            return self._palette_color("turbo", idx / max(total - 1, 1))
        if mode == "palette_viridis":
            return self._palette_color("viridis", idx / max(total - 1, 1))
        if mode == "palette_plasma":
            return self._palette_color("plasma", idx / max(total - 1, 1))
        return "yellow"

    def _remove_actor(self, name: str):
        try:
            self._plotter.remove_actor(name)
        except Exception:
            pass

    def _clear_profile_lines(self):
        for i in range(max(self._profile_actor_count, len(self._profiles))):
            self._remove_actor(f"{self._profile_name_prefix}{i}")
            self._remove_actor(f"{self._profile_curtain_prefix}{i}")
        self._profile_actor_count = 0

    def _profile_channel_index(self, prof) -> int:
        try:
            n_ch = int(getattr(prof, "n_channels", 1) or 1)
        except Exception:
            n_ch = 1
        try:
            idx = int(self._meta.get("channel", 0) or 0)
        except Exception:
            idx = 0
        if idx < 0:
            idx = 0
        return int(np.clip(idx, 0, max(0, n_ch - 1)))

    @staticmethod
    def _resample_profile_vec(vec, n: int) -> np.ndarray:
        arr = np.asarray(vec, dtype=np.float64)
        if n <= 0:
            return np.zeros(0, dtype=np.float64)
        if arr.size == n:
            return arr.astype(np.float64, copy=False)
        if arr.size <= 0:
            return np.zeros(n, dtype=np.float64)
        if arr.size == 1:
            return np.full(n, float(arr[0]), dtype=np.float64)
        src = np.linspace(0.0, 1.0, arr.size, dtype=np.float64)
        dst = np.linspace(0.0, 1.0, n, dtype=np.float64)
        return np.interp(dst, src, arr).astype(np.float64)

    def _radargram_for_profile(self, prof, ch):
        try:
            raw = np.asarray(getattr(ch, "data", None), dtype=np.float32)
        except Exception:
            return None
        if raw.ndim != 2 or raw.size <= 0:
            return None

        params = dict(self._meta.get("pipeline_params") or {})
        use_proc = bool(self._meta.get("use_processing", False))
        cache_key = (
            str(getattr(prof, "path", "")),
            int(self._profile_channel_index(prof)),
            bool(use_proc),
            repr(sorted(params.items())),
            int(raw.shape[0]),
            int(raw.shape[1]),
        )
        if cache_key in self._profile_curtain_cache:
            return self._profile_curtain_cache[cache_key]

        try:
            from .gpr_processing import apply_pipeline, normalize_display
        except Exception:
            apply_pipeline = None
            normalize_display = None

        out = raw.copy()
        if use_proc and apply_pipeline is not None:
            try:
                out = apply_pipeline(
                    out,
                    params,
                    dt_ns=float(getattr(prof, "dt_ns", 0.117) or 0.117),
                    normalize_output=True,
                )
            except Exception:
                out = raw.copy()
        elif normalize_display is not None:
            try:
                clip_pct = float(params.get("clip_pct", 98.0) if params else 98.0)
                out = normalize_display(out, clip_pct=clip_pct)
            except Exception:
                out = raw.copy()

        arr = np.asarray(out, dtype=np.float32)
        if arr.ndim != 2 or arr.size <= 0:
            return None
        self._profile_curtain_cache[cache_key] = arr
        return arr

    def _scene_bounds_local(self) -> tuple[float, float, float, float, float, float]:
        n_x = max(1, int(self._spatial.get("n_x", 1)))
        n_y = max(1, int(self._spatial.get("n_y", 1)))
        res = float(self._spatial.get("res", 1.0) or 1.0)
        z_min = float(self._spatial.get("z_min", 0.0) or 0.0)
        z_max = float(self._spatial.get("z_max", 0.0) or 0.0)
        x0, x1 = 0.0, max((n_x - 1) * res, res)
        y0, y1 = 0.0, max((n_y - 1) * res, res)
        z0, z1 = -max(z_max, z_min), -min(z_max, z_min)
        return x0, x1, y0, y1, z0, z1

    def _apply_projection_mode(self):
        use_parallel = bool(self._chk_parallel_proj.isChecked())
        try:
            cam = getattr(self._plotter, "camera", None)
            if cam is not None:
                cam.parallel_projection = use_parallel
        except Exception:
            pass

    def _apply_z_scale(self):
        if self._plotter is None:
            return
        try:
            z_scale = float(self._z_scale_spin.value())
        except Exception:
            z_scale = 1.0
        if not np.isfinite(z_scale) or z_scale <= 0.0:
            z_scale = 1.0
        try:
            self._plotter.set_scale(xscale=1.0, yscale=1.0, zscale=float(z_scale), reset_camera=False)
        except TypeError:
            try:
                self._plotter.set_scale(zscale=float(z_scale))
            except Exception:
                pass
        except Exception:
            pass

    def _apply_camera_for_mode(self, mode: str | None = None):
        if self._volume_grid is None:
            return
        md = str(mode or self._current_section_mode() or "cscan").strip().lower()
        x0, x1, y0, y1, z0, z1 = self._scene_bounds_local()
        cx, cy, cz = (0.5 * (x0 + x1), 0.5 * (y0 + y1), 0.5 * (z0 + z1))
        span = max((x1 - x0), (y1 - y0), (z1 - z0), 1.0)
        dist = span * 2.2

        if md == "inline":
            pos = (cx, y1 + dist, cz)
            up = (0.0, 0.0, 1.0)
        elif md == "crossline":
            pos = (x1 + dist, cy, cz)
            up = (0.0, 0.0, 1.0)
        else:
            pos = (cx, cy, z1 + dist)
            up = (0.0, 1.0, 0.0)

        try:
            self._plotter.camera_position = [pos, (cx, cy, cz), up]
        except Exception:
            try:
                self._plotter.reset_camera()
            except Exception:
                pass
        self._apply_projection_mode()
        self._apply_z_scale()
        self._render_now()

    def _enable_point_picking(self):
        self._pick_enabled = False
        self._lbl_pick.setText("Pick: not available")
        try:
            self._plotter.enable_point_picking(
                callback=self._on_point_picked,
                show_message=False,
                left_clicking=True,
                use_mesh=True,
            )
            self._pick_enabled = True
            self._lbl_pick.setText("Pick: click in 3D view to inspect voxel")
            return
        except TypeError:
            pass
        except Exception:
            return
        try:
            self._plotter.enable_point_picking(
                callback=self._on_point_picked,
                show_message=False,
            )
            self._pick_enabled = True
            self._lbl_pick.setText("Pick: click in 3D view to inspect voxel")
        except Exception:
            pass

    def _render_now(self):
        if bool(getattr(self, "_render_in_progress", False)):
            return
        self._render_in_progress = True
        try:
            self._plotter.render()
        except Exception:
            pass
        finally:
            self._render_in_progress = False

    def _load_volume_grid(self, vol: np.ndarray, meta: dict):
        info = self._grid_spatial_info(vol, meta)
        self._spatial = dict(info)
        self._z_axis = self._depth_axis(info, meta)
        self._disp_origin_x = float(info.get("x_min", 0.0) or 0.0)
        self._disp_origin_y = float(info.get("y_min", 0.0) or 0.0)

        z_origin = -float(info["z_max"])
        image = pv.ImageData()
        image.dimensions = (info["n_x"] + 1, info["n_y"] + 1, info["n_z"] + 1)
        # Render in local coordinates to avoid precision issues on large UTM values.
        image.origin = (0.0, 0.0, z_origin)
        image.spacing = (info["res"], info["res"], info["z_step"])

        values = np.transpose(vol[::-1, :, :], (2, 1, 0)).ravel(order="F").astype(np.float32, copy=False)
        finite = values[np.isfinite(values)]
        self._total_voxels = int(values.size)
        self._finite_voxels = int(finite.size)
        if finite.size > 0:
            self._data_min = float(np.nanmin(finite))
            self._data_max = float(np.nanmax(finite))
            if not np.isfinite(self._data_min):
                self._data_min = 0.0
            if not np.isfinite(self._data_max) or self._data_max <= self._data_min:
                self._data_max = self._data_min + 1e-6
            self._amp_min = float(np.nanpercentile(finite, 2.0))
            self._amp_max = float(np.nanpercentile(finite, 98.0))
            if not np.isfinite(self._amp_min):
                self._amp_min = float(self._data_min)
            if not np.isfinite(self._amp_max):
                self._amp_max = float(self._data_max)
            if self._amp_max <= self._amp_min:
                self._amp_max = self._amp_min + 1e-6
            self._clip_min = float(self._amp_min)
            self._clip_max = float(self._amp_max)
            if finite.size > 250000:
                idx = np.linspace(0, finite.size - 1, 250000, dtype=np.int64)
                self._hist_values = np.asarray(finite[idx], dtype=np.float32)
            else:
                self._hist_values = np.asarray(finite, dtype=np.float32)
        else:
            self._data_min, self._data_max = 0.0, 1.0
            self._amp_min, self._amp_max = 0.0, 1.0
            self._clip_min, self._clip_max = 0.0, 1.0
            self._hist_values = np.zeros(0, dtype=np.float32)
        values_safe = np.asarray(values, dtype=np.float32).copy()
        if not np.isfinite(values_safe).all():
            values_safe[~np.isfinite(values_safe)] = np.float32(self._amp_min)
        image.cell_data["amplitude"] = values_safe

        self._volume_grid = image

    def _draw_volume_actor(self):
        self._remove_actor(self._volume_actor_name)
        self._remove_actor(self._volume_outline_name)
        if self._volume_grid is None:
            return
        if not bool(self._chk_show_volume.isChecked()):
            return
        clip_lo, clip_hi = self._effective_clim()
        opacity_tf = self._volume_opacity_tf()
        self._plotter.add_volume(
            self._volume_grid,
            scalars="amplitude",
            cmap=self._section_cmap,
            opacity=opacity_tf,
            clim=(clip_lo, clip_hi),
            shade=False,
            blending="composite",
            show_scalar_bar=True,
            name=self._volume_actor_name,
        )
        try:
            self._plotter.add_mesh(
                self._volume_grid.outline(),
                color="black",
                line_width=1.0,
                opacity=0.5,
                name=self._volume_outline_name,
            )
        except Exception:
            pass

    def _volume_opacity_tf(self) -> list[float]:
        op = float(np.clip(self._opacity_spin.value(), 0.05, 1.0))
        return [0.0, 0.0, op * 0.05, op * 0.30, op]

    def _get_volume_actor(self):
        try:
            renderer = getattr(self._plotter, "renderer", None)
            actors = getattr(renderer, "actors", None)
            if isinstance(actors, dict):
                return actors.get(self._volume_actor_name)
        except Exception:
            pass
        return None

    def _update_volume_actor_opacity_fast(self) -> bool:
        """Update opacity on existing VTK volume actor without full re-add."""
        if self._volume_grid is None or not bool(self._chk_show_volume.isChecked()):
            return False
        actor = self._get_volume_actor()
        if actor is None:
            return False

        clip_lo, clip_hi = self._effective_clim()
        if not np.isfinite(clip_lo):
            clip_lo = float(self._amp_min)
        if not np.isfinite(clip_hi) or clip_hi <= clip_lo:
            clip_hi = float(clip_lo) + 1e-6

        try:
            vtk_mod = getattr(pv, "_vtk", None)
            if vtk_mod is None:
                import vtk as vtk_mod  # type: ignore

            pwf = vtk_mod.vtkPiecewiseFunction()
            xs = np.linspace(float(clip_lo), float(clip_hi), num=5, dtype=np.float64)
            for xv, ov in zip(xs, self._volume_opacity_tf()):
                pwf.AddPoint(float(xv), float(ov))

            prop = actor.GetProperty() if hasattr(actor, "GetProperty") else None
            if prop is None:
                return False
            prop.SetScalarOpacity(pwf)

            try:
                mapper = actor.GetMapper() if hasattr(actor, "GetMapper") else None
                if mapper is not None and hasattr(mapper, "SetScalarRange"):
                    mapper.SetScalarRange(float(clip_lo), float(clip_hi))
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _update_volume_actor_clim_fast(self) -> bool:
        """Update scalar range on existing volume actor without full re-add."""
        if self._volume_grid is None or not bool(self._chk_show_volume.isChecked()):
            return False
        actor = self._get_volume_actor()
        if actor is None:
            return False

        clip_lo, clip_hi = self._effective_clim()
        if not np.isfinite(clip_lo):
            clip_lo = float(self._amp_min)
        if not np.isfinite(clip_hi) or clip_hi <= clip_lo:
            clip_hi = float(clip_lo) + 1e-6

        try:
            mapper = actor.GetMapper() if hasattr(actor, "GetMapper") else None
            if mapper is None or not hasattr(mapper, "SetScalarRange"):
                return False
            mapper.SetScalarRange(float(clip_lo), float(clip_hi))
            try:
                mapper.Modified()
            except Exception:
                pass
            try:
                actor.Modified()
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _configure_controls_for_volume(self):
        self._updating_controls = True
        try:
            self._threshold_spin.setRange(self._amp_min, self._amp_max)
            thr_default = float(self._amp_min + 0.70 * (self._amp_max - self._amp_min))
            self._threshold_spin.setValue(thr_default)
            self._threshold_slider.setValue(self._threshold_to_slider(thr_default))
            self._section_combo.setCurrentIndex(0)
            self._set_clip_controls()
            self._update_slice_slider_range()
            self._update_iso_controls_enabled()
        finally:
            self._updating_controls = False
        self._refresh_histogram()

    def _update_iso_controls_enabled(self):
        enabled = bool(self._chk_show_iso.isChecked())
        self._threshold_slider.setEnabled(enabled)
        self._threshold_spin.setEnabled(enabled)
        self._threshold_mode_combo.setEnabled(enabled)
        self._iso_extract_combo.setEnabled(enabled)

    def _replace_volume(self, volume: np.ndarray, meta: dict, reset_camera: bool = False):
        self._volume = np.asarray(volume, dtype=np.float32)
        self._meta = dict(meta or {})
        self._profile_curtain_cache = {}
        try:
            self._plotter.clear()
        except Exception:
            pass
        self._plotter.add_axes()
        self._plotter.show_grid()

        self._load_volume_grid(self._volume, self._meta)
        self._apply_z_scale()
        self._draw_volume_actor()
        self._load_profiles(self._profiles)
        self._configure_controls_for_volume()
        try:
            self._refresh_section_plane()
        except Exception:
            pass
        try:
            self._refresh_isosurface()
        except Exception:
            pass

        if reset_camera:
            self._apply_camera_for_mode(self._current_section_mode())
        else:
            self._apply_projection_mode()
            self._apply_z_scale()
        self._render_now()
        self._status.setText(
            f"Volume: {int(self._spatial.get('n_x', 0))}x{int(self._spatial.get('n_y', 0))}x{int(self._spatial.get('n_z', 0))}  "
            f"res={float(self._spatial.get('res', 0.0)):.3f}m  z_step={float(self._spatial.get('z_step', 0.0)):.3f}m  "
            f"z_scale={float(self._z_scale_spin.value()):.2f}  "
            f"finite={self._finite_voxels}/{self._total_voxels}  "
            f"origin=({float(self._spatial.get('x_min', 0.0)):.3f}, {float(self._spatial.get('y_min', 0.0)):.3f})"
        )
        self._enable_point_picking()

    def _current_section_mode(self) -> str:
        return str(self._section_combo.currentData() or "cscan")

    def _update_slice_slider_range(self):
        mode = self._current_section_mode()
        n = 1
        if mode == "inline":
            n = int(self._spatial.get("n_y", 1))
        elif mode == "crossline":
            n = int(self._spatial.get("n_x", 1))
        else:
            n = int(self._spatial.get("n_z", 1))
        n = max(1, n)
        cur = int(np.clip(self._slice_slider.value(), 0, n - 1))
        self._updating_controls = True
        try:
            self._slice_slider.setRange(0, n - 1)
            self._slice_spin.setRange(0, n - 1)
            self._slice_slider.setValue(cur)
            self._slice_spin.setValue(cur)
        finally:
            self._updating_controls = False
        self._update_slice_label(cur)

    def _update_slice_label(self, idx: int):
        mode = self._current_section_mode()
        if mode == "inline":
            self._lbl_slice.setText(f"Y={self._y_at_index(idx):.3f} m")
        elif mode == "crossline":
            self._lbl_slice.setText(f"X={self._x_at_index(idx):.3f} m")
        else:
            self._lbl_slice.setText(f"Z={self._depth_at_index(idx):.3f} m")

    def _refresh_section_plane(self):
        if self._volume_grid is None:
            return
        mode = self._current_section_mode()
        idx = int(self._slice_slider.value())
        self._update_slice_label(idx)
        clip_lo, clip_hi = self._effective_clim()
        self._remove_actor(self._section_actor_name)

        section = self._build_section_sheet(mode, idx)
        if section is not None:
            try:
                self._plotter.add_mesh(
                    section,
                    scalars="amplitude",
                    cmap=self._section_cmap,
                    clim=(clip_lo, clip_hi),
                    opacity=0.98,
                    name=self._section_actor_name,
                    show_scalar_bar=False,
                    lighting=False,
                    interpolate_before_map=False,
                    nan_opacity=0.0,
                )
            except Exception:
                self._remove_actor(self._section_actor_name)
            self._render_now()
            return

        if mode in {"inline", "crossline"}:
            # Fallback safety: if dedicated sheet creation failed, avoid stale actor.
            self._remove_actor(self._section_actor_name)
            self._render_now()
            return

        cx, cy, _cz = self._center_point_3d()
        origin = (cx, cy, -self._depth_at_index(idx))
        try:
            section = self._volume_grid.slice(normal=(0.0, 0.0, 1.0), origin=origin)
            self._plotter.add_mesh(
                section,
                cmap=self._section_cmap,
                clim=(clip_lo, clip_hi),
                opacity=0.98,
                name=self._section_actor_name,
                show_scalar_bar=False,
                nan_opacity=0.0,
            )
        except Exception:
            self._remove_actor(self._section_actor_name)
        self._render_now()

    def _build_section_sheet(self, mode: str, idx: int):
        """Build a section sheet directly from volume array values."""
        if self._volume is None or self._volume.ndim != 3:
            return None
        try:
            from .gpr_volume_3d import (
                extract_b_scan_crossline,
                extract_b_scan_inline,
                extract_c_scan,
            )
        except Exception:
            return None

        res = float(self._spatial.get("res", 1.0) or 1.0)
        z_step = float(self._spatial.get("z_step", res) or res)
        if not np.isfinite(res) or res <= 0:
            res = 1.0
        if not np.isfinite(z_step) or z_step <= 0:
            z_step = res
        z_max = float(self._spatial.get("z_max", 0.0) or 0.0)
        slab = max(min(res, z_step) * 0.08, 1e-3)

        try:
            if mode == "cscan":
                n_z = int(self._spatial.get("n_z", self._volume.shape[0]))
                iz = int(np.clip(idx, 0, max(0, n_z - 1)))
                sec = np.asarray(extract_c_scan(self._volume, iz), dtype=np.float32)
                # sec: (n_y, n_x) -> cells (x, y, 1)
                n_y, n_x = sec.shape
                image = pv.ImageData()
                image.dimensions = (int(n_x) + 1, int(n_y) + 1, 2)
                image.origin = (0.0, 0.0, -float(self._depth_at_index(iz)) - 0.5 * slab)
                image.spacing = (float(res), float(res), float(slab))
                cells = sec.T.astype(np.float32, copy=False).reshape((int(n_x), int(n_y), 1), order="C")
                image.cell_data["amplitude"] = np.asfortranarray(cells).ravel(order="F")
                return image

            if mode == "inline":
                n_y = int(self._spatial.get("n_y", self._volume.shape[1]))
                iy = int(np.clip(idx, 0, max(0, n_y - 1)))
                sec = np.asarray(extract_b_scan_inline(self._volume, iy), dtype=np.float32)
                # sec: (n_z, n_x) -> cells (x, 1, z)
                n_z, n_x = sec.shape
                image = pv.ImageData()
                image.dimensions = (int(n_x) + 1, 2, int(n_z) + 1)
                image.origin = (0.0, float(self._y_at_index(iy) - 0.5 * slab), -float(z_max))
                image.spacing = (float(res), float(slab), float(z_step))
                cells = sec[::-1, :].T.astype(np.float32, copy=False).reshape((int(n_x), 1, int(n_z)), order="C")
                image.cell_data["amplitude"] = np.asfortranarray(cells).ravel(order="F")
                return image

            n_x = int(self._spatial.get("n_x", self._volume.shape[2]))
            ix = int(np.clip(idx, 0, max(0, n_x - 1)))
            sec = np.asarray(extract_b_scan_crossline(self._volume, ix), dtype=np.float32)
            # sec: (n_z, n_y) -> cells (1, y, z)
            n_z, n_y = sec.shape
            image = pv.ImageData()
            image.dimensions = (2, int(n_y) + 1, int(n_z) + 1)
            image.origin = (float(self._x_at_index(ix) - 0.5 * slab), 0.0, -float(z_max))
            image.spacing = (float(slab), float(res), float(z_step))
            cells = sec[::-1, :].T.astype(np.float32, copy=False).reshape((1, int(n_y), int(n_z)), order="C")
            image.cell_data["amplitude"] = np.asfortranarray(cells).ravel(order="F")
            return image
        except Exception:
            return None

    def _build_bscan_sheet(self, mode: str, idx: int):
        """Backward-compatible alias for old call-sites."""
        return self._build_section_sheet(mode, idx)

    def _refresh_isosurface(self):
        if self._volume_grid is None:
            return
        if not bool(self._chk_show_iso.isChecked()):
            self._remove_actor(self._iso_actor_name)
            self._render_now()
            return
        try:
            from .gpr_volume_3d import extract_isosurface_points
        except Exception:
            return

        threshold = float(self._threshold_spin.value())
        mode = str(self._threshold_mode_combo.currentData() or "above")
        surface_only = str(self._iso_extract_combo.currentData() or "surface") == "surface"
        try:
            points = extract_isosurface_points(
                self._volume,
                self._grids if self._grids else None,
                self._meta,
                threshold=threshold,
                mode=mode,
                surface_only=surface_only,
                max_points=120000,
            )
        except Exception:
            points = np.zeros((0, 4), dtype=np.float32)

        if points.shape[0] <= 0:
            self._remove_actor(self._iso_actor_name)
            self._render_now()
            return

        xyz = points[:, :3].astype(np.float64, copy=True)
        # Viewer uses local XY (offset by volume origin), keep iso cloud aligned.
        xyz[:, 0] = xyz[:, 0] - float(self._disp_origin_x)
        xyz[:, 1] = xyz[:, 1] - float(self._disp_origin_y)
        xyz[:, 2] = -xyz[:, 2]
        cloud = pv.PolyData(xyz)
        cloud["amplitude"] = points[:, 3].astype(np.float32, copy=False)
        clip_lo, clip_hi = self._effective_clim()
        self._plotter.add_mesh(
            cloud,
            style="points",
            render_points_as_spheres=True,
            point_size=4.0,
            cmap=self._section_cmap,
            clim=(clip_lo, clip_hi),
            opacity=0.90,
            name=self._iso_actor_name,
            show_scalar_bar=False,
        )
        self._render_now()

    def _on_section_mode_changed(self, _idx: int):
        self._update_slice_slider_range()
        self._refresh_section_plane()
        self._apply_camera_for_mode(self._current_section_mode())

    def _on_slice_spin_changed(self, idx: int):
        if self._updating_controls:
            return
        idx_i = int(idx)
        if self._slice_slider.value() != idx_i:
            self._slice_slider.setValue(idx_i)
        else:
            self._on_slice_changed(idx_i)

    def _on_slice_changed(self, idx: int):
        if self._updating_controls:
            return
        idx_i = int(idx)
        if self._slice_spin.value() != idx_i:
            self._updating_controls = True
            try:
                self._slice_spin.setValue(idx_i)
            finally:
                self._updating_controls = False
        self._update_slice_label(idx_i)
        self._refresh_section_plane()
        if self._current_section_mode() == "cscan" and not self._suppress_depth_emit:
            try:
                self.depth_changed.emit(float(self._depth_at_index(idx_i)))
            except Exception:
                pass

    def _on_threshold_slider_changed(self, value: int):
        if self._updating_controls:
            return
        self._updating_controls = True
        try:
            self._threshold_spin.setValue(self._slider_to_threshold(int(value)))
        finally:
            self._updating_controls = False
        self._refresh_isosurface()

    def _on_threshold_spin_changed(self, value: float):
        if self._updating_controls:
            return
        self._updating_controls = True
        try:
            self._threshold_slider.setValue(self._threshold_to_slider(float(value)))
        finally:
            self._updating_controls = False
        self._refresh_isosurface()

    def _on_threshold_mode_changed(self, _idx: int):
        self._refresh_isosurface()

    def _on_iso_extract_mode_changed(self, _idx: int):
        self._refresh_isosurface()

    def _on_opacity_changed(self, _value: float):
        if not self._update_volume_actor_opacity_fast():
            self._draw_volume_actor()
        self._render_now()

    def _on_z_scale_changed(self, _value: float):
        self._apply_z_scale()
        self._render_now()

    def _on_show_volume_toggled(self, _checked: bool):
        self._draw_volume_actor()
        self._render_now()

    def _on_show_iso_toggled(self, _checked: bool):
        self._update_iso_controls_enabled()
        self._refresh_isosurface()

    def _on_show_profiles_toggled(self, _checked: bool):
        self._load_profiles(self._profiles)
        self._render_now()

    def _on_projection_toggled(self, _checked: bool):
        self._apply_projection_mode()
        self._render_now()

    def _on_reset_camera(self):
        self._apply_camera_for_mode(self._current_section_mode())
        self._status.setText("Camera reset")

    def _point_arg_to_xyz(self, picked) -> tuple[float, float, float] | None:
        if picked is None:
            return None
        if hasattr(picked, "GetPickPosition"):
            try:
                xyz = picked.GetPickPosition()
                if xyz and len(xyz) >= 3:
                    return float(xyz[0]), float(xyz[1]), float(xyz[2])
            except Exception:
                pass
        try:
            arr = np.asarray(picked, dtype=np.float64).reshape(-1)
            if arr.size >= 3 and np.isfinite(arr[:3]).all():
                return float(arr[0]), float(arr[1]), float(arr[2])
        except Exception:
            pass
        return None

    def _sample_amplitude_local(
        self, x_local: float, y_local: float, z_local: float
    ) -> tuple[float | None, int, int, int]:
        if self._volume is None or self._volume.ndim != 3:
            return None, 0, 0, 0
        n_z, n_y, n_x = [int(v) for v in self._volume.shape]
        res = float(self._spatial.get("res", 1.0) or 1.0)
        if not np.isfinite(res) or res <= 0:
            res = 1.0
        ix = int(np.clip(np.rint(float(x_local) / res), 0, max(0, n_x - 1)))
        iy = int(np.clip(np.rint(float(y_local) / res), 0, max(0, n_y - 1)))
        depth = -float(z_local)
        if self._z_axis.size > 0:
            iz = int(np.argmin(np.abs(self._z_axis.astype(np.float64) - depth)))
        else:
            z_step = float(self._spatial.get("z_step", res) or res)
            z_min = float(self._spatial.get("z_min", 0.0) or 0.0)
            if not np.isfinite(z_step) or z_step <= 0:
                z_step = res
            iz = int(np.rint((depth - z_min) / z_step))
        iz = int(np.clip(iz, 0, max(0, n_z - 1)))
        try:
            amp = float(self._volume[iz, iy, ix])
        except Exception:
            amp = None
        if amp is not None and not np.isfinite(amp):
            amp = None
        return amp, iz, iy, ix

    def _on_point_picked(self, picked):
        xyz = self._point_arg_to_xyz(picked)
        if xyz is None:
            return
        x_local, y_local, z_local = xyz
        east, north = self._local_to_world_xy(x_local, y_local)
        depth = -float(z_local)
        amp, iz, iy, ix = self._sample_amplitude_local(x_local, y_local, z_local)
        amp_str = f"{amp:.6g}" if amp is not None else "n/a"
        self._lbl_pick.setText(
            f"Pick: E {east:.3f}  N {north:.3f}  depth {depth:.3f} m  amp {amp_str}  [iz={iz}, iy={iy}, ix={ix}]"
        )
        self.set_depth_from_external(depth)
        if not self._suppress_depth_emit:
            try:
                self.depth_changed.emit(float(depth))
            except Exception:
                pass

    def _on_export_las(self):
        try:
            from .gpr_volume_3d import extract_isosurface_points, export_points_to_las
        except Exception as exc:
            QMessageBox.warning(self, "Export LAS", f"Export module not available:\n{exc}")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export LAS",
            "gpr_isosurface.las",
            "LAS files (*.las)",
        )
        if not path:
            return
        if not path.lower().endswith(".las"):
            path = f"{path}.las"

        threshold = float(self._threshold_spin.value())
        mode = str(self._threshold_mode_combo.currentData() or "above")
        surface_only = str(self._iso_extract_combo.currentData() or "surface") == "surface"
        try:
            points = extract_isosurface_points(
                self._volume,
                self._grids if self._grids else None,
                self._meta,
                threshold=threshold,
                mode=mode,
                surface_only=surface_only,
                max_points=500000,
            )
            if points.shape[0] <= 0:
                QMessageBox.information(self, "Export LAS", "No points for current threshold.")
                return
            epsg = self._meta.get("epsg")
            try:
                epsg = int(epsg) if epsg is not None else None
            except Exception:
                epsg = None
            info = export_points_to_las(path, points, epsg=epsg)
            self._status.setText(
                f"LAS exported: {info.get('path', path)}  points={info.get('n_points', 0)}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export LAS", f"Error exporting LAS:\n{exc}")

    def _on_export_npz(self):
        try:
            from .gpr_volume_3d import export_volume_to_npz
        except Exception as exc:
            QMessageBox.warning(self, "Export NPZ", f"Export module not available:\n{exc}")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export volume NPZ",
            "gpr_volume.npz",
            "NumPy Archive (*.npz)",
        )
        if not path:
            return
        try:
            info = export_volume_to_npz(self._volume, self._grids, self._meta, path)
            self._status.setText(
                f"NPZ saved: {info.get('path', path)}  voxels={info.get('n_voxels', 0)}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export NPZ", f"Error exporting NPZ:\n{exc}")

    def _on_export_vti(self):
        try:
            from .gpr_volume_3d import export_volume_to_vti
        except Exception as exc:
            QMessageBox.warning(self, "Export VTI", f"Export module not available:\n{exc}")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export volume VTI",
            "gpr_volume.vti",
            "VTK ImageData (*.vti)",
        )
        if not path:
            return
        epsg = self._meta.get("epsg")
        try:
            epsg = int(epsg) if epsg is not None else None
        except Exception:
            epsg = None
        try:
            info = export_volume_to_vti(self._volume, self._grids, self._meta, path, epsg=epsg)
            self._status.setText(
                f"VTI saved: {info.get('path', path)}  spacing={info.get('spacing', ())}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export VTI", f"Error exporting VTI:\n{exc}")

    def _on_load_volume(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load volume",
            "",
            "Volume files (*.npz *.vti);;NumPy Archive (*.npz);;VTK ImageData (*.vti)",
        )
        if not path:
            return

        try:
            if path.lower().endswith(".npz"):
                from .gpr_volume_3d import load_volume_from_npz

                vol, meta = load_volume_from_npz(path)
            elif path.lower().endswith(".vti"):
                from .gpr_volume_3d import load_volume_from_vti

                vol, meta = load_volume_from_vti(path)
            else:
                raise ValueError("Unsupported format. Use .npz or .vti.")
            self._grids = []
            self._replace_volume(vol, meta, reset_camera=True)
            self._status.setText(f"Volume loaded: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Load volume", f"Error loading volume:\n{exc}")

    def _load_profiles(self, profiles):
        self._clear_profile_lines()
        if not bool(self._chk_show_profiles.isChecked()):
            return
        if not profiles:
            return
        n_profiles = max(1, len(profiles))
        for i, prof in enumerate(profiles):
            try:
                ch = prof.channel(self._profile_channel_index(prof))
            except Exception:
                continue
            radar = self._radargram_for_profile(prof, ch)
            if radar is None:
                continue
            n_s, n_t = int(radar.shape[0]), int(radar.shape[1])
            if n_s <= 1 or n_t <= 1:
                continue

            e = self._resample_profile_vec(getattr(ch, "easting", []), n_t)
            n = self._resample_profile_vec(getattr(ch, "northing", []), n_t)
            finite = np.isfinite(e) & np.isfinite(n)
            if int(np.count_nonzero(finite)) < 2:
                continue
            if not np.all(finite):
                idx = np.arange(n_t, dtype=np.float64)
                idx_ok = np.where(finite)[0].astype(np.float64)
                e = np.interp(idx, idx_ok, e[finite]).astype(np.float64)
                n = np.interp(idx, idx_ok, n[finite]).astype(np.float64)

            depth_max = float(getattr(prof, "depth_max_m", 0.0) or 0.0)
            if not np.isfinite(depth_max) or depth_max <= 0:
                depth_max = max(float(n_s - 1), 1.0)
            depth = np.linspace(0.0, depth_max, n_s, dtype=np.float64)

            xg = np.tile((e - float(self._disp_origin_x))[np.newaxis, :], (n_s, 1))
            yg = np.tile((n - float(self._disp_origin_y))[np.newaxis, :], (n_s, 1))
            zg = -np.tile(depth[:, np.newaxis], (1, n_t))
            curtain = pv.StructuredGrid(xg, yg, zg)
            curtain["amplitude"] = np.asarray(radar, dtype=np.float32).ravel(order="F")

            clip_lo, clip_hi = self._effective_clim()
            try:
                self._plotter.add_mesh(
                    curtain,
                    scalars="amplitude",
                    cmap=self._section_cmap,
                    clim=(clip_lo, clip_hi),
                    opacity=0.92,
                    name=f"{self._profile_curtain_prefix}{i}",
                    show_scalar_bar=False,
                    lighting=False,
                    nan_opacity=0.0,
                )
            except Exception:
                pass

            # Keep acquisition trajectory as thin reference line over the curtain.
            try:
                pts = np.column_stack(
                    [e - float(self._disp_origin_x), n - float(self._disp_origin_y), np.zeros(n_t, dtype=np.float64)]
                ).astype(np.float64)
                line = pv.lines_from_points(pts, close=False)
                col = self._profile_line_color(i, n_profiles)
                self._plotter.add_mesh(
                    line,
                    color=col,
                    line_width=1.6,
                    name=f"{self._profile_name_prefix}{i}",
                    render_lines_as_tubes=False,
                )
            except Exception:
                pass
        self._profile_actor_count = len(profiles)

    def update_cursor_position(self, east: float, north: float, depth: float):
        """Update 3D marker and horizontal slice from profile cursor."""
        if self._volume_grid is None:
            return
        if not (np.isfinite(east) and np.isfinite(north) and np.isfinite(depth)):
            return

        east_local, north_local = self._world_to_local_xy(float(east), float(north))
        z = -float(depth)
        clip_lo, clip_hi = self._effective_clim()
        try:
            slice_poly = self._volume_grid.slice(
                normal=(0.0, 0.0, 1.0),
                origin=(float(east_local), float(north_local), z),
            )
            self._plotter.add_mesh(
                slice_poly,
                cmap=self._section_cmap,
                clim=(clip_lo, clip_hi),
                opacity=0.95,
                name=self._cursor_slice_name,
                show_scalar_bar=False,
            )
        except Exception:
            pass

        try:
            radius = max(float(self._meta.get("resolution", 1.0)) * 0.25, 0.05)
        except Exception:
            radius = 0.05
        try:
            marker = pv.Sphere(radius=radius, center=(float(east_local), float(north_local), z))
            self._plotter.add_mesh(
                marker,
                color="cyan",
                name=self._cursor_marker_name,
                smooth_shading=True,
            )
        except Exception:
            pass

        self._lbl_cursor.setText(
            f"Cursor: E {float(east):.3f}  N {float(north):.3f}  depth {float(depth):.3f} m"
        )
        self._render_now()

    def set_depth_from_external(self, depth: float):
        """Set C-scan depth from an external controller without feedback loops."""
        if not np.isfinite(depth):
            return
        if self._z_axis.size <= 0:
            return
        idx = int(np.argmin(np.abs(self._z_axis.astype(np.float64) - float(depth))))
        idx = int(np.clip(idx, 0, max(0, self._z_axis.size - 1)))
        self._suppress_depth_emit = True
        try:
            if self._current_section_mode() != "cscan":
                self._section_combo.setCurrentIndex(0)
            if self._slice_slider.value() != idx:
                self._slice_slider.setValue(idx)
            else:
                self._refresh_section_plane()
        finally:
            self._suppress_depth_emit = False

    def closeEvent(self, event):
        try:
            if self._plotter is not None:
                self._plotter.close()
        except Exception:
            pass
        super().closeEvent(event)
