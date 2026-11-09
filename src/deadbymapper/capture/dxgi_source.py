"""DXGI Desktop Duplication fallback (monitor capture, works with exclusive fullscreen).

STATUS: not implemented - M1 task for the capture agent. Options: ``dxcam``/``bettercam`` packages
or ``windows-capture``'s monitor mode. Must crop to the game client rect (from GameWindowChanged)
and must MASK OUT DeadByMapper's own pane if it sits on the same monitor (never feed our own UI
back into perception).
"""

from __future__ import annotations

from ..core.lifecycle import Service


class DxgiFrameSource(Service):
    name = "capture.dxgi"

    def start(self) -> None:
        self.set_health("disabled", "DXGI fallback not implemented yet (M1)")
