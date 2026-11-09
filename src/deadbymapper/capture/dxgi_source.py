"""DXGI Desktop Duplication fallback (monitor capture; works with exclusive fullscreen).

STATUS: NOT IMPLEMENTED (M1 shipped WGC only; docs/CAPTURE.md). The service exists so the app can
construct it and report its state honestly: ``start()`` publishes health "down" with the reason.

Plan (verified to exist in windows-capture 2.0.1: ``windows_capture.DxgiDuplicationSession(monitor_index)``
with ``acquire_frame(timeout_ms)`` -> BGRA/RGBA/rgba16f frame):
  * duplicate the monitor that contains the game client rect (GameWindowChanged, physical px);
  * crop to the client rect, reuse capture/crop.py + handoff.py + metrics.py and the WGC pacing;
  * MASK OUT DeadByMapper's own windows (pane, dashboard) that intersect the game rect on that monitor -
    desktop duplication sees everything on screen, and our UI must never feed back into perception;
  * handle ACCESS_LOST (mode change, UAC/secure desktop, fullscreen transitions) by recreating;
  * HDR monitors deliver rgba16f: tone-map/clip to 8-bit BGR before analysis.
"""

from __future__ import annotations

from ..core.lifecycle import Service
from .base import FrameMailbox


class DxgiFrameSource(Service):
    name = "capture.dxgi"

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._mailbox = FrameMailbox()

    @property
    def mailbox(self) -> FrameMailbox:
        return self._mailbox

    def start(self) -> None:
        self.set_health(
            "down",
            "DXGI desktop-duplication fallback not implemented yet - use Windowed Fullscreen (WGC window capture)",
        )
