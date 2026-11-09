# Test fixtures

Small, deterministic inputs for tests. Fixture policy (docs/FAIR_PLAY.md, docs/TESTING.md):

| Folder | What goes here |
|---|---|
| `logs/` | Short excerpts of `DeadByDaylight.log`. Only the lines a rule needs, with names and ids redacted. |
| `ini/` | Minimal `GameUserSettings.ini` / `Input.ini` samples for the config reader. |
| `maps/` | Extra synthetic maps for edge cases (never real community map art). |

Before adding anything derived from a user recording:
- the user must approve it;
- it must contain no other players' names and no third-party images;
- keep it as small as possible.
