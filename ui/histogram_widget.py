"""
ui/histogram_widget.py
Renders a live RGBA histogram overlay.
"""

from __future__ import annotations
import numpy as np
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QPen, QFont


class HistogramWidget(QWidget):
    PREFERRED_H = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict | None = None
        self.setMinimumHeight(self.PREFERRED_H)
        self.setMaximumHeight(self.PREFERRED_H * 2)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def update_histogram(self, data: dict):
        self._data = data
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(256, self.PREFERRED_H)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Background
        painter.fillRect(0, 0, w, h, QColor("#1a1a1a"))

        if self._data is None:
            painter.setPen(QColor("#555"))
            painter.setFont(QFont("monospace", 9))
            painter.drawText(w // 2 - 40, h // 2, "no image")
            return

        # Draw each channel with partial transparency
        channels = [
            ("lum", QColor(200, 200, 200, 80)),
            ("r",   QColor(220, 60,  60,  120)),
            ("g",   QColor(60,  200, 60,  120)),
            ("b",   QColor(60,  100, 220, 120)),
        ]

        for ch_name, color in channels:
            if ch_name not in self._data:
                continue
            hist = self._data[ch_name]
            self._draw_channel(painter, hist, color, w, h)

        # Grid lines
        pen = QPen(QColor(255, 255, 255, 20))
        pen.setWidth(1)
        painter.setPen(pen)
        for x in [w // 4, w // 2, 3 * w // 4]:
            painter.drawLine(x, 0, x, h)

    def _draw_channel(self, painter: QPainter, hist: np.ndarray,
                      color: QColor, w: int, h: int):
        n = len(hist)
        path = QPainterPath()
        path.moveTo(0, h)
        for i, v in enumerate(hist):
            x = i / (n - 1) * w
            y = h - v * (h - 2)
            if i == 0:
                path.lineTo(x, y)
            else:
                path.lineTo(x, y)
        path.lineTo(w, h)
        path.closeSubpath()

        fill_color = QColor(color)
        painter.fillPath(path, fill_color)

        pen = QPen(color.lighter(130))
        pen.setWidthF(0.8)
        painter.setPen(pen)
        painter.drawPath(path)
