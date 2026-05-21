"""
ui/edit_panel.py
Non-destructive edit controls panel.
All sliders emit param_changed(name, value) signal.
"""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSlider, QGroupBox, QPushButton, QScrollArea,
    QSizePolicy, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from core.image_pipeline import EditParams


class EditPanel(QScrollArea):
    param_changed  = pyqtSignal(str, object)   # (param_name, value)
    reset_clicked  = pyqtSignal()
    undo_clicked   = pyqtSignal()
    redo_clicked   = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(4)
        self.setWidget(container)

        self._sliders: dict[str, QSlider] = {}
        self._labels:  dict[str, QLabel]  = {}

        self._build_history_buttons()
        self._build_section("Tone", [
            ("exposure",   "Exposure",   -400, 400, 0),    # ×0.01 = EV
            ("highlights", "Highlights", -100, 100, 0),
            ("shadows",    "Shadows",    -100, 100, 0),
            ("whites",     "Whites",     -100, 100, 0),
            ("blacks",     "Blacks",     -100, 100, 0),
            ("brightness", "Brightness", -100, 100, 0),
            ("contrast",   "Contrast",   -100, 100, 0),
        ])
        self._build_section("Color", [
            ("temperature", "Temperature", -100, 100, 0),
            ("tint",        "Tint",        -100, 100, 0),
            ("vibrance",    "Vibrance",    -100, 100, 0),
            ("saturation",  "Saturation",  -100, 100, 0),
        ])
        self._build_section("Detail", [
            ("sharpness",   "Sharpness",   0, 100, 0),
            ("noise_lum",   "Noise (Lum)", 0, 100, 0),
            ("noise_color", "Noise (Col)", 0, 100, 0),
        ])
        self._build_section("Optics", [
            ("vignette", "Vignette", -100, 100, 0),
        ])
        self._build_section("Transform", [
            ("rotation", "Rotation", -1800, 1800, 0),   # ×0.1 = degrees
        ])
        self._build_flip_row()
        self._layout.addStretch()

    # ── History buttons ────────────────────────────────────────────────────

    def _build_history_buttons(self):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 4)
        h.setSpacing(4)

        undo_btn = QPushButton("⟵ Undo")
        undo_btn.setObjectName("histBtn")
        undo_btn.clicked.connect(self.undo_clicked)

        redo_btn = QPushButton("Redo ⟶")
        redo_btn.setObjectName("histBtn")
        redo_btn.clicked.connect(self.redo_clicked)

        reset_btn = QPushButton("Reset All")
        reset_btn.setObjectName("resetBtn")
        reset_btn.clicked.connect(self.reset_clicked)

        h.addWidget(undo_btn)
        h.addWidget(redo_btn)
        h.addWidget(reset_btn)
        self._layout.addWidget(row)

    # ── Section builder ────────────────────────────────────────────────────

    def _build_section(self, title: str, params: list):
        box = QGroupBox(title)
        box.setObjectName("editSection")
        vbox = QVBoxLayout(box)
        vbox.setContentsMargins(6, 4, 6, 4)
        vbox.setSpacing(2)

        for name, label, min_v, max_v, default in params:
            row = self._make_slider_row(name, label, min_v, max_v, default)
            vbox.addWidget(row)

        self._layout.addWidget(box)

    def _make_slider_row(self, name: str, label: str,
                         min_v: int, max_v: int, default: int) -> QWidget:
        widget = QWidget()
        h = QHBoxLayout(widget)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        lbl = QLabel(label)
        lbl.setFixedWidth(86)
        lbl.setObjectName("paramLabel")
        lbl.setFont(QFont("monospace", 8))

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_v, max_v)
        slider.setValue(default)
        slider.setObjectName("paramSlider")
        slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        val_lbl = QLabel("0")
        val_lbl.setFixedWidth(34)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_lbl.setObjectName("paramValue")
        val_lbl.setFont(QFont("monospace", 8))

        def on_change(v, n=name, vl=val_lbl):
            # Special cases: divide to get real value
            if n == "exposure":
                real = v / 100.0
                vl.setText(f"{real:+.2f}")
                self.param_changed.emit(n, real)
            elif n == "rotation":
                real = v / 10.0
                vl.setText(f"{real:.1f}")
                self.param_changed.emit(n, real)
            else:
                vl.setText(str(v))
                self.param_changed.emit(n, float(v))

        slider.valueChanged.connect(on_change)

        h.addWidget(lbl)
        h.addWidget(slider)
        h.addWidget(val_lbl)

        self._sliders[name] = slider
        self._labels[name]  = val_lbl
        return widget

    def _build_flip_row(self):
        box = QGroupBox("Flip")
        box.setObjectName("editSection")
        h = QHBoxLayout(box)
        h.setContentsMargins(6, 4, 6, 4)

        btn_h = QPushButton("Flip H")
        btn_v = QPushButton("Flip V")
        btn_h.setCheckable(True)
        btn_v.setCheckable(True)
        btn_h.setObjectName("flipBtn")
        btn_v.setObjectName("flipBtn")

        btn_h.toggled.connect(lambda v: self.param_changed.emit("flip_h", v))
        btn_v.toggled.connect(lambda v: self.param_changed.emit("flip_v", v))

        h.addWidget(btn_h)
        h.addWidget(btn_v)
        self._layout.addWidget(box)

    # ── Sync from params ───────────────────────────────────────────────────

    def load_params(self, p: EditParams):
        """Populate all sliders from an EditParams object (after undo/redo)."""
        mappings = {
            "exposure":    int(p.exposure * 100),
            "highlights":  int(p.highlights),
            "shadows":     int(p.shadows),
            "whites":      int(p.whites),
            "blacks":      int(p.blacks),
            "brightness":  int(p.brightness),
            "contrast":    int(p.contrast),
            "temperature": int(p.temperature),
            "tint":        int(p.tint),
            "vibrance":    int(p.vibrance),
            "saturation":  int(p.saturation),
            "sharpness":   int(p.sharpness),
            "noise_lum":   int(p.noise_lum),
            "noise_color": int(p.noise_color),
            "vignette":    int(p.vignette),
            "rotation":    int(p.rotation * 10),
        }
        for name, value in mappings.items():
            if name in self._sliders:
                self._sliders[name].blockSignals(True)
                self._sliders[name].setValue(value)
                self._sliders[name].blockSignals(False)
