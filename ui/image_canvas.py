"""
ui/image_canvas.py
Zoomable/pannable image viewer with zero-lag display.
Receives pre-rendered uint8 arrays from the worker.
"""

from __future__ import annotations
import numpy as np
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPoint, QRectF, QSizeF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QImage, QPixmap, QWheelEvent,
    QMouseEvent, QColor, QFont, QPen,
)


class ImageCanvas(QWidget):
    zoom_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._zoom: float = 1.0
        self._offset: QPoint = QPoint(0, 0)
        self._drag_start: QPoint | None = None
        self._drag_offset_start: QPoint | None = None
        self._loading: bool = False

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(200, 200)

    # ── Public ─────────────────────────────────────────────────────────────

    def set_image(self, img_u8: np.ndarray):
        h, w, c = img_u8.shape
        # Convert RGB uint8 → QImage → QPixmap (zero-copy via frombuffer)
        qimg = QImage(
            img_u8.tobytes(),
            w, h,
            w * 3,
            QImage.Format.Format_RGB888,
        )
        self._pixmap = QPixmap.fromImage(qimg)
        self._loading = False
        if self._zoom == 1.0 and self._offset == QPoint(0, 0):
            self.fit_to_window()
        self.update()

    def set_loading(self, loading: bool):
        self._loading = loading
        self.update()

    def fit_to_window(self):
        if self._pixmap is None:
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        ww, wh = self.width(), self.height()
        scale_x = ww / pw
        scale_y = wh / ph
        self._zoom = min(scale_x, scale_y) * 0.95
        self._center()
        self.zoom_changed.emit(self._zoom)
        self.update()

    def zoom_to(self, zoom: float):
        self._zoom = max(0.02, min(32.0, zoom))
        self._center()
        self.zoom_changed.emit(self._zoom)
        self.update()

    def zoom_1_to_1(self):
        self.zoom_to(1.0)

    @property
    def zoom(self) -> float:
        return self._zoom

    # ── Painting ───────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._zoom < 1.0)

        # Checkerboard background (for transparency awareness)
        self._draw_checkerboard(painter)

        if self._loading:
            painter.setPen(QColor("#aaa"))
            painter.setFont(QFont("monospace", 11))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Loading…")
            return

        if self._pixmap is None:
            painter.setPen(QColor("#555"))
            painter.setFont(QFont("monospace", 11))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Drop or open a RAW / JPEG file",
            )
            return

        # Draw image
        pw = int(self._pixmap.width() * self._zoom)
        ph = int(self._pixmap.height() * self._zoom)
        x = self._offset.x()
        y = self._offset.y()
        painter.drawPixmap(x, y, pw, ph, self._pixmap)

        # Zoom label
        pen = QPen(QColor(255, 255, 255, 140))
        painter.setPen(pen)
        painter.setFont(QFont("monospace", 9))
        painter.drawText(8, self.height() - 8, f"{self._zoom * 100:.0f}%")

    def _draw_checkerboard(self, painter: QPainter):
        size = 12
        c1 = QColor("#2c2c2c")
        c2 = QColor("#242424")
        cols = self.width() // size + 1
        rows = self.height() // size + 1
        for r in range(rows):
            for c in range(cols):
                color = c1 if (r + c) % 2 == 0 else c2
                painter.fillRect(c * size, r * size, size, size, color)

    # ── Resize ─────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._pixmap and self._offset == QPoint(0, 0):
            self.fit_to_window()

    # ── Mouse interaction ──────────────────────────────────────────────────

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        factor = 1.12 if delta > 0 else 1 / 1.12
        # Zoom toward cursor
        pos = event.position().toPoint()
        old_zoom = self._zoom
        new_zoom = max(0.02, min(32.0, self._zoom * factor))
        ratio = new_zoom / old_zoom
        self._offset = QPoint(
            int(pos.x() - (pos.x() - self._offset.x()) * ratio),
            int(pos.y() - (pos.y() - self._offset.y()) * ratio),
        )
        self._zoom = new_zoom
        self.zoom_changed.emit(self._zoom)
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            self._drag_start = event.pos()
            self._drag_offset_start = QPoint(self._offset)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_start is not None:
            delta = event.pos() - self._drag_start
            self._offset = self._drag_offset_start + delta
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._drag_start is not None:
            self._drag_start = None
            self._drag_offset_start = None
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self.fit_to_window()

    # ── Helpers ────────────────────────────────────────────────────────────

    def _center(self):
        if self._pixmap is None:
            return
        pw = int(self._pixmap.width() * self._zoom)
        ph = int(self._pixmap.height() * self._zoom)
        self._offset = QPoint(
            (self.width() - pw) // 2,
            (self.height() - ph) // 2,
        )
