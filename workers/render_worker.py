"""
workers/render_worker.py
QThread-based async pipeline worker.
Prevents UI blocking during heavy image processing.
Uses a "latest-wins" strategy: if a new render is requested
before the current one finishes, the current one is abandoned.
"""

from __future__ import annotations
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal, QMutex, QWaitCondition
from core.image_pipeline import ImagePipeline, EditParams
from core import histogram


class RenderWorker(QThread):
    """
    Background thread that processes render requests.
    Emits rendered_ready with (uint8_rgb, hist_dict) when done.
    """

    rendered_ready = pyqtSignal(object, object)   # (np.ndarray, dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, pipeline: ImagePipeline, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._mutex = QMutex()
        self._condition = QWaitCondition()
        self._pending: bool = False
        self._preview_scale: float = 1.0
        self._stop: bool = False

    # ── Public API (call from UI thread) ──────────────────────────────────

    def request_render(self, scale: float = 1.0):
        """Queue a render request. Thread-safe."""
        self._mutex.lock()
        self._pending = True
        self._preview_scale = scale
        self._mutex.unlock()
        self._condition.wakeOne()

    def stop(self):
        self._mutex.lock()
        self._stop = True
        self._mutex.unlock()
        self._condition.wakeOne()
        self.wait()

    # ── Worker loop ────────────────────────────────────────────────────────

    def run(self):
        while True:
            self._mutex.lock()
            while not self._pending and not self._stop:
                self._condition.wait(self._mutex)
            if self._stop:
                self._mutex.unlock()
                return
            self._pending = False
            scale = self._preview_scale
            self._mutex.unlock()

            try:
                img_u8 = self._pipeline.render(scale=scale)
                hist   = histogram.compute(img_u8)
                self.rendered_ready.emit(img_u8, hist)
            except Exception as e:
                self.error_occurred.emit(str(e))
