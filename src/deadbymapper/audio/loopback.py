"""WASAPI loopback capture -> HeartbeatAnalyzer -> TerrorRadiusObserved.

Modes (``AudioSettings.mode``):
  * ``process``: WASAPI *process* loopback of the Dead by Daylight process tree only
    (``ActivateAudioInterfaceAsync`` + PROCESS_LOOPBACK, Windows 10 2004+/11). Discord, music and other
    apps never reach the analyser. Health "ok".
  * ``device``: loopback of a render endpoint (default, or ``device_name``) via PyAudioWPatch. Captures
    ALL system audio, so health is "degraded" with the reason process loopback is not in use.
  * ``auto`` (default): process loopback while the DBD pid is known (``GameProcessChanged``), device
    loopback otherwise or when process loopback fails.

Threads:
  * driver/capture thread: the source callback copies + downmixes + ``BlockQueue.put`` (drop-oldest).
  * ``audio.heartbeat`` thread: ``HeartbeatPipeline.step`` (resample, feed, 4 Hz analyse, publish
    only on tier change + <= 1 Hz keep-alive while tier > 0).
  * ``audio.supervisor`` thread: mode selection, open/reopen with exponential backoff on failure,
    device change (default endpoint switched, unplug) detection, health.
Any failure degrades the service (ServiceHealth) - it never raises into the app.

This is read-only observation of the audio the player already hears (docs/FAIR_PLAY.md).
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..core.bus import EventBus
from ..core.clock import Clock, s_to_ns
from ..core.events import GameProcessChanged, TerrorRadiusObserved
from ..core.lifecycle import Service
from .blocks import AudioBlock, BlockQueue, ModeChoice, select_mode
from .heartbeat import HeartbeatAnalyzer
from .pipeline import HeartbeatPipeline, PipelineStats
from .resample import MonoResampler16k
from .sources import AudioSource, DeviceLoopbackSource, ProcessLoopbackSource, SourceInfo

__all__ = ["LoopbackHeartbeatService", "MonoResampler16k"]

log = logging.getLogger(__name__)

SourceFactory = Callable[[ModeChoice, str | None], AudioSource]


def default_source_factory(choice: ModeChoice, device_name: str | None) -> AudioSource:
    if choice.mode == "process" and choice.pid is not None:
        return ProcessLoopbackSource(choice.pid, name=f"Dead by Daylight (pid {choice.pid})")
    return DeviceLoopbackSource(device_name)


def _default_process_support() -> tuple[bool, str]:
    from .wasapi_com import process_loopback_supported

    return process_loopback_supported()


def _default_pid_lookup() -> int | None:
    """One-off read-only scan (psutil names only) for the DBD pid at start-up, in case the process
    watcher published GameProcessChanged before this service subscribed (start-order race)."""
    from ..core.bus import EventBus as _Bus
    from ..core.clock import MonotonicClock
    from ..gamelink.process_watcher import ProcessWatcher

    found = ProcessWatcher(_Bus(synchronous=True), MonotonicClock()).find()
    return found.pid if found else None


def _default_endpoint_id() -> str | None:
    from .wasapi_com import default_render_endpoint_id

    return default_render_endpoint_id()


@dataclass(frozen=True, slots=True)
class AudioStatus:
    mode: str  # "process" | "device" | "none"
    source: SourceInfo | None
    reason: str  # why process loopback is not used / why nothing runs
    health: str
    stats: PipelineStats


class Backoff:
    def __init__(self, first_s: float = 0.5, max_s: float = 10.0) -> None:
        self.first_s = first_s
        self.max_s = max_s
        self.next_s = first_s

    def fail(self) -> float:
        d = self.next_s
        self.next_s = min(self.max_s, self.next_s * 2.0)
        return d

    def ok(self) -> None:
        self.next_s = self.first_s


class LoopbackHeartbeatService(Service):
    name = "audio.heartbeat"

    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        analyzer: HeartbeatAnalyzer | None = None,
        settings: Any = None,  # config.settings.AudioSettings (mode, device_name, enabled)
        *,
        mode: str | None = None,
        device_name: str | None = None,
        source_factory: SourceFactory | None = None,
        process_support: tuple[bool, str] | None = None,
        endpoint_id: Callable[[], str | None] | None = None,
        pid_lookup: Callable[[], int | None] | None = None,
        queue_max: int = 64,
        tick_s: float = 0.1,
        supervise_s: float = 0.5,
        endpoint_poll_s: float = 2.0,
        process_retry_s: float = 30.0,
    ) -> None:
        super().__init__(bus, clock)
        self.analyzer = analyzer or HeartbeatAnalyzer()
        self.enabled = bool(getattr(settings, "enabled", True))
        self.mode = (mode or getattr(settings, "mode", None) or "auto").lower()
        self.device_name = device_name if device_name is not None else getattr(settings, "device_name", None)
        self._factory = source_factory or default_source_factory
        self._process_support = process_support
        self._endpoint_id = endpoint_id or _default_endpoint_id
        self._pid_lookup = (
            pid_lookup if pid_lookup is not None else (_default_pid_lookup if source_factory is None else None)
        )
        self.supervise_s = supervise_s
        self.endpoint_poll_ns = s_to_ns(endpoint_poll_s)
        self.process_retry_ns = s_to_ns(process_retry_s)
        self.queue = BlockQueue(queue_max)
        self.pipeline = HeartbeatPipeline(clock, self.analyzer, self._publish, queue=self.queue, tick_s=tick_s)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._threads: list[threading.Thread] = []
        self._unsub: Callable[[], None] | None = None
        self._dbd_pid: int | None = None
        self._source: AudioSource | None = None
        self._choice: ModeChoice | None = None
        self._reason = ""
        self._process_error: tuple[int, str, int] | None = None  # (pid, error, retry_at_ns)
        self._process_failures = 0  # consecutive failures for the current pid -> retry 30 s, 60 s, ... 8 min
        self._backoff = Backoff()
        self._retry_at_ns = 0
        self._endpoint: str | None = None
        self._endpoint_checked_ns = 0
        self._info: SourceInfo | None = None
        self._last_health: tuple[str, str] | None = None

    def set_health(self, status: str, detail: str = "") -> None:
        """Publish only when (status, detail) actually changes - the supervisor re-evaluates at 2 Hz."""
        if (status, detail) == self._last_health:
            return
        self._last_health = (status, detail)
        super().set_health(status, detail)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if not self.enabled:
            self.set_health("disabled", "audio disabled in settings")
            return
        if sys.platform != "win32" and self._process_support is None and self._factory is default_source_factory:
            self.set_health("disabled", "Windows only")
            return
        if self._factory is default_source_factory:
            try:
                import pyaudiowpatch  # type: ignore[import-not-found]  # noqa: F401
            except ImportError:
                self.set_health("down", "PyAudioWPatch not installed (uv sync --extra windows)")
                return
        self._stop.clear()
        self._unsub = self.bus.subscribe(GameProcessChanged, self._on_process)
        self._threads = [
            threading.Thread(target=self._analysis_loop, name="audio.heartbeat", daemon=True),
            threading.Thread(target=self._supervisor_loop, name="audio.supervisor", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        for t in self._threads:
            t.join(timeout=6.0)  # longer than ProcessLoopbackSource.open's 5 s so a blocked open cannot outlive us
        self._threads = []
        self._close_source()
        self.set_health("down", "stopped")

    def thread_native_ids(self) -> list[int]:
        return [t.native_id for t in self._threads if t.native_id is not None]

    # ------------------------------------------------------------------ events in
    def _on_process(self, e: GameProcessChanged) -> None:  # bus thread: keep it tiny
        pid = e.pid if e.running else None
        if pid != self._dbd_pid:
            self._dbd_pid = pid
            self._wake.set()

    def set_dbd_pid(self, pid: int | None) -> None:
        """Same as receiving GameProcessChanged (used by tools/audio_probe.py)."""
        self._dbd_pid = pid
        self._wake.set()

    def _publish(self, obs: TerrorRadiusObserved) -> None:
        self.bus.publish(obs)

    def _sink(self, mono: np.ndarray, flags: int) -> None:  # audio thread: copy + enqueue only
        self.queue.put(AudioBlock(self.clock.now_ns(), mono, flags))

    # ------------------------------------------------------------------ threads
    def _analysis_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.pipeline.step(0.05)
            except Exception as exc:  # degrade, never crash
                log.exception("audio analysis failed")
                self.set_health("degraded", f"analysis error: {type(exc).__name__}: {exc}")
                self._stop.wait(0.5)

    def _supervisor_loop(self) -> None:
        if self._pid_lookup is not None and self._dbd_pid is None and self.mode != "device":
            try:
                pid = self._pid_lookup()
                if pid is not None and self._dbd_pid is None:
                    self._dbd_pid = pid
            except Exception:
                log.debug("initial DBD pid lookup failed", exc_info=True)
        while not self._stop.is_set():
            try:
                self.supervise_once()
            except Exception as exc:
                log.exception("audio supervisor failed")
                self.set_health("down", f"supervisor error: {type(exc).__name__}: {exc}")
            self._wake.wait(self.supervise_s)
            self._wake.clear()

    # ------------------------------------------------------------------ supervision (testable, synchronous)
    def desired(self, now_ns: int) -> ModeChoice:
        supported, why = self._process_support if self._process_support is not None else _default_process_support()
        self._process_support = (supported, why)
        perr = None
        if self._process_error is not None:
            pid, err, retry_at = self._process_error
            if pid != self._dbd_pid:
                self._process_error = None
                self._process_failures = 0
            elif now_ns >= retry_at:
                self._process_error = None
            else:
                perr = err
        return select_mode(self.mode, self._dbd_pid, supported, why, perr)

    def supervise_once(self) -> None:
        now = self.clock.now_ns()
        with self._lock:
            choice = self.desired(now)
            reason = None
            cur = self._source
            if cur is not None and self._choice is not None:
                if (choice.mode, choice.pid if choice.mode == "process" else None) != (
                    self._choice.mode,
                    self._choice.pid if self._choice.mode == "process" else None,
                ):
                    reason = "mode change"
                elif not cur.alive():
                    reason = "stream stopped (device unplugged or process exited?)"
                elif choice.mode == "device" and not self.device_name and self._endpoint_changed(now):
                    reason = "default output device changed"
            elif choice.mode != (self._choice.mode if self._choice else None) or cur is None:
                reason = "start"
            if reason is None:
                return
            if cur is not None:
                log.info("audio: reopening (%s)", reason)
                self._close_source()
                self._choice = None
            if choice.mode == "none":
                self._choice = choice
                self._reason = choice.reason
                self.set_health("degraded", f"process loopback: waiting ({choice.reason})")
                return
            if now < self._retry_at_ns:
                return
            self._open(choice, now)

    def _endpoint_changed(self, now_ns: int) -> bool:
        if now_ns - self._endpoint_checked_ns < self.endpoint_poll_ns:
            return False
        self._endpoint_checked_ns = now_ns
        try:
            cur = self._endpoint_id()
        except Exception:
            return False
        return cur is not None and self._endpoint is not None and cur != self._endpoint

    def _open(self, choice: ModeChoice, now_ns: int) -> None:
        src = self._factory(choice, self.device_name)
        try:
            info = src.open(self._sink)
        except Exception as exc:
            with contextlib.suppress(Exception):
                src.close()
            err = f"{type(exc).__name__}: {exc}"
            if choice.mode == "process" and choice.pid is not None:
                log.warning("process loopback failed for pid %s: %s", choice.pid, err)
                self._process_failures += 1
                retry_ns = self.process_retry_ns * 2 ** min(self._process_failures - 1, 4)
                self._process_error = (choice.pid, err, now_ns + retry_ns)
                if self.mode == "auto":  # fall back to device loopback right away
                    self._wake.set()
                    return
            delay = self._backoff.fail()
            self._retry_at_ns = now_ns + s_to_ns(delay)
            self.set_health("down", f"no loopback capture: {err}; retrying in {delay:.1f} s")
            return
        if self._stop.is_set():  # stop() ran while open() blocked: never leave a live stream behind
            with contextlib.suppress(Exception):
                src.close()
            return
        self._backoff.ok()
        self._retry_at_ns = 0
        if info.kind == "process":
            self._process_failures = 0
        self._source = src
        self._choice = choice
        self._reason = choice.reason
        self.queue.clear()
        self.pipeline.set_source_rate(info.sample_rate)
        if info.kind == "device" and not self.device_name:
            try:
                self._endpoint = self._endpoint_id()
            except Exception:
                self._endpoint = None
            self._endpoint_checked_ns = now_ns
        self._info = info
        if info.kind == "process":
            self.set_health("ok", f"process loopback: {info.name}, {info.sample_rate} Hz")
        else:
            self.set_health(
                "degraded",
                f"device loopback: {info.name} (captures ALL system audio; process loopback unavailable: "
                f"{choice.reason or 'n/a'})",
            )

    def _close_source(self) -> None:
        src, self._source = self._source, None
        if src is not None:
            with contextlib.suppress(Exception):
                src.close()

    # ------------------------------------------------------------------ introspection
    def status(self) -> AudioStatus:
        with self._lock:
            live = self._source is not None and self._choice is not None
            mode = self._choice.mode if live and self._choice is not None else "none"
            info = self._info if live else None
            return AudioStatus(mode, info, self._reason, self.health, self.pipeline.stats())
