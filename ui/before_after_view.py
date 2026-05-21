"""
ui/before_after_view.py
=======================
BEFORE / AFTER SPLIT VIEW
--------------------------
Renders the original (unedited) image on the LEFT and the
edited result on the RIGHT, separated by a draggable vertical divider.

IMPLEMENTATION
--------------
This is a custom QWidget that holds TWO pixmaps:
  _before_pixmap: rendered from source float32 with identity EditParams
  _after_pixmap:  the most recent fully-rendered uint8 output

The divider position is a fraction [0.0–1.0] of the widget width.
paintEvent clips left half to before_pixmap, right half to after_pixmap.

CANVAS TRANSFORM
----------------
The before/after view uses the SAME zoom and offset as ImageCanvas
so the user sees the same crop/zoom when toggling the view.
The main window passes the current canvas zoom/offset to this widget.

SIGNALS
-------
None — purely visual. The main window shows/hides this widget
by toggling its visibility via the View menu / toolbar button.
"""

from __future__ import annotations
import numpy as np
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPoint, QRect
from PyQt6.QtGui import (
    QPainter, QPixmap, QImage, QColor, QPen, QFont, QCursor,
)


class BeforeAfterView(QWidget):
    """
    Split view: left = original, right = edited.
    The split divider is draggable horizontally.
    """

    # Divider visual properties
    DIVIDER_WIDTH   = 2     # px width of the centre line
    HANDLE_RADIUS   = 14    # px radius of the circular drag handle
    LABEL_PADDING   = 8     # px from edge of each half to label

    def __init__(self, parent=None):
        super().__init__(parent)

        self._before_pixmap: QPixmap | None = None   # original unedited
        self._after_pixmap:  QPixmap | None = None   # fully edited

        # Divider x position as fraction of widget width (0.0=left, 1.0=right)
        self._split: float = 0.5

        # Drag state for the divider
        self._dragging_divider: bool = False

        # Shared canvas transform (for rendering images at same zoom as canvas)
        self._zoom:   float  = 1.0
        self._offset: QPoint = QPoint(0, 0)

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(200, 200)

    # ── Public API ─────────────────────────────────────────────────────────

    def set_before(self, img_u8: np.ndarray):
        """
        Set the 'before' (original) image.
        Called once when a file is loaded — the before never changes.
        img_u8: (H,W,3) uint8 RGB.
        """
        self._before_pixmap = self._np_to_pixmap(img_u8)
        self.update()

    def set_after(self, img_u8: np.ndarray):
        """
        Set the 'after' (edited) image.
        Called every time the render worker emits a new result.
        img_u8: (H,W,3) uint8 RGB.
        """
        self._after_pixmap = self._np_to_pixmap(img_u8)
        self.update()

    def set_transform(self, zoom: float, offset: QPoint):
        """
        Sync zoom and pan offset from the main ImageCanvas so both
        views show the same crop/zoom level.
        """
        self._zoom   = zoom
        self._offset = offset
        self.update()

    # ── Painting ───────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        h = self.height()

        # Checkerboard background
        self._draw_checkerboard(painter, w, h)

        if self._before_pixmap is None and self._after_pixmap is None:
            # Nothing loaded yet — show hint
            painter.setPen(QColor("#555"))
            painter.setFont(QFont("monospace", 11))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Before / After view\nLoad an image to begin")
            return

        # Calculate the pixel x position of the split divider
        split_x = int(w * self._split)

        # ── Left half: BEFORE ─────────────────────────────────────────────
        # Clip painting to the left of the divider
        painter.save()
        painter.setClipRect(QRect(0, 0, split_x, h))
        if self._before_pixmap:
            self._draw_pixmap(painter, self._before_pixmap)
        painter.restore()

        # ── Right half: AFTER ─────────────────────────────────────────────
        # Clip painting to the right of the divider
        painter.save()
        painter.setClipRect(QRect(split_x, 0, w - split_x, h))
        if self._after_pixmap:
            self._draw_pixmap(painter, self._after_pixmap)
        painter.restore()

        # ── Divider line ──────────────────────────────────────────────────
        # White vertical line with a circular drag handle at the centre
        pen = QPen(QColor(255, 255, 255, 220))
        pen.setWidth(self.DIVIDER_WIDTH)
        painter.setPen(pen)
        painter.drawLine(split_x, 0, split_x, h)

        # Circular handle with arrows indicator
        cy = h // 2
        r  = self.HANDLE_RADIUS
        painter.setBrush(QColor(255, 255, 255, 220))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(split_x - r, cy - r, r * 2, r * 2)

        # Draw ◄ ► arrows inside handle
        painter.setPen(QColor("#1a1a1a"))
        painter.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        painter.drawText(
            split_x - r, cy - r, r * 2, r * 2,
            Qt.AlignmentFlag.AlignCenter, "◄►"
        )

        # ── Labels ────────────────────────────────────────────────────────
        # "BEFORE" label on the left, "AFTER" on the right
        painter.setFont(QFont("monospace", 9, QFont.Weight.Bold))
        label_y = self.LABEL_PADDING + 14

        # Before label (right-aligned within the left half)
        painter.setPen(QColor(255, 255, 255, 160))
        before_rect = QRect(self.LABEL_PADDING, self.LABEL_PADDING,
                            split_x - self.LABEL_PADDING * 2, 20)
        painter.drawText(before_rect, Qt.AlignmentFlag.AlignLeft, "BEFORE")

        # After label (left-aligned within the right half)
        after_rect = QRect(split_x + self.LABEL_PADDING, self.LABEL_PADDING,
                           w - split_x - self.LABEL_PADDING * 2, 20)
        painter.drawText(after_rect, Qt.AlignmentFlag.AlignLeft, "AFTER")

    def _draw_pixmap(self, painter: QPainter, pixmap: QPixmap):
        """
        Draw a pixmap at the current zoom/offset (same transform as ImageCanvas).
        This ensures before and after show the same view.
        """
        pw = int(pixmap.width()  * self._zoom)
        ph = int(pixmap.height() * self._zoom)
        painter.drawPixmap(self._offset.x(), self._offset.y(),
                           pw, ph, pixmap)

    def _draw_checkerboard(self, painter: QPainter, w: int, h: int):
        """Draw a subtle checkerboard background (standard for transparency)."""
        size = 12
        c1 = QColor("#2c2c2c")
        c2 = QColor("#242424")
        for r in range(h // size + 1):
            for c in range(w // size + 1):
                color = c1 if (r + c) % 2 == 0 else c2
                painter.fillRect(c * size, r * size, size, size, color)

    # ── Mouse events ───────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        """Start dragging the divider if mouse is near it."""
        split_x = int(self.width() * self._split)
        if abs(event.position().x() - split_x) <= self.HANDLE_RADIUS + 4:
            self._dragging_divider = True

    def mouseMoveEvent(self, event):
        split_x = int(self.width() * self._split)

        if self._dragging_divider:
            # Update split position, clamped to [5%, 95%] of widget width
            self._split = max(0.05, min(0.95,
                              event.position().x() / self.width()))
            self.update()

        # Change cursor when hovering over the divider handle
        elif abs(event.position().x() - split_x) <= self.HANDLE_RADIUS + 4:
            self.setCursor(QCursor(Qt.CursorShape.SplitHCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def mouseReleaseEvent(self, event):
        self._dragging_divider = False

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _np_to_pixmap(img_u8: np.ndarray) -> QPixmap:
        """Convert a uint8 RGB numpy array to a QPixmap."""
        h, w, _ = img_u8.shape
        qimg = QImage(img_u8.tobytes(), w, h, w * 3,
                      QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg)
