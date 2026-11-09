"""Records every bus event to ``<session>/events.jsonl`` (+ meta.json, optional frames video).

Session layout (docs/DATA_COLLECTION.md):
    recordings/2026-11-08_013000/
        meta.json        app version, record schema, settings snapshot, notes
        events.jsonl     one JSON object per event, in publish order
        frames.mp4       optional downscaled capture (perception fps), frame i <-> frames.idx line i
        frames.idx       ts_ns per video frame
Player hotkey marks (F9 generator, F10 exit gate, F8 note) are ``UserMark`` events inside
events.jsonl - they are the ground-truth labels. Summarise a session: ``tools/recording_info.py``.
Only gated input is ever recorded (input/privacy.py). Recordings may contain screen captures of YOUR
games: they stay local and are git-ignored.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .. import __version__
from ..config.paths import recordings_dir
from ..core.bus import EventBus
from ..core.events import RECORD_SCHEMA_VERSION, Event, to_record


class SessionRecorder:
    def __init__(
        self, root: Path | None = None, *, name: str | None = None, notes: str = "", meta: dict[str, Any] | None = None
    ) -> None:
        stamp = name or time.strftime("%Y-%m-%d_%H%M%S")
        self.dir = (root or recordings_dir()) / stamp
        self.dir.mkdir(parents=True, exist_ok=True)
        self._f = (self.dir / "events.jsonl").open("w", encoding="utf-8")
        self._lock = threading.Lock()
        self.count = 0
        (self.dir / "meta.json").write_text(
            json.dumps(
                {
                    "app_version": __version__,
                    "record_schema": RECORD_SCHEMA_VERSION,
                    "started_unix": time.time(),
                    "notes": notes,
                    **(meta or {}),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self._unsub = None

    def attach(self, bus: EventBus) -> None:
        self._unsub = bus.subscribe(None, self.write)

    def write(self, e: Event) -> None:
        line = json.dumps(to_record(e), separators=(",", ":"))
        with self._lock:
            self._f.write(line + "\n")
            self.count += 1

    def close(self) -> None:
        if self._unsub:
            self._unsub()
        with self._lock:
            self._f.close()
