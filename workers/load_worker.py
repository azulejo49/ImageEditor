"""
workers/load_worker.py
======================
ASYNC FILE LOAD WORKER
-----------------------
Loads image files on a background thread so the UI stays responsive
during potentially slow RAW decoding (libraw can take 1–5 seconds
for large RAW files).

USAGE
-----
  worker = LoadWorker(pipeline, filepath, parent=self)
  worker.load_complete.connect(self._on_load_complete)
  worker.load_error.connect(self._on_load_error)
  worker.load_progress.connect(self._set_status)
  worker.start()   # fires off the background thread

A new LoadWorker instance is created for each file load.
The previous instance is kept alive as Python object until GC.

SIGNALS
-------
load_complete()         — file decoded and stored in pipeline._source_f32
load_error(str)         — human-readable error message
load_progress(str)      — status message shown in the status bar
"""

from __future__ import annotations
from PyQt6.QtCore import QThread, pyqtSignal
from core.image_pipeline import ImagePipeline


class LoadWorker(QThread):
    """
    Single-use QThread: loads one file then exits.
    """

    load_complete = pyqtSignal()        # emitted on success
    load_error    = pyqtSignal(str)     # emitted on failure, carries message
    load_progress = pyqtSignal(str)     # status bar message during load

    def __init__(self, pipeline: ImagePipeline, filepath: str, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._filepath = filepath

    def run(self):
        """
        Background thread body.
        Calls pipeline.load() which handles both RAW (rawpy) and
        raster (OpenCV) formats. Any exception is caught and re-emitted
        as load_error so the UI can display a dialog.
        """
        try:
            # Announce to status bar that loading is in progress
            self.load_progress.emit(
                f"Loading  {self._filepath.split('/')[-1]} …"
            )
            # The heavy work: decode file → float32 array in pipeline
            self._pipeline.load(self._filepath)
            # Success — UI thread will trigger a render
            self.load_complete.emit()
        except Exception as e:
            self.load_error.emit(str(e))
