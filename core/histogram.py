"""
core/histogram.py
=================
FAST HISTOGRAM COMPUTATION
---------------------------
Computes per-channel (R, G, B) and luminance histograms from
a uint8 RGB image array.

OUTPUT FORMAT
-------------
Returns a dict with four keys: 'r', 'g', 'b', 'lum'
Each value is a float32 numpy array of 256 bins, normalised to [0.0–1.0]
so the tallest bin in each channel = 1.0.
This makes all channels equally visible regardless of colour dominance.

USAGE
-----
Called by RenderWorker after every render completes.
Result is forwarded to:
  - HistogramWidget   (bottom bar visual display)
  - CurveWidget       (background context behind the tone curve)
"""

from __future__ import annotations
import numpy as np

BINS = 256   # number of histogram bins (matches 8-bit display range)


def compute(img_u8: np.ndarray) -> dict:
    """
    Compute R/G/B/luminance histograms from a uint8 RGB array.

    Parameters
    ----------
    img_u8 : np.ndarray
        Shape (H, W, 3), dtype uint8, channels in RGB order.

    Returns
    -------
    dict with keys 'r', 'g', 'b', 'lum' — each a float32 array of 256 bins.
    Bins are normalised so the maximum bin value = 1.0 per channel.
    """
    assert img_u8.dtype == np.uint8,  "Expected uint8 input"
    assert img_u8.ndim == 3,          "Expected (H,W,3) array"
    assert img_u8.shape[2] == 3,      "Expected 3 channels (RGB)"

    result = {}

    # ── Per-channel histograms ────────────────────────────────────────────
    # np.histogram returns (counts, bin_edges); we only need counts.
    # range=(0,256) ensures all 256 values map to exactly one bin.
    for i, ch_name in enumerate(["r", "g", "b"]):
        counts, _ = np.histogram(img_u8[:, :, i], bins=BINS, range=(0, 256))
        result[ch_name] = counts.astype(np.float32)

    # ── Luminance histogram ───────────────────────────────────────────────
    # Perceptual luminance (ITU-R BT.709 coefficients):
    #   Y = 0.2126·R + 0.7152·G + 0.0722·B
    # Computed in float32 to avoid integer overflow, then binned.
    lum = (
          0.2126 * img_u8[:, :, 0].astype(np.float32)
        + 0.7152 * img_u8[:, :, 1].astype(np.float32)
        + 0.0722 * img_u8[:, :, 2].astype(np.float32)
    )
    counts_lum, _ = np.histogram(lum, bins=BINS, range=(0, 256))
    result["lum"] = counts_lum.astype(np.float32)

    # ── Normalise each channel independently ──────────────────────────────
    # Dividing by the per-channel maximum keeps all channels visible.
    # Without this, a dominant channel would dwarf others visually.
    for key in result:
        mx = result[key].max()
        if mx > 0:
            result[key] /= mx   # normalise to [0.0, 1.0]

    return result


def compute_f32(img_f32: np.ndarray) -> dict:
    """
    Convenience wrapper: accepts float32 [0–1] input, converts to uint8 first.
    Used when the pipeline renders a float32 preview directly.
    """
    u8 = (np.clip(img_f32, 0.0, 1.0) * 255.0).astype(np.uint8)
    return compute(u8)
