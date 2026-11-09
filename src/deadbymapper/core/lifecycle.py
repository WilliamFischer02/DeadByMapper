"""Service lifecycle: every sensor/estimator is a Service with start/stop/health.

Services publish ``ServiceHealth`` so the dashboard can show glanceable status chips
(Capture / Input / Audio / Log / Map). A failing optional service must degrade, never crash
the app: catch, publish ``status="degraded"``/``"down"`` with a human-readable detail.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .bus import EventBus
from .clock import Clock
from .events import ServiceHealth

log = logging.getLogger(__name__)


class Service:
    name = "service"

    def __init__(self, bus: EventBus, clock: Clock) -> None:
        self.bus = bus
        self.clock = clock
        self._health = "down"

    def start(self) -> None:  # override
        self.set_health("ok")

    def stop(self) -> None:  # override
        self.set_health("down")

    def set_health(self, status: str, detail: str = "") -> None:
        if status != self._health or detail:
            self._health = status
            self.bus.publish(ServiceHealth(ts_ns=self.clock.now_ns(), service=self.name, status=status, detail=detail))

    @property
    def health(self) -> str:
        return self._health


class ThreadedService(Service):
    """Runs ``loop_once`` repeatedly on a daemon thread until stopped."""

    interval_s = 0.25

    def __init__(self, bus: EventBus, clock: Clock) -> None:
        super().__init__(bus, clock)
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        self.set_health("ok")

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.loop_once()
            except Exception as exc:  # degrade, never crash
                log.exception("%s loop failed", self.name)
                self.set_health("degraded", f"{type(exc).__name__}: {exc}")
            self._stop_evt.wait(self.interval_s)

    def loop_once(self) -> None:  # override
        raise NotImplementedError

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.set_health("down")


class ServiceGroup:
    def __init__(self) -> None:
        self._services: list[Service] = []
        self._on_stop: list[Callable[[], None]] = []

    def add(self, service: Service) -> Service:
        self._services.append(service)
        return service

    def on_stop(self, fn: Callable[[], None]) -> None:
        self._on_stop.append(fn)

    def start_all(self) -> None:
        for s in self._services:
            try:
                s.start()
            except Exception as exc:
                log.exception("failed to start %s", s.name)
                s.set_health("down", f"start failed: {exc}")

    def stop_all(self) -> None:
        for s in reversed(self._services):
            try:
                s.stop()
            except Exception:
                log.exception("failed to stop %s", s.name)
        for fn in self._on_stop:
            fn()
