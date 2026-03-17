# -*- coding: utf-8 -*-
"""Standalone 3D viewer dialog for OGPR volumes."""

from __future__ import annotations

import numpy as np

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
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
    """3D volume viewer based on pyvistaqt embedded in a QDialog."""

    def __init__(self, volume, meta, profiles=None, grids=None, parent=None):
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
        self._grids = list(grids or [])

        self._volume_grid = None
        self._cursor_slice_name = "cursor_slice"
        self._cursor_marker_name = "cursor_marker"

        layout = QVBoxLayout(self)
        self._plotter = QtInteractor(self)
        if hasattr(self._plotter, "interactor"):
            layout.addWidget(self._plotter.interactor)
        else:
            layout.addWidget(self._plotter)

        btn_row = QHBoxLayout()
        self._btn_export_vti = QPushButton("Export .vti")
        self._btn_export_npz = QPushButton("Export .npz")
        self._btn_load = QPushButton("Load .vti/.npz")
        self._btn_export_vti.clicked.connect(self._on_export_vti)
        self._btn_export_npz.clicked.connect(self._on_export_npz)
        self._btn_load.clicked.connect(self._on_load_volume)
        btn_row.addWidget(self._btn_export_vti)
        btn_row.addWidget(self._btn_export_npz)
        btn_row.addStretch(1)
        btn_row.addWidget(self._btn_load)
        layout.addLayout(btn_row)

        self._status = QLabel("Volume loaded")
        layout.addWidget(self._status)

        self._replace_volume(self._volume, self._meta, reset_camera=True)

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
        finite = values[np.isfinite(values)]
        if finite.size > 0:
            vmin = float(np.nanpercentile(finite, 2.0))
            vmax = float(np.nanpercentile(finite, 98.0))
            if vmax <= vmin:
                vmax = vmin + 1e-6
        else:
            vmin, vmax = 0.0, 1.0

        self._volume_grid = image
        self._plotter.add_axes()
        self._plotter.show_grid()
        self._plotter.add_volume(
            image,
            scalars="amplitude",
            cmap="RdBu_r",
            opacity="linear",
            clim=(vmin, vmax),
            shade=False,
            blending="composite",
            show_scalar_bar=True,
        )
        self._status.setText(
            f"Volume: {info['n_x']}x{info['n_y']}x{info['n_z']}  "
            f"res={info['res']:.3f}m  z_step={info['z_step']:.3f}m"
        )

    def _replace_volume(self, volume: np.ndarray, meta: dict, reset_camera: bool = False):
        self._volume = np.asarray(volume, dtype=np.float32)
        self._meta = dict(meta or {})
        try:
            self._plotter.clear()
        except Exception:
            pass
        self._load_volume(self._volume, self._meta)
        self._load_profiles(self._profiles)
        if reset_camera:
            self._plotter.reset_camera()
        self._plotter.render()

    def _on_export_npz(self):
        try:
            from .gpr_volume_3d import export_volume_to_npz
        except Exception as exc:
            QMessageBox.warning(self, "Export NPZ", f"Modulo export non disponibile:\n{exc}")
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
                f"NPZ salvato: {info.get('path', path)}  voxels={info.get('n_voxels', 0)}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export NPZ", f"Errore export NPZ:\n{exc}")

    def _on_export_vti(self):
        try:
            from .gpr_volume_3d import export_volume_to_vti
        except Exception as exc:
            QMessageBox.warning(self, "Export VTI", f"Modulo export non disponibile:\n{exc}")
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
                f"VTI salvato: {info.get('path', path)}  spacing={info.get('spacing', ())}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export VTI", f"Errore export VTI:\n{exc}")

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
                raise ValueError("Formato non supportato. Usa .npz o .vti.")
            self._grids = []
            self._replace_volume(vol, meta, reset_camera=True)
            self._status.setText(f"Volume caricato: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Load volume", f"Errore caricamento volume:\n{exc}")

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
