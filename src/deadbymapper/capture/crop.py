"""Client-area crop and analysis downscale (pure numpy/OpenCV; tested in tests/test_capture.py).

WGC window capture delivers the window's DWM *extended frame bounds* (title bar and 1 px borders
included, invisible resize borders excluded), in physical pixels. The game client area sits at
``client_origin - frame_origin`` inside that image. Measured on Windows 11 (100 % scale): a 1280x720
client in a captioned window arrives as a 1282x752 frame with the client at (1, 31). In windowed
fullscreen the frame IS the client area (offset 0, same size).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

Rect = tuple[int, int, int, int]  # x, y, w, h
Box = tuple[int, int, int, int]  # x0, y0, x1, y1 (exclusive)

RESIDUAL_INTERPOLATION = "area"  # residual (ratio in (0.5, 1]) filter: "area" | "linear"


@dataclass(frozen=True, slots=True)
class CaptureGeometry:
    frame_rect: Rect  # DWM extended frame bounds, screen physical px (what WGC captures)
    client_rect: Rect  # client area, screen physical px


def client_crop(frame_w: int, frame_h: int, geom: CaptureGeometry | None) -> Box:
    """Box of the client area inside a captured frame, clamped to the frame.

    Falls back to the whole frame when geometry is unknown or the crop would be empty. While a resize
    is in flight the frame size lags the window size by a frame or two; clamping keeps that safe.
    """
    full = (0, 0, frame_w, frame_h)
    if geom is None or frame_w <= 0 or frame_h <= 0:
        return full
    fx, fy, _, _ = geom.frame_rect
    cx, cy, cw, ch = geom.client_rect
    x0 = min(max(cx - fx, 0), frame_w)
    y0 = min(max(cy - fy, 0), frame_h)
    x1 = min(max(x0 + cw, 0), frame_w)
    y1 = min(max(y0 + ch, 0), frame_h)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return full
    return x0, y0, x1, y1


def analysis_size(src_w: int, src_h: int, width: int) -> tuple[int, int, float]:
    """(out_w, out_h, scale) for downscaling to ``width``; never upscales. scale = out_w / src_w."""
    if src_w <= 0 or src_h <= 0:
        return 0, 0, 1.0
    if width <= 0 or src_w <= width:
        return src_w, src_h, 1.0
    out_h = max(1, round(src_h * width / src_w))
    return width, out_h, width / src_w


def downscale_plan(src_w: int, src_h: int, width: int) -> tuple[int, tuple[int, int], tuple[int, int], float]:
    """How ``bgra_to_analysis`` downscales: (k, (used_w, used_h), (out_w, out_h), scale).

    ``k`` >= 2 is an integer box-filter pre-reduction (OpenCV's INTER_AREA fast path needs an exact
    integer ratio, so up to k-1 edge pixels are trimmed: ``used`` is the area actually sampled); a
    residual INTER_AREA step (ratio in (0.5, 1]) reaches ``out``. ``scale`` = out_w / used_w, so a point
    maps back with ``x_client = x_analysis / scale`` exactly.
    """
    out_w, out_h, _ = analysis_size(src_w, src_h, width)
    if (out_w, out_h) == (src_w, src_h):
        return 1, (src_w, src_h), (src_w, src_h), 1.0
    k = max(src_w // out_w, 1)
    used_w, used_h = ((src_w // k) * k, (src_h // k) * k) if k >= 2 else (src_w, src_h)
    return k, (used_w, used_h), (out_w, out_h), out_w / used_w


def bgra_to_analysis(bgra: np.ndarray, box: Box, width: int) -> tuple[np.ndarray, float]:
    """Crop a BGRA frame (may be a zero-copy view of native memory), INTER_AREA downscale, -> BGR.

    Works on the 4-channel view (OpenCV takes the strided crop without copying; copying first was
    measured slower) and converts the small image to BGR last, so the full-size frame is read once.
    Integer ratios (1920 -> 960) take INTER_AREA's fast exact-box path; others (2560 -> 960) pre-reduce
    by an integer factor first, then a residual step (ratio in (0.5, 1]) - ~2x cheaper than one
    arbitrary-ratio INTER_AREA pass. The result is always a new contiguous array owning its memory
    (safe after the native frame is released).
    """
    import cv2

    x0, y0, x1, y1 = box
    view = bgra[y0:y1, x0:x1]
    h, w = view.shape[:2]
    k, (uw, uh), (out_w, out_h), scale = downscale_plan(w, h, width)
    if k >= 2:
        view = cv2.resize(view[:uh, :uw], (uw // k, uh // k), interpolation=cv2.INTER_AREA)
    if (view.shape[1], view.shape[0]) != (out_w, out_h):
        interp = cv2.INTER_LINEAR if RESIDUAL_INTERPOLATION == "linear" else cv2.INTER_AREA
        view = cv2.resize(view, (out_w, out_h), interpolation=interp)
    return cv2.cvtColor(view, cv2.COLOR_BGRA2BGR), scale


def is_black(image: np.ndarray, threshold: int = 6, step: int = 8) -> bool:
    """True if a sparse sample of the frame is (near) pure black - the symptom of WGC capturing an
    exclusive-fullscreen or protected window. Real dark game scenes still have pixels above ~6/255."""
    if image.size == 0:
        return True
    return int(image[::step, ::step].max()) < threshold
