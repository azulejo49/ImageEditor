"""
ui/main_window.py
Main application window — orchestrates pipeline, workers, and all widgets.
"""

from __future__ import annotations
import os
import numpy as np
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QSplitter, QStatusBar, QMenuBar, QLabel,
    QFileDialog, QMessageBox, QProgressBar,
    QToolBar,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QAction, QKeySequence, QFont, QDragEnterEvent, QDropEvent, QIcon
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtCore import QByteArray

from core.image_pipeline import ImagePipeline
from workers.render_worker import RenderWorker
from workers.load_worker import LoadWorker
from workers.export_worker import ExportWorker
from ui.image_canvas import ImageCanvas
from ui.histogram_widget import HistogramWidget
from ui.edit_panel import EditPanel
from ui.file_panel import FilePanel
from ui.logo_widget import LogoWidget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ImageEdit™ — RAW + JPEG Editor")
        self.resize(1400, 900)
        self.setAcceptDrops(True)
        self._set_window_icon()

        # Core pipeline
        self._pipeline = ImagePipeline()

        # Workers
        self._render_worker = RenderWorker(self._pipeline, parent=self)
        self._render_worker.rendered_ready.connect(self._on_render_ready)
        self._render_worker.error_occurred.connect(self._on_render_error)
        self._render_worker.start()

        self._load_worker: LoadWorker | None = None
        self._export_worker: ExportWorker | None = None

        # Debounce timer — coalesces rapid slider moves
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._schedule_render)
        self._render_debounce_ms = 80

        self._build_ui()
        self._build_menu()
        self._build_toolbar()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ─── Left: file panel ─────────────────────────────────────────────
        self._file_panel = FilePanel()
        self._file_panel.file_selected.connect(self.open_file)

        # ─── Centre: canvas + histogram ──────────────────────────────────
        centre = QWidget()
        centre_v = QVBoxLayout(centre)
        centre_v.setContentsMargins(0, 0, 0, 0)
        centre_v.setSpacing(0)

        self._canvas = ImageCanvas()
        self._canvas.zoom_changed.connect(self._on_zoom_changed)

        self._histogram = HistogramWidget()
        self._histogram.setFixedHeight(90)

        centre_v.addWidget(self._canvas, stretch=1)
        centre_v.addWidget(self._histogram)

        # ─── Right: edit panel ────────────────────────────────────────────
        self._edit_panel = EditPanel()
        self._edit_panel.param_changed.connect(self._on_param_changed)
        self._edit_panel.reset_clicked.connect(self._on_reset)
        self._edit_panel.undo_clicked.connect(self._on_undo)
        self._edit_panel.redo_clicked.connect(self._on_redo)
        self._edit_panel.setFixedWidth(270)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._file_panel)
        splitter.addWidget(centre)
        splitter.addWidget(self._edit_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([200, 900, 270])

        root.addWidget(splitter)

        # ─── Status bar ───────────────────────────────────────────────────
        self._status = QStatusBar()
        self._status.setObjectName("statusBar")
        self._status_lbl = QLabel("Ready")
        self._status_lbl.setFont(QFont("monospace", 8))
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setFixedWidth(120)
        self._progress.setRange(0, 0)   # indeterminate
        self._status.addWidget(self._status_lbl)
        self._status.addPermanentWidget(self._progress)
        self.setStatusBar(self._status)

    def _build_menu(self):
        mb = self.menuBar()

        # File
        fm = mb.addMenu("&File")
        act_open = QAction("&Open…", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self._menu_open)
        fm.addAction(act_open)

        act_save = QAction("&Save (Export)…", self)
        act_save.setShortcut(QKeySequence.StandardKey.Save)
        act_save.triggered.connect(self._menu_save)
        fm.addAction(act_save)

        fm.addSeparator()
        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        fm.addAction(act_quit)

        # Edit
        em = mb.addMenu("&Edit")
        act_undo = QAction("&Undo", self)
        act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        act_undo.triggered.connect(self._on_undo)
        em.addAction(act_undo)

        act_redo = QAction("&Redo", self)
        act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        act_redo.triggered.connect(self._on_redo)
        em.addAction(act_redo)

        act_reset = QAction("Reset All Edits", self)
        act_reset.triggered.connect(self._on_reset)
        em.addAction(act_reset)

        # View
        vm = mb.addMenu("&View")
        act_fit = QAction("Fit to Window", self)
        act_fit.setShortcut(Qt.Key.Key_F)
        act_fit.triggered.connect(self._canvas.fit_to_window)
        vm.addAction(act_fit)

        act_100 = QAction("100% (1:1)", self)
        act_100.setShortcut(Qt.Key.Key_1)
        act_100.triggered.connect(self._canvas.zoom_1_to_1)
        vm.addAction(act_100)

    def _set_window_icon(self):
        """Load the SVG logo as the window/taskbar icon."""
        import os
        from PyQt6.QtGui import QPixmap
        from PyQt6.QtSvgWidgets import QSvgWidget
        svg_path = os.path.join(os.path.dirname(__file__), "..", "resources", "logo.svg")
        svg_path = os.path.normpath(svg_path)
        if os.path.exists(svg_path):
            renderer = QSvgRenderer(svg_path)
            pixmap = QPixmap(64, 64)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = __import__("PyQt6.QtGui", fromlist=["QPainter"]).QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            self.setWindowIcon(QIcon(pixmap))

    def _build_toolbar(self):
        tb = QToolBar("Main Toolbar")
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        # ── Logo on the left ──────────────────────────────────────────────
        logo = LogoWidget()
        tb.addWidget(logo)

        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet("background: #333;")
        tb.addWidget(sep)
        tb.addSeparator()

        acts = [
            ("Open",       self._menu_open),
            ("Save",       self._menu_save),
            ("Undo",       self._on_undo),
            ("Redo",       self._on_redo),
            ("Reset",      self._on_reset),
            ("Fit",        self._canvas.fit_to_window),
            ("1:1",        self._canvas.zoom_1_to_1),
        ]
        for label, slot in acts:
            a = QAction(label, self)
            a.triggered.connect(slot)
            tb.addAction(a)

        self.addToolBar(tb)

    # ── File I/O ───────────────────────────────────────────────────────────

    def open_file(self, filepath: str):
        if not os.path.isfile(filepath):
            return
        self._canvas.set_loading(True)
        self._progress.setVisible(True)
        self._set_status(f"Loading {os.path.basename(filepath)}…")

        self._load_worker = LoadWorker(self._pipeline, filepath, parent=self)
        self._load_worker.load_complete.connect(self._on_load_complete)
        self._load_worker.load_error.connect(self._on_load_error)
        self._load_worker.start()

        self._file_panel.highlight_file(filepath)

    @pyqtSlot()
    def _on_load_complete(self):
        self._progress.setVisible(False)
        w, h = self._pipeline.size
        fp = self._pipeline.filepath
        raw_tag = " [RAW]" if self._pipeline.is_raw else ""
        self._set_status(f"{os.path.basename(fp)}{raw_tag}  —  {w}×{h}")
        self._file_panel.set_info(f"{w}×{h}\n{os.path.basename(fp)}")
        self._canvas.fit_to_window()
        self._request_render()

    @pyqtSlot(str)
    def _on_load_error(self, msg: str):
        self._progress.setVisible(False)
        self._canvas.set_loading(False)
        QMessageBox.critical(self, "Load Error", msg)

    def _menu_open(self):
        filt = (
            "Images (*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp "
            "*.cr2 *.cr3 *.nef *.nrw *.arw *.srf *.sr2 *.orf *.rw2 "
            "*.pef *.dng *.raf *.3fr *.mrw *.x3f *.erf *.kdc *.dcr "
            "*.raw *.rwl);;"
            "All Files (*)"
        )
        paths, _ = QFileDialog.getOpenFileNames(self, "Open Image(s)", "", filt)
        for p in paths:
            self._file_panel.add_file(p)
        if paths:
            self.open_file(paths[0])

    def _menu_save(self):
        if not self._pipeline.loaded:
            return
        filt = "JPEG (*.jpg);;PNG (*.png);;TIFF 16-bit (*.tif);;All Files (*)"
        out, _ = QFileDialog.getSaveFileName(self, "Export Image", "", filt)
        if not out:
            return
        self._progress.setVisible(True)
        self._set_status("Exporting…")
        self._export_worker = ExportWorker(self._pipeline, out, 95, parent=self)
        self._export_worker.export_complete.connect(self._on_export_complete)
        self._export_worker.export_error.connect(self._on_export_error)
        self._export_worker.start()

    @pyqtSlot(str)
    def _on_export_complete(self, path: str):
        self._progress.setVisible(False)
        self._set_status(f"Saved: {os.path.basename(path)}")

    @pyqtSlot(str)
    def _on_export_error(self, msg: str):
        self._progress.setVisible(False)
        QMessageBox.critical(self, "Export Error", msg)

    # ── Edit params ────────────────────────────────────────────────────────

    @pyqtSlot(str, object)
    def _on_param_changed(self, name: str, value):
        if not self._pipeline.loaded:
            return
        self._pipeline.snapshot()
        setattr(self._pipeline.params, name, value)
        self._render_timer.start(self._render_debounce_ms)

    def _on_reset(self):
        self._pipeline.reset_edits()
        self._edit_panel.load_params(self._pipeline.params)
        self._request_render()

    def _on_undo(self):
        if self._pipeline.undo():
            self._edit_panel.load_params(self._pipeline.params)
            self._request_render()

    def _on_redo(self):
        if self._pipeline.redo():
            self._edit_panel.load_params(self._pipeline.params)
            self._request_render()

    # ── Render pipeline ────────────────────────────────────────────────────

    def _request_render(self):
        """Kick the debounce timer."""
        self._render_timer.start(self._render_debounce_ms)

    def _schedule_render(self):
        if not self._pipeline.loaded:
            return
        # Use preview scale for fast response; full-res only at 1:1+
        scale = min(1.0, max(0.25, self._canvas.zoom))
        self._render_worker.request_render(scale=scale)

    @pyqtSlot(object, object)
    def _on_render_ready(self, img_u8: np.ndarray, hist: dict):
        self._canvas.set_image(img_u8)
        self._histogram.update_histogram(hist)

    @pyqtSlot(str)
    def _on_render_error(self, msg: str):
        self._set_status(f"Render error: {msg}")

    def _on_zoom_changed(self, zoom: float):
        # Request new render at new zoom level
        self._render_timer.start(self._render_debounce_ms)

    # ── Drag & Drop ────────────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path):
                self._file_panel.add_file(path)
        urls = event.mimeData().urls()
        if urls:
            self.open_file(urls[0].toLocalFile())

    # ── Helpers ────────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self._status_lbl.setText(msg)

    def closeEvent(self, event):
        self._render_worker.stop()
        super().closeEvent(event)
