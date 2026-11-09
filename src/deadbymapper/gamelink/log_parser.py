"""Rule-driven log parsing (rules in data/game/log_rules.json - all unverified until proven).

Also provides ``probe`` used by tools/log_probe.py to surface candidate lines/tokens from a real
log so a human (or the agent) can write and verify rules.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config.paths import data_dir
from ..core.bus import EventBus
from ..core.clock import Clock
from ..core.events import LogEvent, LogLine


@dataclass(frozen=True, slots=True)
class LogRule:
    id: str
    kind: str
    pattern: re.Pattern[str]
    verified: bool
    enabled: bool


class LogParser:
    def __init__(
        self, rules: list[LogRule], probe_keywords: list[str] | None = None, probe_token_pattern: str | None = None
    ) -> None:
        self.rules = [r for r in rules if r.enabled]
        self.probe_keywords = probe_keywords or []
        self.token_re = re.compile(probe_token_pattern) if probe_token_pattern else None

    @classmethod
    def load(cls, path: Path | None = None) -> LogParser:
        raw = json.loads((path or data_dir() / "game" / "log_rules.json").read_text(encoding="utf-8"))
        rules = [
            LogRule(r["id"], r["kind"], re.compile(r["pattern"]), bool(r.get("verified")), bool(r.get("enabled")))
            for r in raw.get("rules", [])
        ]
        return cls(rules, raw.get("probe_keywords"), raw.get("probe_token_pattern"))

    def parse(self, line: str) -> list[tuple[LogRule, dict[str, Any]]]:
        out = []
        for r in self.rules:
            m = r.pattern.search(line)
            if m:
                out.append((r, m.groupdict()))
        return out

    def attach(self, bus: EventBus, clock: Clock) -> None:
        def on_line(e: LogLine) -> None:
            for rule, data in self.parse(e.text):
                bus.publish(LogEvent(ts_ns=e.ts_ns, kind=rule.kind, data=data, rule_id=rule.id, verified=rule.verified))

        bus.subscribe(LogLine, on_line)

    # ------------------------------------------------------------------ probing (dev tool)
    def probe(self, lines: list[str], max_lines: int = 200) -> dict[str, Any]:
        kw = [k.lower() for k in self.probe_keywords]
        hits = [(i, ln) for i, ln in enumerate(lines) if any(k in ln.lower() for k in kw)]
        tokens: Counter[str] = Counter()
        if self.token_re:
            for ln in lines:
                tokens.update(self.token_re.findall(ln))
        return {"keyword_hits": hits[:max_lines], "n_keyword_hits": len(hits), "tokens": tokens.most_common(80)}
