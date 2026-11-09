"""Interaction detection: prompt / progress-label text -> InteractionObserved start/stop events.

STATUS: logic implemented (keyword table + debounced start/stop); the pixels->text step is
pluggable and unverified. M3 work for the vision agent:
  * calibrate ROIs (interaction_prompt, progress_bar, skill_check) per resolution/HUD scale,
  * build a cheap "is there a prompt at all" gate (template / brightness) so OCR runs <= 5 Hz,
  * add the skill-check ring detector as a second, language-independent repair/heal signal,
  * verify every keyword in data/game/prompts.<lang>.json against captured screenshots.
Ambiguous keywords (e.g. one word shared by gates and chests) are resolved from context or
dropped - a wrong landmark is worse than none (it pulls the particle filter to the wrong place).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..config.paths import data_dir
from ..core.events import InteractionKind, InteractionObserved


@dataclass(frozen=True, slots=True)
class PromptEntry:
    keyword: str
    kind: str
    verified: bool


def load_prompts(lang: str = "en", path: Path | None = None) -> list[PromptEntry]:
    raw = json.loads((path or data_dir() / "game" / f"prompts.{lang}.json").read_text(encoding="utf-8"))
    return [PromptEntry(e["keyword"].upper(), e["kind"], bool(e.get("verified"))) for e in raw["entries"]]


class PromptInterpreter:
    """Debounces per-frame text reads into start/stop events."""

    def __init__(
        self, entries: list[PromptEntry], *, start_frames: int = 2, stop_frames: int = 3, verified_only: bool = False
    ) -> None:
        self.entries = [e for e in entries if e.verified or not verified_only]
        self.start_frames, self.stop_frames = start_frames, stop_frames
        self.active: InteractionKind | None = None
        self._cand: InteractionKind | None = None
        self._hits = 0
        self._misses = 0

    def classify(self, text: str) -> InteractionKind | None:
        words = set(text.upper().replace(":", " ").split())
        for e in sorted(self.entries, key=lambda e: -len(e.keyword)):
            if e.keyword in words:
                if e.kind.startswith("ambiguous"):
                    return None
                return InteractionKind(e.kind)
        return None

    def feed(self, texts: list[str], ts_ns: int, *, progress_visible: bool) -> list[InteractionObserved]:
        """``progress_visible``: an interaction progress bar is on screen (the action is happening,
        not merely offered). A prompt alone only means 'near'; we emit on progress."""
        kind = None
        if progress_visible:
            for t in texts:
                kind = self.classify(t)
                if kind:
                    break
        out: list[InteractionObserved] = []
        if kind is not None and kind == self._cand:
            self._hits += 1
        elif kind is not None:
            self._cand, self._hits = kind, 1
        if kind is None:
            self._misses += 1
        else:
            self._misses = 0
        if self.active is None and self._cand is not None and self._hits >= self.start_frames:
            self.active = self._cand
            out.append(InteractionObserved(ts_ns=ts_ns, kind=self.active, active=True))
        elif self.active is not None and self._misses >= self.stop_frames:
            out.append(InteractionObserved(ts_ns=ts_ns, kind=self.active, active=False))
            self.active, self._cand, self._hits = None, None, 0
        return out
