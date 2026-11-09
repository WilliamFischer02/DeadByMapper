"""Validate every map JSON and print an authoring-status table.

uv run python tools/validate_maps.py            # summary
uv run python tools/validate_maps.py --verbose  # per-map detail
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from deadbymapper.world.map_registry import MapRegistry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    try:
        reg = MapRegistry.load()
    except ValueError as exc:
        print(f"INVALID: {exc}")
        return 1
    counts = Counter(m.status for m in reg.all())
    print(f"{len(reg)} maps OK  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if args.verbose:
        for m in sorted(reg.all(), key=lambda m: (m.realm, m.name)):
            live = "live" if m.available else "off "
            print(
                f"  {m.status:9} {live} gens={len(m.generators):2} gates={len(m.exit_gates):2} "
                f"structs={len(m.structures):2}  {m.realm} / {m.name}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
