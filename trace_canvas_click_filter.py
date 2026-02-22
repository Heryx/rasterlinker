# -*- coding: utf-8 -*-
"""Canvas click filter for trace vertex context capture."""

from qgis.PyQt.QtCore import QObject, QEvent, Qt
from qgis.PyQt.QtWidgets import QApplication


class TraceCanvasClickFilter(QObject):
    def __init__(self, on_left_click=None, on_wheel=None, wheel_modifier_getter=None, parent=None):
        super().__init__(parent)
        self.on_left_click = on_left_click
        self.on_wheel = on_wheel
        self.wheel_modifier_getter = wheel_modifier_getter
        self.enabled = False
        self._alt_pressed = False

    def _required_modifier(self):
        try:
            txt = str(self.wheel_modifier_getter() if callable(self.wheel_modifier_getter) else "alt").strip().lower()
        except Exception:
            txt = "alt"
        if txt not in ("alt", "shift", "ctrl"):
            txt = "alt"
        return txt

    def _matches_modifier(self, modifiers):
        # Some platforms/toolchains may not propagate Alt consistently in
        # wheel events; use both event modifiers and global keyboard state.
        kb_mods = Qt.NoModifier
        try:
            kb_mods = QApplication.keyboardModifiers()
        except Exception:
            kb_mods = Qt.NoModifier
        try:
            kb_query_mods = QApplication.queryKeyboardModifiers()
        except Exception:
            kb_query_mods = Qt.NoModifier

        mods = modifiers | kb_mods | kb_query_mods
        if self._alt_pressed:
            mods = mods | Qt.AltModifier

        req = self._required_modifier()
        if req == "alt":
            return bool(mods & Qt.AltModifier)
        if req == "shift":
            return bool(mods & Qt.ShiftModifier)
        return bool(mods & Qt.ControlModifier)

    def eventFilter(self, _obj, event):
        if not self.enabled:
            return False
        if event is None:
            return False
        try:
            if event.type() == QEvent.KeyPress:
                try:
                    if event.key() == Qt.Key_Alt:
                        self._alt_pressed = True
                except Exception:
                    pass
                return False
            if event.type() == QEvent.KeyRelease:
                try:
                    if event.key() == Qt.Key_Alt:
                        self._alt_pressed = False
                except Exception:
                    pass
                return False
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                if callable(self.on_left_click):
                    self.on_left_click()
                return False
            if event.type() == QEvent.Wheel:
                mods = event.modifiers()
                if not self._matches_modifier(mods):
                    return False
                delta = 0
                try:
                    delta = int(event.angleDelta().y())
                    if delta == 0:
                        delta = int(event.angleDelta().x())
                except Exception:
                    try:
                        delta = int(event.delta())
                    except Exception:
                        delta = 0
                if delta == 0:
                    return False
                handled = False
                if callable(self.on_wheel):
                    try:
                        handled = bool(self.on_wheel(delta))
                    except Exception:
                        handled = False
                return bool(handled)
        except Exception:
            pass
        return False
