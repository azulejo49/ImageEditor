"""
ui/curve_widget.py
==================
INTERACTIVE TONE CURVE EDITOR
------------------------------
Renders an editable Bezier-style spline over a histogram background.
The user adds, moves, or removes control points to shape the curve.

COORDINATE SYSTEM
-----------------
Widget space: (0,0) top-left, (W,H) bottom-right.
Curve space:  (0,0) = black input/black output (bottom-left in photographic convention).
             (1,1) = white input/white output (top-right).
The Y axis is FLIPPED: higher curve output = brighter = higher on screen.

INTERACTION
-----------
- Click on empty area          → add new control point
- Click and drag a point       → move it
- Right-click a point          → remove it (min 2 points kept)
- Points are always sorted by X so the curve stays a function (no loops).

SIGNAL
------
curve_changed(list) emits the control points as [(x,y), ...] whenever
any point changes. MainWindow connects this to the pipeline snapshot
and re-render trigger.
"""

from __future__ import annotations
import numpy as np
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPoint, QPointF, pyqtSignal, QSize
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QPainterPath, QLinearGradient,
    QBrush, QFont,
)


class CurveWidget(QWidget):
    """
    Interactive tone curve editor.
    Emits curve_changed whenever control points are modified.
    """

    curve_changed = pyqtSignal(list)   # list of (float, float) tuples

    # Diameter of draggable control point circles
    POINT_RADIUS = 6
    # Snap distance for clicking near a point (px)
    SNAP_DIST    = 10
    # Minimum number of control points (endpoints are protected)
    MIN_POINTS   = 2

    def __init__(self, parent=None):
        super().__init__(parent)

        # Control points in curve space [0.0–1.0] sorted by x
        # Default: identity curve (straight diagonal = no change)
        self._points: list[tuple[float, float]] = [(0.0, 0.0), (1.0, 1.0)]

        # Index of the point currently being dragged (-1 = none)
        self._drag_idx: int = -1

        # Histogram background data (optional, for visual context)
        self._hist_lum: np.ndarray | None = None

        self.setMinimumSize(200, 160)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ── Public API ─────────────────────────────────────────────────────────

    def set_points(self, points: list):
        """
        Set control points from external source (e.g. undo/redo).
        Sorts them by x to ensure valid curve.
        """
        self._points = sorted(points, key=lambda p: p[0])
        self.update()

    def set_histogram(self, hist_lum: np.ndarray):
        """
        Provide luminance histogram data for background display.
        Helps user see tone distribution while adjusting the curve.
        """
        self._hist_lum = hist_lum
        self.update()

    def reset(self):
        """Reset to identity (no adjustment)."""
        self._points = [(0.0, 0.0), (1.0, 1.0)]
        self.update()
        self.curve_changed.emit(self._points)

    # ── Painting ───────────────────────────────────────────────────────────

    def sizeHint(self) -> QSize:
        return QSize(240, 200)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        pad  = 12   # padding around the curve area

        # ── Background ────────────────────────────────────────────────────
        painter.fillRect(0, 0, w, h, QColor("#1a1a1a"))

        # ── Histogram background (if available) ──────────────────────────
        # Draws a subtle luminance histogram so user can see tone distribution
        if self._hist_lum is not None:
            self._draw_hist_bg(painter, pad, w, h)

        # ── Grid ──────────────────────────────────────────────────────────
        # 4×4 grid dividing the curve area into tonal zones
        grid_pen = QPen(QColor(255, 255, 255, 18))
        grid_pen.setWidthF(0.5)
        painter.setPen(grid_pen)
        cw = w - 2 * pad   # curve area width
        ch = h - 2 * pad   # curve area height
        for i in range(1, 4):
            x = pad + cw * i // 4
            y = pad + ch * i // 4
            painter.drawLine(x, pad, x, h - pad)
            painter.drawLine(pad, y, w - pad, y)

        # ── Border ────────────────────────────────────────────────────────
        border_pen = QPen(QColor("#333"))
        painter.setPen(border_pen)
        painter.drawRect(pad, pad, cw, ch)

        # ── Identity diagonal ─────────────────────────────────────────────
        # Dashed grey line showing the "no change" position
        diag_pen = QPen(QColor(255, 255, 255, 35))
        diag_pen.setStyle(Qt.PenStyle.DashLine)
        diag_pen.setWidthF(0.8)
        painter.setPen(diag_pen)
        painter.drawLine(pad, h - pad, w - pad, pad)

        # ── Curve ─────────────────────────────────────────────────────────
        self._draw_curve(painter, pad, w, h)

        # ── Control points ────────────────────────────────────────────────
        self._draw_points(painter, pad, w, h)

    def _draw_hist_bg(self, painter: QPainter, pad: int, w: int, h: int):
        """
        Draw histogram bars as a dim filled area behind the curve.
        Uses the luminance channel so it matches the tone curve purpose.
        """
        cw = w - 2 * pad
        ch = h - 2 * pad
        n  = len(self._hist_lum)

        hist_path = QPainterPath()
        hist_path.moveTo(pad, h - pad)
        for i, v in enumerate(self._hist_lum):
            x = pad + i / (n - 1) * cw
            y = (h - pad) - v * ch   # histogram grows upward
            hist_path.lineTo(x, y)
        hist_path.lineTo(w - pad, h - pad)
        hist_path.closeSubpath()

        # Semi-transparent fill — dim enough not to distract from curve
        painter.fillPath(hist_path, QColor(120, 120, 120, 35))

    def _draw_curve(self, painter: QPainter, pad: int, w: int, h: int):
        """
        Build and draw the interpolated curve through all control points.
        Uses numpy linear interpolation (or scipy Pchip if available) at
        100 sample points for a smooth visual.
        """
        cw = w - 2 * pad
        ch = h - 2 * pad

        # Sample the curve at 100 evenly-spaced input values
        xs_ctrl = np.array([p[0] for p in self._points])
        ys_ctrl = np.array([p[1] for p in self._points])
        x_sample = np.linspace(0, 1, 100)

        try:
            from scipy.interpolate import PchipInterpolator
            # Pchip = monotonic cubic — no ringing between points
            interp = PchipInterpolator(xs_ctrl, ys_ctrl)
            y_sample = np.clip(interp(x_sample), 0, 1)
        except ImportError:
            # Numpy linear fallback — straight lines between points
            y_sample = np.clip(np.interp(x_sample, xs_ctrl, ys_ctrl), 0, 1)

        # Build QPainterPath from samples
        path = QPainterPath()
        for i, (xi, yi) in enumerate(zip(x_sample, y_sample)):
            # Curve space → widget space: X goes right, Y goes UP (hence h-pad-y*ch)
            sx = pad + xi * cw
            sy = (h - pad) - yi * ch
            if i == 0:
                path.moveTo(sx, sy)
            else:
                path.lineTo(sx, sy)

        # Draw the curve with a bright blue line
        curve_pen = QPen(QColor("#4d87c8"))
        curve_pen.setWidthF(1.8)
        curve_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(curve_pen)
        painter.drawPath(path)

        # Fill area below curve with a subtle gradient tint
        fill_path = QPainterPath(path)
        fill_path.lineTo(w - pad, h - pad)
        fill_path.lineTo(pad, h - pad)
        fill_path.closeSubpath()
        painter.fillPath(fill_path, QColor(77, 135, 200, 25))

    def _draw_points(self, painter: QPainter, pad: int, w: int, h: int):
        """
        Draw draggable control point circles.
        Dragging point gets a brighter colour.
        """
        cw = w - 2 * pad
        ch = h - 2 * pad
        r  = self.POINT_RADIUS

        for i, (px, py) in enumerate(self._points):
            sx = pad + px * cw
            sy = (h - pad) - py * ch

            # Outer ring
            painter.setPen(QPen(QColor("#4d87c8"), 1.5))
            color = QColor("#7ab8f5") if i == self._drag_idx else QColor("#1e1e1e")
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QPointF(sx, sy), r, r)

    # ── Mouse interaction ──────────────────────────────────────────────────

    def mousePressEvent(self, event):
        w, h, pad = self.width(), self.height(), 12
        cw, ch = w - 2 * pad, h - 2 * pad

        # Convert screen position to curve space [0,1]
        cx = (event.position().x() - pad) / cw
        cy = 1.0 - (event.position().y() - pad) / ch
        cx = max(0.0, min(1.0, cx))
        cy = max(0.0, min(1.0, cy))

        if event.button() == Qt.MouseButton.LeftButton:
            # Check if clicking near an existing point
            idx = self._find_point(event.position().toPoint(), pad, cw, ch)
            if idx >= 0:
                # Start dragging existing point
                self._drag_idx = idx
            else:
                # Add new control point at click position
                self._points.append((cx, cy))
                self._points.sort(key=lambda p: p[0])   # keep sorted by x
                self._drag_idx = self._points.index(
                    min(self._points, key=lambda p: abs(p[0] - cx))
                )
                self.curve_changed.emit(self._points)
                self.update()

        elif event.button() == Qt.MouseButton.RightButton:
            # Remove point under cursor (protect endpoints and minimum count)
            idx = self._find_point(event.position().toPoint(), pad, cw, ch)
            if idx >= 0 and len(self._points) > self.MIN_POINTS:
                # Protect the black (0,0) and white (1,1) endpoints
                if 0 < idx < len(self._points) - 1:
                    self._points.pop(idx)
                    self.curve_changed.emit(self._points)
                    self.update()

    def mouseMoveEvent(self, event):
        if self._drag_idx < 0:
            return

        w, h, pad = self.width(), self.height(), 12
        cw, ch = w - 2 * pad, h - 2 * pad

        # Convert to curve space, clamped to [0,1]
        cx = max(0.0, min(1.0, (event.position().x() - pad) / cw))
        cy = max(0.0, min(1.0, 1.0 - (event.position().y() - pad) / ch))

        # Protect x position of first and last points (keep them at 0 and 1)
        if self._drag_idx == 0:
            cx = 0.0
        elif self._drag_idx == len(self._points) - 1:
            cx = 1.0

        self._points[self._drag_idx] = (cx, cy)
        self._points.sort(key=lambda p: p[0])   # re-sort after x may have moved
        # Re-find drag index after sort (point may have moved in the list)
        nearest = min(range(len(self._points)),
                      key=lambda i: abs(self._points[i][0] - cx))
        self._drag_idx = nearest

        self.curve_changed.emit(self._points)
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag_idx = -1

    def _find_point(self, screen_pos: QPoint, pad: int, cw: int, ch: int) -> int:
        """
        Find the index of a control point within SNAP_DIST pixels of screen_pos.
        Returns -1 if no point is close enough.
        """
        w, h = self.width(), self.height()
        for i, (px, py) in enumerate(self._points):
            sx = pad + px * cw
            sy = (h - pad) - py * ch
            dist = math.sqrt((screen_pos.x() - sx) ** 2 + (screen_pos.y() - sy) ** 2)
            if dist <= self.SNAP_DIST:
                return i
        return -1


import math   # needed by _find_point
