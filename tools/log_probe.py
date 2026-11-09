"""Surface candidate lines/tokens from a REAL DeadByDaylight.log so map/match rules can be written.

    uv run python tools/log_probe.py "%LOCALAPPDATA%/DeadByDaylight/Saved/Logs/DeadByDaylight.log"
    uv run python tools/log_probe.py recordings/<session>/DeadByDaylight.log --grep "Coal"

Workflow (docs/MAP_DETECTION.md): play one trial on a KNOWN map, copy the log, probe it, find the
line(s) that name the map at load time, write a rule in data/game/log_rules.json, mark it
verified with the patch number, add the token to that map's ``log_ids``, add a test fixture.
Never write rules that expose information the player cannot otherwise see (docs/FAIR_PLAY.md).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from deadbymapper.gamelink.log_parser import LogParser  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    ap.add_argument("--grep", help="extra case-insensitive regex to show matching lines")
    ap.add_argument("--max", type=int, default=120)
    args = ap.parse_args()
    raw = args.log.read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    parser = LogParser.load()
    report = parser.probe(lines, max_lines=args.max)
    print(f"{len(lines)} lines, {report['n_keyword_hits']} keyword hits\n")
    print("== Prefix_Name tokens (most common) ==")
    for tok, n in report["tokens"]:
        print(f"  {n:5}  {tok}")
    print("\n== keyword hits ==")
    for i, ln in report["keyword_hits"]:
        print(f"{i:7}: {ln[:220]}")
    if args.grep:
        rx = re.compile(args.grep, re.IGNORECASE)
        print(f"\n== --grep {args.grep!r} ==")
        for i, ln in enumerate(lines):
            if rx.search(ln):
                print(f"{i:7}: {ln[:220]}")
    print("\n== rules currently enabled ==")
    for r in parser.rules:
        hits = sum(1 for ln in lines if r.pattern.search(ln))
        print(f"  {r.id:30} verified={r.verified} hits={hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
