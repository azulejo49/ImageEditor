"""
ui/image_canvas.py
==================
ZOOMABLE / PANNABLE IMAGE CANVAS WITH CROP OVERLAY SUPPORT
-----------------------------------------------------------
The central display widget. Receives pre-rendered uint8 RGB arrays
from the RenderWorker and displays them with zero additional processing.

COORDINATE SYSTEMS
------------------
  Screen space : widget pixel coordinates (0,0) = top-left of widget
  Image space  : pixel coordinates in the rendered image array
  Canvas transform: screen = image * zoom + offset

  The same zoom/offset values are shared with:
    - CropOverlay   (draws crop rect in screen space)
    - BeforeAfterView (synced for consistent appearance)

ZOOM
----
  - Mouse wheel zooms toward the cursor position (not toward centre)
  - Zoom range: 2% to 3200%
  - Factor per wheel tick: 1.12 (smooth geometric steps)
  - Double-click → fit to window
  - Key F → fit, Key 1 → 100%

PAN
---
  - Middle mouse button drag
  - Alt + Left mouse button drag (Lightroom/Photoshop convention)
  - NOT available when crop tool is active (crop tool owns mouse)

CROP TOOL INTEGRATION
---------------------
When crop_active=True:
  - Mouse events are delegated to the CropOverlay object
  - The overlay draws on top of the image in paintEvent
  - Canvas pan is disabled
  - Cursor changes to match crop handle under mouse

RENDER PIPELINE
---------------
  RenderWorker.rendered_ready
    → MainWindow._on_render_ready
      → ImageCanvas.set_image(uint8_array)
        → converts to QPixmap, calls update()
          → paintEvent draws pixmap at zoom/offset
"""

from __future__ import annotations
import numpy as np
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QImage, QPixmap,
    QWheelEvent, QMouseEvent, QColor, QFont, QPen,
)
from ui.crop_tool import CropOverlay


