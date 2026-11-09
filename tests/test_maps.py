from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from deadbymapper.world import gen_strategy
from deadbymapper.world.map_model import MapData
from deadbymapper.world.map_registry import normalize_name

ROOT = Path(__file__).resolve().parent.parent


def test_all_map_files_validate(registry):
    assert len(registry) >= 46  # 45 live + PTB/removed + synthetic test arena
    live = registry.available()
    assert len(live) >= 44
    names = {m.name for m in live}
    required_maps = (
        "Coal Tower",
        "The Game",
        "Midwich Elementary School",
        "Nostromo Wreckage",
        "Freddy Fazbear's Pizza",
    )
    for required in required_maps:
        assert required in names


def test_index_matches_files(registry):
    index = json.loads((ROOT / "data/maps/_index.json").read_text(encoding="utf-8"))
    ids = {m["id"] for m in index["maps"]}
    real_ids = {m.id for m in registry.all() if m.status != "synthetic"}
    assert ids == real_ids


def test_schema_is_generated_from_model():
    schema_path = ROOT / "data/schemas/map.schema.json"
    on_disk = json.loads(schema_path.read_text(encoding="utf-8"))
    on_disk.pop("$schema", None)
    assert on_disk == json.loads(json.dumps(MapData.model_json_schema())), (
        "map.schema.json is stale - run: uv run python tools/gen_schemas.py"
    )


def test_arena_geometry(arena):
    assert arena.has_geometry and arena.width_m == 96
    assert len(arena.generators) >= 7 and len(arena.exit_gates) >= 2


def test_verified_maps_need_enough_candidates(arena):
    data = arena.model_dump(mode="json")
    data["status"] = "verified"
    data["generators"] = data["generators"][:5]
    with pytest.raises(ValidationError):
        MapData.model_validate(data)


def test_candidate_outside_bbox_rejected(arena):
    data = arena.model_dump(mode="json")
    data["generators"][0]["pos_m"] = [500, 500]
    with pytest.raises(ValidationError):
        MapData.model_validate(data)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("COAL TOWER", "macmillan_estate.coal_tower"),
        ("C0AL T0WER", "macmillan_estate.coal_tower"),
        ("the game", "gideon_meat_plant.game"),
        ("Midwich Elementary Schoo", "silent_hill.midwich_elementary_school"),
        ("FREDDY FAZBEARS PIZZA", "withered_isle.freddy_fazbears_pizza"),
        ("Treatment Theatre", "lerys_memorial_institute.treatment_theatre"),
    ],
)
def test_fuzzy_name_matching(registry, text, expected):
    best = registry.match_text(text)
    assert best and best[0].map_id == expected, best


def test_normalize_name():
    assert normalize_name("Léry's Memorial Institute") == "lerys memorial institute"
    assert normalize_name("The Pale Rose") == "pale rose"


def test_log_token_lookup(registry):
    assert registry.by_log_token("tst_arena") == "test_realm.test_arena"
    assert registry.by_log_token("nope") is None


# --------------------------------------------------------------------------- strategy
def test_three_gen_detection_prefers_breaking_the_cluster():
    gens = [("a", 40, 40), ("b", 50, 44), ("c", 44, 54), ("far1", 5, 5), ("far2", 90, 8), ("far3", 88, 90)]
    rep = gen_strategy.analyse(gens, 96, 96)
    assert rep.tightest is not None and set(rep.tightest.ids) == {"a", "b", "c"}
    top3 = {s.gen_id for s in rep.ranking[:3]}
    assert top3 == {"a", "b", "c"}
    assert all(s.gen_id.startswith("far") for s in rep.ranking[3:])
    assert "edge" in " ".join(rep.ranking[-1].reasons)


def test_strategy_with_two_gens_left():
    rep = gen_strategy.analyse([("a", 1, 1), ("b", 2, 2)])
    assert rep.tightest is None and len(rep.ranking) == 2
