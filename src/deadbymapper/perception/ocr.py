"""OCR backends behind one protocol.

Default on Windows: Windows.Media.Ocr (built into Windows 10/11, fast, uses installed language
packs, no model download). Optional: RapidOCR (ONNX, offline, multilingual) via ``--extra ocr``.
OCR is only run on small, calibrated regions and only when the state machine expects text
(loading -> intro title, menu overlay -> map name, interaction prompts at <= 5 Hz).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OcrResult:
    text: str
    confidence: float  # 0..1 (backends without confidences report 1.0)
    bbox: tuple[int, int, int, int] | None = None


class OcrBackend(Protocol):
    name: str

    def read(self, image_bgr: np.ndarray) -> list[OcrResult]: ...


class NullOcr:
    name = "null"

    def read(self, image_bgr: np.ndarray) -> list[OcrResult]:
        return []


def preprocess(image_bgr: np.ndarray, *, upscale: float = 2.0, invert_if_light_text: bool = True) -> np.ndarray:
    """Grayscale + contrast stretch + crisp upscale (INTER_CUBIC) for small UI text."""
    import cv2

    g = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    lo, hi = np.percentile(g, (2, 98))
    g = np.clip((g.astype(np.float32) - lo) * (255.0 / max(hi - lo, 1.0)), 0, 255).astype(np.uint8)
    if invert_if_light_text and float(np.mean(g)) < 110:  # light text on dark -> dark on light
        g = 255 - g
    if upscale != 1.0:
        g = cv2.resize(g, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    return g


class WinRtOcr:  # pragma: no cover - Windows only, verified in M3
    """Windows.Media.Ocr via pywinrt. STATUS: scaffold, verify API names against installed winrt packages."""

    name = "winrt"

    def __init__(self, language_tag: str | None = None) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows only")
        from winrt.windows.globalization import Language  # type: ignore[import-not-found]
        from winrt.windows.media.ocr import OcrEngine  # type: ignore[import-not-found]

        self._engine = (
            OcrEngine.try_create_from_language(Language(language_tag))
            if language_tag
            else OcrEngine.try_create_from_user_profile_languages()
        )
        if self._engine is None:
            raise RuntimeError("no OCR language pack available")

    def read(self, image_bgr: np.ndarray) -> list[OcrResult]:
        import asyncio

        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap  # type: ignore[import-not-found]
        from winrt.windows.storage.streams import DataWriter  # type: ignore[import-not-found]

        g = preprocess(image_bgr)
        rgba = np.dstack([g, g, g, np.full_like(g, 255)]).astype(np.uint8)
        h, w = g.shape
        writer = DataWriter()
        writer.write_bytes(rgba.tobytes())
        bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.RGBA8, w, h)
        result = asyncio.run(self._engine.recognize_async(bmp))
        return [OcrResult(line.text, 1.0) for line in result.lines]


class RapidOcrBackend:  # pragma: no cover - optional dependency
    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]

        self._ocr = RapidOCR()

    def read(self, image_bgr: np.ndarray) -> list[OcrResult]:
        res, _ = self._ocr(preprocess(image_bgr, invert_if_light_text=False))
        return [OcrResult(text, float(conf)) for _box, text, conf in (res or [])]


def best_backend(language_tag: str | None = None) -> OcrBackend:
    for factory in (lambda: WinRtOcr(language_tag), RapidOcrBackend):
        try:
            return factory()
        except Exception as exc:
            log.info("OCR backend unavailable: %s", exc)
    return NullOcr()
