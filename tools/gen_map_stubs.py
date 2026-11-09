"""Generate data/maps/<realm>/<map>.json stubs + _index.json + the synthetic test arena.

Safe to re-run: existing files whose status is not "stub" are NEVER overwritten (authored data
is precious). Use --force-stubs to refresh stub files only.

Map list + community sizes: timesaver.gg "DBD Maps List 2026" (re-checked 2026-11-01, patch
10.2.2a), which takes sizes from the DBD wiki (1 tile = 8x8 m = one border-wall segment).
Haddonfield/Lampkin Lane left the map pool in January 2026 (kept as available=false so old
recordings still resolve). Hartshorne Mall is PTB 10.3.0 content (expected 2026-11-24).

    uv run python tools/gen_map_stubs.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from deadbymapper.world.map_model import MapData  # noqa: E402

MAPS_DIR = ROOT / "data" / "maps"
SRC_LIST = {
    "title": "DBD Maps List 2026 (timesaver.gg) - sizes per DBD wiki",
    "url": "https://timesaver.gg/blog/dbd-maps-list-all-maps-realms",
    "accessed": "2026-11-08",
}
SRC_WIKI = {
    "title": "Dead by Daylight Wiki - Maps/Realms",
    "url": "https://deadbydaylight.wiki.gg/wiki/Map",
    "accessed": "2026-11-08",
}

# (name, realm, tiles_1v4, tiles_2v8, kind, extra)
MAPS: list[tuple[str, str, float | None, float | None, str, dict]] = [
    ("Coal Tower", "The MacMillan Estate", 132, 180, "outdoor", {}),
    ("Groaning Storehouse", "The MacMillan Estate", 156, None, "outdoor", {}),
    ("Ironworks of Misery", "The MacMillan Estate", 160, None, "outdoor", {}),
    (
        "Shelter Woods",
        "The MacMillan Estate",
        None,
        180,
        "outdoor",
        {"notes": "Source lists 176 tiles marked 2v8 but calls it the 1v4 size - 1v4 size unclear."},
    ),
    ("Suffocation Pit", "The MacMillan Estate", 160, 184, "outdoor", {}),
    (
        "Azarov's Resting Place",
        "Autohaven Wreckers",
        None,
        184,
        "outdoor",
        {"notes": "1v4 size unclear in source (176 listed as 2v8)."},
    ),
    ("Blood Lodge", "Autohaven Wreckers", 156, None, "outdoor", {}),
    ("Gas Heaven", "Autohaven Wreckers", 156, 184, "outdoor", {}),
    ("Wreckers' Yard", "Autohaven Wreckers", 144, 176, "outdoor", {}),
    ("Wretched Shop", "Autohaven Wreckers", 164, None, "outdoor", {}),
    ("Fractured Cowshed", "Coldwind Farm", 152, None, "outdoor", {}),
    ("Rancid Abattoir", "Coldwind Farm", 140, 192, "outdoor", {}),
    ("Rotten Fields", "Coldwind Farm", 160, None, "outdoor", {}),
    ("The Thompson House", "Coldwind Farm", 152, 180, "outdoor", {}),
    ("Torment Creek", "Coldwind Farm", 156, 180, "outdoor", {}),
    ("Disturbed Ward", "Crotus Prenn Asylum", 152, 188, "outdoor", {}),
    ("Father Campbell's Chapel", "Crotus Prenn Asylum", 140, 180, "outdoor", {}),
    (
        "The Pale Rose",
        "Backwater Swamp",
        161,
        None,
        "outdoor",
        {"notes": "Some border sections here are 16 m double walls (wiki Distances)."},
    ),
    ("Grim Pantry", "Backwater Swamp", 168, None, "outdoor", {}),
    ("Treatment Theatre", "Léry's Memorial Institute", 98, None, "indoor", {}),
    ("Mother's Dwelling", "Red Forest", 152, 200, "outdoor", {}),
    ("The Temple of Purgation", "Red Forest", 136, None, "outdoor", {}),
    ("Badham Preschool", "Springwood", 144, None, "outdoor", {}),
    ("The Game", "Gideon Meat Plant", 142, None, "indoor", {"floors": ["ground", "upper"]}),
    ("Family Residence", "Yamaoka Estate", 156, 184, "outdoor", {}),
    ("Sanctum of Wrath", "Yamaoka Estate", 156, 192, "outdoor", {}),
    ("Mount Ormond Resort", "Ormond", 156, 180, "outdoor", {}),
    ("Ormond Lake Mine", "Ormond", 132, None, "outdoor", {}),
    ("The Underground Complex", "Hawkins National Laboratory", 138, None, "indoor", {"floors": ["ground", "upper"]}),
    ("Dead Dawg Saloon", "Grave of Glenvale", 136, 180, "outdoor", {}),
    ("Midwich Elementary School", "Silent Hill", 113.5, None, "indoor", {"floors": ["ground", "upper"]}),
    (
        "Raccoon City Police Station",
        "Raccoon City",
        None,
        None,
        "indoor",
        {"modes": ["2v8"], "floors": ["ground", "upper"], "notes": "Full RPD is 2v8-only. Not measured (multi-floor)."},
    ),
    (
        "Raccoon City Police Station East Wing",
        "Raccoon City",
        None,
        None,
        "indoor",
        {"floors": ["ground", "upper"], "notes": "Not measured (multi-floor)."},
    ),
    (
        "Raccoon City Police Station West Wing",
        "Raccoon City",
        None,
        None,
        "indoor",
        {"floors": ["ground", "upper"], "notes": "Not measured (multi-floor)."},
    ),
    ("Dead Sands", "Forsaken Boneyard", 140, None, "outdoor", {}),
    ("Eyrie of Crows", "Forsaken Boneyard", 148, None, "outdoor", {}),
    ("Garden of Joy", "Withered Isle", 164, None, "outdoor", {}),
    ("Greenville Square", "Withered Isle", 160, None, "outdoor", {}),
    ("Freddy Fazbear's Pizza", "Withered Isle", 148, None, "outdoor", {}),
    (
        "Fallen Refuge",
        "Withered Isle",
        128,
        None,
        "outdoor",
        {
            "notes": "A 9.2.0 PTB bug briefly labelled this map 'Greenville Square' in the intro text and pause menu - OCR must not assume names are unique per patch."
        },
    ),
    ("The Shattered Square", "The Decimated Borgo", 144, None, "outdoor", {}),
    ("Forgotten Ruins", "The Decimated Borgo", 132, None, "outdoor", {}),
    ("Toba Landing", "Dvarka Deepwood", 136, None, "outdoor", {}),
    ("Nostromo Wreckage", "Dvarka Deepwood", 152, None, "outdoor", {}),
    (
        "Trickster's Delusion",
        "Sleepless District",
        None,
        None,
        "outdoor",
        {"notes": "Not in the wiki size table yet; kind unverified."},
    ),
    (
        "Hartshorne Mall",
        "The Subdivisions",
        None,
        None,
        "indoor",
        {
            "available": False,
            "floors": ["ground", "upper"],
            "notes": "PTB 10.3.0 map (Terrifier Chapter, expected 2026-11-24). Two floors; hatch can spawn on either; drop-downs to the Gym and Fountain tiles (community PTB notes). Flip available=true when live.",
        },
    ),
    (
        "Lampkin Lane",
        "Haddonfield",
        None,
        None,
        "outdoor",
        {
            "available": False,
            "notes": "Removed from the map pool in January 2026 (Halloween licence ended). Kept so old recordings resolve.",
        },
    ),
]


def slug(s: str) -> str:
    s = s.lower().replace("'", "").replace("é", "e").replace("&", "and")
    s = re.sub(r"^the\s+", "", s)
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def stub(name: str, realm: str, t1: float | None, t2: float | None, kind: str, extra: dict) -> dict:
    floors = extra.get("floors", ["ground"])
    modes = extra.get("modes", ["1v4", "2v8"] if t2 else ["1v4"])
    aliases = [name] + ([name[4:]] if name.startswith("The ") else [])
    d = {
        "schema_version": 1,
        "id": f"{slug(realm)}.{slug(name)}",
        "name": name,
        "realm": realm,
        "status": "stub",
        "available": extra.get("available", True),
        "kind": kind,
        "modes": modes,
        "aliases": aliases,
        "log_ids": [],
        "size_tiles_reported": t1,
        "size_tiles_2v8": t2,
        "floors": [{"id": f, "name": f.title()} for f in floors],
        "generators": [],
        "exit_gates": [],
        "hatches": [],
        "sources": [SRC_LIST, SRC_WIKI],
        "notes": extra.get("notes"),
    }
    return MapData.model_validate(d).model_dump(mode="json")


def test_arena() -> dict:
    W = H = 96.0
    gens = [
        ("g01", (12, 10), 2.5, False),
        ("g02", (48, 8), 2.5, False),
        ("g03", (84, 12), 2.5, False),
        ("g04", (10, 46), 2.5, False),
        ("g05", (42, 30), 1.0, True),
        ("g06", (60, 44), 1.0, False),
        ("g07", (86, 48), 2.5, False),
        ("g08", (26, 60), 2.5, False),
        ("g09", (48, 64), 1.0, False),
        ("g10", (70, 60), 2.5, False),
        ("g11", (14, 86), 2.5, False),
        ("g12", (50, 88), 2.5, False),
        ("g13", (84, 88), 2.5, False),
    ]
    gates = [
        ("e01", (24, 0.8), 0, "north"),
        ("e02", (72, 0.8), 0, "north"),
        ("e03", (95.2, 30), 90, "east"),
        ("e04", (36, 95.2), 180, "south"),
        ("e05", (84, 95.2), 180, "south"),
        ("e06", (0.8, 70), 270, "west"),
    ]

    def rect(x: float, y: float, w: float, h: float) -> list[list[float]]:
        return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]

    d = {
        "schema_version": 1,
        "id": "test_realm.test_arena",
        "name": "Test Arena",
        "realm": "DeadByMapper Test Realm",
        "status": "synthetic",
        "available": True,
        "kind": "outdoor",
        "modes": ["1v4"],
        "aliases": ["Test Arena", "Arena of Tests"],
        "log_ids": ["Tst_Arena"],
        "size_tiles_reported": 144,
        "width_m": W,
        "height_m": H,
        "bounds_polygon_m": [[0, 0], [W, 0], [W, H], [0, H]],
        "structures": [
            {
                "id": "main",
                "type": "main_building",
                "name": "Arena Hall",
                "polygon_m": rect(30, 36, 24, 16),
                "solid": True,
            },
            {"id": "shack", "type": "killer_shack", "name": "Shack", "polygon_m": rect(72, 70, 8, 8), "solid": True},
            {"id": "w1", "type": "wall", "polygon_m": rect(14, 20, 8, 0.6), "solid": True},
            {"id": "w2", "type": "wall", "polygon_m": rect(66, 14, 0.6, 12), "solid": True},
            {"id": "w3", "type": "wall", "polygon_m": rect(18, 66, 0.6, 14), "solid": True},
            {"id": "w4", "type": "wall", "polygon_m": rect(58, 86, 12, 0.6), "solid": True},
            {"id": "jg1", "type": "tile", "name": "Jungle gym", "polygon_m": rect(4, 28, 16, 16), "solid": False},
        ],
        "tile_slots": [
            {"id": "s_nw", "origin_m": [4, 28], "size_m": [16, 16], "allowed": ["jungle_gym_lt", "filler"]},
            {"id": "s_se", "origin_m": [76, 52], "size_m": [16, 16], "allowed": ["jungle_gym_lt", "shack", "filler"]},
        ],
        "generators": [
            {
                "id": gid,
                "pos_m": list(p),
                "sigma_m": s,
                "fixed": f,
                **({"slot": "s_nw"} if gid == "g04" else {}),
                **({"slot": "s_se"} if gid == "g07" else {}),
            }
            for gid, p, s, f in gens
        ],
        "exit_gates": [
            {"id": gid, "pos_m": list(p), "facing_deg": fd, "edge": e, "sigma_m": 1.0} for gid, p, fd, e in gates
        ],
        "hatches": [{"id": "h1", "pos_m": [30, 20]}, {"id": "h2", "pos_m": [70, 34]}, {"id": "h3", "pos_m": [40, 76]}],
        "survivor_spawns": [
            {"id": "sp1", "pos_m": [8, 30], "sigma_m": 3},
            {"id": "sp2", "pos_m": [88, 30], "sigma_m": 3},
            {"id": "sp3", "pos_m": [30, 90], "sigma_m": 3},
            {"id": "sp4", "pos_m": [62, 6], "sigma_m": 3},
        ],
        "sources": [{"title": "Synthetic map for tests and --demo. Not a real DBD map."}],
        "notes": "Synthetic 96x96 m arena (12x12 border-wall segments). Used by tests, the simulator and the UI demo.",
    }
    return MapData.model_validate(d).model_dump(mode="json")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-stubs", action="store_true", help="overwrite files that are still status=stub")
    args = ap.parse_args()
    index = []
    written = skipped = 0
    for name, realm, t1, t2, kind, extra in MAPS:
        data = stub(name, realm, t1, t2, kind, extra)
        path = MAPS_DIR / slug(realm) / f"{slug(name)}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("status") != "stub" or not args.force_stubs:
                skipped += 1
                data = existing
            else:
                write_json(path, data)
                written += 1
        else:
            write_json(path, data)
            written += 1
        index.append(
            {
                "id": data["id"],
                "name": data["name"],
                "realm": data["realm"],
                "status": data["status"],
                "available": data["available"],
                "path": str(path.relative_to(MAPS_DIR)).replace("\\", "/"),
            }
        )
    arena = MAPS_DIR / "_test" / "test_arena.json"
    if not arena.exists() or args.force_stubs:
        write_json(arena, test_arena())
    index.sort(key=lambda r: (r["realm"], r["name"]))
    write_json(
        MAPS_DIR / "_index.json",
        {"_doc": "Generated by tools/gen_map_stubs.py - do not edit by hand.", "count": len(index), "maps": index},
    )
    print(f"stubs written: {written}, kept existing: {skipped}, index entries: {len(index)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
