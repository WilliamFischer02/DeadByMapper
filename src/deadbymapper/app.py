"""Composition root: builds the bus, services and models for a run mode and exposes a single
thread-safe ``snapshot()`` for the UI.

Modes
    live    real sensors (Windows): process/window/log/raw-input/capture/audio
    demo    simulated survivor + killer on the Test Arena (no game needed)
    replay  a recorded session replayed in real time through the full pipeline
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .config.settings import Settings
from .core.bus import EventBus
from .core.clock import MonotonicClock
from .core.events import (
    GameProcessChanged,
    GameWindowChanged,
    MapAmbiguous,
    ServiceHealth,
    Tick,
)
from .core.lifecycle import ServiceGroup, ThreadedService
from .input.keymap import KeyMap
from .localization.localizer import Localizer
from .perception.map_identifier import MapIdentifier
from .perception.state_machine import MatchStateMachine
from .recording.recorder import SessionRecorder
from .world.map_registry import MapRegistry
from .world.match_model import MatchModel, MatchSnapshot

log = logging.getLogger(__name__)

TEST_MAP_ID = "test_realm.test_arena"


@dataclass(frozen=True, slots=True)
class AppSnapshot:
    version: str
    mode: str
    game_running: bool
    platform: str | None
    window: tuple[int, int, int, int] | None
    display_mode: str
    health: dict[str, tuple[str, str]]
    match: MatchSnapshot
    calibrated: bool
    ambiguous_maps: tuple[str, ...] = ()
    map_counts: dict[str, int] = field(default_factory=dict)


class Ticker(ThreadedService):
    """60 Hz Tick source for live mode (sim/replay supply their own ticks)."""

    name = "ticker"

    def __init__(self, bus: EventBus, clock: MonotonicClock, hz: float = 60.0) -> None:
        super().__init__(bus, clock)
        self.interval_s = 1.0 / hz
        self.hz = hz

    def loop_once(self) -> None:
        self.bus.publish(Tick(ts_ns=self.clock.now_ns(), hz=self.hz))


class App:
    def __init__(self, mode: str, settings: Settings, *, replay_dir: Path | None = None, record: bool = False) -> None:
        self.mode = mode
        self.settings = settings
        self.clock = MonotonicClock()
        self.bus = EventBus(name="main")
        self.registry = MapRegistry.load()
        self.model = MatchModel(self.registry, self.clock)
        self.model.attach(self.bus)
        dpc = settings.input.survivor_deg_per_count
        if mode == "demo":
            dpc = 0.05 * 1.01  # the simulator's true value, slightly mis-calibrated on purpose
        self.keymap = KeyMap.load()
        self.localizer = Localizer(
            self.clock,
            self.registry,
            keymap=self.keymap,
            deg_per_count=dpc,
            n_particles=settings.localization.n_particles,
        )
        self.localizer.attach(self.bus)
        self.map_identifier = MapIdentifier(self.registry, self.clock)
        self.map_identifier.attach(self.bus)
        # Attached in every mode: it owns MatchStateChanged in live mode and adopts the states the
        # demo driver / replay publish, so the dashboard's manual Start/Stop trial always works.
        self.state_machine = MatchStateMachine(self.clock)
        self.state_machine.attach(self.bus)
        self.services = ServiceGroup()
        self.recorder: SessionRecorder | None = None

        self._lock = threading.Lock()
        self._health: dict[str, tuple[str, str]] = {}
        self._proc: GameProcessChanged | None = None
        self._win: GameWindowChanged | None = None
        self._ambiguous: tuple[str, ...] = ()
        self.bus.subscribe(ServiceHealth, self._on_health)
        self.bus.subscribe(GameProcessChanged, self._on_proc)
        self.bus.subscribe(GameWindowChanged, self._on_win)
        self.bus.subscribe(MapAmbiguous, self._on_ambiguous)

        if record or settings.record_sessions:
            self.recorder = SessionRecorder(notes=f"mode={mode}")
            self.recorder.attach(self.bus)

        if mode == "live":
            self._build_live()
        elif mode == "demo":
            from .sim.demo import DemoDriver

            self.services.add(DemoDriver(self.bus, self.clock, self.registry.get(TEST_MAP_ID)))  # type: ignore[arg-type]
        elif mode == "replay":
            if replay_dir is None:
                raise ValueError("replay mode needs replay_dir")
            self._replay_dir = replay_dir

    def _build_live(self) -> None:
        from .audio.loopback import LoopbackHeartbeatService
        from .capture.wgc_source import WgcFrameSource
        from .gamelink.log_parser import LogParser
        from .gamelink.log_tail import LogTailService
        from .gamelink.process_watcher import ProcessWatcher
        from .gamelink.window_tracker import WindowTracker
        from .input.hotkeys import HotkeyMapper
        from .input.raw_input import RawInputListener

        LogParser.load().attach(self.bus, self.clock)
        HotkeyMapper(self.keymap).attach(self.bus)
        for svc in (
            ProcessWatcher(self.bus, self.clock),
            WindowTracker(self.bus, self.clock),
            LogTailService(self.bus, self.clock),
            RawInputListener(self.bus, self.clock, keymap=self.keymap),
            WgcFrameSource(
                self.bus, self.clock, window_title="DeadByDaylight", analysis_width=self.settings.capture.analysis_width
            ),
            LoopbackHeartbeatService(self.bus, self.clock, settings=self.settings.audio),
            Ticker(self.bus, self.clock, self.settings.localization.tick_hz),
        ):
            self.services.add(svc)  # type: ignore[arg-type]
        # M3+: perception workers (flow, prompts, HUD, OCR map title) subscribe to the capture mailbox here.

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.bus.start()
        self.services.start_all()
        if self.mode == "replay":
            threading.Thread(target=self._replay, name="replay", daemon=True).start()
        log.info("DeadByMapper %s started in %s mode", __version__, self.mode)

    def _replay(self) -> None:
        import dataclasses
        import time

        from .recording.replayer import read_events

        events = list(read_events(self._replay_dir))
        if not events:
            return
        t0_evt, t0 = events[0].ts_ns, self.clock.now_ns()
        for e in events:
            target = t0 + (e.ts_ns - t0_evt)
            delay = (target - self.clock.now_ns()) / 1e9
            if delay > 0:
                time.sleep(delay)
            self.bus.publish(dataclasses.replace(e, ts_ns=target))

    def stop(self) -> None:
        self.services.stop_all()
        self.bus.stop()
        if self.recorder:
            self.recorder.close()

    # ------------------------------------------------------------------ state for the UI
    def _on_health(self, e: ServiceHealth) -> None:
        with self._lock:
            self._health[e.service] = (e.status, e.detail)

    def _on_proc(self, e: GameProcessChanged) -> None:
        with self._lock:
            self._proc = e

    def _on_win(self, e: GameWindowChanged) -> None:
        with self._lock:
            self._win = e

    def _on_ambiguous(self, e: MapAmbiguous) -> None:
        with self._lock:
            self._ambiguous = e.candidates

    def map_counts(self) -> dict[str, int]:
        """Counts over maps currently in the live pool (synthetic/removed/PTB maps excluded)."""
        counts: dict[str, int] = {"stub": 0, "partial": 0, "verified": 0}
        for m in self.registry.available():
            counts[m.status] = counts.get(m.status, 0) + 1
        counts["total"] = counts["stub"] + counts["partial"] + counts["verified"]
        return counts

    def snapshot(self) -> AppSnapshot:
        with self._lock:
            proc, win, health, amb = self._proc, self._win, dict(self._health), self._ambiguous
        running = self.mode == "demo" or bool(proc and proc.running)
        return AppSnapshot(
            version=__version__,
            mode=self.mode,
            game_running=running,
            platform="simulated" if self.mode == "demo" else (proc.platform if proc else None),
            window=win.client_rect if win else None,
            display_mode=win.display_mode if win else "unknown",
            health=health,
            match=self.model.snapshot(),
            calibrated=self.localizer.calibrated,
            ambiguous_maps=amb,
            map_counts=self.map_counts(),
        )
