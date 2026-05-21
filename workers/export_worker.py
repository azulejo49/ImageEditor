"""
workers/export_worker.py
QThread worker for exporting/saving files.
"""

from __future__ import annotations
from PyQt6.QtCore import QThread, pyqtSignal
from core.image_pipeline import ImagePipeline


class ExportWorker(QThread):
    export_complete = pyqtSignal(str)
    export_error    = pyqtSignal(str)

    def __init__(self, pipeline: ImagePipeline, out_path: str,
                 quality: int = 95, parent=None):
        super().__init__(parent)
        self._pipeline  = pipeline
        self._out_path  = out_path
        self._quality   = quality

    def run(self):
        try:
            self._pipeline.export(self._out_path, self._quality)
            self.export_complete.emit(self._out_path)
        except Exception as e:
            self.export_error.emit(str(e))
