"""Frame source abstraction + latest-frame mailbox.

Consumers (perception workers) never queue frames: they pull the *latest* frame at their own
rate (flow 15 Hz, OCR on demand, HUD 5 Hz). Producers overwrite. This bounds latency and memory.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

import numpy as np

from ..core.clock import Clock
from .frame import Frame


class FrameSource(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    @property
    def mailbox(self) -> FrameMailbox: ...


class FrameMailbox:
    def __init__(self) -> None:
        self._frame: Frame | None = None
        self._cond = threading.Condition()
        self._seq = 0

    def put(self, frame: Frame) -> None:
        with self._cond:
            self._seq += 1
            self._frame = Frame(frame.ts_ns, frame.image, frame.scale, self._seq)
            self._cond.notify_all()

    def latest(self) -> Frame | None:
        with self._cond:
            return self._frame

    def wait_newer(self, seq: int, timeout: float = 0.5) -> Frame | None:
        with self._cond:
            if self._frame is not None and self._frame.seq > seq:
                return self._frame
            self._cond.wait(timeout)
            return self._frame if self._frame is not None and self._frame.seq > seq else None


def downscale(image: np.ndarray, width: int) -> tuple[np.ndarray, float]:
    """Area-averaged downscale for analysis (sharp, no ringing). Returns (image, scale)."""
    import cv2

    h, w = image.shape[:2]
    if w <= width:
        return image, 1.0
    s = width / w
    return cv2.resize(image, (width, round(h * s)), interpolation=cv2.INTER_AREA), s


class SyntheticFrameSource:
    """Generates frames from a callable - for tests and the demo."""

    def __init__(self, clock: Clock, make: Callable[[int], np.ndarray], fps: float = 15.0) -> None:
        self.clock = clock
        self.make = make
        self.fps = fps
        self._mailbox = FrameMailbox()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def mailbox(self) -> FrameMailbox:
        return self._mailbox

    def start(self) -> None:
        def run() -> None:
            i = 0
            while not self._stop.wait(1.0 / self.fps):
                self._mailbox.put(Frame(self.clock.now_ns(), self.make(i)))
                i += 1

        self._thread = threading.Thread(target=run, name="capture.synthetic", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def iter_video(path: Path, width: int | None = None) -> Iterator[tuple[int, np.ndarray]]:
    """Yield (frame_index, BGR image) from a recorded session video (replay)."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    i = 0
    try:
        while True:
            ok, img = cap.read()
            if not ok:
                return
            if width:
                img, _ = downscale(img, width)
            yield i, img
            i += 1
    finally:
        cap.release()
