"""
ui/edit_panel.py
================
NON-DESTRUCTIVE EDIT CONTROLS PANEL
-------------------------------------
Right-side scrollable panel containing all image adjustment controls.

SECTIONS (top to bottom)
-------------------------
  History    — Undo / Redo / Reset buttons
  Tone       — Exposure, Highlights, Shadows, Whites, Blacks, Brightness, Contrast
  Tone Curve — Embedded CurveWidget (interactive spline editor)
  Color      — Temperature, Tint, Vibrance, Saturation
  HSL        — Per-hue Hue/Saturation/Luminance for 6 colour ranges
  Detail     — Sharpness, Noise Luminance, Noise Colour
  Optics     — Vignette
  Transform  — Rotation, Flip H/V

SIGNAL ARCHITECTURE
-------------------
All controls emit param_changed(str, object) — the main window
connects this to the pipeline and render worker.
Special signals for undo/redo/reset are separate to avoid coupling.

SLIDER ENCODING
---------------
Sliders work with integers internally (Qt limitation).
Float parameters are encoded:
  exposure:  int / 100 = EV stops (range ±400 int → ±4.0 EV)
  rotation:  int / 10  = degrees  (range ±1800 int → ±180.0°)
All other params: int value = float value (range ±100 or 0–100)

LOAD_PARAMS
-----------
Called after undo/redo to synchronise all sliders back to
the restored EditParams without triggering param_changed signals.
"""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSlider, QGroupBox, QPushButton, QScrollArea,
    QSizePolicy, QFrame, QTabWidget, QComboBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from core.image_pipeline import EditParams
from ui.curve_widget import CurveWidget


