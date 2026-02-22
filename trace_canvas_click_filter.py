# -*- coding: utf-8 -*-
"""Canvas click filter for trace vertex context capture."""

from qgis.PyQt.QtCore import QObject, QEvent, Qt


class TraceCanvasClickFilter(QObject):
    def __init__(self, on_left_click=None, on_wheel=None, wheel_modifier_getter=None, parent=None):
        super().__init__(parent)
        self.on_left_click = on_left_click
        self.on_wheel = on_wheel
        self.wheel_modifier_getter = wheel_modifier_getter
        self.enabled = False

    def _required_modifier(self):
        try:
            txt = str(self.wheel_modifier_getter() if callable(self.wheel_modifier_getter) else "alt").strip().lower()
        except Exception:
            txt = "alt"
        if txt not in ("alt", "shift", "ctrl"):
            txt = "alt"
        return txt

    def _matches_modifier(self, modifiers):
        req = self._required_modifier()
        if req == "alt":
            return bool(modifiers & Qt.AltModifier)
        if req == "shift":
            return bool(modifiers & Qt.ShiftModifier)
        return bool(modifiers & Qt.ControlModifier)

    def eventFilter(self, _obj, event):
        if not self.enabled:
            return False
        if event is None:
            return False
        try:
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
