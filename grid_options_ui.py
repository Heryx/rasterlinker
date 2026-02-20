# -*- coding: utf-8 -*-
"""
Helpers for grid option controls in the dock.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
    QPushButton,
    QGroupBox,
    QSizePolicy,
)


def build_grid_options_controls(
    layout,
    use_snap,
    snap_mode,
    snap_tolerance,
    snap_units,
    force_orthogonal,
    relative_orthogonal,
    keep_source_polygon,
    dimension_mode,
):
    """
    Builds grid option UI controls and returns created widgets.
    """
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    wrapper = QWidget()
    wrapper.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    wrapper_layout = QVBoxLayout(wrapper)
    wrapper_layout.setContentsMargins(0, 0, 0, 0)
    wrapper_layout.setSpacing(6)

    group = QGroupBox("Drawing Options")
    group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    group.setMinimumHeight(0)
    group.setMinimumWidth(0)
    group_layout = QVBoxLayout(group)
    group_layout.setContentsMargins(10, 10, 10, 10)
    group_layout.setSpacing(10)

    flags_grid = QGridLayout()
    flags_grid.setContentsMargins(0, 0, 0, 0)
    flags_grid.setHorizontalSpacing(8)
    flags_grid.setVerticalSpacing(8)

    snap_checkbox = QCheckBox("Snap")
    snap_checkbox.setChecked(use_snap)
    snap_checkbox.setToolTip("Use QGIS snapping while drawing and previewing the polygon.")
    snap_checkbox.setMinimumHeight(20)

    ortho_checkbox = QCheckBox("Ortho 0/90")
    ortho_checkbox.setChecked(force_orthogonal)
    ortho_checkbox.setToolTip("Force orthogonal orientation for preview and click points.")
    ortho_checkbox.setMinimumHeight(20)

    ortho_base_checkbox = QCheckBox("Ortho base")
    ortho_base_checkbox.setChecked(relative_orthogonal)
    ortho_base_checkbox.setToolTip("Use orthogonal snapping relative to the drawn base orientation.")
    ortho_base_checkbox.setMinimumHeight(20)

    keep_area_checkbox = QCheckBox("Keep area")
    keep_area_checkbox.setChecked(keep_source_polygon)
    keep_area_checkbox.setToolTip("Keep source polygon layer in project after grid generation.")
    keep_area_checkbox.setMinimumHeight(20)

    # Single-column flags improve readability on narrow dock widths and HiDPI.
    flags_grid.addWidget(snap_checkbox, 0, 0, 1, 2)
    flags_grid.addWidget(ortho_checkbox, 1, 0, 1, 2)
    flags_grid.addWidget(ortho_base_checkbox, 2, 0, 1, 2)
    flags_grid.addWidget(keep_area_checkbox, 3, 0, 1, 2)
    flags_grid.setColumnStretch(0, 1)
    flags_grid.setColumnStretch(1, 1)

    snap_mode_combo = QComboBox()
    snap_mode_combo.addItem("All", "all")
    snap_mode_combo.addItem("Vertex + Segment", "vertex_segment")
    snap_mode_combo.addItem("Vertex", "vertex")
    snap_mode_combo.addItem("Segment", "segment")
    snap_mode_combo.addItem("Intersection", "intersection")
    snap_mode_index = snap_mode_combo.findData(snap_mode)
    snap_mode_combo.setCurrentIndex(snap_mode_index if snap_mode_index >= 0 else 0)
    snap_mode_combo.setMinimumWidth(104)
    snap_mode_combo.setToolTip("Snap target type used while drawing.")

    snap_tolerance_spin = QDoubleSpinBox()
    snap_tolerance_spin.setDecimals(2)
    snap_tolerance_spin.setRange(0.01, 1000000.0)
    snap_tolerance_spin.setValue(float(snap_tolerance))
    snap_tolerance_spin.setSingleStep(1.0)
    snap_tolerance_spin.setMinimumWidth(72)
    snap_tolerance_spin.setToolTip("Snap tolerance value.")

    snap_units_combo = QComboBox()
    snap_units_combo.addItem("px", "pixels")
    snap_units_combo.addItem("mm", "mm")
    snap_units_combo.addItem("cm", "cm")
    snap_units_combo.addItem("map", "map_units")
    snap_units_index = snap_units_combo.findData(snap_units)
    snap_units_combo.setCurrentIndex(snap_units_index if snap_units_index >= 0 else 0)
    snap_units_combo.setMinimumWidth(64)
    snap_units_combo.setToolTip("Tolerance unit: pixels, mm/cm on screen, or map units.")

    dimension_mode_combo = QComboBox()
    dimension_mode_combo.addItem("Ask", "ask")
    dimension_mode_combo.addItem("Manual", "manual")
    dimension_mode_combo.addItem("Canvas (Free)", "canvas")
    mode_index = dimension_mode_combo.findData(dimension_mode)
    dimension_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
    dimension_mode_combo.setMinimumWidth(92)
    dimension_mode_combo.setToolTip(
        "Ask: choose rectangle method at 3rd click. "
        "Manual: numeric rectangle. "
        "Canvas (Free): keep polygon free-form (use D/middle-click for rectangle mode)."
    )

    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setHorizontalSpacing(8)
    form.setVerticalSpacing(10)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.WrapLongRows)
    form.addRow("Snap to", snap_mode_combo)
    form.addRow("Tolerance", snap_tolerance_spin)
    form.addRow("Units", snap_units_combo)
    form.addRow("Dimension Input", dimension_mode_combo)

    buttons_row = QHBoxLayout()
    buttons_row.setContentsMargins(0, 0, 0, 0)
    buttons_row.setSpacing(10)

    help_button = QPushButton("Help")
    help_button.setToolTip("Show drawing shortcuts and workflow.")
    help_button.setMinimumWidth(86)
    help_button.setMinimumHeight(32)

    export_button = QPushButton("Export")
    export_button.setToolTip("Export latest area + grid to GeoPackage and load it into the current project.")
    export_button.setMinimumWidth(92)
    export_button.setMinimumHeight(32)

    buttons_row.addWidget(help_button)
    buttons_row.addWidget(export_button)
    buttons_row.addStretch(1)

    info_row = QHBoxLayout()
    info_row.setContentsMargins(0, 0, 0, 0)
    info_row.setSpacing(8)

    angle_label = QLabel("Base: --")
    angle_label.setMinimumWidth(72)
    angle_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    length_label = QLabel("Len: --")
    length_label.setMinimumWidth(72)
    length_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    info_row.addWidget(angle_label)
    info_row.addWidget(length_label)
    info_row.addStretch(1)

    orientation_status_label = QLabel("Orientation: idle (click Set Orientation: P0 -> X1 -> Y1)")
    orientation_status_label.setWordWrap(True)
    orientation_status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    orientation_status_label.setStyleSheet("color: #505050;")
    orientation_status_label.setMinimumHeight(18)

    group_layout.addLayout(flags_grid)
    group_layout.addLayout(form)
    group_layout.addLayout(buttons_row)
    group_layout.addLayout(info_row)
    group_layout.addWidget(orientation_status_label)
    group_layout.addStretch(1)

    wrapper_layout.addWidget(group)
    layout.addWidget(wrapper)

    return (
        snap_checkbox,
        snap_mode_combo,
        snap_tolerance_spin,
        snap_units_combo,
        ortho_checkbox,
        ortho_base_checkbox,
        keep_area_checkbox,
        dimension_mode_combo,
        help_button,
        export_button,
        angle_label,
        length_label,
        orientation_status_label,
    )