class EditPanel(QScrollArea):
    """
    Scrollable panel hosting all non-destructive edit controls.
    """

    # Emitted whenever any parameter changes: (param_name, new_value)
    param_changed  = pyqtSignal(str, object)
    reset_clicked  = pyqtSignal()
    undo_clicked   = pyqtSignal()
    redo_clicked   = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)

        # Container widget holds all sections in a vertical layout
        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(4)
        self.setWidget(container)

        # Registries for programmatic access (used by load_params)
        self._sliders: dict[str, QSlider] = {}
        self._labels:  dict[str, QLabel]  = {}

        # Build all sections in order
        self._build_history_buttons()

        self._build_section("Tone", [
            # (param_name, display_label, min_int, max_int, default_int)
            ("exposure",   "Exposure",    -400, 400, 0),   # /100 = EV
            ("highlights", "Highlights",  -100, 100, 0),
            ("shadows",    "Shadows",     -100, 100, 0),
            ("whites",     "Whites",      -100, 100, 0),
            ("blacks",     "Blacks",      -100, 100, 0),
            ("brightness", "Brightness",  -100, 100, 0),
            ("contrast",   "Contrast",    -100, 100, 0),
        ])

        self._build_curve_section()

        self._build_section("Color", [
            ("temperature", "Temperature", -100, 100, 0),
            ("tint",        "Tint",        -100, 100, 0),
            ("vibrance",    "Vibrance",    -100, 100, 0),
            ("saturation",  "Saturation",  -100, 100, 0),
        ])

        self._build_hsl_section()

        self._build_section("Detail", [
            ("sharpness",   "Sharpness",    0, 100, 0),
            ("noise_lum",   "Noise (Lum)",  0, 100, 0),
            ("noise_color", "Noise (Col)",  0, 100, 0),
        ])

        self._build_section("Optics", [
            ("vignette", "Vignette", -100, 100, 0),
        ])

        self._build_section("Transform", [
            ("rotation", "Rotation", -1800, 1800, 0),   # /10 = degrees
        ])

        self._build_flip_row()
        self._layout.addStretch()   # push everything to the top

    # ──────────────────────────────────────────────────────────────────────
    # SECTION BUILDERS
    # ──────────────────────────────────────────────────────────────────────

    def _build_history_buttons(self):
        """
        Undo / Redo / Reset buttons at the very top of the panel.
        These are the most frequently used controls so they live at the top.
        """
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 4)
        h.setSpacing(4)

        undo_btn  = QPushButton("⟵ Undo")
        undo_btn.setObjectName("histBtn")
        undo_btn.clicked.connect(self.undo_clicked)
        undo_btn.setToolTip("Undo last edit (Ctrl+Z)")

        redo_btn  = QPushButton("Redo ⟶")
        redo_btn.setObjectName("histBtn")
        redo_btn.clicked.connect(self.redo_clicked)
        redo_btn.setToolTip("Redo (Ctrl+Y)")

        reset_btn = QPushButton("Reset All")
        reset_btn.setObjectName("resetBtn")
        reset_btn.clicked.connect(self.reset_clicked)
        reset_btn.setToolTip("Reset all parameters to defaults")

        h.addWidget(undo_btn)
        h.addWidget(redo_btn)
        h.addWidget(reset_btn)
        self._layout.addWidget(row)

    def _build_section(self, title: str, params: list):
        """
        Generic section builder: creates a QGroupBox with a slider row
        for each param in the list.
        params: list of (name, label, min_int, max_int, default_int)
        """
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
        """
        Build a horizontal row: [Label] [━━━●━━━] [value]
        The slider emits param_changed with the decoded float value.
        Encoding/decoding handled in the on_change closure.
        """
        widget = QWidget()
        h = QHBoxLayout(widget)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        # Fixed-width label so all sliders align regardless of label length
        lbl = QLabel(label)
        lbl.setFixedWidth(86)
        lbl.setObjectName("paramLabel")
        lbl.setFont(QFont("monospace", 8))

        # Slider — integer range, horizontal
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_v, max_v)
        slider.setValue(default)
        slider.setObjectName("paramSlider")
        slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Right-aligned numeric value display
        val_lbl = QLabel("0")
        val_lbl.setFixedWidth(38)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_lbl.setObjectName("paramValue")
        val_lbl.setFont(QFont("monospace", 8))

        def on_change(v, n=name, vl=val_lbl):
            """
            Decode integer slider value to float param value and emit signal.
            Special handling for exposure (÷100) and rotation (÷10).
            """
            if n == "exposure":
                # Slider: ±400 → EV: ±4.00
                real = v / 100.0
                vl.setText(f"{real:+.2f}")
                self.param_changed.emit(n, real)
            elif n == "rotation":
                # Slider: ±1800 → degrees: ±180.0
                real = v / 10.0
                vl.setText(f"{real:.1f}°")
                self.param_changed.emit(n, real)
            else:
                # All other params: integer value = float value
                vl.setText(str(v))
                self.param_changed.emit(n, float(v))

        slider.valueChanged.connect(on_change)

        h.addWidget(lbl)
        h.addWidget(slider)
        h.addWidget(val_lbl)

        # Register for programmatic access
        self._sliders[name] = slider
        self._labels[name]  = val_lbl
        return widget

    def _build_curve_section(self):
        """
        Tone Curve section containing the embedded CurveWidget.
        Also provides a Reset Curve button to restore identity.
        The CurveWidget emits its own curve_changed signal which is
        forwarded as param_changed('curve_points', [...]).
        """
        box = QGroupBox("Tone Curve")
        box.setObjectName("editSection")
        vbox = QVBoxLayout(box)
        vbox.setContentsMargins(6, 4, 6, 4)
        vbox.setSpacing(4)

        # Instruction hint
        hint = QLabel("Click to add point · Right-click to remove")
        hint.setObjectName("hintLabel")
        hint.setFont(QFont("monospace", 7))
        vbox.addWidget(hint)

        # The interactive curve editor widget
        self._curve_widget = CurveWidget()
        self._curve_widget.setFixedHeight(160)

        # Forward curve changes as a param_changed signal
        self._curve_widget.curve_changed.connect(
            lambda pts: self.param_changed.emit("curve_points", pts)
        )
        vbox.addWidget(self._curve_widget)

        # Reset curve button
        reset_curve_btn = QPushButton("Reset Curve")
        reset_curve_btn.setObjectName("histBtn")
        reset_curve_btn.clicked.connect(self._curve_widget.reset)
        vbox.addWidget(reset_curve_btn)

        self._layout.addWidget(box)

    def _build_hsl_section(self):
        """
        HSL (Hue/Saturation/Luminance) per-hue adjustment section.

        Six colour ranges (reds, oranges, yellows, greens, blues, purples),
        each with three sliders: Hue shift, Saturation delta, Luminance delta.

        Implementation: a QTabWidget with one tab per colour range.
        Each tab contains three slider rows.

        param_changed is emitted as ('hsl', (range_name, [h,s,l]))
        The main window unpacks this and updates pipeline.params.hsl[range_name].
        """
        box = QGroupBox("HSL — Per-Colour Adjustments")
        box.setObjectName("editSection")
        vbox = QVBoxLayout(box)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(2)

        # Colour range tabs
        tabs = QTabWidget()
        tabs.setObjectName("hslTabs")
        tabs.setDocumentMode(True)

        # Registry: hsl_sliders[range_name][component] = QSlider
        self._hsl_sliders: dict[str, dict[str, QSlider]] = {}

        # Define 6 hue ranges with identifying colours for tab styling
        ranges = [
            ("reds",    "Reds"),
            ("oranges", "Oranges"),
            ("yellows", "Yellows"),
            ("greens",  "Greens"),
            ("blues",   "Blues"),
            ("purples", "Purples"),
        ]

        for range_id, range_label in ranges:
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            tab_layout.setContentsMargins(4, 4, 4, 4)
            tab_layout.setSpacing(2)

            self._hsl_sliders[range_id] = {}

            # Three sliders per range: Hue shift, Saturation, Luminance
            hsl_params = [
                ("hue", "Hue",  -180, 180, 0),
                ("sat", "Sat",  -100, 100, 0),
                ("lum", "Lum",  -100, 100, 0),
            ]

            for comp, comp_label, min_v, max_v, default in hsl_params:
                row_widget = QWidget()
                row_h = QHBoxLayout(row_widget)
                row_h.setContentsMargins(0, 0, 0, 0)
                row_h.setSpacing(4)

                lbl = QLabel(comp_label)
                lbl.setFixedWidth(28)
                lbl.setObjectName("paramLabel")
                lbl.setFont(QFont("monospace", 8))

                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(min_v, max_v)
                slider.setValue(default)
                slider.setObjectName("paramSlider")
                slider.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Fixed)

                val_lbl = QLabel("0")
                val_lbl.setFixedWidth(34)
                val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight |
                                     Qt.AlignmentFlag.AlignVCenter)
                val_lbl.setObjectName("paramValue")
                val_lbl.setFont(QFont("monospace", 8))

                def on_hsl_change(v, rid=range_id, comp=comp, vl=val_lbl):
                    """
                    Build the full [h, s, l] list for this range by reading
                    current slider values, update the changed component,
                    then emit param_changed('hsl', (range_id, [h, s, l])).
                    """
                    vl.setText(str(v))
                    # Read all three sliders for this range
                    h_val = self._hsl_sliders[rid]["hue"].value()
                    s_val = self._hsl_sliders[rid]["sat"].value()
                    l_val = self._hsl_sliders[rid]["lum"].value()
                    self.param_changed.emit(
                        "hsl", (rid, [float(h_val), float(s_val), float(l_val)])
                    )

                slider.valueChanged.connect(on_hsl_change)

                row_h.addWidget(lbl)
                row_h.addWidget(slider)
                row_h.addWidget(val_lbl)
                tab_layout.addWidget(row_widget)

                # Register for load_params access
                self._hsl_sliders[range_id][comp] = slider

            tab_layout.addStretch()
            tabs.addTab(tab, range_label[:3])   # short tab label: "Red", "Ora" etc.

        vbox.addWidget(tabs)
        self._layout.addWidget(box)

    def _build_flip_row(self):
        """
        Flip H / Flip V toggle buttons.
        Toggle buttons stay pressed (checked) when active.
        """
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
        btn_h.setToolTip("Flip image horizontally (mirror left-right)")
        btn_v.setToolTip("Flip image vertically (mirror top-bottom)")

        btn_h.toggled.connect(lambda v: self.param_changed.emit("flip_h", v))
        btn_v.toggled.connect(lambda v: self.param_changed.emit("flip_v", v))

        h.addWidget(btn_h)
        h.addWidget(btn_v)

        # Store reference so load_params can update them
        self._flip_h_btn = btn_h
        self._flip_v_btn = btn_v

        self._layout.addWidget(box)

    # ──────────────────────────────────────────────────────────────────────
    # SYNC FROM PARAMS  —  called after undo/redo to update UI
    # ──────────────────────────────────────────────────────────────────────

    def load_params(self, p: EditParams):
        """
        Populate ALL controls from an EditParams object.
        Uses blockSignals(True) to prevent each slider setValue from
        triggering param_changed and causing an infinite loop.
        Called after undo/redo to bring UI in sync with restored state.
        """

        # ── Standard sliders ──────────────────────────────────────────────
        mappings = {
            "exposure":    int(p.exposure * 100),    # EV → slider int
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
            "rotation":    int(p.rotation * 10),     # degrees → slider int
        }

        for name, value in mappings.items():
            if name in self._sliders:
                self._sliders[name].blockSignals(True)
                self._sliders[name].setValue(value)
                self._sliders[name].blockSignals(False)

        # ── Tone curve ────────────────────────────────────────────────────
        # Update the CurveWidget directly with the stored control points
        self._curve_widget.blockSignals(True)
        self._curve_widget.set_points(p.curve_points)
        self._curve_widget.blockSignals(False)

        # ── HSL sliders ───────────────────────────────────────────────────
        for range_id, vals in p.hsl.items():
            if range_id in self._hsl_sliders:
                h_v, s_v, l_v = vals
                for comp, val in [("hue", h_v), ("sat", s_v), ("lum", l_v)]:
                    sl = self._hsl_sliders[range_id].get(comp)
                    if sl:
                        sl.blockSignals(True)
                        sl.setValue(int(val))
                        sl.blockSignals(False)

        # ── Flip buttons ──────────────────────────────────────────────────
        self._flip_h_btn.blockSignals(True)
        self._flip_v_btn.blockSignals(True)
        self._flip_h_btn.setChecked(p.flip_h)
        self._flip_v_btn.setChecked(p.flip_v)
        self._flip_h_btn.blockSignals(False)
        self._flip_v_btn.blockSignals(False)

    def update_curve_histogram(self, hist_lum):
        """
        Pass histogram data to the curve widget for background display.
        Called by main window after each render completes.
        """
        self._curve_widget.set_histogram(hist_lum)
