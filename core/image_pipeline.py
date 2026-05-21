"""
core/image_pipeline.py
True 32-bit floating point image processing pipeline.
All internal operations in float32 [0.0 – 1.0] (or HDR beyond 1.0).
Non-destructive: edits are stored as parameters, applied on-demand.
"""

from __future__ import annotations
import os
import numpy as np
import cv2
from dataclasses import dataclass, field
from typing import Optional
import copy


# ── Edit Parameters (non-destructive) ──────────────────────────────────────

@dataclass
class EditParams:
    # Tone
    exposure:     float = 0.0    # EV stops, ±4
    highlights:   float = 0.0    # ±100
    shadows:      float = 0.0    # ±100
    whites:       float = 0.0    # ±100
    blacks:       float = 0.0    # ±100
    brightness:   float = 0.0    # ±100
    contrast:     float = 0.0    # ±100

    # Color
    temperature:  float = 0.0    # ±100  (warm/cool shift)
    tint:         float = 0.0    # ±100  (green/magenta shift)
    vibrance:     float = 0.0    # ±100
    saturation:   float = 0.0    # ±100

    # Detail
    sharpness:    float = 0.0    # 0–100
    noise_lum:    float = 0.0    # 0–100 luminance NR
    noise_color:  float = 0.0    # 0–100 color NR

    # Optics
    vignette:     float = 0.0    # ±100

    # Transform
    rotation:     float = 0.0    # degrees
    flip_h:       bool  = False
    flip_v:       bool  = False

    def reset(self):
        for f in self.__dataclass_fields__:
            setattr(self, f, self.__dataclass_fields__[f].default)

    def clone(self) -> "EditParams":
        return copy.deepcopy(self)


# ── Image Pipeline ──────────────────────────────────────────────────────────

