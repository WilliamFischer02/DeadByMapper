"""Copy + hand-off between the WGC callback thread and our converter thread (latest-only).

Why a hand-off instead of converting in the callback: OpenCV's default parallel backend on Windows
(MS Concurrency Runtime, pip builds) crashed the process with an access violation when a WGC session
whose native callback thread had run ``cv2.resize``/``cvtColor`` ended (window closed) and the next
session started (windows-capture 2.0.1, Win11, reproduced 3/3; never with OpenCV unused on that thread).
So the callback only copies the client crop into a pooled buffer - a plain memcpy - and a long-lived
Python thread does all OpenCV work. Bonus: the native frame is released as soon as possible.

Buffers are recycled (no per-frame 4-15 MB allocation). If the converter falls behind, the pending
buffer is replaced by the newer one (``dropped`` counts it) - consumers want the latest frame only.
"""

from __future__ import annotations

import threading

import numpy as np


class FrameHandoff:
    def __init__(self, pool: int = 3) -> None:
        self._cond = threading.Condition()
        self._pending: tuple[np.ndarray, int] | None = None
        self._free: list[np.ndarray] = []
        self._pool = pool
        self.published = 0
        self.dropped = 0

    def copy_in(self, src: np.ndarray, ts_ns: int) -> None:
        """Copy ``src`` (any strided view, e.g. into native memory) and publish it. Never waits on the
        consumer; the only lock is held for a few list operations."""
        buf = self._buffer(src.shape, src.dtype)
        np.copyto(buf, src)
        with self._cond:
            if self._pending is not None:
                self.dropped += 1
                self._recycle(self._pending[0])
            self._pending = (buf, ts_ns)
            self.published += 1
            self._cond.notify()

    def take(self, timeout: float | None = None) -> tuple[np.ndarray, int] | None:
        """Wait for the latest published buffer; the caller must ``release`` it when done."""
        with self._cond:
            if self._pending is None:
                self._cond.wait(timeout)
            item, self._pending = self._pending, None
            return item

    def release(self, buf: np.ndarray) -> None:
        with self._cond:
            self._recycle(buf)

    def clear(self) -> None:
        with self._cond:
            if self._pending is not None:
                self._recycle(self._pending[0])
            self._pending = None
            self._cond.notify_all()

    def wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def _buffer(self, shape: tuple[int, ...], dtype: np.dtype) -> np.ndarray:
        with self._cond:
            while self._free:
                buf = self._free.pop()
                if buf.shape == shape and buf.dtype == dtype:
                    return buf
        return np.empty(shape, dtype)

    def _recycle(self, buf: np.ndarray) -> None:  # caller holds the lock
        if len(self._free) < self._pool:
            self._free.append(buf)
