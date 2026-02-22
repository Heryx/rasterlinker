# -*- coding: utf-8 -*-
"""Canvas click filter for trace vertex context capture."""

from qgis.PyQt.QtCore import QObject, QEvent, Qt


class TraceCanvasClickFilter(QObject):
    def __init__(self, on_left_click=None, parent=None):
        super().__init__(parent)
        self.on_left_click = on_left_click
        self.enabled = False

    def eventFilter(self, _obj, event):
        if not self.enabled:
            return False
        if event is None:
            return False
        try:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                if callable(self.on_left_click):
                    self.on_left_click()
        except Exception:
            pass
        return False

