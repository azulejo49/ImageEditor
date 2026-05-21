"""
ui/crop_tool.py
===============
INTERACTIVE CROP OVERLAY
-------------------------
Drawn directly on top of the ImageCanvas using QPainter.
The crop rectangle is defined in IMAGE SPACE (pixel coordinates),
then projected to SCREEN SPACE for drawing using the canvas zoom/offset.

INTERACTION MODEL
-----------------
- Click and drag inside the crop rect  → move the whole rect
- Click and drag a corner handle       → resize freely
- Click and drag an edge handle        → resize along one axis
- Click and drag outside the rect      → draw a new crop rect
- Enter key or Confirm button          → commit crop to EditParams
- Escape key or Cancel button          → discard, restore previous rect

ASPECT RATIO MODES
------------------
  FREE   → no constraint
  1:1    → square
  4:3    → landscape standard
  16:9   → widescreen
  3:2    → 35mm film standard
  CUSTOM → user-specified ratio string e.g. "5:4"
"""

from __future__ import annotations
from enum import Enum
from PyQt6.QtCore import Qt, QRect, QPoint, QSize, pyqtSignal, QObject
from PyQt6.QtGui import QPainter, QColor, QPen, QCursor, QFont
import math


# ── Aspect ratio presets ───────────────────────────────────────────────────

class AspectMode(Enum):
    FREE  = "Free"
    RATIO_1_1  = "1:1"
    RATIO_4_3  = "4:3"
    RATIO_16_9 = "16:9"
    RATIO_3_2  = "3:2"


# Map AspectMode → (width_ratio, height_ratio) or None for free
ASPECT_RATIOS = {
    AspectMode.FREE:       None,
    AspectMode.RATIO_1_1:  (1, 1),
    AspectMode.RATIO_4_3:  (4, 3),
    AspectMode.RATIO_16_9: (16, 9),
    AspectMode.RATIO_3_2:  (3, 2),
}

# ── Hit zones for mouse interaction ────────────────────────────────────────

class HitZone(Enum):
    NONE        = 0
    INSIDE      = 1    # move
    TOP_LEFT    = 2    # resize corner
    TOP_RIGHT   = 3
    BOTTOM_LEFT = 4
    BOTTOM_RIGHT= 5
    TOP         = 6    # resize edge
    BOTTOM      = 7
    LEFT        = 8
    RIGHT       = 9
    OUTSIDE     = 10   # start new rect


# ── CropOverlay ────────────────────────────────────────────────────────────

