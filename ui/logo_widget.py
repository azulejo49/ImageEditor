"""
ui/logo_widget.py
Renders the ImageEdit SVG logo as a QWidget for use in the toolbar.
Falls back to a text label if SVG loading fails.
"""

from __future__ import annotations
import os
from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QPainter
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtSvg import QSvgRenderer


_SVG_PATH = os.path.join(os.path.dirname(__file__), "..", "resources", "logo.svg")


class LogoWidget(QWidget):
    """
    Compact logo banner for the toolbar.
    Renders the SVG at a fixed height; scales width proportionally.
    """

    LOGO_HEIGHT = 36   # px — fits neatly in a toolbar

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 12, 2)
        layout.setSpacing(0)

        svg_path = os.path.normpath(_SVG_PATH)

        if os.path.exists(svg_path):
            try:
                svg = QSvgWidget(svg_path, self)
                # SVG native viewBox is 600×120 → aspect ratio 5:1
                w = self.LOGO_HEIGHT * 5
                svg.setFixedSize(w, self.LOGO_HEIGHT)
                layout.addWidget(svg)
                return
            except Exception:
                pass   # fall through to text fallback

        # Text fallback
        lbl = QLabel("ImageEdit™")
        lbl.setObjectName("logoFallback")
        from PyQt6.QtGui import QFont
        f = QFont("Arial", 14, QFont.Weight.Bold)
        lbl.setFont(f)
        lbl.setStyleSheet("color: #34d399; letter-spacing: 1px;")
        layout.addWidget(lbl)

    def sizeHint(self) -> QSize:
        return QSize(self.LOGO_HEIGHT * 5 + 16, self.LOGO_HEIGHT + 4)
