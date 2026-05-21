"""
workers/export_worker.py
========================
ASYNC EXPORT / SAVE WORKER
---------------------------
Renders the full-resolution float32 pipeline output and saves it
to disk on a background thread.

WHY ASYNC?
----------
Full-resolution export can be slow:
  - A 45 MP RAW file renders to ~135 MB float32 array
  - Noise reduction on that image can take 10–30 seconds
  - OpenCV TIFF write of 16-bit data is also non-trivial
Running this synchronously would freeze the UI.

FORMATS SUPPORTED (delegated to pipeline.export)
-------------------------------------------------
  .jpg / .jpeg   — JPEG, quality parameter 0–100
  .png           — PNG lossless, compression level 6
  .tif / .tiff   — 16-bit TIFF, lossless, maximum quality

SIGNALS
-------
export_complete(str)   — path of the saved file (for status bar message)
export_error(str)      — human-readable error if save fails
"""

from __future__ import annotations
from PyQt6.QtCore import QThread, pyqtSignal
from core.image_pipeline import ImagePipeline


class ExportWorker(QThread):
    """
    Single-use QThread: exports one file then exits.
    """

    export_complete = pyqtSignal(str)   # carries output path
    export_error    = pyqtSignal(str)   # carries error message

    def __init__(self, pipeline: ImagePipeline, out_path: str,
                 quality: int = 95, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._out_path = out_path
        self._quality  = quality   # JPEG quality 0–100 (ignored for PNG/TIFF)

    def run(self):
        """
        Background thread body.
        Calls pipeline.export() which renders full-res float32 and
        saves to the chosen format. Emits result signal when done.
        """
        try:
            self._pipeline.export(self._out_path, self._quality)
            self.export_complete.emit(self._out_path)
        except Exception as e:
            self.export_error.emit(str(e))
