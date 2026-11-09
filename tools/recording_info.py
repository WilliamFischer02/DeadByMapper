"""Summarise a recorded session: duration, event counts, match states, maps, and hotkey marks.

    uv run python tools/recording_info.py "%LOCALAPPDATA%/DeadByMapper/recordings/2026-11-08_013000"
    uv run python tools/recording_info.py <session> --marks      # list every mark with its time

Agents: ask the user for the session folder name, then run this first (docs/DATA_COLLECTION.md).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from deadbymapper.core.events import (  # noqa: E402
    InteractionObserved,
    MapIdentified,
    MatchStateChanged,
    UserMapOverride,
    UserMark,
)
from deadbymapper.recording.replayer import read_events  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", type=Path)
    ap.add_argument("--marks", action="store_true", help="list every UserMark")
    args = ap.parse_args()
    if not (args.session / "events.jsonl").exists():
        print(f"no events.jsonl in {args.session}", file=sys.stderr)
        return 2
    meta_path = args.session / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    counts: Counter[str] = Counter()
    t0 = t1 = None
    states: list[tuple[float, str]] = []
    maps: list[tuple[float, str, str]] = []
    marks: list[tuple[float, str]] = []
    interactions = 0
    events = list(read_events(args.session, skip_types=()))
    for e in events:
        t0 = e.ts_ns if t0 is None else t0
        t1 = e.ts_ns
        counts[type(e).__name__] += 1
        rel = (e.ts_ns - t0) / 1e9
        if isinstance(e, MatchStateChanged):
            states.append((rel, e.state.value))
        elif isinstance(e, MapIdentified):
            maps.append((rel, e.map_id, e.source.value))
        elif isinstance(e, UserMapOverride):
            maps.append((rel, e.map_id, "manual"))
        elif isinstance(e, UserMark):
            marks.append((rel, e.kind))
        elif isinstance(e, InteractionObserved) and e.active:
            interactions += 1
    dur = 0.0 if t0 is None or t1 is None else (t1 - t0) / 1e9
    print(f"session   {args.session.name}  ({meta.get('app_version', '?')}, schema {meta.get('record_schema', '?')})")
    print(f"duration  {dur:,.1f} s   events {sum(counts.values()):,}")
    print("states    " + (" -> ".join(f"{s}@{t:.0f}s" for t, s in states) or "none"))
    print("maps      " + (", ".join(f"{m} ({src}) @{t:.0f}s" for t, m, src in maps) or "none"))
    by_kind = Counter(k for _t, k in marks)
    print(f"marks     {dict(by_kind) or 'none'}   perceived interactions: {interactions}")
    print("counts    " + ", ".join(f"{k} {v:,}" for k, v in counts.most_common()))
    if args.marks:
        for t, k in marks:
            print(f"  {t:8.2f}s  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
