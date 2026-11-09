"""Detects the Dead by Daylight process (Steam / Epic / Microsoft Store builds).

Only enumerates processes by name/exe path via psutil - it never opens a handle to the game
process with read/write rights (see docs/FAIR_PLAY.md).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..core.bus import EventBus
from ..core.clock import Clock
from ..core.events import GameProcessChanged
from ..core.lifecycle import ThreadedService

log = logging.getLogger(__name__)

# Known shipping executables. Win64 = Steam/Epic, WinGDK = Microsoft Store / Game Pass.
DBD_EXE_NAMES = ("DeadByDaylight-Win64-Shipping.exe", "DeadByDaylight-WinGDK-Shipping.exe")


@dataclass(frozen=True, slots=True)
class ProcInfo:
    pid: int
    name: str
    exe: str | None


def platform_from_exe(exe: str | None) -> str:
    p = (exe or "").replace("/", "\\").lower()
    if "steamapps" in p:
        return "steam"
    if "epic games" in p or "epicgames" in p:
        return "epic"
    if "windowsapps" in p or "xboxgames" in p or "wingdk" in p:
        return "msstore"
    return "unknown"


def _psutil_procs() -> Iterable[ProcInfo]:
    import psutil

    for p in psutil.process_iter(["pid", "name", "exe"]):
        info = p.info
        yield ProcInfo(info["pid"], info.get("name") or "", info.get("exe"))


class ProcessWatcher(ThreadedService):
    name = "gamelink.process"
    interval_s = 2.0

    def __init__(self, bus: EventBus, clock: Clock, procs: Callable[[], Iterable[ProcInfo]] = _psutil_procs) -> None:
        super().__init__(bus, clock)
        self._procs = procs
        self._current: ProcInfo | None = None

    def find(self) -> ProcInfo | None:
        names = {n.lower() for n in DBD_EXE_NAMES}
        for p in self._procs():
            if p.name.lower() in names:
                return p
        return None

    def loop_once(self) -> None:
        found = self.find()
        if (found is None) != (self._current is None) or (found and self._current and found.pid != self._current.pid):
            self._current = found
            self.bus.publish(
                GameProcessChanged(
                    ts_ns=self.clock.now_ns(),
                    running=found is not None,
                    pid=found.pid if found else None,
                    exe=found.exe if found else None,
                    platform=platform_from_exe(found.exe) if found else None,
                )
            )
            log.info("game process %s", f"found pid={found.pid}" if found else "gone")
