# -*- coding: utf-8 -*-
"""
Link editor to manage radargram <-> line/time-slice relations.
"""

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QDoubleSpinBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
)

from .project_catalog import load_catalog, save_catalog, normalize_link_record, utc_now_iso


class LinkEditorDialog(QDialog):
    def __init__(self, project_root, parent=None):
        super().__init__(parent)
        self.project_root = project_root
        self._catalog = {}
        self._current_links = []
        self.setWindowTitle("RasterLinker Link Editor")
        self.resize(980, 460)
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel("Create links between radargrams and survey line/time-slice identifiers.")
        layout.addWidget(info)

        form = QFormLayout()
        self.radargram_combo = QComboBox()
        form.addRow("Radargram", self.radargram_combo)

        self.line_id_edit = QLineEdit()
        self.line_id_edit.setPlaceholderText("Example: L001")
        form.addRow("Line ID", self.line_id_edit)

        self.timeslice_id_edit = QLineEdit()
        self.timeslice_id_edit.setPlaceholderText("Optional timeslice id")
        form.addRow("Timeslice ID", self.timeslice_id_edit)

        self.trace_from_edit = QLineEdit()
        self.trace_from_edit.setPlaceholderText("Optional start trace")
        form.addRow("Trace From", self.trace_from_edit)

        self.trace_to_edit = QLineEdit()
        self.trace_to_edit.setPlaceholderText("Optional end trace")
        form.addRow("Trace To", self.trace_to_edit)

        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setDecimals(2)
        self.confidence_spin.setRange(0.0, 1.0)
        self.confidence_spin.setSingleStep(0.05)
        self.confidence_spin.setValue(1.0)
        form.addRow("Confidence", self.confidence_spin)

        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("Optional notes")
        form.addRow("Notes", self.notes_edit)
        layout.addLayout(form)

        actions = QHBoxLayout()
        add_btn = QPushButton("Add Link")
        add_btn.clicked.connect(self._add_link)
        actions.addWidget(add_btn)

        update_btn = QPushButton("Update Selected")
        update_btn.clicked.connect(self._update_selected)
        actions.addWidget(update_btn)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_selected)
        actions.addWidget(remove_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        actions.addWidget(refresh_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        actions.addWidget(close_btn)

        layout.addLayout(actions)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Radargram ID", "Line ID", "Timeslice ID", "Trace", "Conf.", "Notes"]
        )
        self.table.setSelectionBehavior(self.table.SelectRows)
        self.table.setEditTriggers(self.table.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._load_selected_into_form)
        layout.addWidget(self.table)

    def _refresh(self):
        self._catalog = load_catalog(self.project_root)
        self._current_links = list(self._catalog.get("links", []))

        self.radargram_combo.blockSignals(True)
        self.radargram_combo.clear()
        for rg in self._catalog.get("radargrams", []):
            rid = rg.get("id")
            label = rg.get("normalized_name") or rid or "radargram"
            self.radargram_combo.addItem(f"{label} [{rid}]", rid)
        self.radargram_combo.blockSignals(False)

        self.table.setRowCount(len(self._current_links))
        for i, rec in enumerate(self._current_links):
            trace_txt = ""
            if rec.get("trace_from") is not None or rec.get("trace_to") is not None:
                trace_txt = f"{rec.get('trace_from')}..{rec.get('trace_to')}"
            self.table.setItem(i, 0, QTableWidgetItem(str(rec.get("id", ""))))
            self.table.setItem(i, 1, QTableWidgetItem(str(rec.get("radargram_id", ""))))
            self.table.setItem(i, 2, QTableWidgetItem(str(rec.get("line_id", ""))))
            self.table.setItem(i, 3, QTableWidgetItem(str(rec.get("timeslice_id", ""))))
            self.table.setItem(i, 4, QTableWidgetItem(trace_txt))
            self.table.setItem(i, 5, QTableWidgetItem(str(rec.get("confidence", ""))))
            self.table.setItem(i, 6, QTableWidgetItem(str(rec.get("notes", ""))))
        self.table.resizeColumnsToContents()

    def _selected_row(self):
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _load_selected_into_form(self):
        row = self._selected_row()
        if row is None or row >= len(self._current_links):
            return
        rec = self._current_links[row]

        rid = rec.get("radargram_id")
        idx = self.radargram_combo.findData(rid)
        if idx >= 0:
            self.radargram_combo.setCurrentIndex(idx)
        self.line_id_edit.setText(str(rec.get("line_id") or ""))
        self.timeslice_id_edit.setText(str(rec.get("timeslice_id") or ""))
        self.trace_from_edit.setText("" if rec.get("trace_from") is None else str(rec.get("trace_from")))
        self.trace_to_edit.setText("" if rec.get("trace_to") is None else str(rec.get("trace_to")))
        self.confidence_spin.setValue(float(rec.get("confidence", 1.0) or 1.0))
        self.notes_edit.setText(str(rec.get("notes") or ""))

    def _form_record(self):
        rid = self.radargram_combo.currentData()
        if not rid:
            return None, "Select a radargram."

        line_id = self.line_id_edit.text().strip() or None
        timeslice_id = self.timeslice_id_edit.text().strip() or None
        if not line_id and not timeslice_id:
            return None, "Provide at least Line ID or Timeslice ID."

        def _parse_int_or_none(text):
            txt = text.strip()
            if not txt:
                return None
            try:
                return int(txt)
            except Exception:
                return None

        return {
            "radargram_id": rid,
            "line_id": line_id,
            "timeslice_id": timeslice_id,
            "trace_from": _parse_int_or_none(self.trace_from_edit.text()),
            "trace_to": _parse_int_or_none(self.trace_to_edit.text()),
            "confidence": float(self.confidence_spin.value()),
            "notes": self.notes_edit.text().strip(),
        }, ""

    def _add_link(self):
        rec, err = self._form_record()
        if err:
            QMessageBox.warning(self, "Link Editor", err)
            return
        rec.update(
            {
                "id": f"link_{utc_now_iso()}",
                "created_at": utc_now_iso(),
            }
        )
        self._catalog["links"].append(normalize_link_record(rec))
        save_catalog(self.project_root, self._catalog)
        self._refresh()

    def _update_selected(self):
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "Link Editor", "Select a row first.")
            return
        rec, err = self._form_record()
        if err:
            QMessageBox.warning(self, "Link Editor", err)
            return
        old = self._current_links[row]
        rec["id"] = old.get("id")
        rec["created_at"] = old.get("created_at")
        self._catalog["links"][row] = normalize_link_record(rec)
        save_catalog(self.project_root, self._catalog)
        self._refresh()

    def _remove_selected(self):
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "Link Editor", "Select a row first.")
            return
        rec = self._current_links[row]
        answer = QMessageBox.question(
            self,
            "Remove link",
            f"Remove selected link?\n{rec.get('id')}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        del self._catalog["links"][row]
        save_catalog(self.project_root, self._catalog)
        self._refresh()
