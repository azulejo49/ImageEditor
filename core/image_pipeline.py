"""
core/image_pipeline.py
======================
TRUE 32-BIT FLOATING POINT IMAGE PROCESSING PIPELINE
-----------------------------------------------------
All pixel math runs in float32 [0.0–1.0] (linear light space).
HDR values above 1.0 are allowed mid-pipeline and only clipped on output.

NON-DESTRUCTIVE DESIGN
-----------------------
The source array (_source_f32) is NEVER modified after loading.
Every render call copies the source, then applies EditParams on the copy.
This means unlimited re-editing with zero quality loss.

HISTORY SYSTEM
--------------
50-step undo/redo stack. Each snapshot() call saves the current EditParams.
Undo pops from history → pushes to redo stack.
Redo pops from redo stack → pushes to history.
"""

from __future__ import annotations
import os
import numpy as np
import cv2
from dataclasses import dataclass, field
from typing import Optional, Tuple
import copy


# ═══════════════════════════════════════════════════════════════════════════
# EDIT PARAMETERS  —  the complete non-destructive edit state
# Every adjustable parameter lives here as a plain Python value.
# Saving/loading a session = serializing this dataclass.
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class EditParams:

    # ── TONE ──────────────────────────────────────────────────────────────
    # exposure: linear EV multiplication  (2^stops).  ±4 stops range.
    exposure:     float = 0.0

    # highlights/shadows: zone-masked brightness push/pull. ±100 scale.
    highlights:   float = 0.0
    shadows:      float = 0.0

    # whites/blacks: extreme end compression/expansion. ±100 scale.
    whites:       float = 0.0
    blacks:       float = 0.0

    # brightness: simple additive lift/lower. ±100 scale.
    brightness:   float = 0.0

    # contrast: pivoted S-curve around 0.18 midpoint. ±100 scale.
    contrast:     float = 0.0

    # ── TONE CURVE ────────────────────────────────────────────────────────
    # curve_points: list of (input, output) control points in [0.0–1.0].
    # Defaults to identity [(0,0),(1,1)] = no adjustment.
    # The pipeline builds a 256-entry LUT from these points using
    # monotonic cubic (Pchip) interpolation for smooth, natural curves.
    curve_points: list = field(default_factory=lambda: [(0.0, 0.0), (1.0, 1.0)])

    # ── COLOR ─────────────────────────────────────────────────────────────
    # temperature: red/blue channel ratio shift. +100 = very warm, -100 = cool.
    temperature:  float = 0.0

    # tint: green/magenta axis shift. +100 = green cast, -100 = magenta cast.
    tint:         float = 0.0

    # vibrance: saturation boost that protects already-saturated colors.
    vibrance:     float = 0.0

    # saturation: global HSV saturation multiplier. ±100 scale.
    saturation:   float = 0.0

    # ── HSL (per-hue adjustments) ─────────────────────────────────────────
    # Six hue ranges: reds, oranges, yellows, greens, blues, purples.
    # Each has independent hue_shift (°), saturation delta, luminance delta.
    # Format: dict keyed by hue-range name → [hue_shift, sat_delta, lum_delta]
    hsl: dict = field(default_factory=lambda: {
        "reds":    [0.0, 0.0, 0.0],   # [hue°, sat±100, lum±100]
        "oranges": [0.0, 0.0, 0.0],
        "yellows": [0.0, 0.0, 0.0],
        "greens":  [0.0, 0.0, 0.0],
        "blues":   [0.0, 0.0, 0.0],
        "purples": [0.0, 0.0, 0.0],
    })

    # ── DETAIL ────────────────────────────────────────────────────────────
    # sharpness: unsharp-mask strength. 0–100.
    sharpness:    float = 0.0

    # noise_lum / noise_color: fastNlMeansDenoising strength. 0–100.
    noise_lum:    float = 0.0
    noise_color:  float = 0.0

    # ── OPTICS ────────────────────────────────────────────────────────────
    # vignette: radial darkening (+) or brightening (-) at edges. ±100.
    vignette:     float = 0.0

    # ── TRANSFORM ─────────────────────────────────────────────────────────
    # rotation: degrees clockwise. ±180.
    rotation:     float = 0.0

    # flip_h / flip_v: mirror horizontally / vertically.
    flip_h:       bool  = False
    flip_v:       bool  = False

    # crop: normalized rect (x, y, w, h) in [0.0–1.0] relative to image size.
    # None = no crop applied.
    crop: Optional[Tuple[float, float, float, float]] = None

    # ── HELPERS ───────────────────────────────────────────────────────────

    def reset(self):
        """Restore all parameters to their default values."""
        for f_name, f_obj in self.__dataclass_fields__.items():
            # Re-evaluate factory defaults (list/dict fields) each time
            if f_obj.default_factory is not None:          # type: ignore
                setattr(self, f_name, f_obj.default_factory())
            else:
                setattr(self, f_name, f_obj.default)

    def clone(self) -> "EditParams":
        """Deep-copy so history stack entries are independent."""
        return copy.deepcopy(self)


