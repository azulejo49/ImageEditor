"""
workers/load_worker.py
QThread worker for loading files without freezing the UI.
"""

from __future__ import annotations
from PyQt6.QtCore import QThread, pyqtSignal
from core.image_pipeline import ImagePipeline


class LoadWorker(QThread):
    load_complete = pyqtSignal()
    load_error    = pyqtSignal(str)
    load_progress = pyqtSignal(str)   # status message

    def __init__(self, pipeline: ImagePipeline, filepath: str, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._filepath = filepath

    def run(self):
        try:
            self.load_progress.emit(f"Loading {self._filepath} …")
            self._pipeline.load(self._filepath)
            self.load_complete.emit()
        except Exception as e:
            self.load_error.emit(str(e))
