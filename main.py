#!/usr/bin/env python3
"""
ImageEdit — Professional RAW + JPEG Photo Editor
Entry point: python main.py
"""

import sys
import os

# Ensure HiDPI scaling before QApplication
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon

from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ImageEdit")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("ImageEdit")
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    # Dark professional stylesheet
    app.setStyleSheet(load_stylesheet())

    window = MainWindow()
    window.show()

    # Open files passed as CLI arguments
    for arg in sys.argv[1:]:
        if os.path.isfile(arg):
            window.open_file(arg)

    sys.exit(app.exec())


def load_stylesheet() -> str:
    css_path = os.path.join(os.path.dirname(__file__), "resources", "dark.qss")
    if os.path.exists(css_path):
        with open(css_path, "r") as f:
            return f.read()
    return ""


if __name__ == "__main__":
    main()
