# -*- coding: utf-8 -*-
"""
Helpers per i controlli opzioni griglia nel dock.
"""

from PyQt5.QtWidgets import QCheckBox, QComboBox, QLabel, QHBoxLayout, QVBoxLayout, QWidget, QPushButton


def build_grid_options_controls(
    layout,
    use_snap,
    force_orthogonal,
    relative_orthogonal,
    keep_source_polygon,
    dimension_mode,
):
    """
    Costruisce i controlli UI opzioni griglia e restituisce i widget creati.
    """
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # Container a due righe per evitare testi troncati nel dock stretto.
    wrapper = QWidget()
    wrapper_layout = QVBoxLayout(wrapper)
    wrapper_layout.setContentsMargins(0, 0, 0, 0)
    wrapper_layout.setSpacing(2)

    top_row = QHBoxLayout()
    top_row.setContentsMargins(0, 0, 0, 0)
    top_row.setSpacing(6)

    bottom_row = QHBoxLayout()
    bottom_row.setContentsMargins(0, 0, 0, 0)
    bottom_row.setSpacing(6)

    footer_row = QHBoxLayout()
    footer_row.setContentsMargins(0, 0, 0, 0)
    footer_row.setSpacing(6)

    snap_checkbox = QCheckBox("Snap")
    snap_checkbox.setChecked(use_snap)
    snap_checkbox.setToolTip("Use QGIS snapping while drawing and previewing the polygon.")

    ortho_checkbox = QCheckBox("Ortho 0/90")
    ortho_checkbox.setChecked(force_orthogonal)
    ortho_checkbox.setToolTip("Force orthogonal orientation for preview and click points.")

    ortho_base_checkbox = QCheckBox("Ortho base")
    ortho_base_checkbox.setChecked(relative_orthogonal)
    ortho_base_checkbox.setToolTip("Use orthogonal snapping relative to the drawn base orientation.")

    keep_area_checkbox = QCheckBox("Keep area")
    keep_area_checkbox.setChecked(keep_source_polygon)
    keep_area_checkbox.setToolTip("Keep the source polygon layer in the project after grid generation.")

    mode_label = QLabel("Mode")
    dimension_mode_combo = QComboBox()
    dimension_mode_combo.addItem("Ask", "ask")
    dimension_mode_combo.addItem("Manual", "manual")
    dimension_mode_combo.addItem("Canvas", "canvas")
    mode_index = dimension_mode_combo.findData(dimension_mode)
    dimension_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
    dimension_mode_combo.setMinimumContentsLength(7)
    dimension_mode_combo.setToolTip("How to choose total length/width after orientation lock.")

    top_row.addWidget(snap_checkbox)
    top_row.addWidget(ortho_checkbox)
    top_row.addWidget(ortho_base_checkbox)
    top_row.addStretch(1)

    bottom_row.addWidget(keep_area_checkbox)
    bottom_row.addWidget(mode_label)
    bottom_row.addWidget(dimension_mode_combo)

    help_button = QPushButton("Help")
    help_button.setToolTip("Show drawing shortcuts and workflow.")
    help_button.setFixedWidth(52)
    export_button = QPushButton("Export")
    export_button.setToolTip("Export latest area + grid to GeoPackage and load it into the current project.")
    export_button.setFixedWidth(58)
    angle_label = QLabel("Base: --")
    angle_label.setMinimumWidth(74)
    bottom_row.addStretch(1)

    length_label = QLabel("Len: --")
    length_label.setMinimumWidth(74)
    footer_row.addWidget(help_button)
    footer_row.addWidget(export_button)
    footer_row.addWidget(angle_label)
    footer_row.addWidget(length_label)
    footer_row.addStretch(1)

    wrapper_layout.addLayout(top_row)
    wrapper_layout.addLayout(bottom_row)
    wrapper_layout.addLayout(footer_row)
    layout.addWidget(wrapper)

    return (
        snap_checkbox,
        ortho_checkbox,
        ortho_base_checkbox,
        keep_area_checkbox,
        dimension_mode_combo,
        help_button,
        export_button,
        angle_label,
        length_label,
    )
