# Testing strategy

## Layers
| Layer | What | Where | Runs in CI |
|---|---|---|---|
| Pure unit tests | geometry, events round-trip, keymap, privacy gate, calibration maths, DSP, map registry and fuzzy matching, generator strategy | `tests/test_*.py` | yes (Windows + Ubuntu) |
| Simulation tests | localizer convergence on the Test Arena (fixed seeds), user tap, breadcrumb, stuck gate, interaction lock, mark dedupe | `tests/test_localization.py` | yes |
| GUI smoke | QML loads with zero warnings; dashboard and pane visible in demo mode (offscreen, software backend) | `tests/test_ui_smoke.py` (marker `gui`) | yes |
| UI law | no colour literals in QML; every `theme.*` token exists | `tests/test_ui_tokens.py` | yes |
| Windows-only | Win32 and WGC paths (marker `windows_only`) | per lane | Windows runner |
| Needs game | anything that needs DBD running (marker `needs_game`, opt-in with `DBM_GAME_TESTS=1`) | per lane | never in CI |
| Replay tests | recorded sessions replayed through the pipeline with `ManualClock` (M3+) | `tests/replays/` (user-approved, small) | yes, once fixtures exist |
| Benchmarks | `tools/bench_localization.py`; perception benches per lane (M3) | `tools/` | no; numbers go in reports |

## Rules
- **Deterministic.** Use a synchronous `EventBus(synchronous=True)` plus `ManualClock`, and seed
  every RNG. Never assert on wall-clock timing.
- **Write tests first** for new pure logic. Every bug fix comes with a regression test. Name the
  test after the behaviour, e.g. `test_reconfirming_the_same_map_keeps_the_localizer_estimate`.
- **Fakes over mocks.** Inject `procs=` into ProcessWatcher, use a `foreground_hwnd` callable for
  InputGate, use SyntheticFrameSource for capture, and use synthetic audio for the DSP.
- **Isolation.** `conftest.py` points `DBM_USER_DIR` and `DBM_DBD_SAVED_DIR` at tmp dirs, so tests
  never touch the real profile or real game files.
- **No third-party assets.** Fixtures are synthetic, or small crops the user approved
  (docs/FAIR_PLAY.md).
- **Localization changes:** run the bench before and after, and paste both tables. A false-LOCKED
  rate above 0 % in the sim blocks the merge.

## Commands
```
uv run pytest -q                       # everything that can run here
uv run pytest -q -m "not gui"          # skip the Qt test (e.g. headless box without Qt libs)
uv run pytest -q tests/test_localization.py -k converges
DBM_GAME_TESTS=1 uv run pytest -q -m needs_game     # with DBD running (user machine)
uv run python tools/bench_localization.py --seeds 30 --visits 4
```
