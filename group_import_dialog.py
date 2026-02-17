# -*- coding: utf-8 -*-
"""
Dialog to select which catalog groups must be visible/loaded in QGIS dock.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QHBoxLayout,
    QPushButton,
)


class GroupImportDialog(QDialog):
    def __init__(self, groups, selected_group_names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Groups")
        self.resize(460, 380)
        self._groups = groups
        self._selected_group_names = set(selected_group_names or [])
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Select groups to show in the QGIS dock.\n"
                "Checked = visible/loaded, Unchecked = hidden (not deleted)."
            )
        )

        self.list_widget = QListWidget(self)
        for grp in self._groups:
            name = grp.get("name", "Group")
            count = len(grp.get("timeslice_ids", []) or [])
            item = QListWidgetItem(f"{name} ({count} images)")
            item.setData(Qt.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in self._selected_group_names else Qt.Unchecked)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)

        row = QHBoxLayout()
        check_all = QPushButton("Check All", self)
        check_all.clicked.connect(self._check_all)
        row.addWidget(check_all)

        uncheck_all = QPushButton("Uncheck All", self)
        uncheck_all.clicked.connect(self._uncheck_all)
        row.addWidget(uncheck_all)

        row.addStretch(1)

        apply_btn = QPushButton("Apply Selection", self)
        apply_btn.clicked.connect(self.accept)
        row.addWidget(apply_btn)

        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)

        layout.addLayout(row)

    def _check_all(self):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.Checked)

    def _uncheck_all(self):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.Unchecked)

    def selected_group_names(self):
        names = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                names.append(item.data(Qt.UserRole))
        return names
