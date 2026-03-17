# -*- coding: utf-8 -*-
"""Standalone 3D viewer dialog for OGPR volumes."""

from __future__ import annotations

import numpy as np

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QLabel

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
    """3D volume viewer based on pyvistaqt embedded in a QDialog."""

    def __init__(self, volume, meta, profiles=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("GPR 3D Volume Viewer")
        self.resize(1100, 760)

        if not _HAS_PYVISTA:
            raise ImportError(
                "pyvista non disponibile. Installa: pip install pyvista pyvistaqt"
            ) from _PYVISTA_IMPORT_ERROR
        if not _HAS_QTINTERACTOR:
            raise ImportError(
                "pyvistaqt non disponibile. Installa: pip install pyvistaqt"
            ) from _QTINTERACTOR_IMPORT_ERROR

        self._volume = np.asarray(volume, dtype=np.float32)
        if self._volume.ndim != 3:
            raise ValueError("volume must be 3D with shape (n_z, n_y, n_x)")

        self._meta = dict(meta or {})
        self._profiles = list(profiles or [])

        self._volume_grid = None
        self._cursor_slice_name = "cursor_slice"
        self._cursor_marker_name = "cursor_marker"

        layout = QVBoxLayout(self)
        self._plotter = QtInteractor(self)
        if hasattr(self._plotter, "interactor"):
            layout.addWidget(self._plotter.interactor)
        else:
            layout.addWidget(self._plotter)

        self._status = QLabel("Volume loaded")
        layout.addWidget(self._status)

        self._load_volume(self._volume, self._meta)
        self._load_profiles(self._profiles)
        self._plotter.reset_camera()
        self._plotter.render()

    def _grid_spatial_info(self, vol: np.ndarray, meta: dict) -> dict:
        n_z, n_y, n_x = [int(v) for v in vol.shape]
        res = float(meta.get("resolution", 1.0) or 1.0)
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

    def _load_volume(self, vol: np.ndarray, meta: dict):
        info = self._grid_spatial_info(vol, meta)

        # Z is rendered as negative depth so surface is near z=0.
        z_origin = -float(info["z_max"])
        image = pv.ImageData()
        image.dimensions = (info["n_x"] + 1, info["n_y"] + 1, info["n_z"] + 1)
        image.origin = (info["x_min"], info["y_min"], z_origin)
        image.spacing = (info["res"], info["res"], info["z_step"])

        # Reorder from (z, y, x) to image cells expected in Fortran order.
        values = np.transpose(vol[::-1, :, :], (2, 1, 0)).ravel(order="F")
        image.cell_data["amplitude"] = values.astype(np.float32, copy=False)

        self._volume_grid = image
        self._plotter.add_axes()
        self._plotter.show_grid()
        self._plotter.add_volume(
            image,
            scalars="amplitude",
            cmap="RdBu_r",
            opacity="sigmoid",
            shade=False,
            blending="composite",
            show_scalar_bar=True,
        )
        self._status.setText(
            f"Volume: {info['n_x']}x{info['n_y']}x{info['n_z']}  "
            f"res={info['res']:.3f}m  z_step={info['z_step']:.3f}m"
        )

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
                    name=f"profile_line_{i}",
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

        self._status.setText(
            f"Cursor -> E {float(east):.3f}  N {float(north):.3f}  depth {float(depth):.3f} m"
        )
        self._plotter.render()

    def closeEvent(self, event):
        try:
            if self._plotter is not None:
                self._plotter.close()
        except Exception:
            pass
        super().closeEvent(event)
