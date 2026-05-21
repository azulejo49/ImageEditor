"""
ui/main_window.py
=================
MAIN APPLICATION WINDOW — ORCHESTRATION LAYER
----------------------------------------------
Creates and connects every subsystem:
  - ImagePipeline          (core float32 processing engine)
  - RenderWorker           (background QThread renderer)
  - LoadWorker             (background QThread file loader)
  - ExportWorker           (background QThread file exporter)
  - ImageCanvas            (central zoom/pan display + crop overlay)
  - BeforeAfterView        (split before/after comparison view)
  - HistogramWidget        (live R/G/B/Lum histogram)
  - EditPanel              (all non-destructive sliders + curve + HSL)
  - FilePanel              (left-side file browser)
  - ExifPanel              (EXIF/metadata viewer tab)
  - CropToolbar            (confirm/cancel/aspect controls for crop mode)
  - LogoWidget             (SVG logo in toolbar)

LAYOUT
------
  ┌────────────────────────────────────────────────────┐
  │  Toolbar: [Logo] [Open][Save][Undo][Redo]...       │
  ├──────────┬─────────────────────────┬───────────────┤
  │          │                         │               │
  │  File    │   Canvas / Before-After │  Edit Panel   │
  │  Panel   │      (centre)           │  (right)      │
  │  (left)  │                         │               │
  │          ├─────────────────────────┤               │
  │          │   Histogram (bottom)    │               │
  └──────────┴─────────────────────────┴───────────────┘
  │  Status bar: filename · dimensions · zoom · progress │
  └─────────────────────────────────────────────────────┘

RENDER DEBOUNCING
-----------------
Sliders emit param_changed on every tick, which could trigger hundreds
of renders per second during a drag. A QTimer with 80ms single-shot
debouncing coalesces rapid changes into one render request.

CROP WORKFLOW
-------------
  1. User clicks "Crop" button in toolbar
  2. main_window calls canvas.start_crop(img_w, img_h, existing_crop)
  3. Canvas shows crop overlay; crop toolbar appears
  4. User adjusts crop rect interactively
  5. Confirm: canvas.confirm_crop() → crop_confirmed signal →
     _on_crop_confirmed() → pipeline.params.crop updated → render
  6. Cancel: canvas.cancel_crop() → restore previous state

BEFORE/AFTER TOGGLE
-------------------
View menu "Before / After" or toolbar button toggles between:
  - Normal mode: canvas shows edited image
  - Before/After mode: BeforeAfterView replaces canvas
Before image is captured once on file load (identity EditParams render).
After image is updated on every render worker result.
"""

from __future__ import annotations
import os
import numpy as np
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QSplitter, QStatusBar, QLabel, QFileDialog,
    QMessageBox, QProgressBar, QToolBar, QStackedWidget,
    QTabWidget,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import (
    QAction, QKeySequence, QFont, QIcon, QPixmap,
    QDragEnterEvent, QDropEvent,
)

from core.image_pipeline import ImagePipeline
from workers.render_worker import RenderWorker
from workers.load_worker   import LoadWorker
from workers.export_worker import ExportWorker

from ui.image_canvas      import ImageCanvas
from ui.before_after_view import BeforeAfterView
from ui.histogram_widget  import HistogramWidget
from ui.edit_panel        import EditPanel
from ui.file_panel        import FilePanel
from ui.exif_panel        import ExifPanel
from ui.logo_widget       import LogoWidget
from ui.crop_tool         import AspectMode