class ImageCanvas(QWidget):
    """
    Main image display canvas.
    Handles zoom, pan, loading state, and hosts the crop overlay.
    """

    # Emitted whenever zoom changes (used by render pipeline to request
    # a re-render at the appropriate scale for speed/quality balance)
    zoom_changed = pyqtSignal(float)

    # Emitted when crop is confirmed: carries normalised (x,y,w,h) tuple
    crop_confirmed = pyqtSignal(object)

    # Emitted when crop is cancelled
    crop_cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Current display pixmap (set by set_image())
        self._pixmap: QPixmap | None = None

        # Canvas transform state
        self._zoom:   float  = 1.0
        self._offset: QPoint = QPoint(0, 0)

        # Pan drag state (middle-click or Alt+drag)
        self._pan_start:        QPoint | None = None
        self._pan_offset_start: QPoint | None = None

        # True while a file is loading (shows spinner text)
        self._loading: bool = False

        # Crop overlay object — always exists, only active when crop mode is on
        self._crop = CropOverlay(self)
        self._crop.crop_confirmed.connect(self.crop_confirmed)
        self._crop.crop_cancelled.connect(self.crop_cancelled)

        # Enable mouse tracking so cursor updates without button held
        self.setMouseTracking(True)
        # Accept keyboard focus for key shortcuts (F=fit, Escape=cancel crop)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        # Opaque paint: Qt skips background erase, we fill everything ourselves
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(200, 200)

    # ── Public API ─────────────────────────────────────────────────────────

    def set_image(self, img_u8: np.ndarray):
        """
        Accept a fresh render from the worker and display it.

        Converts uint8 RGB numpy array → QImage → QPixmap.
        QImage.tobytes() is used here (not frombuffer) to ensure the
        data is fully copied into Qt memory — safe across thread boundaries.

        Only fits to window on the very first image load
        (zoom==1.0 and offset at origin = initial state).
        """
        h, w, _ = img_u8.shape
        # Create QImage from raw bytes — Format_RGB888 = 3 bytes per pixel, no alpha
        qimg = QImage(img_u8.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)
        self._pixmap  = QPixmap.fromImage(qimg)
        self._loading = False

        # First load: fit image to widget
        if self._zoom == 1.0 and self._offset == QPoint(0, 0):
            self.fit_to_window()
        self.update()

    def set_loading(self, loading: bool):
        """Show/hide the 'Loading…' overlay text."""
        self._loading = loading
        self.update()

    def fit_to_window(self):
        """
        Scale and centre the image so it fits entirely within the widget
        with a 5% margin on all sides.
        Uses the smaller of the two scale factors (letterbox fit).
        """
        if self._pixmap is None:
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        ww, wh = self.width(), self.height()
        scale  = min(ww / pw, wh / ph) * 0.95   # 5% padding
        self._zoom = scale
        self._center()
        self.zoom_changed.emit(self._zoom)
        self.update()

    def zoom_to(self, z: float):
        """Set zoom to an exact value, clamped to [2%, 3200%]."""
        self._zoom = max(0.02, min(32.0, z))
        self._center()
        self.zoom_changed.emit(self._zoom)
        self.update()

    def zoom_1_to_1(self):
        """Jump to 100% zoom, centred."""
        self.zoom_to(1.0)

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def offset(self) -> QPoint:
        return self._offset

    # ── Crop tool public interface ─────────────────────────────────────────

    def start_crop(self, img_w: int, img_h: int, existing_crop=None):
        """
        Activate the crop overlay for an image of (img_w × img_h) pixels.
        existing_crop: normalised (x,y,w,h) from EditParams, or None for full image.
        """
        self._crop.activate(img_w, img_h, existing_crop)
        self.update()

    def confirm_crop(self):
        """Programmatically confirm the crop (e.g. from toolbar button)."""
        self._crop.confirm()
        self.update()

    def cancel_crop(self):
        """Programmatically cancel the crop."""
        self._crop.cancel()
        self.update()

    def set_crop_aspect(self, mode):
        """Pass aspect ratio mode to the crop overlay."""
        self._crop.set_aspect(mode)
        self.update()

    @property
    def crop_active(self) -> bool:
        return self._crop.active

    # ── Painting ───────────────────────────────────────────────────────────

    def paintEvent(self, event):
        """
        Full custom paint: checkerboard → image → crop overlay → HUD labels.
        Called automatically by Qt whenever update() is invoked.
        """
        painter = QPainter(self)
        # SmoothPixmapTransform: bilinear filtering when zoomed out.
        # Disabled when zoomed in (nearest-neighbour = crisper pixel grid).
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform, self._zoom < 1.0
        )

        # ── Background ────────────────────────────────────────────────────
        # Subtle checkerboard: distinguishes true black from "no image" area
        self._draw_checkerboard(painter)

        # ── Loading state ─────────────────────────────────────────────────
        if self._loading:
            painter.setPen(QColor("#aaa"))
            painter.setFont(QFont("monospace", 12))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "Loading…"
            )
            return

        # ── Empty state ───────────────────────────────────────────────────
        if self._pixmap is None:
            painter.setPen(QColor("#555"))
            painter.setFont(QFont("monospace", 11))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Open or drop a RAW / JPEG file\n\n"
                "Ctrl+O  ·  drag & drop  ·  use file panel",
            )
            return

        # ── Image ─────────────────────────────────────────────────────────
        # Scale pixmap by current zoom factor, position at offset.
        # Qt handles sub-pixel accuracy via float internally.
        pw = int(self._pixmap.width()  * self._zoom)
        ph = int(self._pixmap.height() * self._zoom)
        painter.drawPixmap(
            self._offset.x(), self._offset.y(), pw, ph, self._pixmap
        )

        # ── Crop overlay ──────────────────────────────────────────────────
        # Overlay draws on top of image: darkened mask + rect + handles
        if self._crop.active:
            self._crop.paint(painter, self._zoom, self._offset)

        # ── HUD: zoom percentage ──────────────────────────────────────────
        painter.setPen(QPen(QColor(255, 255, 255, 130)))
        painter.setFont(QFont("monospace", 9))
        painter.drawText(8, self.height() - 8, f"{self._zoom * 100:.0f}%")

    def _draw_checkerboard(self, painter: QPainter):
        """
        Draw a 12×12 px two-tone checkerboard covering the entire widget.
        Two very similar dark greys (not black/white) keep it subtle.
        """
        sz = 12
        c1 = QColor("#2c2c2c")
        c2 = QColor("#242424")
        cols = self.width()  // sz + 1
        rows = self.height() // sz + 1
        for r in range(rows):
            for c in range(cols):
                painter.fillRect(
                    c * sz, r * sz, sz, sz,
                    c1 if (r + c) % 2 == 0 else c2
                )

    # ── Resize event ───────────────────────────────────────────────────────

    def resizeEvent(self, event):
        """Re-fit image when widget is resized and image hasn't been panned."""
        super().resizeEvent(event)
        if self._pixmap and self._offset == QPoint(0, 0):
            self.fit_to_window()

    # ── Mouse events ───────────────────────────────────────────────────────

    def wheelEvent(self, event: QWheelEvent):
        """
        Zoom toward cursor position (not toward widget centre).

        Formula for cursor-anchored zoom:
          new_offset.x = cursor.x - (cursor.x - old_offset.x) * zoom_ratio
        This keeps the pixel under the cursor stationary during zoom.
        """
        if self._crop.active:
            return   # ignore zoom during crop to avoid confusion

        delta   = event.angleDelta().y()
        factor  = 1.12 if delta > 0 else 1.0 / 1.12
        pos     = event.position().toPoint()
        old_z   = self._zoom
        new_z   = max(0.02, min(32.0, old_z * factor))
        ratio   = new_z / old_z

        # Anchor zoom to cursor: keep pixel under cursor fixed on screen
        self._offset = QPoint(
            int(pos.x() - (pos.x() - self._offset.x()) * ratio),
            int(pos.y() - (pos.y() - self._offset.y()) * ratio),
        )
        self._zoom = new_z
        self.zoom_changed.emit(self._zoom)
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        """
        Delegate to crop tool if active.
        Otherwise start pan if middle-click or Alt+left-click.
        """
        if self._crop.active:
            # Crop overlay handles all mouse interaction during crop mode
            self._crop.mouse_press(event.pos(), self._zoom, self._offset)
            self.update()
            return

        # Pan: middle-click or Alt + left-click
        if (event.button() == Qt.MouseButton.MiddleButton or
                (event.button() == Qt.MouseButton.LeftButton and
                 event.modifiers() & Qt.KeyboardModifier.AltModifier)):
            self._pan_start        = event.pos()
            self._pan_offset_start = QPoint(self._offset)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent):
        """
        During crop: delegate to crop tool (updates rect, requests repaint).
        During pan: translate the offset by mouse delta.
        Otherwise: update cursor shape for crop handles.
        """
        if self._crop.active:
            if self._crop.mouse_move(event.pos(), self._zoom, self._offset):
                self.update()
            # Set cursor to match whichever crop handle is under the mouse
            self.setCursor(
                self._crop.cursor_for_pos(event.pos(), self._zoom, self._offset)
            )
            return

        if self._pan_start is not None:
            # Pan: new offset = start_offset + (current_pos - drag_start)
            delta         = event.pos() - self._pan_start
            self._offset  = self._pan_offset_start + delta
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        """End crop drag or pan drag."""
        if self._crop.active:
            self._crop.mouse_release(event.pos(), self._zoom, self._offset)
            return

        if self._pan_start is not None:
            self._pan_start = None
            self._pan_offset_start = None
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """Double-click → fit image to window (quick reset view)."""
        if not self._crop.active:
            self.fit_to_window()

    def keyPressEvent(self, event):
        """
        Keyboard shortcuts within the canvas:
          Escape   → cancel crop if active
          Enter    → confirm crop if active
          F        → fit to window
          1        → 100% zoom
        """
        if self._crop.active:
            if event.key() == Qt.Key.Key_Escape:
                self._crop.cancel()
                self.update()
                return
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._crop.confirm()
                self.update()
                return

        if event.key() == Qt.Key.Key_F:
            self.fit_to_window()
        elif event.key() == Qt.Key.Key_1:
            self.zoom_1_to_1()

    # ── Helpers ────────────────────────────────────────────────────────────

    def _center(self):
        """Centre the image pixmap in the widget at current zoom."""
        if self._pixmap is None:
            return
        pw = int(self._pixmap.width()  * self._zoom)
        ph = int(self._pixmap.height() * self._zoom)
        self._offset = QPoint(
            (self.width()  - pw) // 2,
            (self.height() - ph) // 2,
        )
