"""Windows Graphics Capture of the DBD window (primary backend).

STATUS: scaffold - written against the ``windows-capture`` package API as understood in 2025-26;
the capture agent must verify it on Windows (M1). Requirements:
  * capture the game WINDOW (not the monitor) so other windows (incl. our pane) never appear;
  * disable the yellow capture border when the OS allows it (Windows 11);
  * crop to the client area, convert BGRA->BGR, downscale with INTER_AREA to
    settings.capture.analysis_width, publish into a FrameMailbox (latest-only);
  * never block the capture callback (copy + hand off; heavy work happens in consumers);
  * report ServiceHealth: "ok" when frames flow, "degraded" if fps < 5, "down" with a reason.

Exclusive fullscreen: WGC window capture may return black frames; fall back to DXGI desktop
duplication of the game's monitor (dxgi_source.py) and recommend Windowed Fullscreen in the UI.
"""

from __future__ import annotations

import logging
import sys

from ..core.bus import EventBus
from ..core.clock import Clock
from ..core.lifecycle import Service
from .base import FrameMailbox, downscale
from .frame import Frame

log = logging.getLogger(__name__)


class WgcFrameSource(Service):
    name = "capture.wgc"

    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        *,
        window_title: str | None = None,
        hwnd: int | None = None,
        analysis_width: int = 960,
    ) -> None:
        super().__init__(bus, clock)
        self.window_title = window_title
        self.hwnd = hwnd
        self.analysis_width = analysis_width
        self._mailbox = FrameMailbox()
        self._control = None

    @property
    def mailbox(self) -> FrameMailbox:
        return self._mailbox

    def start(self) -> None:
        if sys.platform != "win32":
            self.set_health("disabled", "Windows only")
            return
        try:
            from windows_capture import WindowsCapture  # type: ignore[import-not-found]
        except ImportError:
            self.set_health("down", "windows-capture not installed (uv sync --extra windows)")
            return

        capture = WindowsCapture(cursor_capture=False, draw_border=False, window_name=self.window_title)

        @capture.event
        def on_frame_arrived(frame, capture_control):  # type: ignore[no-untyped-def]
            img = frame.frame_buffer[:, :, :3]  # BGRA -> BGR view
            small, scale = downscale(img, self.analysis_width)
            self._mailbox.put(Frame(self.clock.now_ns(), small.copy(), scale))

        @capture.event
        def on_closed():  # type: ignore[no-untyped-def]
            self.set_health("down", "capture closed (window gone?)")

        self._control = capture.start_free_threaded()
        self.set_health("ok")

    def stop(self) -> None:
        if self._control is not None:
            try:
                self._control.stop()
            except Exception:
                log.exception("stopping capture failed")
            self._control = None
        self.set_health("down")
