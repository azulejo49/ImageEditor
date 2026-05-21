"""
ui/file_panel.py
================
LEFT-SIDE FILE BROWSER PANEL
-----------------------------
Provides file opening, folder scanning, and a scrollable file list.
RAW files are highlighted in amber to distinguish them from JPEG/PNG.

FEATURES
--------
  Open File   — QFileDialog for one or multiple files
  Open Folder — scans entire folder for supported image formats,
                populates list, auto-opens first file
  File list   — click to open; RAW files shown in amber
  Info label  — shows dimensions and filename of current image
  Drag & drop — dropping onto the main window adds files here too

SUPPORTED EXTENSIONS
---------------------
JPEG, PNG, TIFF, BMP, WebP, plus all major RAW formats:
  Canon (CR2, CR3), Nikon (NEF, NRW), Sony (ARW, SRF, SR2),
  Olympus (ORF), Panasonic (RW2), Pentax (PEF), Adobe (DNG),
  Fujifilm (RAF), Hasselblad (3FR), Minolta (MRW), Sigma (X3F),
  Epson (ERF), Kodak (KDC, DCR), Generic/Leica (RAW, RWL)

SIGNAL
------
file_selected(str)  — emitted with full file path when user clicks a file
                      or opens a file/folder via dialog.
                      Connected to MainWindow.open_file().
"""

from __future__ import annotations
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QPushButton, QLabel, QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QColor

# All image extensions this app can open
SUPPORTED = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp",
    ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".orf", ".rw2", ".pef", ".dng", ".raf", ".3fr", ".mrw",
    ".x3f", ".erf", ".kdc", ".dcr", ".raw", ".rwl",
}

# RAW-only subset (used for amber colouring in the list)
RAW_EXTS = {
    ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".orf", ".rw2", ".pef", ".dng", ".raf", ".3fr", ".mrw",
    ".x3f", ".erf", ".kdc", ".dcr", ".raw", ".rwl",
}

# Open file dialog filter string
FILE_FILTER = (
    "Images ("
    "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp "
    "*.cr2 *.cr3 *.nef *.nrw *.arw *.srf *.sr2 *.orf *.rw2 "
    "*.pef *.dng *.raf *.3fr *.mrw *.x3f *.erf *.kdc *.dcr "
    "*.raw *.rwl"
    ");;"
    "All Files (*)"
)


class FilePanel(QWidget):
    """Left-side panel: file browser + list + info label."""

    file_selected = pyqtSignal(str)   # full path of file to open

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filePanel")
        self.setFixedWidth(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Toolbar: Open File / Open Folder buttons ──────────────────────
        toolbar = QWidget()
        tb_h = QHBoxLayout(toolbar)
        tb_h.setContentsMargins(0, 0, 0, 0)
        tb_h.setSpacing(4)

        open_btn = QPushButton("File")
        open_btn.setObjectName("toolBtn")
        open_btn.setToolTip("Open one or more image files (Ctrl+O)")
        open_btn.clicked.connect(self._open_file_dialog)

        folder_btn = QPushButton("Folder")
        folder_btn.setObjectName("toolBtn")
        folder_btn.setToolTip("Open all images in a folder")
        folder_btn.clicked.connect(self._open_folder_dialog)

        tb_h.addWidget(open_btn)
        tb_h.addWidget(folder_btn)
        layout.addWidget(toolbar)

        # ── Drag-drop hint ────────────────────────────────────────────────
        hint = QLabel("drag & drop files here")
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setFont(QFont("monospace", 7))
        layout.addWidget(hint)

        # ── File list ─────────────────────────────────────────────────────
        # Shows filenames; RAW files highlighted in amber.
        # Clicking an item emits file_selected.
        self._list = QListWidget()
        self._list.setObjectName("fileList")
        self._list.setIconSize(QSize(48, 36))
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.setToolTip("Click a file to open it")
        layout.addWidget(self._list)

        # ── Info label: dimensions + filename ────────────────────────────
        self._info = QLabel("")
        self._info.setObjectName("infoLabel")
        self._info.setFont(QFont("monospace", 7))
        self._info.setWordWrap(True)
        layout.addWidget(self._info)

    # ── Public API ─────────────────────────────────────────────────────────

    def add_file(self, filepath: str):
        """
        Add a single file path to the list widget.
        Duplicate detection: skip if already present.
        RAW files are coloured amber for easy identification.
        """
        # Check for duplicates before adding
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == filepath:
                return   # already in list — skip

        item = QListWidgetItem(os.path.basename(filepath))
        item.setData(Qt.ItemDataRole.UserRole, filepath)   # store full path
        item.setToolTip(filepath)                          # show full path on hover

        # Colour RAW files amber so they stand out from JPEG/PNG
        if os.path.splitext(filepath)[1].lower() in RAW_EXTS:
            item.setForeground(QColor("#e8aa44"))

        self._list.addItem(item)

    def set_info(self, text: str):
        """Update the info label (called with 'WxH  filename' after load)."""
        self._info.setText(text)

    def highlight_file(self, filepath: str):
        """
        Visually select the list item matching filepath.
        Called when a file is opened programmatically (e.g. from toolbar).
        """
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == filepath:
                self._list.setCurrentItem(item)
                break

    # ── Private ────────────────────────────────────────────────────────────

    def _open_file_dialog(self):
        """
        Show a file picker supporting multiple selection.
        Adds all selected files to the list, opens the first one.
        """
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open Image(s)", "", FILE_FILTER
        )
        for p in paths:
            self.add_file(p)
        if paths:
            self.file_selected.emit(paths[0])

    def _open_folder_dialog(self):
        """
        Show a folder picker, scan for all supported files,
        populate the list, and auto-open the first file found.
        Files are sorted alphabetically (natural filename order).
        """
        folder = QFileDialog.getExistingDirectory(self, "Open Folder")
        if not folder:
            return

        # Scan folder for supported extensions (non-recursive)
        self._list.clear()
        files = sorted(
            f for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in SUPPORTED
        )
        for fname in files:
            self.add_file(os.path.join(folder, fname))

        # Auto-open first file in the folder
        if files:
            self.file_selected.emit(os.path.join(folder, files[0]))

    def _on_item_clicked(self, item: QListWidgetItem):
        """Emit file_selected when user clicks a list item."""
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.file_selected.emit(path)
