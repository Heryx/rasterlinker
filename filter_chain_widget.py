from __future__ import annotations

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class FilterBlockWidget(QWidget):
    """Single reorderable processing step."""

    moved_up = pyqtSignal(str)
    moved_down = pyqtSignal(str)
    toggled = pyqtSignal(str, bool)

    def __init__(self, step_id: str, label: str, help_text: str = "", parent=None):
        super().__init__(parent)
        self.step_id = str(step_id)
        self._label_text = str(label)
        self._help_text = str(help_text or "")
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 1, 0, 1)
        outer.setSpacing(0)

        header = QFrame(self)
        header.setFrameShape(QFrame.StyledPanel)
        h = QHBoxLayout(header)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(4)

        self._btn_up = QToolButton(header)
        self._btn_up.setText("^")
        self._btn_up.setToolTip("Move up")
        self._btn_up.setFixedWidth(22)
        self._btn_up.clicked.connect(lambda: self.moved_up.emit(self.step_id))

        self._btn_down = QToolButton(header)
        self._btn_down.setText("v")
        self._btn_down.setToolTip("Move down")
        self._btn_down.setFixedWidth(22)
        self._btn_down.clicked.connect(lambda: self.moved_down.emit(self.step_id))

        self._chk_enabled = QCheckBox(header)
        self._chk_enabled.setChecked(True)
        self._chk_enabled.setToolTip("Enable/disable this step")
        self._chk_enabled.toggled.connect(lambda v: self.toggled.emit(self.step_id, bool(v)))

        self._btn_expand = QToolButton(header)
        self._btn_expand.setText("> " + self._label_text)
        self._btn_expand.setCheckable(True)
        self._btn_expand.setChecked(False)
        self._btn_expand.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._btn_expand.toggled.connect(self._on_expand_toggled)

        h.addWidget(self._btn_up)
        h.addWidget(self._btn_down)
        h.addWidget(self._chk_enabled)
        h.addWidget(self._btn_expand, 1)

        self._content = QWidget(self)
        self._content.setVisible(False)
        c = QVBoxLayout(self._content)
        c.setContentsMargins(12, 3, 6, 5)
        c.setSpacing(2)
        self._content_layout = c
        self._lbl_help = QLabel(self._help_text or "No extra parameters")
        self._lbl_help.setWordWrap(True)
        c.addWidget(self._lbl_help)
        self._params_widget = None

        outer.addWidget(header)
        outer.addWidget(self._content)

    def _on_expand_toggled(self, checked: bool):
        self._btn_expand.setText(("v " if checked else "> ") + self._label_text)
        self._content.setVisible(bool(checked))

    def set_enabled(self, enabled: bool):
        self._chk_enabled.setChecked(bool(enabled))

    def is_enabled(self) -> bool:
        return bool(self._chk_enabled.isChecked())

    def set_move_enabled(self, can_move_up: bool, can_move_down: bool):
        self._btn_up.setEnabled(bool(can_move_up))
        self._btn_down.setEnabled(bool(can_move_down))

    def set_content_widget(self, widget: QWidget | None):
        """Attach custom parameter controls shown when the block is expanded."""
        if self._params_widget is not None:
            try:
                self._content_layout.removeWidget(self._params_widget)
                self._params_widget.setParent(None)
            except Exception:
                pass
            self._params_widget = None
        if widget is not None:
            self._params_widget = widget
            self._content_layout.addWidget(widget)


class FilterChainWidget(QWidget):
    """Ordered collection of filter blocks with up/down controls."""

    chain_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._blocks: list[FilterBlockWidget] = []
        self._suspend_emit = False
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._layout.addStretch(1)

    def add_block(self, block: FilterBlockWidget):
        self._blocks.append(block)
        self._layout.insertWidget(len(self._blocks) - 1, block)
        block.moved_up.connect(lambda sid: self._move_block(str(sid), -1))
        block.moved_down.connect(lambda sid: self._move_block(str(sid), +1))
        block.toggled.connect(lambda *_: self._emit_changed())
        self._refresh_move_buttons()

    def _emit_changed(self):
        if not self._suspend_emit:
            self.chain_changed.emit()

    def _move_block(self, step_id: str, delta: int):
        idx = next((i for i, b in enumerate(self._blocks) if b.step_id == step_id), -1)
        if idx < 0:
            return
        new_idx = idx + int(delta)
        if new_idx < 0 or new_idx >= len(self._blocks):
            return
        block = self._blocks.pop(idx)
        self._blocks.insert(new_idx, block)
        self._rebuild_layout()
        self._emit_changed()

    def _rebuild_layout(self):
        for b in self._blocks:
            self._layout.removeWidget(b)
        for i, b in enumerate(self._blocks):
            self._layout.insertWidget(i, b)
        self._refresh_move_buttons()

    def _refresh_move_buttons(self):
        n = len(self._blocks)
        for i, b in enumerate(self._blocks):
            b.set_move_enabled(i > 0, i < n - 1)

    def get_ordered_chain_ids(self) -> list[str]:
        return [b.step_id for b in self._blocks]

    def get_enabled_map(self) -> dict[str, bool]:
        return {b.step_id: b.is_enabled() for b in self._blocks}

    def set_enabled_map(self, enabled_map: dict[str, bool], emit_signal: bool = False):
        prev = self._suspend_emit
        self._suspend_emit = not bool(emit_signal)
        try:
            for b in self._blocks:
                if b.step_id in enabled_map:
                    b.set_enabled(bool(enabled_map[b.step_id]))
        finally:
            self._suspend_emit = prev
        if emit_signal:
            self.chain_changed.emit()

    def set_order(self, ordered_ids: list[str], emit_signal: bool = False):
        if not ordered_ids:
            return
        order_map = {sid: i for i, sid in enumerate(ordered_ids)}
        self._blocks.sort(key=lambda b: order_map.get(b.step_id, len(order_map)))
        self._rebuild_layout()
        if emit_signal:
            self._emit_changed()
