"""Tails DeadByDaylight.log (shared read; the game keeps writing it).

Handles: file not existing yet, the game recreating it on launch (new file identity or size
shrink), UTF-8 or UTF-16 encodings (BOM-detected), partial last lines.
"""

from __future__ import annotations

import codecs
import logging
import os
from collections.abc import Callable
from pathlib import Path

from ..config.paths import dbd_log_file
from ..core.bus import EventBus
from ..core.clock import Clock
from ..core.events import LogLine
from ..core.lifecycle import ThreadedService

log = logging.getLogger(__name__)


def _detect_encoding(head: bytes) -> tuple[str, int]:
    if head.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le", 2
    if head.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be", 2
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8", 3
    return "utf-8", 0


def _identity(st: os.stat_result) -> tuple[int, int, float]:
    # Windows: st_ctime is the CREATION time (changes when the game recreates the log).
    # POSIX: st_ctime changes on every write, so rely on device/inode only.
    return (st.st_dev, st.st_ino, st.st_ctime if os.name == "nt" else 0.0)


class LogTailer:
    """Pure tailing logic (no threads) - unit-testable."""

    def __init__(self, path: Path, *, start_at_end: bool = True, max_read: int = 4 << 20) -> None:
        self.path = path
        self.start_at_end = start_at_end
        self.max_read = max_read
        self._pos = 0
        self._ident: tuple[int, int, float] | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._partial = ""
        self.rotations = 0

    def _reset(self, st: os.stat_result, *, first: bool) -> None:
        with self.path.open("rb") as f:
            head = f.read(4)
        encoding, bom = _detect_encoding(head)
        self._decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        self._partial = ""
        self._ident = _identity(st)
        if first and self.start_at_end:
            pos = st.st_size
            if encoding.startswith("utf-16") and (pos - bom) % 2:
                pos -= 1
            self._pos = max(pos, bom)
        else:
            self._pos = bom

    def poll(self) -> list[str]:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return []
        if self._ident is None:
            self._reset(st, first=True)
        elif _identity(st) != self._ident or st.st_size < self._pos:
            self.rotations += 1
            log.info("log file rotated/recreated - reading new file from the top")
            self._reset(st, first=False)
        if st.st_size <= self._pos:
            return []
        with self.path.open("rb") as f:  # CPython opens with FILE_SHARE_READ|FILE_SHARE_WRITE on Windows
            f.seek(self._pos)
            data = f.read(min(st.st_size - self._pos, self.max_read))
        self._pos += len(data)
        text = self._partial + self._decoder.decode(data)
        lines = text.split("\n")
        self._partial = lines.pop()  # incomplete last line (no newline yet)
        return [ln.rstrip("\r") for ln in lines if ln.strip()]


class LogTailService(ThreadedService):
    name = "gamelink.log"
    interval_s = 0.25

    def __init__(
        self, bus: EventBus, clock: Clock, path: Path | None = None, on_line: Callable[[str], None] | None = None
    ) -> None:
        super().__init__(bus, clock)
        self.tailer = LogTailer(path or dbd_log_file())
        self._on_line = on_line

    def loop_once(self) -> None:
        for line in self.tailer.poll():
            self.bus.publish(LogLine(ts_ns=self.clock.now_ns(), text=line))
            if self._on_line:
                self._on_line(line)
