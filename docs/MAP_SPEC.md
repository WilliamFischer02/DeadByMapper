# Map data specification and authoring

Source of truth: `src/deadbymapper/world/map_model.py` (pydantic). Generated schema:
`data/schemas/map.schema.json`. One file per map: `data/maps/<realm_slug>/<map_slug>.json`.
Synthetic test map: `data/maps/_test/test_arena.json`. Index: `data/maps/_index.json` (generated).

## Coordinate system
- Metres. Origin = top-left of the map's bounding box. +x right (east), +y DOWN (south). North-up.
- Headings: degrees, 0 = up, clockwise. Exit-gate `facing_deg` = outward normal.
- Scale reference in any overhead image: border-wall posts are 8 m apart (16 m double sections on some
  maps). The 8 m grid drawn on the pane = one wall segment.

## Status ladder
| status | Requirements | Localizer | UI |
|---|---|---|---|
| `stub` | name, realm, aliases, sources | breadcrumb only | "No layout data yet" |
| `partial` | + width/height + bounds polygon (+ any candidates) | works with what exists | normal, map chip amber |
| `verified` | + >= 7 generator candidates (1v4) and >= 2 exit gates, checked in game, `verified_patch` set | full | map chip green |
| `synthetic` | test-only geometry | full | DEMO |

## Fields that matter most
- `bounds_polygon_m`: the playable outline (inside the border walls).
- `structures[]`: polygons; `solid: true` blocks movement in the occupancy grid (building exterior
  walls, rock walls, long walls). Use several polygons for buildings with doorways rather than one
  solid block if survivors can walk through.
- `generators[]`: every POSSIBLE spot. `sigma_m` = how precisely you know it (1 m for fixed main-building
  spots, 3-6 m for tile-dependent spots whose tile rotates). `fixed: true` if it always spawns.
  `slot` links tile-dependent spots to a `tile_slots[]` entry.
- `exit_gates[]`: possible gate spots on the edge with `facing_deg` and `edge`.
- `hatches[]`, `survivor_spawns[]`: optional priors.
- `aliases[]`: the map name exactly as the game prints it in every language you play in (OCR matches
  these). `log_ids[]`: tokens the log uses for this map (filled after M3 log probing).
- `floors[]`: `ground` plus e.g. `upper`; every candidate/structure names its floor.
- `overlay`: personal reference image name + pixel->metre affine (never committed image).

## Tile type ids (for `tile_slots[].allowed`, extend as needed)
`jungle_gym_lt` (L/T walls), `jungle_gym_long`, `jungle_gym_short`, `four_lane`, `shack`, `filler`,
`hill`, `main_building`, `realm_unique:<name>`. These feed the M8 "tile scan" feature (stamping a
recognised tile's walls/windows/pallet into its slot).

## Authoring workflow (M5 map editor; until then, edit JSON by hand)
1. Get a top-down reference you are allowed to use for personal reference (your own screenshots from a
   custom game, or a community image saved to your overlays folder).
2. Load it in the editor; click two border posts 8 m apart -> scale; click two known landmarks
   (e.g. main-building corners) -> rotation/offset. The editor stores `overlay.px_to_m`.
3. Trace `bounds_polygon_m`, solid structures, main building, killer shack.
4. Place every possible generator spot (with sigma) and exit-gate spot.
5. Set `status: "partial"`, run `uv run python tools/validate_maps.py --verbose`.
6. **Verify in game**: custom game on that map, run with `--record`, visit gens/gates. The localizer
   locks; the editor's "mark here" (when LOCKED) shows offsets between authored and observed spots.
   Fix, then set `status: "verified"` and `verified_patch`.
7. Commit the JSON only (never the image).

## Per-patch checklist
- New chapter map? Add it to `tools/gen_map_stubs.py` MAPS and run the tool.
- Map reworked (BHVR patch notes "map update")? Drop status to `partial`, re-verify.
- Map removed from the pool? `available: false` (keep the file for old recordings).
- Renamed map or intro text changes? Update `aliases`.
