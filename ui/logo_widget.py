"""
ui/logo_widget.py
=================
SVG LOGO WIDGET FOR THE TOOLBAR
--------------------------------
Renders the ImageEdit SVG logo at a fixed height inside the toolbar.
Uses PyQt6's QSvgWidget which renders the SVG at native resolution
(no rasterisation artefacts at any DPI).

FALLBACK
--------
If PyQt6.QtSvgWidgets is unavailable (incomplete Qt6 installation),
falls back to a styled QLabel with the app name in green text.
This makes the app launchable even on minimal Qt installs.

SIZING
------
Logo SVG viewBox is 600×120 = 5:1 aspect ratio.
Displayed at LOGO_HEIGHT=36px → width = 180px in the toolbar.
"""

from __future__ import annotations
import os
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont

# Path to the SVG file relative to this module
_SVG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "resources", "logo.svg")
)


class LogoWidget(QWidget):
    """
    Compact logo for the left side of the main toolbar.
    Tries QSvgWidget first; falls back to a styled QLabel.
    """

    LOGO_HEIGHT = 36   # px — fits neatly within a 44px toolbar
    ASPECT      = 5.0  # viewBox 600/120 = 5:1

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 12, 2)
        layout.setSpacing(0)

        if os.path.exists(_SVG_PATH):
            try:
                # QSvgWidget renders the SVG directly — crisp at all DPIs
                from PyQt6.QtSvgWidgets import QSvgWidget
                svg = QSvgWidget(_SVG_PATH, self)
                w   = int(self.LOGO_HEIGHT * self.ASPECT)
                svg.setFixedSize(w, self.LOGO_HEIGHT)
                layout.addWidget(svg)
                return   # success — skip fallback
            except Exception:
                pass   # SVGWidgets not available — fall through

        # ── Text fallback ─────────────────────────────────────────────────
        # Styled label mimicking the logo's most important elements.
        lbl = QLabel("ImageEdit™")
        lbl.setFont(QFont("Segoe UI", 13, QFont.Weight.SemiBold))
        lbl.setStyleSheet("color: #4d87c8; letter-spacing: 1px;")
        layout.addWidget(lbl)

    def sizeHint(self) -> QSize:
        return QSize(int(self.LOGO_HEIGHT * self.ASPECT) + 16, self.LOGO_HEIGHT + 4)
