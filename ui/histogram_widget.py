"""
ui/histogram_widget.py
======================
LIVE RGB + LUMINANCE HISTOGRAM DISPLAY
---------------------------------------
Custom QWidget drawn entirely with QPainter.
No Qt charts library required — pure geometry.

CHANNELS DISPLAYED (layered, back to front)
--------------------------------------------
  Luminance  — white/grey filled area (perceptual brightness distribution)
  Red        — red semi-transparent filled area
  Green      — green semi-transparent filled area
  Blue       — blue semi-transparent filled area

Channels are layered with transparency so overlaps are visible.
Each channel is normalised independently (tallest bin = full height)
so all four channels are clearly readable even when one dominates.

GRID
----
Three faint vertical lines at 25%, 50%, 75% input values
(shadows / midtones / highlights markers).

LABELS
------
"S  M  H" labels at the bottom: Shadows, Midtones, Highlights.

UPDATE CYCLE
------------
HistogramWidget.update_histogram(data) is called by MainWindow
after each render worker cycle. Qt's update() schedules a repaint
on the next frame — no UI thread blocking.
"""

from __future__ import annotations
import numpy as np
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QPen, QFont


class HistogramWidget(QWidget):
    """
    Renders a four-channel histogram (R, G, B, luminance) as filled curves.
    """

    # Default and maximum pixel heights
    PREFERRED_H = 90

    def __init__(self, parent=None):
        super().__init__(parent)

        # Histogram data: dict with keys 'r', 'g', 'b', 'lum'
        # Each value is a float32 array of 256 bins, normalised 0–1.
        # None = no image loaded yet.
        self._data: dict | None = None

        self.setMinimumHeight(self.PREFERRED_H)
        self.setMaximumHeight(self.PREFERRED_H * 2)
        # WA_OpaquePaintEvent: tells Qt we fill every pixel — skip background erase
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    # ── Public API ─────────────────────────────────────────────────────────

    def update_histogram(self, data: dict):
        """
        Receive new histogram data from the render pipeline.
        Stores the data and triggers a repaint on the next Qt frame.
        Called from the UI thread after RenderWorker emits rendered_ready.
        """
        self._data = data
        self.update()   # schedule repaint (non-blocking)

    def sizeHint(self) -> QSize:
        return QSize(256, self.PREFERRED_H)

    # ── Painting ───────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # ── Solid dark background ─────────────────────────────────────────
        painter.fillRect(0, 0, w, h, QColor("#1a1a1a"))

        # ── Empty state ───────────────────────────────────────────────────
        if self._data is None:
            painter.setPen(QColor("#444"))
            painter.setFont(QFont("monospace", 8))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "histogram"
            )
            return

        # ── Channel layers (back to front for correct blending) ───────────
        # Order: luminance behind colour channels so RGB is readable on top.
        # Alpha values chosen so overlapping channels remain distinguishable.
        channels = [
            ("lum", QColor(210, 210, 210, 70)),    # grey, very transparent
            ("r",   QColor(220, 55,  55,  110)),   # red
            ("g",   QColor(55,  200, 55,  110)),   # green
            ("b",   QColor(55,  100, 220, 110)),   # blue
        ]

        for ch_name, color in channels:
            if ch_name not in self._data:
                continue
            self._draw_channel(painter, self._data[ch_name], color, w, h)

        # ── Grid lines at shadows/midtones/highlights ─────────────────────
        grid_pen = QPen(QColor(255, 255, 255, 18))
        grid_pen.setWidthF(0.8)
        painter.setPen(grid_pen)
        for frac in (0.25, 0.50, 0.75):
            x = int(w * frac)
            painter.drawLine(x, 0, x, h - 14)   # stop above the labels

        # ── Zone labels: S  M  H ─────────────────────────────────────────
        # "S" = shadows (left), "M" = midtones (centre), "H" = highlights (right)
        painter.setPen(QColor(80, 80, 80))
        painter.setFont(QFont("monospace", 7))
        painter.drawText(2,       h - 2, "S")
        painter.drawText(w//2-4,  h - 2, "M")
        painter.drawText(w - 10,  h - 2, "H")

        # ── Top border line ───────────────────────────────────────────────
        painter.setPen(QPen(QColor("#2e2e2e")))
        painter.drawLine(0, 0, w, 0)

    def _draw_channel(self, painter: QPainter, hist: np.ndarray,
                      color: QColor, w: int, h: int):
        """
        Draw one histogram channel as a filled path.

        Method:
          1. Build a QPainterPath tracing the histogram curve from left to right.
          2. Close the path at the bottom to create a filled polygon.
          3. Fill with the semi-transparent colour.
          4. Stroke the top edge with a slightly brighter version of the colour.

        The path maps bin index i → x position, bin value v → y position.
        v=0 (empty bin) → y = bottom of widget
        v=1 (full bin)  → y = top of widget (minus small padding)
        """
        n       = len(hist)
        pad_top = 4   # leave a few pixels at top so full bins don't clip

        path = QPainterPath()
        path.moveTo(0, h)   # start at bottom-left

        for i, v in enumerate(hist):
            x = i / (n - 1) * w
            y = h - v * (h - pad_top)   # map value to y (inverted: high=up)
            path.lineTo(x, y)

        path.lineTo(w, h)   # bottom-right corner
        path.closeSubpath() # back to bottom-left → closed polygon

        # Fill with semi-transparent colour
        painter.fillPath(path, color)

        # Stroke the curve edge slightly brighter
        edge_pen = QPen(color.lighter(140))
        edge_pen.setWidthF(0.9)
        painter.setPen(edge_pen)
        painter.drawPath(path)
