# -*- coding: utf-8 -*-
"""Dialog for Z-range and grid parameter selection before LAS → Mesh conversion."""

import math

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLabel,
    QDoubleSpinBox, QDialogButtonBox, QFrame, QMessageBox,
)
from qgis.PyQt.QtCore import Qt


class GprZRangeDialog(QDialog):
    """
    Let the user fine-tune the Z range and grid parameters before the
    LAS → NetCDF mesh conversion.

    Exposed properties: z_min, z_max, z_step, resolution
    """

    def __init__(
        self,
        z_min_detected: float,
        z_max_detected: float,
        resolution: float = 0.10,
        z_step: float = 0.05,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Parametri import LAS → Mesh GPR")
        self.setMinimumWidth(380)
        self._build_ui(z_min_detected, z_max_detected, resolution, z_step)
        self._update_estimate()

    # ------------------------------------------------------------------ #

    def _build_ui(self, z_min_d, z_max_d, resolution, z_step):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        banner = QLabel(
            f"Range Z rilevato nel file:  [{z_min_d:.4f} m,  {z_max_d:.4f} m]"
        )
        banner.setStyleSheet("color: #555; font-style: italic; font-size: 11px;")
        root.addWidget(banner)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)

        def _spin(lo, hi, val, step, dec, suffix):
            sb = QDoubleSpinBox()
            sb.setDecimals(dec)
            sb.setRange(lo, hi)
            sb.setValue(val)
            sb.setSingleStep(step)
            sb.setSuffix(f"  {suffix}")
            sb.setMinimumWidth(130)
            return sb

        self.spin_zmin  = _spin(-99999, 99999, z_min_d,   0.01, 4, "m")
        self.spin_zmax  = _spin(-99999, 99999, z_max_d,   0.01, 4, "m")
        self.spin_zstep = _spin(0.0001,  100.0, z_step,   0.01, 4, "m")
        self.spin_res   = _spin(0.0001, 1000.0, resolution, 0.01, 4, "m")

        form.addRow("Z minimo:",      self.spin_zmin)
        form.addRow("Z massimo:",     self.spin_zmax)
        form.addRow("Passo Z (dz):",  self.spin_zstep)
        form.addRow("Risoluzione XY:", self.spin_res)
        root.addLayout(form)

        self.lbl_estimate = QLabel()
        self.lbl_estimate.setAlignment(Qt.AlignCenter)
        self.lbl_estimate.setStyleSheet(
            "font-weight: bold; color: #1565c0; padding: 4px; font-size: 12px;"
        )
        root.addWidget(self.lbl_estimate)

        self.spin_zmin.valueChanged.connect(self._update_estimate)
        self.spin_zmax.valueChanged.connect(self._update_estimate)
        self.spin_zstep.valueChanged.connect(self._update_estimate)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _update_estimate(self):
        try:
            z_range = self.spin_zmax.value() - self.spin_zmin.value()
            dz = self.spin_zstep.value()
            if z_range <= 0 or dz <= 0:
                self.lbl_estimate.setText("⚠\u2009 Z massimo deve essere > Z minimo")
                return
            n = max(1, math.ceil(z_range / dz))
            self.lbl_estimate.setText(f"\u2192\u2009 {n} slice stimate")
        except Exception:
            self.lbl_estimate.setText("")

    def _on_accept(self):
        if self.spin_zmax.value() <= self.spin_zmin.value():
            QMessageBox.warning(
                self,
                "Parametri non validi",
                "Z massimo deve essere maggiore di Z minimo.",
            )
            return
        self.accept()

    # ------------------------------------------------------------------ #
    # Properties

    @property
    def z_min(self) -> float:
        return self.spin_zmin.value()

    @property
    def z_max(self) -> float:
        return self.spin_zmax.value()

    @property
    def z_step(self) -> float:
        return self.spin_zstep.value()

    @property
    def resolution(self) -> float:
        return self.spin_res.value()