class MainWindow(QMainWindow):
    """
    Top-level application window.
    Owns all major objects and connects their signals.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ImageEdit™  —  RAW + JPEG Professional Editor")
        self.resize(1440, 900)
        self.setAcceptDrops(True)   # enable drag & drop onto window
        self._set_window_icon()

        # ── Core pipeline (single instance for app lifetime) ──────────────
        self._pipeline = ImagePipeline()

        # ── Background workers ────────────────────────────────────────────
        # RenderWorker runs continuously; started here, stopped on close.
        self._render_worker = RenderWorker(self._pipeline, parent=self)
        self._render_worker.rendered_ready.connect(self._on_render_ready)
        self._render_worker.error_occurred.connect(self._on_render_error)
        self._render_worker.start()

        # LoadWorker and ExportWorker are created fresh per operation
        self._load_worker:   LoadWorker   | None = None
        self._export_worker: ExportWorker | None = None

        # ── Render debounce timer ─────────────────────────────────────────
        # Single-shot timer: restarted on each param change.
        # Only fires if no new change arrives within 80ms.
        # This prevents per-tick rendering during fast slider drags.
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(80)
        self._render_timer.timeout.connect(self._schedule_render)

        # ── Before/after: store the unedited render once per file load ────
        # This is the source image rendered with identity EditParams.
        self._before_img: np.ndarray | None = None

        # ── Before/after mode active flag ─────────────────────────────────
        self._before_after_active: bool = False

        # ── Build UI ──────────────────────────────────────────────────────
        self._build_ui()
        self._build_menu()
        self._build_toolbar()

    # ══════════════════════════════════════════════════════════════════════
    # UI CONSTRUCTION
    # ══════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        """
        Build the central widget layout:
          [FilePanel] [QStackedWidget: canvas | before-after] [Right panel]
        The QStackedWidget allows switching between normal and before/after views.
        The right panel is a QTabWidget with Edit and EXIF tabs.
        """
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left: file browser ────────────────────────────────────────────
        self._file_panel = FilePanel()
        self._file_panel.file_selected.connect(self.open_file)

        # ── Centre: view stack + histogram ───────────────────────────────
        centre_widget = QWidget()
        centre_v = QVBoxLayout(centre_widget)
        centre_v.setContentsMargins(0, 0, 0, 0)
        centre_v.setSpacing(0)

        # QStackedWidget holds both views; only one visible at a time
        self._view_stack = QStackedWidget()

        # Page 0: normal canvas (zoom/pan + crop overlay)
        self._canvas = ImageCanvas()
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
        self._canvas.crop_confirmed.connect(self._on_crop_confirmed)
        self._canvas.crop_cancelled.connect(self._on_crop_cancelled)
        self._view_stack.addWidget(self._canvas)   # index 0

        # Page 1: before/after split view
        self._before_after = BeforeAfterView()
        self._view_stack.addWidget(self._before_after)   # index 1

        # Start on normal canvas
        self._view_stack.setCurrentIndex(0)

        self._histogram = HistogramWidget()
        self._histogram.setFixedHeight(90)

        centre_v.addWidget(self._view_stack, stretch=1)
        centre_v.addWidget(self._histogram)

        # ── Right: tabbed panel (Edit + EXIF) ─────────────────────────────
        right_tabs = QTabWidget()
        right_tabs.setObjectName("rightTabs")
        right_tabs.setFixedWidth(280)

        # Edit tab: all non-destructive adjustment controls
        self._edit_panel = EditPanel()
        self._edit_panel.param_changed.connect(self._on_param_changed)
        self._edit_panel.reset_clicked.connect(self._on_reset)
        self._edit_panel.undo_clicked.connect(self._on_undo)
        self._edit_panel.redo_clicked.connect(self._on_redo)
        right_tabs.addTab(self._edit_panel, "Edit")

        # EXIF tab: metadata viewer
        self._exif_panel = ExifPanel()
        right_tabs.addTab(self._exif_panel, "Info")

        # ── Splitter ──────────────────────────────────────────────────────
        # Allows user to resize the three columns by dragging the handles
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._file_panel)
        splitter.addWidget(centre_widget)
        splitter.addWidget(right_tabs)
        splitter.setStretchFactor(0, 0)   # file panel: fixed
        splitter.setStretchFactor(1, 1)   # canvas: stretches
        splitter.setStretchFactor(2, 0)   # edit panel: fixed
        splitter.setSizes([200, 960, 280])

        root.addWidget(splitter)

        # ── Status bar ────────────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self._status_bar.setObjectName("statusBar")

        self._status_lbl = QLabel("Ready  —  open a file to begin")
        self._status_lbl.setFont(QFont("monospace", 8))

        # Indeterminate progress bar shown during load/export
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)         # 0,0 = indeterminate spin
        self._progress.setFixedWidth(120)
        self._progress.setVisible(False)

        self._status_bar.addWidget(self._status_lbl)
        self._status_bar.addPermanentWidget(self._progress)
        self.setStatusBar(self._status_bar)

    def _build_menu(self):
        """
        Build the menu bar:
          File — Open, Save, Quit
          Edit — Undo, Redo, Reset, Crop
          View — Fit, 1:1, Before/After
        """
        mb = self.menuBar()

        # ── File menu ─────────────────────────────────────────────────────
        fm = mb.addMenu("&File")

        act_open = QAction("&Open…", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.setToolTip("Open image file(s)")
        act_open.triggered.connect(self._menu_open)
        fm.addAction(act_open)

        act_save = QAction("&Export / Save…", self)
        act_save.setShortcut(QKeySequence.StandardKey.Save)
        act_save.setToolTip("Export edited image to JPEG, PNG, or 16-bit TIFF")
        act_save.triggered.connect(self._menu_save)
        fm.addAction(act_save)

        fm.addSeparator()

        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        fm.addAction(act_quit)

        # ── Edit menu ─────────────────────────────────────────────────────
        em = mb.addMenu("&Edit")

        act_undo = QAction("&Undo", self)
        act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        act_undo.triggered.connect(self._on_undo)
        em.addAction(act_undo)

        act_redo = QAction("&Redo", self)
        act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        act_redo.triggered.connect(self._on_redo)
        em.addAction(act_redo)

        em.addSeparator()

        act_reset = QAction("Reset All Edits", self)
        act_reset.triggered.connect(self._on_reset)
        em.addAction(act_reset)

        em.addSeparator()

        act_crop = QAction("&Crop Tool", self)
        act_crop.setShortcut(Qt.Key.Key_C)
        act_crop.setToolTip("Activate interactive crop tool (C)")
        act_crop.triggered.connect(self._activate_crop)
        em.addAction(act_crop)

        # ── View menu ─────────────────────────────────────────────────────
        vm = mb.addMenu("&View")

        act_fit = QAction("Fit to Window", self)
        act_fit.setShortcut(Qt.Key.Key_F)
        act_fit.triggered.connect(self._canvas.fit_to_window)
        vm.addAction(act_fit)

        act_100 = QAction("100%  (1:1)", self)
        act_100.setShortcut(Qt.Key.Key_1)
        act_100.triggered.connect(self._canvas.zoom_1_to_1)
        vm.addAction(act_100)

        vm.addSeparator()

        # Before/After is a checkable action: toggles split view
        self._act_ba = QAction("Before / After", self)
        self._act_ba.setShortcut(Qt.Key.Key_Backslash)
        self._act_ba.setCheckable(True)
        self._act_ba.setChecked(False)
        self._act_ba.triggered.connect(self._toggle_before_after)
        vm.addAction(self._act_ba)

    def _build_toolbar(self):
        """
        Build the main toolbar with logo on the left and action buttons.

        Crop tool buttons (confirm/cancel/aspect) are in a secondary
        toolbar that is only visible when the crop tool is active.
        This keeps the main toolbar clean.
        """
        # ── Main toolbar ──────────────────────────────────────────────────
        tb = QToolBar("Main")
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        # Logo — left-most element
        logo = LogoWidget()
        tb.addWidget(logo)

        # Visual separator after logo
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet("background:#333; margin:6px 0;")
        tb.addWidget(sep)
        tb.addSeparator()

        # Standard action buttons
        for label, tip, slot in [
            ("Open",    "Open image file (Ctrl+O)",           self._menu_open),
            ("Save",    "Export/save edited image (Ctrl+S)",  self._menu_save),
            ("Undo",    "Undo last edit (Ctrl+Z)",            self._on_undo),
            ("Redo",    "Redo (Ctrl+Y)",                      self._on_redo),
            ("Reset",   "Reset all edits to defaults",        self._on_reset),
            ("Fit",     "Fit image to window (F)",            self._canvas.fit_to_window),
            ("1:1",     "Zoom to 100% (1)",                   self._canvas.zoom_1_to_1),
        ]:
            a = QAction(label, self)
            a.setToolTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)

        tb.addSeparator()

        # Crop button — activates the crop overlay
        act_crop = QAction("✂ Crop", self)
        act_crop.setToolTip("Activate crop tool (C)")
        act_crop.triggered.connect(self._activate_crop)
        tb.addAction(act_crop)

        # Before/After toggle button
        self._ba_action = QAction("⊞ B/A", self)
        self._ba_action.setToolTip("Toggle before/after split view (\\)")
        self._ba_action.setCheckable(True)
        self._ba_action.triggered.connect(self._toggle_before_after)
        tb.addAction(self._ba_action)

        self.addToolBar(tb)

        # ── Crop confirmation toolbar ─────────────────────────────────────
        # Shown only when crop tool is active.
        # Contains: aspect ratio selector, Confirm, Cancel.
        self._crop_tb = QToolBar("Crop")
        self._crop_tb.setObjectName("cropToolbar")
        self._crop_tb.setMovable(False)
        self._crop_tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._crop_tb.setVisible(False)   # hidden until crop mode activated

        # Aspect ratio label
        asp_lbl = QLabel("  Aspect: ")
        asp_lbl.setObjectName("paramLabel")
        self._crop_tb.addWidget(asp_lbl)

        # Aspect ratio buttons (radio-style: one active at a time)
        self._aspect_actions = {}
        for mode in [
            (AspectMode.FREE,       "Free"),
            (AspectMode.RATIO_1_1,  "1:1"),
            (AspectMode.RATIO_4_3,  "4:3"),
            (AspectMode.RATIO_16_9, "16:9"),
            (AspectMode.RATIO_3_2,  "3:2"),
        ]:
            asp_mode, asp_label = mode
            act = QAction(asp_label, self)
            act.setCheckable(True)
            act.setChecked(asp_mode == AspectMode.FREE)
            # Lambda captures asp_mode by default-arg binding
            act.triggered.connect(
                lambda checked, m=asp_mode: self._set_crop_aspect(m)
            )
            self._crop_tb.addAction(act)
            self._aspect_actions[asp_mode] = act

        self._crop_tb.addSeparator()

        # Confirm crop button — commits the crop to EditParams
        act_confirm = QAction("✔ Confirm", self)
        act_confirm.setToolTip("Apply crop (Enter)")
        act_confirm.triggered.connect(self._canvas.confirm_crop)
        self._crop_tb.addAction(act_confirm)

        # Cancel crop button — discards changes and hides overlay
        act_cancel = QAction("✖ Cancel", self)
        act_cancel.setToolTip("Cancel crop (Escape)")
        act_cancel.triggered.connect(self._canvas.cancel_crop)
        self._crop_tb.addAction(act_cancel)

        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self._crop_tb)

    def _set_window_icon(self):
        """
        Render the SVG logo at 64×64 px and use it as the window/taskbar icon.
        QSvgRenderer renders vector SVG to a QPixmap at any resolution.
        Falls back silently if the SVG file is missing.
        """
        from PyQt6.QtSvg import QSvgRenderer
        svg_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "resources", "logo.svg")
        )
        if not os.path.exists(svg_path):
            return
        try:
            from PyQt6.QtGui import QPainter
            renderer = QSvgRenderer(svg_path)
            pixmap   = QPixmap(64, 64)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter  = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            self.setWindowIcon(QIcon(pixmap))
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    # FILE I/O
    # ══════════════════════════════════════════════════════════════════════

    def open_file(self, filepath: str):
        """
        Begin async file load for filepath.
        Shows progress indicator, creates a LoadWorker thread.
        Called by: file panel click, toolbar Open, drag-drop, CLI arg.
        """
        if not os.path.isfile(filepath):
            return

        # Cancel any active crop before loading a new file
        if self._canvas.crop_active:
            self._canvas.cancel_crop()
            self._crop_tb.setVisible(False)

        self._canvas.set_loading(True)
        self._progress.setVisible(True)
        self._set_status(f"Loading  {os.path.basename(filepath)} …")

        # Create a fresh worker for this load operation
        self._load_worker = LoadWorker(self._pipeline, filepath, parent=self)
        self._load_worker.load_complete.connect(self._on_load_complete)
        self._load_worker.load_error.connect(self._on_load_error)
        self._load_worker.load_progress.connect(self._set_status)
        self._load_worker.start()

        # Highlight this file in the browser list
        self._file_panel.highlight_file(filepath)

    @pyqtSlot()
    def _on_load_complete(self):
        """
        Called (on UI thread) when LoadWorker finishes successfully.
        Updates status, refreshes EXIF panel, captures the 'before' image,
        fits canvas, and triggers the first render.
        """
        self._progress.setVisible(False)
        w, h  = self._pipeline.size
        fp    = self._pipeline.filepath
        raw_tag = "  [RAW]" if self._pipeline.is_raw else ""
        self._set_status(
            f"{os.path.basename(fp)}{raw_tag}  ·  {w} × {h} px"
        )
        self._file_panel.set_info(f"{w} × {h}\n{os.path.basename(fp)}")

        # Populate EXIF viewer with metadata from the loaded file
        self._exif_panel.update_meta(self._pipeline.meta)

        # Capture the 'before' image: render source with identity params.
        # This is done synchronously here (one-time cost) because it's
        # the baseline that never changes for this file.
        self._before_img = self._pipeline.render(scale=1.0)
        if self._before_after.isVisible():
            self._before_after.set_before(self._before_img)

        # Reset canvas view and trigger first render
        self._canvas.fit_to_window()
        self._request_render()

    @pyqtSlot(str)
    def _on_load_error(self, msg: str):
        """Show error dialog on load failure."""
        self._progress.setVisible(False)
        self._canvas.set_loading(False)
        QMessageBox.critical(self, "Load Error", msg)

    def _menu_open(self):
        """File menu → Open: show file dialog, add to panel, open first."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open Image(s)", "",
            "Images ("
            "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp "
            "*.cr2 *.cr3 *.nef *.nrw *.arw *.srf *.sr2 *.orf *.rw2 "
            "*.pef *.dng *.raf *.3fr *.mrw *.x3f *.erf *.kdc *.dcr "
            "*.raw *.rwl"
            ");;All Files (*)"
        )
        for p in paths:
            self._file_panel.add_file(p)
        if paths:
            self.open_file(paths[0])

    def _menu_save(self):
        """
        File menu → Export: show save dialog, start ExportWorker.
        Supports JPEG (quality 95), PNG, and 16-bit TIFF.
        """
        if not self._pipeline.loaded:
            QMessageBox.information(self, "No Image", "Open an image first.")
            return
        out, _ = QFileDialog.getSaveFileName(
            self, "Export Image", "",
            "JPEG (*.jpg);;PNG (*.png);;TIFF 16-bit (*.tif);;All Files (*)"
        )
        if not out:
            return

        self._progress.setVisible(True)
        self._set_status("Exporting…")
        self._export_worker = ExportWorker(
            self._pipeline, out, quality=95, parent=self
        )
        self._export_worker.export_complete.connect(self._on_export_complete)
        self._export_worker.export_error.connect(self._on_export_error)
        self._export_worker.start()

    @pyqtSlot(str)
    def _on_export_complete(self, path: str):
        self._progress.setVisible(False)
        self._set_status(f"Saved  →  {os.path.basename(path)}")

    @pyqtSlot(str)
    def _on_export_error(self, msg: str):
        self._progress.setVisible(False)
        QMessageBox.critical(self, "Export Error", msg)

    # ══════════════════════════════════════════════════════════════════════
    # EDIT PARAMETER HANDLING
    # ══════════════════════════════════════════════════════════════════════

    @pyqtSlot(str, object)
    def _on_param_changed(self, name: str, value):
        """
        Called whenever any edit control changes (slider, curve point, flip, etc.).

        Handles two special cases:
          'hsl'          — value is a (range_name, [h,s,l]) tuple
          'curve_points' — value is a list of (x,y) control point tuples

        For all others: directly sets the named attribute on EditParams.

        Takes a history snapshot BEFORE applying the change so undo
        restores the pre-change state. Then kicks the debounce timer.
        """
        if not self._pipeline.loaded:
            return

        # Save current state to undo history before making the change
        self._pipeline.snapshot()

        if name == "hsl":
            # Unpack: value = (range_name, [hue, sat, lum])
            range_name, hsl_vals = value
            self._pipeline.params.hsl[range_name] = hsl_vals

        elif name == "curve_points":
            # Replace the entire curve_points list
            self._pipeline.params.curve_points = list(value)

        else:
            # Standard param: setattr on the EditParams dataclass
            setattr(self._pipeline.params, name, value)

        # Restart the debounce timer — render fires 80ms after last change
        self._render_timer.start()

    def _on_reset(self):
        """Reset all edit parameters to defaults. One undo snapshot taken."""
        self._pipeline.reset_edits()
        self._edit_panel.load_params(self._pipeline.params)
        self._request_render()

    def _on_undo(self):
        """
        Step back one edit in history.
        Updates all UI controls to reflect restored params.
        """
        if self._pipeline.undo():
            self._edit_panel.load_params(self._pipeline.params)
            self._request_render()

    def _on_redo(self):
        """
        Step forward one edit in redo stack.
        Updates all UI controls to reflect restored params.
        """
        if self._pipeline.redo():
            self._edit_panel.load_params(self._pipeline.params)
            self._request_render()

    # ══════════════════════════════════════════════════════════════════════
    # CROP TOOL
    # ══════════════════════════════════════════════════════════════════════

    def _activate_crop(self):
        """
        Enter crop mode:
          1. Show the crop confirmation toolbar
          2. Activate the crop overlay on the canvas with current crop state
        """
        if not self._pipeline.loaded:
            return
        self._crop_tb.setVisible(True)
        w, h = self._pipeline.size
        # Pass existing crop rect (if any) so user can refine a previous crop
        self._canvas.start_crop(w, h, self._pipeline.params.crop)

    def _set_crop_aspect(self, mode: AspectMode):
        """
        Change aspect ratio constraint for the active crop.
        Updates the toolbar button checked state (radio-button behaviour).
        """
        # Uncheck all aspect buttons, then check the selected one
        for m, act in self._aspect_actions.items():
            act.setChecked(m == mode)
        self._canvas.set_crop_aspect(mode)

    @pyqtSlot(object)
    def _on_crop_confirmed(self, norm_rect):
        """
        Called when user confirms the crop (Enter key or Confirm button).
        norm_rect: normalised (x, y, w, h) tuple, or None (no crop).

        Saves a history snapshot, updates EditParams.crop,
        hides crop toolbar, triggers a re-render.
        """
        self._pipeline.snapshot()
        self._pipeline.params.crop = norm_rect
        self._crop_tb.setVisible(False)
        self._request_render()

    @pyqtSlot()
    def _on_crop_cancelled(self):
        """
        Called when user cancels crop (Escape or Cancel button).
        Hides crop toolbar; EditParams.crop is unchanged.
        """
        self._crop_tb.setVisible(False)

    # ══════════════════════════════════════════════════════════════════════
    # BEFORE / AFTER VIEW
    # ══════════════════════════════════════════════════════════════════════

    def _toggle_before_after(self, checked: bool = None):
        """
        Toggle between normal canvas and before/after split view.

        When entering B/A mode:
          - Sets 'before' pixmap from stored pre-edit render
          - Sets 'after' pixmap from latest canvas render
          - Syncs zoom/offset so both views match
          - Switches QStackedWidget to show BeforeAfterView

        When leaving B/A mode:
          - Switches back to ImageCanvas
          - Triggers a fresh render
        """
        # Sync checked state between menu item and toolbar button
        if checked is None:
            checked = not self._before_after_active
        self._act_ba.setChecked(checked)
        self._ba_action.setChecked(checked)
        self._before_after_active = checked

        if checked:
            # Enter before/after mode
            if self._before_img is not None:
                self._before_after.set_before(self._before_img)
            # Pass current canvas transform so both views align
            self._before_after.set_transform(
                self._canvas.zoom, self._canvas.offset
            )
            self._view_stack.setCurrentIndex(1)   # show BeforeAfterView
        else:
            # Return to normal canvas
            self._view_stack.setCurrentIndex(0)   # show ImageCanvas
            self._request_render()

    # ══════════════════════════════════════════════════════════════════════
    # RENDER PIPELINE
    # ══════════════════════════════════════════════════════════════════════

    def _request_render(self):
        """Restart the debounce timer (80ms single-shot)."""
        self._render_timer.start()

    def _schedule_render(self):
        """
        Called by debounce timer. Determines the appropriate preview scale
        based on current zoom, then requests a render from the worker.

        Scale logic:
          zoom ≥ 1.0 → scale = 1.0 (full resolution — user sees pixel-level detail)
          zoom < 1.0 → scale = max(0.25, zoom) (match screen pixels for speed)
        This avoids rendering 24 MP at 10% zoom when 240×160 px is sufficient.
        """
        if not self._pipeline.loaded:
            return
        scale = min(1.0, max(0.25, self._canvas.zoom))
        self._render_worker.request_render(scale=scale)

    @pyqtSlot(object, object)
    def _on_render_ready(self, img_u8: np.ndarray, hist: dict):
        """
        Called (on UI thread) when RenderWorker finishes a render.
        Distributes the result to all consumer widgets.

        img_u8: (H, W, 3) uint8 RGB array — the display-ready image
        hist:   dict with 'r','g','b','lum' float32 histogram arrays
        """
        # Update the main canvas display
        self._canvas.set_image(img_u8)

        # Update histogram display (bottom bar)
        self._histogram.update_histogram(hist)

        # Pass luminance histogram to curve widget for background context
        if "lum" in hist:
            self._edit_panel.update_curve_histogram(hist["lum"])

        # Update the 'after' side of the before/after view
        self._before_after.set_after(img_u8)

        # Sync before/after transform with canvas in case user panned/zoomed
        if self._before_after_active:
            self._before_after.set_transform(
                self._canvas.zoom, self._canvas.offset
            )

    @pyqtSlot(str)
    def _on_render_error(self, msg: str):
        """Show render error in status bar (not a dialog — non-blocking)."""
        self._set_status(f"Render error: {msg}")

    def _on_zoom_changed(self, zoom: float):
        """
        Called when user zooms the canvas.
        Re-requests a render at the new scale for quality/speed matching.
        Also syncs the before/after view transform.
        """
        self._render_timer.start()
        if self._before_after_active:
            self._before_after.set_transform(zoom, self._canvas.offset)

    # ══════════════════════════════════════════════════════════════════════
    # DRAG & DROP
    # ══════════════════════════════════════════════════════════════════════

    def dragEnterEvent(self, event: QDragEnterEvent):
        """
        Accept drag events that contain file URLs.
        Qt will call dropEvent if the drag is accepted here.
        """
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        """
        Handle file drop: add all dropped files to the panel,
        then open the first one.
        """
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            if os.path.isfile(path):
                self._file_panel.add_file(path)
        if urls:
            first = urls[0].toLocalFile()
            if os.path.isfile(first):
                self.open_file(first)

    # ══════════════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════════════

    def _set_status(self, msg: str):
        """Update the status bar label text."""
        self._status_lbl.setText(msg)

    def closeEvent(self, event):
        """
        Clean shutdown: stop the render worker thread before Qt destroys widgets.
        RenderWorker.stop() blocks until the thread exits cleanly.
        """
        self._render_worker.stop()
        super().closeEvent(event)