class ImagePipeline:
    """
    Holds the source float32 image and applies EditParams on demand.
    Source is never mutated — all edits produce a new array.
    """

    def __init__(self):
        self._source_f32: Optional[np.ndarray] = None   # (H,W,3) float32 linear
        self._filepath: str = ""
        self._is_raw: bool = False
        self._meta: dict = {}

        self.params = EditParams()
        self._history: list[EditParams] = []
        self._redo_stack: list[EditParams] = []

    # ── Loading ────────────────────────────────────────────────────────────

    def load(self, filepath: str) -> bool:
        ext = os.path.splitext(filepath)[1].lower()
        raw_exts = {
            ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2",
            ".orf", ".rw2", ".pef", ".dng", ".raf", ".3fr", ".mrw",
            ".x3f", ".erf", ".kdc", ".dcr", ".raw", ".rwl",
        }
        try:
            if ext in raw_exts:
                self._load_raw(filepath)
            else:
                self._load_raster(filepath)
            self._filepath = filepath
            self.params = EditParams()
            self._history.clear()
            self._redo_stack.clear()
            return True
        except Exception as e:
            raise RuntimeError(f"Failed to load '{filepath}': {e}") from e

    def _load_raw(self, filepath: str):
        import rawpy
        with rawpy.imread(filepath) as raw:
            # postprocess to 16-bit linear RGB, no auto-correction
            rgb16 = raw.postprocess(
                output_bps=16,
                no_auto_bright=True,
                use_camera_wb=True,
                gamma=(1, 1),          # linear
                output_color=rawpy.ColorSpace.sRGB,
            )
            self._meta = {
                "raw_type": raw.raw_type.name,
                "black_level": raw.black_level_per_channel,
                "white_level": raw.white_level,
            }
        self._source_f32 = (rgb16.astype(np.float32) / 65535.0)
        self._is_raw = True

    def _load_raster(self, filepath: str):
        bgr = cv2.imread(filepath, cv2.IMREAD_UNCHANGED)
        if bgr is None:
            raise IOError("cv2 could not read file")
        if bgr.ndim == 2:                          # grayscale
            bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
        if bgr.shape[2] == 4:                      # strip alpha for now
            bgr = bgr[:, :, :3]
        if bgr.dtype == np.uint8:
            f32 = bgr.astype(np.float32) / 255.0
        elif bgr.dtype == np.uint16:
            f32 = bgr.astype(np.float32) / 65535.0
        else:
            f32 = bgr.astype(np.float32)
        # BGR → RGB
        self._source_f32 = f32[:, :, ::-1].copy()
        self._is_raw = False

    # ── Render ─────────────────────────────────────────────────────────────

    def render(self, scale: float = 1.0) -> np.ndarray:
        """
        Apply all current EditParams and return a uint8 sRGB (H,W,3) array.
        scale < 1 for preview down-sampling (faster).
        """
        if self._source_f32 is None:
            raise RuntimeError("No image loaded")

        img = self._source_f32.copy()

        # Optional preview down-scale
        if scale < 1.0:
            h, w = img.shape[:2]
            nh, nw = max(1, int(h * scale)), max(1, int(w * scale))
            img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)

        img = self._apply_params(img, self.params)
        return float32_to_uint8(img)

    def render_f32(self) -> np.ndarray:
        """Return full-res float32 after all edits (for export)."""
        if self._source_f32 is None:
            raise RuntimeError("No image loaded")
        img = self._source_f32.copy()
        return self._apply_params(img, self.params)

    # ── Parameter application ──────────────────────────────────────────────

    @staticmethod
    def _apply_params(img: np.ndarray, p: EditParams) -> np.ndarray:
        """Pure function: float32 RGB in → float32 RGB out."""

        # --- Exposure (linear multiply, EV stops) ---
        if p.exposure != 0.0:
            img = img * (2.0 ** p.exposure)

        # --- Highlights / Shadows (zone-based) ---
        if p.highlights != 0.0:
            mask = _smooth_zone(img, bright=True)
            delta = p.highlights / 100.0 * 0.5
            img = img + mask * delta

        if p.shadows != 0.0:
            mask = _smooth_zone(img, bright=False)
            delta = p.shadows / 100.0 * 0.5
            img = img + mask * delta

        # --- Whites / Blacks ---
        if p.whites != 0.0:
            w = p.whites / 100.0 * 0.15
            img = np.where(img > 0.75, img + w * (img - 0.75) * 4, img)

        if p.blacks != 0.0:
            b = p.blacks / 100.0 * 0.15
            img = np.where(img < 0.25, img + b * (0.25 - img) * 4, img)

        # --- Brightness ---
        if p.brightness != 0.0:
            img = img + p.brightness / 100.0 * 0.3

        # --- Contrast (S-curve around midpoint) ---
        if p.contrast != 0.0:
            c = p.contrast / 100.0
            img = _apply_contrast(img, c)

        # --- Temperature / Tint (white-balance shift in linear RGB) ---
        if p.temperature != 0.0 or p.tint != 0.0:
            img = _apply_wb(img, p.temperature, p.tint)

        # --- Vibrance / Saturation ---
        if p.saturation != 0.0 or p.vibrance != 0.0:
            img = _apply_sat_vib(img, p.saturation, p.vibrance)

        # --- Sharpness ---
        if p.sharpness > 0.0:
            img = _apply_sharpness(img, p.sharpness)

        # --- Noise reduction ---
        if p.noise_lum > 0.0 or p.noise_color > 0.0:
            img = _apply_nr(img, p.noise_lum, p.noise_color)

        # --- Vignette ---
        if p.vignette != 0.0:
            img = _apply_vignette(img, p.vignette)

        # --- Rotation / Flip ---
        if p.flip_h:
            img = img[:, ::-1, :]
        if p.flip_v:
            img = img[::-1, :, :]
        if p.rotation != 0.0:
            img = _rotate(img, p.rotation)

        return np.clip(img, 0.0, 1.0)

    # ── History ────────────────────────────────────────────────────────────

    def snapshot(self):
        self._history.append(self.params.clone())
        self._redo_stack.clear()
        if len(self._history) > 50:
            self._history.pop(0)

    def undo(self) -> bool:
        if not self._history:
            return False
        self._redo_stack.append(self.params.clone())
        self.params = self._history.pop()
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._history.append(self.params.clone())
        self.params = self._redo_stack.pop()
        return True

    def reset_edits(self):
        self.snapshot()
        self.params.reset()

    # ── Export ─────────────────────────────────────────────────────────────

    def export(self, out_path: str, quality: int = 95):
        f32 = self.render_f32()
        ext = os.path.splitext(out_path)[1].lower()
        if ext in (".tif", ".tiff"):
            # Save 16-bit TIFF
            u16 = (np.clip(f32, 0, 1) * 65535).astype(np.uint16)
            bgr = u16[:, :, ::-1]
            cv2.imwrite(out_path, bgr)
        else:
            u8 = float32_to_uint8(f32)
            bgr = u8[:, :, ::-1]
            if ext == ".jpg" or ext == ".jpeg":
                cv2.imwrite(out_path, bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
            elif ext == ".png":
                cv2.imwrite(out_path, bgr, [cv2.IMWRITE_PNG_COMPRESSION, 6])
            else:
                cv2.imwrite(out_path, bgr)

    # ── Info ───────────────────────────────────────────────────────────────

    @property
    def loaded(self) -> bool:
        return self._source_f32 is not None

    @property
    def size(self):
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
        return self._meta


# ── Utility math ───────────────────────────────────────────────────────────

def float32_to_uint8(img: np.ndarray) -> np.ndarray:
    return (np.clip(img, 0.0, 1.0) * 255.0).astype(np.uint8)


def _luminance(img: np.ndarray) -> np.ndarray:
    """(H,W,1) luminance from linear RGB."""
    lum = 0.2126 * img[:, :, 0:1] + 0.7152 * img[:, :, 1:2] + 0.0722 * img[:, :, 2:3]
    return lum


def _smooth_zone(img: np.ndarray, bright: bool) -> np.ndarray:
    lum = _luminance(img)
    if bright:
        mask = np.clip((lum - 0.5) * 2.0, 0.0, 1.0)
    else:
        mask = np.clip((0.5 - lum) * 2.0, 0.0, 1.0)
    return mask ** 2.0


def _apply_contrast(img: np.ndarray, c: float) -> np.ndarray:
    # Pivoted S-curve: contrast around 0.18 (photographic midpoint)
    pivot = 0.18
    if c >= 0:
        alpha = 1.0 + c * 1.5
    else:
        alpha = 1.0 / (1.0 - c * 1.5)
    return (img - pivot) * alpha + pivot


def _apply_wb(img: np.ndarray, temp: float, tint: float) -> np.ndarray:
    # Temperature: shift red/blue channels
    t_scale = temp / 100.0 * 0.15
    tint_scale = tint / 100.0 * 0.08
    out = img.copy()
    out[:, :, 0] = img[:, :, 0] * (1.0 + t_scale)          # R warmer
    out[:, :, 2] = img[:, :, 2] * (1.0 - t_scale)          # B cooler
    out[:, :, 1] = img[:, :, 1] * (1.0 + tint_scale)       # G tint
    return out


def _apply_sat_vib(img: np.ndarray, sat: float, vib: float) -> np.ndarray:
    # Convert to HSV, adjust S
    u8 = float32_to_uint8(img)
    hsv = cv2.cvtColor(u8, cv2.COLOR_RGB2HSV).astype(np.float32)
    s = hsv[:, :, 1] / 255.0

    if sat != 0.0:
        s = np.clip(s * (1.0 + sat / 100.0), 0.0, 1.0)

    if vib != 0.0:
        # Vibrance: boost less-saturated pixels more
        v_scale = vib / 100.0
        boost = (1.0 - s) * v_scale
        s = np.clip(s + boost, 0.0, 1.0)

    hsv[:, :, 1] = s * 255.0
    u8_out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
    return u8_out.astype(np.float32) / 255.0


def _apply_sharpness(img: np.ndarray, amount: float) -> np.ndarray:
    u8 = float32_to_uint8(img)
    blur = cv2.GaussianBlur(u8, (0, 0), sigmaX=1.0)
    sharp = cv2.addWeighted(u8, 1.0 + amount / 100.0 * 1.5, blur, -amount / 100.0 * 1.5, 0)
    return sharp.astype(np.float32) / 255.0


def _apply_nr(img: np.ndarray, lum: float, color: float) -> np.ndarray:
    u8 = float32_to_uint8(img)
    h_val = int(lum / 100.0 * 10)
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
    h, w = img.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    Y, X = np.ogrid[:h, :w]
    dist = np.sqrt(((X - cx) / cx) ** 2 + ((Y - cy) / cy) ** 2)
    dist = np.clip(dist, 0, 1)
    vig = 1.0 - (dist ** 2) * (amount / 100.0) * 0.6
    vig = np.clip(vig, 0.0, 1.5)[:, :, np.newaxis]
    return np.clip(img * vig, 0, 1)


def _rotate(img: np.ndarray, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), -angle, 1.0)
    u8 = float32_to_uint8(img)
    rotated = cv2.warpAffine(u8, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
    return rotated.astype(np.float32) / 255.0
