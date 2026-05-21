"""
main.py
=======
APPLICATION ENTRY POINT
------------------------
Bootstrap sequence:
  1. Set environment variables for HiDPI scaling before QApplication exists
  2. Create QApplication with the correct argv
  3. Set app metadata (name, version, organisation)
  4. Load and apply the dark QSS stylesheet
  5. Create and show the MainWindow
  6. If file paths were passed as CLI arguments, open them
  7. Hand control to the Qt event loop (app.exec())

CLI USAGE
---------
  python main.py                    # start with empty state
  python main.py photo.cr2          # open one file on launch
  python main.py a.jpg b.nef c.dng  # open multiple files
"""

import sys
import os

# ── HiDPI setup (must be set before QApplication is created) ──────────────
# AA_UseHighDpiPixmaps: draw all pixmaps at native resolution on HiDPI screens
# QT_AUTO_SCREEN_SCALE_FACTOR: let Qt scale UI elements for the display DPI
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore    import Qt
from ui.main_window  import MainWindow


def main():
    # ── Create application ────────────────────────────────────────────────
    app = QApplication(sys.argv)
    app.setApplicationName("ImageEdit")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("ImageEdit")

    # Enable crisp icons on Retina / HiDPI displays
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    # ── Load stylesheet ───────────────────────────────────────────────────
    # The QSS file lives in resources/dark.qss alongside the project.
    # It defines colours, fonts, and widget styling for the entire app.
    stylesheet = _load_stylesheet()
    if stylesheet:
        app.setStyleSheet(stylesheet)

    # ── Create and show main window ───────────────────────────────────────
    window = MainWindow()
    window.show()

    # ── Open CLI file arguments ───────────────────────────────────────────
    # Any extra arguments after 'main.py' are treated as file paths.
    # Files are added to the file panel and the first is opened.
    for arg in sys.argv[1:]:
        if os.path.isfile(arg):
            window._file_panel.add_file(arg)
    if len(sys.argv) > 1:
        first = next((a for a in sys.argv[1:] if os.path.isfile(a)), None)
        if first:
            window.open_file(first)

    # ── Enter Qt event loop ───────────────────────────────────────────────
    # app.exec() blocks until the last window is closed.
    # Returns exit code 0 on clean close, non-zero on error.
    sys.exit(app.exec())


def _load_stylesheet() -> str:
    """
    Read the dark.qss file from resources/ and return it as a string.
    Returns empty string if the file is not found (app still works, unstyled).
    """
    qss_path = os.path.join(os.path.dirname(__file__), "resources", "dark.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


if __name__ == "__main__":
    main()
