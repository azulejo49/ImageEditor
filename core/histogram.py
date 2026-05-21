"""
core/histogram.py
Fast histogram computation from float32 or uint8 RGB arrays.
Returns per-channel + luminance histograms as numpy arrays.
"""

from __future__ import annotations
import numpy as np
from typing import Tuple


BINS = 256


def compute(img_u8: np.ndarray) -> dict:
    """
    Accepts uint8 (H,W,3) RGB.
    Returns dict with keys: 'r', 'g', 'b', 'lum'
    Each value is a (256,) float32 array normalized to [0,1].
    """
    assert img_u8.dtype == np.uint8, "Expected uint8 input"
    assert img_u8.ndim == 3 and img_u8.shape[2] == 3

    result = {}
    for i, ch in enumerate(["r", "g", "b"]):
        hist, _ = np.histogram(img_u8[:, :, i], bins=BINS, range=(0, 256))
        result[ch] = hist.astype(np.float32)

    # Luminance (perceptual)
    lum = (
        0.2126 * img_u8[:, :, 0].astype(np.float32)
        + 0.7152 * img_u8[:, :, 1].astype(np.float32)
        + 0.0722 * img_u8[:, :, 2].astype(np.float32)
    )
    hist_lum, _ = np.histogram(lum, bins=BINS, range=(0, 256))
    result["lum"] = hist_lum.astype(np.float32)

    # Normalize each channel independently (for display)
    for k in result:
        mx = result[k].max()
        if mx > 0:
            result[k] /= mx

    return result


def compute_f32(img_f32: np.ndarray) -> dict:
    """Accepts float32 (H,W,3). Converts to uint8 then computes."""
    u8 = (np.clip(img_f32, 0, 1) * 255).astype(np.uint8)
    return compute(u8)
