"""Thread-safe publish/subscribe event bus.

* ``publish`` never blocks the caller (sensor threads stay real-time).
* Handlers run on ONE dispatcher thread, in publish order, so estimators need no locks
  for their own state. Handlers must be fast (< 1 ms typical). Heavy work belongs in a
  worker that publishes results back.
* ``synchronous=True`` dispatches inline - used by tests, the simulator and replay to get
  fully deterministic behaviour.
* A handler that raises is logged and counted; it never kills the bus.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

from .events import Event

log = logging.getLogger(__name__)

E = TypeVar("E", bound=Event)
Handler = Callable[[Any], None]
_STOP = object()


class EventBus:
    def __init__(self, *, synchronous: bool = False, name: str = "bus") -> None:
        self._sync = synchronous
        self._name = name
        self._handlers: dict[type | None, list[Handler]] = defaultdict(list)
        self._lock = threading.RLock()
        self._queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._dispatch_cache: dict[type, tuple[Handler, ...]] = {}
        self.error_count = 0
        self.published_count = 0

    # ----------------------------------------------------------------- subscription
    def subscribe(self, event_type: type[E] | None, handler: Callable[[E], None]) -> Callable[[], None]:
        """Subscribe to an event class (and its subclasses). ``None`` = every event."""
        with self._lock:
            self._handlers[event_type].append(handler)
            self._dispatch_cache.clear()

        def unsubscribe() -> None:
            with self._lock, contextlib.suppress(ValueError):
                self._handlers[event_type].remove(handler)
            with self._lock:
                self._dispatch_cache.clear()

        return unsubscribe

    def _handlers_for(self, cls: type) -> tuple[Handler, ...]:
        cached = self._dispatch_cache.get(cls)
        if cached is not None:
            return cached
        with self._lock:
            out: list[Handler] = []
            for klass in cls.__mro__:
                out.extend(self._handlers.get(klass, ()))
            out.extend(self._handlers.get(None, ()))
            result = tuple(out)
            self._dispatch_cache[cls] = result
            return result

    # ----------------------------------------------------------------- publishing
    def publish(self, event: Event) -> None:
        self.published_count += 1
        if self._sync:
            self._dispatch(event)
        else:
            self._queue.put(event)

    def _dispatch(self, event: Event) -> None:
        for handler in self._handlers_for(type(event)):
            try:
                handler(event)
            except Exception:
                self.error_count += 1
                log.exception("[%s] handler %r failed on %s", self._name, handler, type(event).__name__)

    # ----------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._sync or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=f"{self._name}-dispatch", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            self._dispatch(item)

    def stop(self, timeout: float = 2.0) -> None:
        if self._thread is None:
            return
        self._queue.put(_STOP)
        self._thread.join(timeout)
        self._thread = None

    def drain(self) -> None:
        """Dispatch everything queued so far on the calling thread (tests only)."""
        if self._thread is not None:
            raise RuntimeError("drain() is only valid when the dispatcher thread is not running")
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is not _STOP:
                self._dispatch(item)
