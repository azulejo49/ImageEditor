"""
ui/file_panel.py
Left-side file browser + thumbnail filmstrip.
"""

from __future__ import annotations
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QPushButton, QLabel, QFileDialog,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QIcon, QFont, QPixmap, QColor

SUPPORTED = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp",
    ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".orf", ".rw2", ".pef", ".dng", ".raf", ".3fr", ".mrw",
    ".x3f", ".erf", ".kdc", ".dcr", ".raw", ".rwl",
}


class FilePanel(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filePanel")
        self.setFixedWidth(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Toolbar
        toolbar = QWidget()
        tb_h = QHBoxLayout(toolbar)
        tb_h.setContentsMargins(0, 0, 0, 0)
        tb_h.setSpacing(4)

        open_btn = QPushButton("Open File")
        open_btn.setObjectName("toolBtn")
        open_btn.clicked.connect(self._open_file_dialog)

        folder_btn = QPushButton("Open Folder")
        folder_btn.setObjectName("toolBtn")
        folder_btn.clicked.connect(self._open_folder_dialog)

        tb_h.addWidget(open_btn)
        tb_h.addWidget(folder_btn)
        layout.addWidget(toolbar)

        # Drag-drop hint
        hint = QLabel("or drag & drop")
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setFont(QFont("monospace", 8))
        layout.addWidget(hint)

        # File list
        self._list = QListWidget()
        self._list.setObjectName("fileList")
        self._list.setIconSize(QSize(48, 36))
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

        # Info label at bottom
        self._info = QLabel("")
        self._info.setObjectName("infoLabel")
        self._info.setFont(QFont("monospace", 7))
        self._info.setWordWrap(True)
        layout.addWidget(self._info)

        self._current_dir: str = ""

    # ── Public ─────────────────────────────────────────────────────────────

    def add_file(self, filepath: str):
        item = QListWidgetItem(os.path.basename(filepath))
        item.setData(Qt.ItemDataRole.UserRole, filepath)
        item.setToolTip(filepath)
        ext = os.path.splitext(filepath)[1].lower()
        raw_exts = {".cr2", ".cr3", ".nef", ".nrw", ".arw", ".dng",
                    ".orf", ".rw2", ".pef", ".raf", ".3fr", ".mrw",
                    ".x3f", ".erf", ".kdc", ".dcr", ".raw", ".rwl"}
        if ext in raw_exts:
            item.setForeground(QColor("#e8aa44"))   # amber for RAW
        self._list.addItem(item)

    def set_info(self, text: str):
        self._info.setText(text)

    def highlight_file(self, filepath: str):
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == filepath:
                self._list.setCurrentItem(item)
                break

    # ── Private ────────────────────────────────────────────────────────────

    def _open_file_dialog(self):
        filt = (
            "Images (*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp "
            "*.cr2 *.cr3 *.nef *.nrw *.arw *.srf *.sr2 *.orf *.rw2 "
            "*.pef *.dng *.raf *.3fr *.mrw *.x3f *.erf *.kdc *.dcr "
            "*.raw *.rwl);;"
            "All Files (*)"
        )
        paths, _ = QFileDialog.getOpenFileNames(self, "Open Image(s)", "", filt)
        for p in paths:
            self.add_file(p)
        if paths:
            self.file_selected.emit(paths[0])

    def _open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Open Folder")
        if not folder:
            return
        self._current_dir = folder
        self._list.clear()
        files = sorted(
            f for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in SUPPORTED
        )
        for fname in files:
            self.add_file(os.path.join(folder, fname))
        if files:
            first = os.path.join(folder, files[0])
            self.file_selected.emit(first)

    def _on_item_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.file_selected.emit(path)
