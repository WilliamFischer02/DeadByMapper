"""Regenerate data/schemas/*.json from the pydantic models (the models are the source of truth).

uv run python tools/gen_schemas.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from deadbymapper.config.settings import Settings  # noqa: E402
from deadbymapper.world.map_model import MapData  # noqa: E402

OUT = ROOT / "data" / "schemas"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, model in (("map.schema.json", MapData), ("settings.schema.json", Settings)):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        (OUT / name).write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        print("wrote", OUT / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