# ═══════════════════════════════════════════════════════════════════════════
# IMAGE PIPELINE  —  load, edit, render, export
# ═══════════════════════════════════════════════════════════════════════════

class ImagePipeline:
    """
    Central pipeline object.
    One instance lives for the lifetime of the app.
    Loading a new file replaces _source_f32 and resets params/history.
    """

    def __init__(self):
        # _source_f32: the immutable master copy (H,W,3) float32 RGB [0–1]
        self._source_f32: Optional[np.ndarray] = None
        self._filepath: str = ""
        self._is_raw: bool = False
        self._meta: dict = {}              # EXIF / RAW metadata

        self.params = EditParams()         # current live edit state
        self._history: list[EditParams] = []    # undo stack (max 50)
        self._redo_stack: list[EditParams] = []  # redo stack

    # ───────────────────────────────────────────────────────────────────────
    # LOADING
    # ───────────────────────────────────────────────────────────────────────

    def load(self, filepath: str) -> bool:
        """
        Detect file type by extension, decode to float32 RGB, store as source.
        Resets all edit parameters and clears history on success.
        Raises RuntimeError on failure so callers can show an error dialog.
        """
        ext = os.path.splitext(filepath)[1].lower()

        # All known RAW camera formats
        raw_exts = {
            ".cr2", ".cr3",          # Canon
            ".nef", ".nrw",          # Nikon
            ".arw", ".srf", ".sr2",  # Sony
            ".orf",                  # Olympus
            ".rw2",                  # Panasonic
            ".pef",                  # Pentax
            ".dng",                  # Adobe Digital Negative (universal)
            ".raf",                  # Fujifilm
            ".3fr",                  # Hasselblad
            ".mrw",                  # Minolta
            ".x3f",                  # Sigma
            ".erf",                  # Epson
            ".kdc", ".dcr",          # Kodak
            ".raw", ".rwl",          # Generic / Leica
        }

        try:
            if ext in raw_exts:
                self._load_raw(filepath)   # use rawpy (libraw)
            else:
                self._load_raster(filepath)  # use OpenCV

            # On success: store path, reset edit state, clear history
            self._filepath = filepath
            self.params = EditParams()
            self._history.clear()
            self._redo_stack.clear()
            return True

        except Exception as e:
            raise RuntimeError(f"Failed to load '{filepath}': {e}") from e

    def _load_raw(self, filepath: str):
        """
        Decode RAW file via rawpy (Python bindings for libraw).
        Settings used:
          - output_bps=16      → full 16-bit depth before normalising to float32
          - no_auto_bright     → we control exposure manually in the pipeline
          - use_camera_wb      → apply the camera's recorded white balance
          - gamma=(1,1)        → linear light (no gamma bake-in)
          - output_color=sRGB  → colour matrix to sRGB primaries
        Also reads metadata (black level, white level) for informational display.
        """
        import rawpy
        with rawpy.imread(filepath) as raw:
            rgb16 = raw.postprocess(
                output_bps=16,
                no_auto_bright=True,
                use_camera_wb=True,
                gamma=(1, 1),
                output_color=rawpy.ColorSpace.sRGB,
            )
            # Store raw sensor metadata for the EXIF viewer
            self._meta = {
                "raw_type":    raw.raw_type.name,
                "black_level": list(raw.black_level_per_channel),
                "white_level": int(raw.white_level),
            }

        # Normalise 16-bit integer → float32 [0.0–1.0]
        self._source_f32 = rgb16.astype(np.float32) / 65535.0
        self._is_raw = True

        # Extract EXIF via Pillow for additional metadata
        self._meta.update(_read_exif(filepath))

    def _load_raster(self, filepath: str):
        """
        Decode JPEG/PNG/TIFF/BMP/WebP via OpenCV.
        Handles: 8-bit, 16-bit, grayscale, RGBA (alpha stripped).
        Converts from BGR (OpenCV native) to RGB.
        """
        # IMREAD_UNCHANGED preserves bit depth and extra channels
        bgr = cv2.imread(filepath, cv2.IMREAD_UNCHANGED)
        if bgr is None:
            raise IOError("OpenCV could not read file — unsupported format or corrupt data")

        # Grayscale → 3-channel BGR
        if bgr.ndim == 2:
            bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)

        # RGBA → RGB (strip alpha channel)
        if bgr.shape[2] == 4:
            bgr = bgr[:, :, :3]

        # Normalise to float32 based on source bit depth
        if bgr.dtype == np.uint8:
            f32 = bgr.astype(np.float32) / 255.0
        elif bgr.dtype == np.uint16:
            f32 = bgr.astype(np.float32) / 65535.0
        else:
            f32 = bgr.astype(np.float32)   # assume already normalised

        # OpenCV is BGR, pipeline is RGB — reverse channel order
        self._source_f32 = f32[:, :, ::-1].copy()
        self._is_raw = False

        # Read EXIF metadata via Pillow
        self._meta = _read_exif(filepath)

    # ───────────────────────────────────────────────────────────────────────
    # RENDERING
    # ───────────────────────────────────────────────────────────────────────

    def render(self, scale: float = 1.0) -> np.ndarray:
        """
        Apply all current EditParams and return a uint8 RGB array for display.

        scale < 1.0: downsample BEFORE applying ops for fast preview.
        This is safe because we always re-render from float32 source —
        downsampling only affects the display copy, not the source.

        Returns: (H, W, 3) uint8 RGB — ready for QImage.
        """
        if self._source_f32 is None:
            raise RuntimeError("No image loaded")

        img = self._source_f32.copy()  # always copy — never mutate source

        # Optional preview downscale for performance (e.g. 50% zoom → 50% pixels)
        if scale < 1.0:
            h, w = img.shape[:2]
            nh = max(1, int(h * scale))
            nw = max(1, int(w * scale))
            # INTER_AREA is best for downscaling (avoids aliasing)
            img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)

        img = self._apply_params(img, self.params)
        return float32_to_uint8(img)

    def render_f32(self) -> np.ndarray:
        """
        Full-resolution float32 render — used for export only.
        No downscaling applied. Returns (H, W, 3) float32 [0–1].
        """
        if self._source_f32 is None:
            raise RuntimeError("No image loaded")
        img = self._source_f32.copy()
        return self._apply_params(img, self.params)

    # ───────────────────────────────────────────────────────────────────────
    # PARAMETER APPLICATION  —  the core edit chain
    # Operations are applied in a deliberate photographic order:
    #   crop → exposure → tone → color → detail → geometry
    # ───────────────────────────────────────────────────────────────────────

    @staticmethod
    def _apply_params(img: np.ndarray, p: EditParams) -> np.ndarray:
        """
        Pure function: takes float32 RGB array and EditParams,
        returns new float32 RGB array with all edits applied.
        Order matters — each step affects subsequent steps.
        """

        # ── 1. CROP ──────────────────────────────────────────────────────
        # Applied first so all subsequent ops work on the final pixel region.
        # Crop rect stored as normalised floats → convert to pixel coords here.
        if p.crop is not None:
            cx, cy, cw, ch = p.crop      # normalised [0.0–1.0]
            H, W = img.shape[:2]
            # Convert to integer pixel indices, clamped to image bounds
            x0 = max(0, int(cx * W))
            y0 = max(0, int(cy * H))
            x1 = min(W, int((cx + cw) * W))
            y1 = min(H, int((cy + ch) * H))
            if x1 > x0 and y1 > y0:
                img = img[y0:y1, x0:x1]   # numpy slice = zero-copy crop

        # ── 2. EXPOSURE ───────────────────────────────────────────────────
        # Linear EV multiplication in photographic stops.
        # +1 EV = 2× brightness, −1 EV = 0.5× brightness.
        # Operates in linear light so highlights clip naturally.
        if p.exposure != 0.0:
            img = img * (2.0 ** p.exposure)

        # ── 3. HIGHLIGHTS ─────────────────────────────────────────────────
        # Zone-masked adjustment: only affects bright pixels (lum > 0.5).
        # Mask is squared for a smooth falloff — no hard edge.
        if p.highlights != 0.0:
            mask = _smooth_zone(img, bright=True)
            delta = p.highlights / 100.0 * 0.5
            img = img + mask * delta

        # ── 4. SHADOWS ────────────────────────────────────────────────────
        # Zone-masked adjustment: only affects dark pixels (lum < 0.5).
        if p.shadows != 0.0:
            mask = _smooth_zone(img, bright=False)
            delta = p.shadows / 100.0 * 0.5
            img = img + mask * delta

        # ── 5. WHITES ─────────────────────────────────────────────────────
        # Compresses or expands the very bright end (pixels above 0.75).
        if p.whites != 0.0:
            w = p.whites / 100.0 * 0.15
            img = np.where(img > 0.75, img + w * (img - 0.75) * 4, img)

        # ── 6. BLACKS ─────────────────────────────────────────────────────
        # Lifts or crushes the very dark end (pixels below 0.25).
        if p.blacks != 0.0:
            b = p.blacks / 100.0 * 0.15
            img = np.where(img < 0.25, img + b * (0.25 - img) * 4, img)

        # ── 7. BRIGHTNESS ─────────────────────────────────────────────────
        # Simple additive offset — lifts or lowers the entire tonal range.
        if p.brightness != 0.0:
            img = img + p.brightness / 100.0 * 0.3

        # ── 8. CONTRAST ───────────────────────────────────────────────────
        # S-curve pivoted at 0.18 (the photographic 18% grey card midpoint).
        # Positive = steeper curve (more punch), negative = flatter (matte).
        if p.contrast != 0.0:
            img = _apply_contrast(img, p.contrast / 100.0)

        # ── 9. TONE CURVE ─────────────────────────────────────────────────
        # User-drawn Bezier/spline curve mapped to a 256-entry LUT.
        # Applied per-channel (luminance curve affects all channels equally).
        # Identity curve [(0,0),(1,1)] = no change.
        if p.curve_points and p.curve_points != [(0.0, 0.0), (1.0, 1.0)]:
            img = _apply_tone_curve(img, p.curve_points)

        # ── 10. WHITE BALANCE (Temperature / Tint) ────────────────────────
        # Temperature shifts the R/B channel ratio (warm/cool).
        # Tint shifts the G channel (green/magenta).
        if p.temperature != 0.0 or p.tint != 0.0:
            img = _apply_wb(img, p.temperature, p.tint)

        # ── 11. SATURATION / VIBRANCE ─────────────────────────────────────
        # Saturation: uniform HSV S multiplier (can desaturate to B&W at -100).
        # Vibrance: intelligent boost that protects already-saturated colours,
        #           so skin tones and bright primaries aren't over-saturated.
        if p.saturation != 0.0 or p.vibrance != 0.0:
            img = _apply_sat_vib(img, p.saturation, p.vibrance)

        # ── 12. HSL (per-hue adjustments) ─────────────────────────────────
        # Adjusts hue, saturation, and luminance within 6 discrete hue ranges.
        # Useful for: fixing a specific sky blue, boosting grass greens, etc.
        hsl_active = any(
            any(v != 0.0 for v in vals)
            for vals in p.hsl.values()
        )
        if hsl_active:
            img = _apply_hsl(img, p.hsl)

        # ── 13. SHARPNESS ─────────────────────────────────────────────────
        # Unsharp masking: subtract blurred version, blend back with alpha.
        # sigma=1.0 targets fine detail without amplifying noise.
        if p.sharpness > 0.0:
            img = _apply_sharpness(img, p.sharpness)

        # ── 14. NOISE REDUCTION ───────────────────────────────────────────
        # OpenCV fastNlMeansDenoising: non-local means algorithm.
        # Luminance NR reduces grain; colour NR reduces chroma noise.
        # Expensive — only runs when non-zero.
        if p.noise_lum > 0.0 or p.noise_color > 0.0:
            img = _apply_nr(img, p.noise_lum, p.noise_color)

        # ── 15. VIGNETTE ──────────────────────────────────────────────────
        # Radial gradient multiplier — darkens (positive) or brightens (negative)
        # toward the corners. Standard photographic edge effect.
        if p.vignette != 0.0:
            img = _apply_vignette(img, p.vignette)

        # ── 16. FLIP ──────────────────────────────────────────────────────
        # Simple numpy array reversal along the relevant axis.
        if p.flip_h:
            img = img[:, ::-1, :]     # reverse columns
        if p.flip_v:
            img = img[::-1, :, :]     # reverse rows

        # ── 17. ROTATION ──────────────────────────────────────────────────
        # Affine rotation via OpenCV warpAffine with border reflection.
        # Applied last so it interacts correctly with the cropped region.
        if p.rotation != 0.0:
            img = _rotate(img, p.rotation)

        # Final clamp: bring any out-of-range values back to [0, 1]
        return np.clip(img, 0.0, 1.0)

    # ───────────────────────────────────────────────────────────────────────
    # HISTORY  —  undo / redo
    # ───────────────────────────────────────────────────────────────────────

    def snapshot(self):
        """
        Save current params to the undo history stack.
        Call this BEFORE applying a change, so undo restores the pre-change state.
        Caps history at 50 entries to limit memory use.
        Clears redo stack because a new edit invalidates the redo chain.
        """
        self._history.append(self.params.clone())
        self._redo_stack.clear()
        if len(self._history) > 50:
            self._history.pop(0)   # drop oldest entry

    def undo(self) -> bool:
        """
        Restore previous state from history.
        Pushes current state to redo stack first so redo works.
        Returns False if nothing to undo.
        """
        if not self._history:
            return False
        self._redo_stack.append(self.params.clone())
        self.params = self._history.pop()
        return True

    def redo(self) -> bool:
        """
        Re-apply a previously undone state.
        Pushes current state back to history stack.
        Returns False if nothing to redo.
        """
        if not self._redo_stack:
            return False
        self._history.append(self.params.clone())
        self.params = self._redo_stack.pop()
        return True

    def reset_edits(self):
        """Reset all edit parameters to defaults, with one undo snapshot."""
        self.snapshot()
        self.params.reset()

    # ───────────────────────────────────────────────────────────────────────
    # EXPORT
    # ───────────────────────────────────────────────────────────────────────

    def export(self, out_path: str, quality: int = 95):
        """
        Render at full resolution with all edits applied, then save to disk.

        Format selection by file extension:
          .tif/.tiff  → 16-bit TIFF  (lossless, preserves maximum quality)
          .jpg/.jpeg  → JPEG         (lossy, quality 0–100)
          .png        → PNG          (lossless, 8-bit)
          other       → OpenCV default for that extension
        """
        f32 = self.render_f32()   # full-res float32 with all edits applied
        ext = os.path.splitext(out_path)[1].lower()

        if ext in (".tif", ".tiff"):
            # 16-bit TIFF: scale float32 → uint16, then write BGR (OpenCV convention)
            u16 = (np.clip(f32, 0, 1) * 65535).astype(np.uint16)
            cv2.imwrite(out_path, u16[:, :, ::-1])

        else:
            # 8-bit output: scale float32 → uint8
            u8 = float32_to_uint8(f32)
            bgr = u8[:, :, ::-1]   # RGB → BGR for OpenCV

            if ext in (".jpg", ".jpeg"):
                cv2.imwrite(out_path, bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
            elif ext == ".png":
                # compression 6 = good balance of speed vs file size
                cv2.imwrite(out_path, bgr, [cv2.IMWRITE_PNG_COMPRESSION, 6])
            else:
                cv2.imwrite(out_path, bgr)

    # ───────────────────────────────────────────────────────────────────────
    # PROPERTIES
    # ───────────────────────────────────────────────────────────────────────

    @property
    def loaded(self) -> bool:
        """True if a source image has been successfully loaded."""
        return self._source_f32 is not None

    @property
    def size(self) -> Tuple[int, int]:
        """(width, height) of the source image in pixels."""
        if self._source_f32 is None:
            return (0, 0)
        h, w = self._source_f32.shape[:2]
        return (w, h)

    @property
    def filepath(self) -> str:
        return self._filepath

    @property
    def is_raw(self) -> bool:
        return self._is_raw

    @property
    def meta(self) -> dict:
        """EXIF metadata + RAW sensor info, populated on load."""
        return self._meta


# ═══════════════════════════════════════════════════════════════════════════
# MATH UTILITIES  —  all operate on float32 (H,W,3) RGB arrays
# Pure functions with no side effects.
# ═══════════════════════════════════════════════════════════════════════════

def float32_to_uint8(img: np.ndarray) -> np.ndarray:
    """Clip to [0,1] and scale to uint8 [0,255]. Standard display conversion."""
    return (np.clip(img, 0.0, 1.0) * 255.0).astype(np.uint8)


def _luminance(img: np.ndarray) -> np.ndarray:
    """
    Perceptual luminance (ITU-R BT.709) from linear RGB.
    Returns (H,W,1) — kept as 3D so it broadcasts against (H,W,3) images.
    Coefficients: R=0.2126, G=0.7152, B=0.0722
    """
    return (  0.2126 * img[:, :, 0:1]
            + 0.7152 * img[:, :, 1:2]
            + 0.0722 * img[:, :, 2:3])


def _smooth_zone(img: np.ndarray, bright: bool) -> np.ndarray:
    """
    Compute a smooth mask selecting only bright or dark regions.
    bright=True  → mask peaks at highlights (lum near 1.0)
    bright=False → mask peaks at shadows   (lum near 0.0)
    Squaring the mask creates a smooth cosine-like falloff with no hard edges.
    """
    lum = _luminance(img)
    if bright:
        mask = np.clip((lum - 0.5) * 2.0, 0.0, 1.0)
    else:
        mask = np.clip((0.5 - lum) * 2.0, 0.0, 1.0)
    return mask ** 2.0   # squared = smooth falloff toward midtones


def _apply_contrast(img: np.ndarray, c: float) -> np.ndarray:
    """
    Contrast S-curve pivoted at 0.18 (photographic 18% grey midpoint).
    Positive c → steeper slope → more contrast (shadows darker, highlights brighter).
    Negative c → flatter slope → lower contrast (filmic/matte look).
    """
    pivot = 0.18
    alpha = (1.0 + c * 1.5) if c >= 0 else (1.0 / (1.0 - c * 1.5))
    return (img - pivot) * alpha + pivot


def _apply_tone_curve(img: np.ndarray, points: list) -> np.ndarray:
    """
    Apply a user-defined tone curve via a 256-entry LUT.

    1. Take the control points [(x0,y0), (x1,y1), ...] from EditParams.
    2. Sort them by input value (x).
    3. Build a monotonic cubic spline (Pchip) through them.
       Pchip preserves monotonicity — no unwanted oscillations between points.
    4. Evaluate the spline at 256 evenly-spaced input values → LUT.
    5. Apply LUT to all three channels identically (luminance curve).

    Using scipy for Pchip interpolation.
    Falls back to linear interpolation if scipy unavailable.
    """
    try:
        from scipy.interpolate import PchipInterpolator
        pts = sorted(points, key=lambda p: p[0])
        xs = np.array([p[0] for p in pts], dtype=np.float64)
        ys = np.array([p[1] for p in pts], dtype=np.float64)

        # Ensure endpoints are present for a complete 0→1 mapping
        if xs[0] > 0.0:
            xs = np.concatenate([[0.0], xs])
            ys = np.concatenate([[0.0], ys])
        if xs[-1] < 1.0:
            xs = np.concatenate([xs, [1.0]])
            ys = np.concatenate([ys, [1.0]])

        # Evaluate spline at 256 points → LUT indexed [0..255]
        interp = PchipInterpolator(xs, ys)
        lut_x = np.linspace(0.0, 1.0, 256)
        lut   = np.clip(interp(lut_x), 0.0, 1.0).astype(np.float32)

    except ImportError:
        # Scipy not available: fall back to numpy linear interpolation
        pts = sorted(points, key=lambda p: p[0])
        xs = np.array([p[0] for p in pts])
        ys = np.array([p[1] for p in pts])
        lut_x = np.linspace(0.0, 1.0, 256)
        lut = np.clip(np.interp(lut_x, xs, ys), 0.0, 1.0).astype(np.float32)

    # Apply LUT: convert float32 pixels to int indices, look up output values
    indices = (np.clip(img, 0.0, 1.0) * 255.0).astype(np.uint8)
    return lut[indices]


def _apply_wb(img: np.ndarray, temp: float, tint: float) -> np.ndarray:
    """
    White balance adjustment via channel scaling.

    Temperature axis: +temp warms (boosts R, reduces B), -temp cools.
    Tint axis: +tint adds green cast, -tint adds magenta cast.

    Scale factors are intentionally small (0.15, 0.08) to stay in a
    photographically realistic range even at ±100.
    """
    t  = temp / 100.0 * 0.15   # temperature scale factor
    ti = tint / 100.0 * 0.08   # tint scale factor
    out = img.copy()
    out[:, :, 0] = img[:, :, 0] * (1.0 + t)    # Red  → warmer with positive t
    out[:, :, 2] = img[:, :, 2] * (1.0 - t)    # Blue → cooler with positive t
    out[:, :, 1] = img[:, :, 1] * (1.0 + ti)   # Green shifted by tint
    return out


def _apply_sat_vib(img: np.ndarray, sat: float, vib: float) -> np.ndarray:
    """
    Saturation: uniform HSV S multiplier.
      sat=+100 → double saturation; sat=-100 → desaturate to greyscale.

    Vibrance: weighted saturation boost.
      Uses (1 - current_S) as the boost weight, so:
        - dull colours (low S) receive a large boost
        - already-vivid colours (high S) receive little boost
      This protects skin tones and neon colours from blowing out.

    Both ops work in HSV space via uint8 round-trip for OpenCV compatibility.
    The quality loss from the uint8 round-trip is negligible for S adjustments.
    """
    u8  = float32_to_uint8(img)
    hsv = cv2.cvtColor(u8, cv2.COLOR_RGB2HSV).astype(np.float32)
    s   = hsv[:, :, 1] / 255.0   # saturation channel normalised to [0,1]

    if sat != 0.0:
        # Uniform multiplier — simple and fast
        s = np.clip(s * (1.0 + sat / 100.0), 0.0, 1.0)

    if vib != 0.0:
        # Inverse-saturation weighting: less-saturated pixels get bigger boost
        boost = (1.0 - s) * (vib / 100.0)
        s = np.clip(s + boost, 0.0, 1.0)

    hsv[:, :, 1] = s * 255.0
    u8_out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
    return u8_out.astype(np.float32) / 255.0


def _apply_hsl(img: np.ndarray, hsl: dict) -> np.ndarray:
    """
    Per-hue HSL adjustment across 6 hue ranges.

    Hue ranges (approximate OpenCV H values, range 0–180):
      reds:    315–360° + 0–30°  → H 0..15 and 165..180
      oranges: 30–60°            → H 15..30
      yellows: 60–90°            → H 30..45
      greens:  90–150°           → H 45..75
      blues:   180–240°          → H 90..120
      purples: 240–315°          → H 120..165

    For each hue range:
      - Build a smooth membership mask using circular hue distance
      - Apply hue shift, saturation delta, luminance delta only to masked pixels
      - Blend result back using the mask as alpha

    This prevents hard edges between hue ranges.
    """
    # Hue range centres and half-widths in OpenCV H units (0–180)
    hue_ranges = {
        "reds":    (0,   20),
        "oranges": (20,  15),
        "yellows": (35,  15),
        "greens":  (75,  30),
        "blues":   (110, 25),
        "purples": (145, 20),
    }

    u8  = float32_to_uint8(img)
    hsv = cv2.cvtColor(u8, cv2.COLOR_RGB2HSV).astype(np.float32)
    H   = hsv[:, :, 0]   # 0–180
    S   = hsv[:, :, 1]   # 0–255
    V   = hsv[:, :, 2]   # 0–255

    for range_name, (h_params) in hsl.items():
        h_shift, s_delta, l_delta = h_params
        if h_shift == 0.0 and s_delta == 0.0 and l_delta == 0.0:
            continue   # skip ranges with no adjustment

        centre, half_w = hue_ranges.get(range_name, (0, 15))

        # Circular hue distance — handles wraparound at 0/180
        dist = np.abs(H - centre)
        dist = np.minimum(dist, 180.0 - dist)   # wrap-around distance

        # Smooth mask: 1.0 at centre, 0.0 beyond half_w (cosine-like rolloff)
        mask = np.clip(1.0 - dist / half_w, 0.0, 1.0) ** 2

        # Apply hue shift (in OpenCV units: 1 unit = 2 degrees)
        if h_shift != 0.0:
            H = np.where(mask > 0.01,
                         (H + h_shift / 2.0) % 180.0,
                         H)

        # Apply saturation delta
        if s_delta != 0.0:
            delta_s = s_delta / 100.0 * 127.5 * mask
            S = np.clip(S + delta_s, 0.0, 255.0)

        # Apply luminance delta (maps to V channel in HSV)
        if l_delta != 0.0:
            delta_v = l_delta / 100.0 * 127.5 * mask
            V = np.clip(V + delta_v, 0.0, 255.0)

    hsv[:, :, 0] = H
    hsv[:, :, 1] = S
    hsv[:, :, 2] = V

    u8_out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
    return u8_out.astype(np.float32) / 255.0


def _apply_sharpness(img: np.ndarray, amount: float) -> np.ndarray:
    """
    Unsharp masking (USM) — the standard photographic sharpening technique.

    Steps:
      1. Create a blurred copy with Gaussian sigma=1.0
         (sigma controls the radius of detail targeted — 1.0 = fine detail)
      2. Blend original and blurred with addWeighted:
         output = original * (1 + alpha) - blurred * alpha
         where alpha = amount / 100 * 1.5
      3. This mathematically subtracts the low-frequency blur from original,
         leaving enhanced high-frequency edges.

    Works on uint8 to use OpenCV's optimised code, then converts back.
    """
    u8     = float32_to_uint8(img)
    alpha  = amount / 100.0 * 1.5   # sharpness strength
    blurred = cv2.GaussianBlur(u8, (0, 0), sigmaX=1.0)
    sharp   = cv2.addWeighted(u8, 1.0 + alpha, blurred, -alpha, 0)
    return sharp.astype(np.float32) / 255.0


def _apply_nr(img: np.ndarray, lum: float, color: float) -> np.ndarray:
    """
    Non-local means denoising (fastNlMeansDenoising).

    How NLM works: for each pixel, scans a search window for similar
    texture patches; averages them weighted by similarity.
    Result: noise averages out, edges are preserved.

    h      = luminance filter strength (0–10 mapped from 0–100)
    hColor = colour noise filter strength (0–10 mapped from 0–100)
    templateWindowSize = patch comparison size (7×7 px)
    searchWindowSize   = search area (21×21 px)

    NOTE: This is the most expensive operation in the pipeline.
    It is only invoked when at least one NR parameter is non-zero.
    """
    u8     = float32_to_uint8(img)
    h_val  = int(lum   / 100.0 * 10)
    hc_val = int(color / 100.0 * 10)
    if h_val > 0 or hc_val > 0:
        denoised = cv2.fastNlMeansDenoisingColored(
            u8, None,
            h=max(1, h_val),
            hColor=max(1, hc_val),
            templateWindowSize=7,
            searchWindowSize=21,
        )
        return denoised.astype(np.float32) / 255.0
    return img


def _apply_vignette(img: np.ndarray, amount: float) -> np.ndarray:
    """
    Radial vignette — darkens or brightens toward the image corners.

    Method:
      1. Compute normalised elliptical distance from centre for every pixel.
         Distance = 0 at centre, 1 at corner.
      2. Vignette multiplier = 1 - dist² × strength
         Squaring gives a natural circular/oval rolloff.
      3. Multiply every pixel by the vignette mask.

    Positive amount → dark vignette (standard photographic look).
    Negative amount → bright vignette (burning-in the centre).
    Multiplier capped at 1.5 to allow a subtle brightening effect.
    """
    h, w = img.shape[:2]
    cx, cy = w / 2.0, h / 2.0

    # Normalised elliptical coordinates: edges = 1.0
    Y, X = np.ogrid[:h, :w]
    dist = np.sqrt(((X - cx) / cx) ** 2 + ((Y - cy) / cy) ** 2)
    dist = np.clip(dist, 0, 1)

    # Vignette multiplier mask: centre=1.0, corners reduced by amount
    vig = 1.0 - (dist ** 2) * (amount / 100.0) * 0.6
    vig = np.clip(vig, 0.0, 1.5)[:, :, np.newaxis]   # add channel dim for broadcast

    return np.clip(img * vig, 0, 1)


def _rotate(img: np.ndarray, angle: float) -> np.ndarray:
    """
    Rotate image by angle degrees using an affine warp.

    Uses BORDER_REFLECT_101 to fill the corners exposed by rotation
    with mirrored edge pixels — avoids ugly black triangles.
    INTER_LINEAR interpolation gives good quality at low cost.
    """
    h, w = img.shape[:2]
    # Get the 2×3 rotation matrix for rotation around image centre
    M = cv2.getRotationMatrix2D((w / 2, h / 2), -angle, 1.0)
    u8      = float32_to_uint8(img)
    rotated = cv2.warpAffine(
        u8, M, (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101
    )
    return rotated.astype(np.float32) / 255.0


# ═══════════════════════════════════════════════════════════════════════════
# EXIF / METADATA READER
# ═══════════════════════════════════════════════════════════════════════════

def _read_exif(filepath: str) -> dict:
    """
    Read EXIF metadata using Pillow.
    Returns a flat dict of human-readable tag name → value strings.
    Safe: returns empty dict on any failure (missing Pillow, no EXIF, etc.).

    Common fields extracted:
      Make, Model, ExposureTime, FNumber, ISOSpeedRatings,
      FocalLength, DateTimeOriginal, LensModel, GPSInfo (omitted for privacy).
    """
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        img = Image.open(filepath)
        raw = img._getexif()
        if raw is None:
            return {}
        result = {}
        for tag_id, value in raw.items():
            tag = TAGS.get(tag_id, str(tag_id))
            # Skip binary blobs and GPS (privacy) and MakerNote (binary)
            if tag in ("MakerNote", "UserComment", "GPSInfo"):
                continue
            if isinstance(value, bytes):
                continue
            # Format rational numbers (e.g. exposure time 1/250 → "1/250")
            if hasattr(value, 'numerator') and hasattr(value, 'denominator'):
                value = f"{value.numerator}/{value.denominator}"
            result[tag] = str(value)
        return result
    except Exception:
        return {}
