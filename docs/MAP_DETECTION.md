# Detecting the map (live, on load-in)

Layered sources, fused by `perception/map_identifier.py`. Strongest first:

| # | Source | When | How | Status |
|---|---|---|---|---|
| 1 | **Manual** | any time | tap the map in the dashboard / confirm chip on the pane | done (UI M7 polish) |
| 2 | **Log** | loading | VERIFIED regex rule in `data/game/log_rules.json` -> token -> map `log_ids` | rules unknown: M3 |
| 3 | **Intro title OCR** | first seconds of the trial | the game prints the map name as the trial starts; OCR the calibrated `intro_map_title` ROI at 4 Hz for ~6 s | M3 |
| 4 | **Menu OCR** | player opens pause/TAB menu | the menu shows the map name; OCR `menu_map_title` when the state classifier sees the overlay | M3 |
| 5 | Visual classifier | fallback | per-map image embedding (realm lighting, landmarks) | future |

Decision rules: manual always wins and sticks for the trial; a verified log rule (conf >= 0.9) wins;
OCR needs 2 agreeing reads >= 0.85 (or one >= 0.97); two maps within 5 points -> `MapAmbiguous` ->
one-tap chip on the pane. Identification resets on LOBBY/MENU.

Fuzzy matching (`MapRegistry.match_text`): accent/case/punctuation-insensitive, leading "the" dropped,
common OCR digit confusions fixed (0->o, 1->l, 5->s), rapidfuzz scoring against every alias. Test cases
in tests/test_maps.py. Aliases for other languages are harvested by the user playing one trial per
language with `--record` (or from the wiki's localised pages - verify against the game).

## Log probing workflow (M3)
1. Play one trial on a known map. Copy `%LOCALAPPDATA%\DeadByDaylight\Saved\Logs\DeadByDaylight.log`.
2. `uv run python tools/log_probe.py <copy> --grep "<map name or Prefix_Name token>"`.
3. Find a line that appears at load and names the map (wiki icon files use `Prefix_MapName` tokens such
   as Hos_Treatment, so look for that shape). Write a rule with a named group `token`.
4. Add the token to the map's `log_ids`, mark the rule `verified: true` with `verified_patch`, add a
   one-line fixture test. Repeat for a second map to confirm the pattern generalises.
5. **Never** add rules for lobby information the player cannot see (killer, perks, add-ons, offerings
   before reveal). See docs/FAIR_PLAY.md.

## Match state (needed to know WHEN to look)
`perception/state_machine.py` fuses process presence, `ScreenObserved` (classifier) and verified log
events with hysteresis. Screen classifier plan (M3): HUD templates (survivor portraits + generator
counter bottom-left; perk diamonds bottom-right) captured from the user's own screenshots at their
resolution and HUD scale; loading bar/tips band; tally panels; pause/TAB overlay.
