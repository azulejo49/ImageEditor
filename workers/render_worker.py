"""
workers/render_worker.py
========================
ASYNC RENDER WORKER — QThread-based pipeline executor
------------------------------------------------------
Runs the float32 image pipeline on a background thread so the
Qt UI thread is NEVER blocked by heavy numpy/OpenCV operations.

LATEST-WINS STRATEGY
---------------------
If the user moves a slider rapidly (e.g. dragging exposure back and forth),
each slider tick calls request_render(). Rather than queuing every request,
we use a "latest-wins" approach:
  - A single pending flag is set on each request.
  - The worker loop grabs the flag, clears it, renders.
  - If another request arrived while rendering, the worker renders again.
  - All intermediate requests between two renders are coalesced into one.
This ensures the UI always shows the most recent state without lag buildup.

THREAD SAFETY
-------------
The pending flag and scale value are protected by a QMutex.
The QWaitCondition is used to sleep the thread when no work is pending,
consuming zero CPU instead of busy-waiting.

SIGNALS (emitted on UI thread via Qt's signal-slot queuing)
---------------------------------------------------------------------------
rendered_ready(np.ndarray, dict)  — (uint8 RGB image, histogram dict)
error_occurred(str)               — error message if pipeline raises
"""

from __future__ import annotations
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal, QMutex, QWaitCondition
from core.image_pipeline import ImagePipeline
from core import histogram


class RenderWorker(QThread):
    """
    Background render thread.
    Created once, runs for the lifetime of the app.
    """

    # Emitted with the rendered uint8 image and histogram data.
    # Connected to MainWindow._on_render_ready() via Qt auto-connection
    # (cross-thread safe: Qt marshals the signal to the UI thread).
    rendered_ready = pyqtSignal(object, object)   # (np.ndarray, dict)

    # Emitted on render failure (e.g. no image loaded, numpy error)
    error_occurred = pyqtSignal(str)

    def __init__(self, pipeline: ImagePipeline, parent=None):
        super().__init__(parent)

        # Reference to the shared pipeline object.
        # The pipeline holds the source image and current EditParams.
        # The worker only READS from the pipeline (calls render()),
        # never modifies it — thread safety is by convention.
        self._pipeline = pipeline

        # Mutex protects: _pending, _preview_scale, _stop
        self._mutex = QMutex()

        # Condition variable: worker sleeps here when idle,
        # wakes when request_render() or stop() is called.
        self._condition = QWaitCondition()

        # True = a render has been requested but not yet started
        self._pending: bool = False

        # The downscale factor for the next render (set by caller)
        self._preview_scale: float = 1.0

        # True = thread should exit its run loop cleanly
        self._stop: bool = False

    # ── Public API (called from UI thread) ────────────────────────────────

    def request_render(self, scale: float = 1.0):
        """
        Queue a render request at the given preview scale.
        Thread-safe: uses mutex to set the pending flag.
        Wakes the worker thread if it is sleeping.

        scale: downsample factor for preview speed.
               1.0 = full resolution
               0.5 = half resolution (4× fewer pixels, ~4× faster)
        """
        self._mutex.lock()
        self._pending       = True
        self._preview_scale = scale
        self._mutex.unlock()
        self._condition.wakeOne()   # wake the sleeping thread

    def stop(self):
        """
        Signal the worker to exit its run loop and wait for it to finish.
        Called from MainWindow.closeEvent() to ensure clean shutdown.
        """
        self._mutex.lock()
        self._stop = True
        self._mutex.unlock()
        self._condition.wakeOne()   # wake thread so it can see _stop=True
        self.wait()                 # block caller until thread exits

    # ── Worker loop (runs on the background thread) ────────────────────────

    def run(self):
        """
        Main loop of the background thread.
        Sleeps when idle (zero CPU), wakes on request_render() or stop().

        Loop structure:
          1. Lock mutex, wait on condition if no work pending
          2. Check stop flag → exit if set
          3. Grab pending flag + scale, clear pending flag, unlock mutex
          4. Call pipeline.render() — heavy float32 ops happen here
          5. Compute histogram from rendered image
          6. Emit rendered_ready signal (Qt delivers it to UI thread)
          7. Go back to step 1
        """
        while True:
            # ── Wait for work ─────────────────────────────────────────────
            self._mutex.lock()
            # Sleep until _pending or _stop becomes True
            while not self._pending and not self._stop:
                self._condition.wait(self._mutex)

            # ── Check stop signal ─────────────────────────────────────────
            if self._stop:
                self._mutex.unlock()
                return   # exit thread

            # ── Grab current request and reset pending flag ───────────────
            # Clearing _pending here (under the lock) means any new
            # request_render() call that arrives DURING the render below
            # will set _pending=True again, and we'll render once more.
            self._pending = False
            scale = self._preview_scale
            self._mutex.unlock()

            # ── Render ────────────────────────────────────────────────────
            # This is the only call that runs float32 pipeline operations.
            # It may take 10ms–500ms depending on image size and enabled ops.
            try:
                img_u8 = self._pipeline.render(scale=scale)
                hist   = histogram.compute(img_u8)
                # Emit result — Qt queues delivery to the UI thread
                self.rendered_ready.emit(img_u8, hist)
            except Exception as e:
                self.error_occurred.emit(str(e))