class CropOverlay(QObject):
    """
    Manages crop rectangle state and painting.
    Does NOT subclass QWidget — it is driven by the ImageCanvas
    paint event and mouse events, keeping the canvas as the single
    Qt widget for rendering.

    Coordinate systems:
      - image_rect: QRect in image pixel space (the canonical truth)
      - screen_rect: derived from image_rect via zoom/offset transform
    """

    # Emitted when the user confirms or cancels
    crop_confirmed = pyqtSignal(object)   # emits (x,y,w,h) normalised tuple or None
    crop_cancelled = pyqtSignal()

    # Handle size in screen pixels
    HANDLE_SIZE = 8
    # Minimum crop rectangle size in screen pixels
    MIN_SIZE = 20

    def __init__(self, parent: QObject = None):
        super().__init__(parent)

        # Image-space crop rect (pixels in the loaded image)
        # None = crop tool not yet active / no rect drawn
        self._img_rect: QRect | None = None

        # Image dimensions (set when crop tool is activated)
        self._img_w: int = 0
        self._img_h: int = 0

        # Current aspect ratio mode
        self._aspect: AspectMode = AspectMode.FREE

        # Mouse drag state
        self._dragging: bool = False
        self._hit: HitZone = HitZone.NONE
        self._drag_start_screen: QPoint = QPoint()
        self._drag_start_rect: QRect = QRect()

        # Active flag — overlay only draws/responds when active
        self._active: bool = False

    # ── Activation / deactivation ──────────────────────────────────────────

    def activate(self, img_w: int, img_h: int,
                 existing_crop=None):
        """
        Start the crop tool for an image of size (img_w, img_h).
        existing_crop: normalised (x,y,w,h) tuple from EditParams.crop,
                       or None to start with a full-image rect.
        """
        self._img_w = img_w
        self._img_h = img_h
        self._active = True
        self._dragging = False

        if existing_crop is not None:
            # Restore previous crop rect from normalised coords
            cx, cy, cw, ch = existing_crop
            self._img_rect = QRect(
                int(cx * img_w),
                int(cy * img_h),
                int(cw * img_w),
                int(ch * img_h),
            )
        else:
            # Default: full image
            self._img_rect = QRect(0, 0, img_w, img_h)

    def deactivate(self):
        """Deactivate without confirming. Crop tool disappears."""
        self._active = False
        self._img_rect = None

    @property
    def active(self) -> bool:
        return self._active

    def set_aspect(self, mode: AspectMode):
        """
        Change the aspect ratio constraint.
        If switching to a fixed ratio, adjust the current rect to match.
        """
        self._aspect = mode
        if self._img_rect and mode != AspectMode.FREE:
            ratio = ASPECT_RATIOS[mode]
            if ratio:
                self._img_rect = self._enforce_aspect(self._img_rect, ratio)

    # ── Painting ───────────────────────────────────────────────────────────

    def paint(self, painter: QPainter, zoom: float, offset: QPoint):
        """
        Called by ImageCanvas.paintEvent() after the image is drawn.
        Draws the crop overlay on top.

        zoom, offset: canvas transform (same values used to draw the image).
        """
        if not self._active or self._img_rect is None:
            return

        sr = self._to_screen(self._img_rect, zoom, offset)  # screen rect

        # ── Darkened mask outside crop area ──────────────────────────────
        # Draws four semi-transparent black rects covering the excluded area.
        # This is faster and simpler than a clip path.
        canvas_w = painter.device().width()
        canvas_h = painter.device().height()
        overlay = QColor(0, 0, 0, 140)   # 55% opaque black

        # Top strip
        painter.fillRect(0, 0, canvas_w, sr.top(), overlay)
        # Bottom strip
        painter.fillRect(0, sr.bottom(), canvas_w, canvas_h - sr.bottom(), overlay)
        # Left strip (between top and bottom strips)
        painter.fillRect(0, sr.top(), sr.left(), sr.height(), overlay)
        # Right strip
        painter.fillRect(sr.right(), sr.top(), canvas_w - sr.right(), sr.height(), overlay)

        # ── Crop border ───────────────────────────────────────────────────
        # Bright white border with 1px antialiased pen
        border_pen = QPen(QColor(255, 255, 255, 220))
        border_pen.setWidthF(1.2)
        painter.setPen(border_pen)
        painter.drawRect(sr)

        # ── Rule-of-thirds grid ───────────────────────────────────────────
        # Subtle inner grid lines dividing crop area into 3×3
        grid_pen = QPen(QColor(255, 255, 255, 60))
        grid_pen.setWidthF(0.6)
        painter.setPen(grid_pen)
        for i in (1, 2):
            # Vertical thirds
            x = sr.left() + sr.width() * i // 3
            painter.drawLine(x, sr.top(), x, sr.bottom())
            # Horizontal thirds
            y = sr.top() + sr.height() * i // 3
            painter.drawLine(sr.left(), y, sr.right(), y)

        # ── Corner handles ────────────────────────────────────────────────
        # Filled white squares at each corner and edge midpoint
        hs = self.HANDLE_SIZE
        handle_color = QColor(255, 255, 255, 230)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(handle_color)

        # Corner positions
        corners = [
            sr.topLeft(),
            sr.topRight() - QPoint(hs, 0),
            sr.bottomLeft() - QPoint(0, hs),
            sr.bottomRight() - QPoint(hs, hs),
        ]
        for c in corners:
            painter.drawRect(c.x(), c.y(), hs, hs)

        # Edge midpoint handles
        mid_x = sr.left() + sr.width() // 2 - hs // 2
        mid_y = sr.top()  + sr.height() // 2 - hs // 2
        edges = [
            QPoint(mid_x, sr.top()),
            QPoint(mid_x, sr.bottom() - hs),
            QPoint(sr.left(), mid_y),
            QPoint(sr.right() - hs, mid_y),
        ]
        for e in edges:
            painter.drawRect(e.x(), e.y(), hs, hs)

        # ── Dimensions label ──────────────────────────────────────────────
        # Shows current crop size in pixels, bottom-left corner of rect
        painter.setPen(QColor(255, 255, 255, 200))
        painter.setFont(QFont("monospace", 9))
        label = f"{self._img_rect.width()} × {self._img_rect.height()}"
        painter.drawText(sr.left() + 4, sr.bottom() - 4, label)

    # ── Mouse events ───────────────────────────────────────────────────────

    def mouse_press(self, pos: QPoint, zoom: float, offset: QPoint) -> bool:
        """
        Handle mouse press. Returns True if event was consumed (crop tool handled it).
        """
        if not self._active:
            return False

        sr = self._to_screen(self._img_rect, zoom, offset)
        self._hit = self._hit_test(pos, sr)
        self._drag_start_screen = pos
        self._drag_start_rect = QRect(self._img_rect)
        self._dragging = True
        return True

    def mouse_move(self, pos: QPoint, zoom: float, offset: QPoint) -> bool:
        """
        Handle mouse move. Returns True if canvas should repaint.
        Computes the delta in IMAGE space to keep the crop pixel-accurate.
        """
        if not self._active:
            return False

        if self._dragging and self._img_rect is not None:
            # Delta in screen pixels → convert to image pixels
            delta_screen = pos - self._drag_start_screen
            dx = int(delta_screen.x() / zoom)
            dy = int(delta_screen.y() / zoom)

            r = QRect(self._drag_start_rect)   # start from clean copy

            if self._hit == HitZone.INSIDE:
                # MOVE: translate rect, clamp to image bounds
                r.translate(dx, dy)
                r = self._clamp_to_image(r)

            elif self._hit == HitZone.OUTSIDE:
                # DRAW NEW RECT: anchor at drag start, expand to current pos
                anchor = self._to_image(self._drag_start_screen, zoom, offset)
                current = self._to_image(pos, zoom, offset)
                x0 = min(anchor.x(), current.x())
                y0 = min(anchor.y(), current.y())
                x1 = max(anchor.x(), current.x())
                y1 = max(anchor.y(), current.y())
                r = QRect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))

            else:
                # RESIZE: adjust the appropriate edge(s)
                r = self._resize_rect(r, dx, dy, self._hit)

            # Enforce aspect ratio if not free
            if self._aspect != AspectMode.FREE:
                ratio = ASPECT_RATIOS[self._aspect]
                if ratio:
                    r = self._enforce_aspect(r, ratio)

            # Only update if result is valid (minimum size)
            if r.width() >= 4 and r.height() >= 4:
                self._img_rect = self._clamp_to_image(r)

            return True   # request repaint

        return False

    def mouse_release(self, pos: QPoint, zoom: float, offset: QPoint):
        """End drag operation."""
        self._dragging = False
        self._hit = HitZone.NONE

    def cursor_for_pos(self, pos: QPoint, zoom: float, offset: QPoint) -> Qt.CursorShape:
        """Return the appropriate cursor shape for the current mouse position."""
        if not self._active or self._img_rect is None:
            return Qt.CursorShape.ArrowCursor
        sr = self._to_screen(self._img_rect, zoom, offset)
        hit = self._hit_test(pos, sr)
        cursors = {
            HitZone.INSIDE:        Qt.CursorShape.SizeAllCursor,
            HitZone.TOP_LEFT:      Qt.CursorShape.SizeFDiagCursor,
            HitZone.BOTTOM_RIGHT:  Qt.CursorShape.SizeFDiagCursor,
            HitZone.TOP_RIGHT:     Qt.CursorShape.SizeBDiagCursor,
            HitZone.BOTTOM_LEFT:   Qt.CursorShape.SizeBDiagCursor,
            HitZone.TOP:           Qt.CursorShape.SizeVerCursor,
            HitZone.BOTTOM:        Qt.CursorShape.SizeVerCursor,
            HitZone.LEFT:          Qt.CursorShape.SizeHorCursor,
            HitZone.RIGHT:         Qt.CursorShape.SizeHorCursor,
            HitZone.OUTSIDE:       Qt.CursorShape.CrossCursor,
            HitZone.NONE:          Qt.CursorShape.ArrowCursor,
        }
        return cursors.get(hit, Qt.CursorShape.ArrowCursor)

    # ── Confirm / Cancel ───────────────────────────────────────────────────

    def confirm(self):
        """
        Convert image-space rect to normalised (x,y,w,h) tuple and emit.
        The pipeline stores this in EditParams.crop.
        """
        if self._img_rect is None or not self._active:
            self.crop_confirmed.emit(None)
            return
        r = self._img_rect
        norm = (
            r.x()      / self._img_w,
            r.y()      / self._img_h,
            r.width()  / self._img_w,
            r.height() / self._img_h,
        )
        self._active = False
        self.crop_confirmed.emit(norm)

    def cancel(self):
        """Discard changes and deactivate the crop tool."""
        self._active = False
        self.crop_cancelled.emit()

    # ── Internal geometry helpers ──────────────────────────────────────────

    def _to_screen(self, img_rect: QRect, zoom: float, offset: QPoint) -> QRect:
        """Project an image-space QRect to screen-space using zoom/offset."""
        x = int(img_rect.x()      * zoom + offset.x())
        y = int(img_rect.y()      * zoom + offset.y())
        w = int(img_rect.width()  * zoom)
        h = int(img_rect.height() * zoom)
        return QRect(x, y, w, h)

    def _to_image(self, screen_pos: QPoint, zoom: float, offset: QPoint) -> QPoint:
        """Convert a screen position to image pixel coordinates."""
        ix = int((screen_pos.x() - offset.x()) / zoom)
        iy = int((screen_pos.y() - offset.y()) / zoom)
        return QPoint(
            max(0, min(self._img_w - 1, ix)),
            max(0, min(self._img_h - 1, iy)),
        )

    def _hit_test(self, pos: QPoint, sr: QRect) -> HitZone:
        """
        Determine which part of the screen rect the cursor is near.
        Returns HitZone enum value based on proximity to handles/edges.
        """
        hs = self.HANDLE_SIZE + 2   # detection radius slightly larger than handle

        if not sr.adjusted(-hs, -hs, hs, hs).contains(pos):
            return HitZone.OUTSIDE

        # Check corners first (they overlap edges)
        if abs(pos.x() - sr.left())  < hs and abs(pos.y() - sr.top())    < hs: return HitZone.TOP_LEFT
        if abs(pos.x() - sr.right()) < hs and abs(pos.y() - sr.top())    < hs: return HitZone.TOP_RIGHT
        if abs(pos.x() - sr.left())  < hs and abs(pos.y() - sr.bottom()) < hs: return HitZone.BOTTOM_LEFT
        if abs(pos.x() - sr.right()) < hs and abs(pos.y() - sr.bottom()) < hs: return HitZone.BOTTOM_RIGHT

        # Check edges
        if abs(pos.y() - sr.top())    < hs: return HitZone.TOP
        if abs(pos.y() - sr.bottom()) < hs: return HitZone.BOTTOM
        if abs(pos.x() - sr.left())   < hs: return HitZone.LEFT
        if abs(pos.x() - sr.right())  < hs: return HitZone.RIGHT

        if sr.contains(pos):
            return HitZone.INSIDE

        return HitZone.OUTSIDE

    def _resize_rect(self, r: QRect, dx: int, dy: int, hit: HitZone) -> QRect:
        """
        Modify rect edges based on which handle is being dragged.
        dx, dy are delta in image-space pixels.
        """
        if hit in (HitZone.TOP_LEFT, HitZone.TOP, HitZone.LEFT):
            r.setLeft(r.left() + dx)
        if hit in (HitZone.TOP_LEFT, HitZone.TOP, HitZone.TOP_RIGHT):
            r.setTop(r.top() + dy)
        if hit in (HitZone.TOP_RIGHT, HitZone.RIGHT, HitZone.BOTTOM_RIGHT):
            r.setRight(r.right() + dx)
        if hit in (HitZone.BOTTOM_LEFT, HitZone.BOTTOM, HitZone.BOTTOM_RIGHT):
            r.setBottom(r.bottom() + dy)
        return r.normalized()   # ensure positive width/height

    def _enforce_aspect(self, r: QRect, ratio) -> QRect:
        """
        Adjust rect dimensions to match the given aspect ratio (w_ratio, h_ratio).
        Preserves the top-left corner position.
        Adjusts height to match width.
        """
        w_ratio, h_ratio = ratio
        target_h = int(r.width() * h_ratio / w_ratio)
        return QRect(r.left(), r.top(), r.width(), target_h)

    def _clamp_to_image(self, r: QRect) -> QRect:
        """Clamp rect so it stays within the image boundaries."""
        x = max(0, min(r.x(), self._img_w - r.width()))
        y = max(0, min(r.y(), self._img_h - r.height()))
        w = min(r.width(),  self._img_w - x)
        h = min(r.height(), self._img_h - y)
        return QRect(x, y, max(1, w), max(1, h))
