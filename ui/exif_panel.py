"""
ui/exif_panel.py
================
EXIF / METADATA VIEWER PANEL
-----------------------------
Displays camera metadata extracted from loaded images.
Shown in a scrollable two-column table (tag name | value).

DATA SOURCES
------------
  JPEG/PNG/TIFF: Pillow extracts standard EXIF tags (IFD0, Exif IFD, GPS IFD).
  RAW files:     rawpy provides sensor metadata (black level, white level,
                 raw type); Pillow extracts embedded EXIF from the RAW file.

DISPLAYED FIELDS (priority order, others shown below)
------------------------------------------------------
  Camera:        Make, Model, LensModel
  Exposure:      ExposureTime, FNumber, ISOSpeedRatings, ExposureBias
  Optics:        FocalLength, FocalLengthIn35mmFilm
  Time:          DateTimeOriginal, DateTime
  Image info:    PixelXDimension, PixelYDimension, Orientation
  RAW specifics: raw_type, black_level, white_level

PRIVACY
-------
GPS coordinates are intentionally excluded from display and never shown.
"""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QLabel, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor


# Ordered list of priority EXIF tags shown at the top of the table.
# Tags not in this list are shown below in alphabetical order.
PRIORITY_TAGS = [
    "Make", "Model", "LensModel",
    "ExposureTime", "FNumber", "ISOSpeedRatings", "ExposureBiasValue",
    "FocalLength", "FocalLengthIn35mmFilm",
    "DateTimeOriginal", "DateTime",
    "PixelXDimension", "PixelYDimension",
    "Orientation", "ColorSpace",
    "Software",
    # RAW-specific keys from rawpy
    "raw_type", "black_level", "white_level",
]

# Human-readable display names for common EXIF tags
TAG_DISPLAY = {
    "Make":                   "Camera Make",
    "Model":                  "Camera Model",
    "LensModel":              "Lens",
    "ExposureTime":           "Shutter Speed",
    "FNumber":                "Aperture",
    "ISOSpeedRatings":        "ISO",
    "ExposureBiasValue":      "Exp. Bias",
    "FocalLength":            "Focal Length",
    "FocalLengthIn35mmFilm":  "35mm Equiv.",
    "DateTimeOriginal":       "Date Taken",
    "DateTime":               "Date Modified",
    "PixelXDimension":        "Width",
    "PixelYDimension":        "Height",
    "Orientation":            "Orientation",
    "ColorSpace":             "Color Space",
    "Software":               "Software",
    "raw_type":               "RAW Type",
    "black_level":            "Black Level",
    "white_level":            "White Level",
}


class ExifPanel(QWidget):
    """
    Scrollable EXIF metadata viewer.
    Call update_meta(meta_dict) when a new image is loaded.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("exifPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Section header label
        header = QLabel("Image Info")
        header.setObjectName("sectionHeader")
        header.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        header.setStyleSheet("color: #666; text-transform: uppercase; "
                             "letter-spacing: 1px; padding: 2px 0;")
        layout.addWidget(header)

        # Two-column table: Tag | Value
        self._table = QTableWidget(0, 2)
        self._table.setObjectName("exifTable")
        self._table.setHorizontalHeaderLabels(["Tag", "Value"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)   # read-only
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setFont(QFont("monospace", 8))
        self._table.verticalHeader().setDefaultSectionSize(18)

        layout.addWidget(self._table)

        # "No metadata" placeholder label
        self._empty_label = QLabel("No metadata available")
        self._empty_label.setObjectName("hintLabel")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setFont(QFont("monospace", 8))
        layout.addWidget(self._empty_label)
        self._empty_label.setVisible(True)
        self._table.setVisible(False)

    # ── Public ─────────────────────────────────────────────────────────────

    def update_meta(self, meta: dict):
        """
        Populate the table with metadata from the pipeline.
        meta: flat dict {tag_name: value_str} from ImagePipeline.meta
        """
        self._table.setRowCount(0)   # clear existing rows

        if not meta:
            # No metadata at all — show placeholder
            self._table.setVisible(False)
            self._empty_label.setVisible(True)
            return

        self._table.setVisible(True)
        self._empty_label.setVisible(False)

        # Build ordered list: priority tags first, then remaining alphabetically
        ordered_keys = []
        for key in PRIORITY_TAGS:
            if key in meta:
                ordered_keys.append(key)
        # Append any remaining tags not in the priority list
        for key in sorted(meta.keys()):
            if key not in ordered_keys:
                ordered_keys.append(key)

        # Populate table rows
        for key in ordered_keys:
            value = meta.get(key, "")
            display_name = TAG_DISPLAY.get(key, key)

            # Format certain values for readability
            value = self._format_value(key, value)

            row = self._table.rowCount()
            self._table.insertRow(row)

            # Tag name cell — muted colour
            tag_item = QTableWidgetItem(display_name)
            tag_item.setForeground(QColor("#888"))
            self._table.setItem(row, 0, tag_item)

            # Value cell — bright colour
            val_item = QTableWidgetItem(value)
            val_item.setForeground(QColor("#d4d4d4"))
            self._table.setItem(row, 1, val_item)

    def clear(self):
        """Clear the panel when no image is loaded."""
        self._table.setRowCount(0)
        self._table.setVisible(False)
        self._empty_label.setVisible(True)

    # ── Value formatting ───────────────────────────────────────────────────

    @staticmethod
    def _format_value(tag: str, value: str) -> str:
        """
        Apply human-readable formatting to specific tag values.
        E.g. FNumber "28/10" → "f/2.8"
             ExposureTime "1/250" → "1/250 s"
             FocalLength "50/1" → "50 mm"
        """
        try:
            if tag == "FNumber" and "/" in value:
                # Convert rational to f-stop: "28/10" → "f/2.8"
                n, d = value.split("/")
                f = float(n) / float(d)
                return f"f/{f:.1f}"

            elif tag == "ExposureTime" and "/" in value:
                # Keep as fraction for sub-second, decimal for long exposures
                n, d = value.split("/")
                fv = float(n) / float(d)
                if fv < 1.0:
                    return f"1/{int(1/fv)} s"
                else:
                    return f"{fv:.1f} s"

            elif tag == "FocalLength" and "/" in value:
                n, d = value.split("/")
                mm = float(n) / float(d)
                return f"{mm:.0f} mm"

            elif tag == "FocalLengthIn35mmFilm":
                return f"{value} mm"

            elif tag == "ISOSpeedRatings":
                return f"ISO {value}"

            elif tag == "ExposureBiasValue" and "/" in value:
                n, d = value.split("/")
                ev = float(n) / float(d)
                return f"{ev:+.1f} EV"

            elif tag == "PixelXDimension":
                return f"{value} px"

            elif tag == "PixelYDimension":
                return f"{value} px"

        except Exception:
            pass  # If formatting fails, return raw value

        return value
