# -*- coding: utf-8 -*-
"""Standalone 3D viewer dialog for OGPR volumes."""

from __future__ import annotations

import numpy as np

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
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
    QSplitter,
    QVBoxLayout,
    QWidget,
)

try:
    import pyvista as pv

    _HAS_PYVISTA = True
except Exception as _pv_exc:  # pragma: no cover - optional dependency
    pv = None
    _HAS_PYVISTA = False
    _PYVISTA_IMPORT_ERROR = _pv_exc
else:  # pragma: no cover - optional dependency
    _PYVISTA_IMPORT_ERROR = None

try:  # pragma: no cover - optional dependency
    from pyvistaqt import QtInteractor

    _HAS_QTINTERACTOR = True
    _QTINTERACTOR_IMPORT_ERROR = None
except Exception as _qtint_exc:  # pragma: no cover - optional dependency
    QtInteractor = None
    _HAS_QTINTERACTOR = False
    _QTINTERACTOR_IMPORT_ERROR = _qtint_exc


class Gpr3dViewerDialog(QDialog):
    """Interactive 3D volume viewer based on pyvistaqt in a QDialog."""

    def __init__(self, volume, meta, profiles=None, grids=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("GPR 3D Volume Viewer")
        self.resize(1280, 800)

        if not _HAS_PYVISTA:
            raise ImportError(
                "pyvista not available. Install with: pip install pyvista pyvistaqt"
            ) from _PYVISTA_IMPORT_ERROR
        if not _HAS_QTINTERACTOR:
            raise ImportError(
                "pyvistaqt not available. Install with: pip install pyvistaqt"
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
        self._amp_min = 0.0
        self._amp_max = 1.0
        self._updating_controls = False

        self._volume_actor_name = "volume_actor"
        self._section_actor_name = "section_actor"
        self._iso_actor_name = "iso_actor"
        self._cursor_slice_name = "cursor_slice"
        self._cursor_marker_name = "cursor_marker"
        self._profile_name_prefix = "profile_line_"

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
        self._lbl_slice = QLabel("-")
        self._lbl_slice.setMinimumWidth(160)
        slice_row_l.addWidget(self._slice_slider, 1)
        slice_row_l.addWidget(self._lbl_slice, 0)
        form.addRow("Slice index:", slice_row)

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

        self._opacity_spin = QDoubleSpinBox()
        self._opacity_spin.setDecimals(2)
        self._opacity_spin.setRange(0.05, 1.00)
        self._opacity_spin.setSingleStep(0.05)
        self._opacity_spin.setValue(0.35)
        self._opacity_spin.valueChanged.connect(self._on_opacity_changed)
        form.addRow("Volume opacity:", self._opacity_spin)

        right_layout.addLayout(form)

        btn_row = QHBoxLayout()
        self._btn_export_las = QPushButton("Export LAS")
        self._btn_export_las.clicked.connect(self._on_export_las)
        self._btn_export_vti = QPushButton("Export .vti")
        self._btn_export_vti.clicked.connect(self._on_export_vti)
        self._btn_export_npz = QPushButton("Export .npz")
        self._btn_export_npz.clicked.connect(self._on_export_npz)
        self._btn_load = QPushButton("Load .vti/.npz")
        self._btn_load.clicked.connect(self._on_load_volume)
        btn_row.addWidget(self._btn_export_las)
        btn_row.addWidget(self._btn_export_vti)
        btn_row.addWidget(self._btn_export_npz)
        right_layout.addLayout(btn_row)
        right_layout.addWidget(self._btn_load)

        self._lbl_cursor = QLabel("Cursor: -")
        self._lbl_cursor.setWordWrap(True)
        right_layout.addWidget(self._lbl_cursor)

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
        return float(self._spatial.get("x_min", 0.0)) + i * float(self._spatial.get("res", 1.0))

    def _y_at_index(self, idx: int) -> float:
        n_y = int(self._spatial.get("n_y", 1))
        i = int(np.clip(idx, 0, max(0, n_y - 1)))
        return float(self._spatial.get("y_min", 0.0)) + i * float(self._spatial.get("res", 1.0))

    def _center_point_3d(self) -> tuple[float, float, float]:
        x = self._x_at_index(max(0, int(self._spatial.get("n_x", 1) // 2)))
        y = self._y_at_index(max(0, int(self._spatial.get("n_y", 1) // 2)))
        z = -self._depth_at_index(max(0, int(self._spatial.get("n_z", 1) // 2)))
        return x, y, z

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

    def _remove_actor(self, name: str):
        try:
            self._plotter.remove_actor(name)
        except Exception:
            pass

    def _load_volume_grid(self, vol: np.ndarray, meta: dict):
        info = self._grid_spatial_info(vol, meta)
        self._spatial = dict(info)
        self._z_axis = self._depth_axis(info, meta)

        z_origin = -float(info["z_max"])
        image = pv.ImageData()
        image.dimensions = (info["n_x"] + 1, info["n_y"] + 1, info["n_z"] + 1)
        image.origin = (info["x_min"], info["y_min"], z_origin)
        image.spacing = (info["res"], info["res"], info["z_step"])

        values = np.transpose(vol[::-1, :, :], (2, 1, 0)).ravel(order="F").astype(np.float32, copy=False)
        image.cell_data["amplitude"] = values
        finite = values[np.isfinite(values)]
        if finite.size > 0:
            self._amp_min = float(np.nanpercentile(finite, 2.0))
            self._amp_max = float(np.nanpercentile(finite, 98.0))
            if not np.isfinite(self._amp_min):
                self._amp_min = float(np.nanmin(finite))
            if not np.isfinite(self._amp_max):
                self._amp_max = float(np.nanmax(finite))
            if self._amp_max <= self._amp_min:
                self._amp_max = self._amp_min + 1e-6
        else:
            self._amp_min, self._amp_max = 0.0, 1.0

        self._volume_grid = image

    def _draw_volume_actor(self):
        if self._volume_grid is None:
            return
        self._remove_actor(self._volume_actor_name)
        self._plotter.add_volume(
            self._volume_grid,
            scalars="amplitude",
            cmap="RdBu_r",
            opacity=float(self._opacity_spin.value()),
            clim=(self._amp_min, self._amp_max),
            shade=False,
            blending="composite",
            show_scalar_bar=True,
            name=self._volume_actor_name,
        )

    def _configure_controls_for_volume(self):
        self._updating_controls = True
        try:
            self._threshold_spin.setRange(self._amp_min, self._amp_max)
            thr_default = float(self._amp_min + 0.70 * (self._amp_max - self._amp_min))
            self._threshold_spin.setValue(thr_default)
            self._threshold_slider.setValue(self._threshold_to_slider(thr_default))
            self._section_combo.setCurrentIndex(0)
            self._update_slice_slider_range()
        finally:
            self._updating_controls = False

    def _replace_volume(self, volume: np.ndarray, meta: dict, reset_camera: bool = False):
        self._volume = np.asarray(volume, dtype=np.float32)
        self._meta = dict(meta or {})
        try:
            self._plotter.clear()
        except Exception:
            pass
        self._plotter.add_axes()
        self._plotter.show_grid()

        self._load_volume_grid(self._volume, self._meta)
        self._draw_volume_actor()
        self._load_profiles(self._profiles)
        self._configure_controls_for_volume()
        self._refresh_section_plane()
        self._refresh_isosurface()

        if reset_camera:
            self._plotter.reset_camera()
        self._plotter.render()
        self._status.setText(
            f"Volume: {int(self._spatial.get('n_x', 0))}x{int(self._spatial.get('n_y', 0))}x{int(self._spatial.get('n_z', 0))}  "
            f"res={float(self._spatial.get('res', 0.0)):.3f}m  z_step={float(self._spatial.get('z_step', 0.0)):.3f}m"
        )

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
        self._slice_slider.setRange(0, n - 1)
        self._slice_slider.setValue(cur)
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

        cx, cy, cz = self._center_point_3d()
        if mode == "inline":
            origin = (cx, self._y_at_index(idx), cz)
            normal = (0.0, 1.0, 0.0)
        elif mode == "crossline":
            origin = (self._x_at_index(idx), cy, cz)
            normal = (1.0, 0.0, 0.0)
        else:
            origin = (cx, cy, -self._depth_at_index(idx))
            normal = (0.0, 0.0, 1.0)

        try:
            section = self._volume_grid.slice(normal=normal, origin=origin)
            self._plotter.add_mesh(
                section,
                cmap="RdBu_r",
                clim=(self._amp_min, self._amp_max),
                opacity=0.98,
                name=self._section_actor_name,
                show_scalar_bar=False,
            )
        except Exception:
            self._remove_actor(self._section_actor_name)
        self._plotter.render()

    def _refresh_isosurface(self):
        if self._volume_grid is None:
            return
        try:
            from .gpr_volume_3d import extract_isosurface_points
        except Exception:
            return

        threshold = float(self._threshold_spin.value())
        mode = str(self._threshold_mode_combo.currentData() or "above")
        try:
            points = extract_isosurface_points(
                self._volume,
                self._grids if self._grids else None,
                self._meta,
                threshold=threshold,
                mode=mode,
                max_points=120000,
            )
        except Exception:
            points = np.zeros((0, 4), dtype=np.float32)

        if points.shape[0] <= 0:
            self._remove_actor(self._iso_actor_name)
            self._plotter.render()
            return

        xyz = points[:, :3].astype(np.float64, copy=True)
        xyz[:, 2] = -xyz[:, 2]
        cloud = pv.PolyData(xyz)
        cloud["amplitude"] = points[:, 3].astype(np.float32, copy=False)
        self._plotter.add_mesh(
            cloud,
            style="points",
            render_points_as_spheres=True,
            point_size=4.0,
            cmap="viridis",
            clim=(self._amp_min, self._amp_max),
            opacity=0.90,
            name=self._iso_actor_name,
            show_scalar_bar=False,
        )
        self._plotter.render()

    def _on_section_mode_changed(self, _idx: int):
        self._update_slice_slider_range()
        self._refresh_section_plane()

    def _on_slice_changed(self, idx: int):
        if self._updating_controls:
            return
        self._update_slice_label(int(idx))
        self._refresh_section_plane()

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

    def _on_opacity_changed(self, _value: float):
        self._draw_volume_actor()
        self._plotter.render()

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
        try:
            points = extract_isosurface_points(
                self._volume,
                self._grids if self._grids else None,
                self._meta,
                threshold=threshold,
                mode=mode,
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
        if not profiles:
            return
        for i, prof in enumerate(profiles):
            try:
                ch = prof.channel(0)
            except Exception:
                continue
            e = np.asarray(getattr(ch, "easting", []), dtype=np.float64)
            n = np.asarray(getattr(ch, "northing", []), dtype=np.float64)
            if e.size < 2 or n.size < 2:
                continue
            finite = np.isfinite(e) & np.isfinite(n)
            if int(np.count_nonzero(finite)) < 2:
                continue
            e = e[finite]
            n = n[finite]
            z = np.zeros(e.shape[0], dtype=np.float64)
            pts = np.column_stack([e, n, z]).astype(np.float64)
            try:
                line = pv.lines_from_points(pts, close=False)
                self._plotter.add_mesh(
                    line,
                    color="yellow",
                    line_width=2.0,
                    name=f"{self._profile_name_prefix}{i}",
                    render_lines_as_tubes=False,
                )
            except Exception:
                continue

    def update_cursor_position(self, east: float, north: float, depth: float):
        """Update 3D marker and horizontal slice from profile cursor."""
        if self._volume_grid is None:
            return
        if not (np.isfinite(east) and np.isfinite(north) and np.isfinite(depth)):
            return

        z = -float(depth)
        try:
            slice_poly = self._volume_grid.slice(
                normal=(0.0, 0.0, 1.0),
                origin=(float(east), float(north), z),
            )
            self._plotter.add_mesh(
                slice_poly,
                cmap="RdBu_r",
                clim=(self._amp_min, self._amp_max),
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
            marker = pv.Sphere(radius=radius, center=(float(east), float(north), z))
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
        self._plotter.render()

    def closeEvent(self, event):
        try:
            if self._plotter is not None:
                self._plotter.close()
        except Exception:
            pass
        super().closeEvent(event)
