# UI design - glance philosophy (this document is law for `ui/`)

## The glance contract
The player is in a chase, a loop, or a 45-second repair. The pane gets **one look of under 1 second**,
from peripheral vision. Every pixel has to earn its place by answering one of three questions:

1. **Where am I?** The dot, the heading cone, and the halo, which shows certainty.
2. **Where next?** The ringed generator, the gens-left number, and the gates.
3. **How close is the killer?** A red band around *you*. You cannot see the killer; you can only see
   how far away they could be.

Anything else lives in the dashboard, which you read when you are not playing.

### Rules
- **Pre-attentive first.** Meaning is carried by shape, then colour, then motion. Text comes last and
  is never needed mid-chase.
- **One number, one line.** The pane shows a single number (gens left) and a single hint line. It
  never shows a second number, a paragraph, a dialog, a toast or a modal.
- **Never steals focus.** The pane is Tool + frameless + always-on-top + `WindowDoesNotAcceptFocus`.
  It is click-through by default, and interaction is opt-in (a hold-modifier unlock in M7).
- **Uncertainty is spatial.** The halo radius equals sigma, ghost rings show the alternative
  hypotheses, and a breathing pip means "still locating". The pane never shows a confidence
  percentage.
- **Calm by default; motion means "act now".** Only three things animate:
  - the rank-1 generator ring breathes (1.4 s);
  - a regressing gen pulses (1.1 s);
  - the terror band beats at the measured bpm.
  Nothing else moves.
- **Honest states.** BREADCRUMB, NO FIX, LOCATING and LOCKED look different. A "locked-looking" dot
  that is wrong is the worst possible bug (the false-lock rate is benchmarked).
- **Player-facing words, never ids.** Write "breaks the tightest 3-gen", not "g07 rank 1".

## Marker language (shape AND colour, so it works for colour-blind players)
| Thing | Shape | Colour token | States |
|---|---|---|---|
| You | solid 12 px dot, 2 px dark outline, 60-degree heading cone | `player` | halo = sigma; cone only when heading is known |
| Generator | 12 px diamond | `gen` orange | candidate = outline only; spawned = filled; completed = `genDone` green + tick; regressing = `genRegress` vermilion + pulse; rank-1 = breathing ring |
| Exit gate | 15x4 px bar on the map edge, along the wall | `gate` sky blue | candidate = faint; known = solid + outline |
| Hatch | ring | `hatch` purple | M8 |
| Killer distance | annulus around you, from inner to outer radius of the heartbeat tier | `danger` red | chase = solid band; otherwise dashed/translucent; beats at bpm |
| Alternative positions | thin rings, tap to confirm | `player` at 35 % | only while SEARCHING/CONVERGING |
| Trail | 1.5 px polyline, last ~40 s | `trail` | drawn only when CONVERGING/LOCKED |
| Structures | filled polygons | `structFill`/`structStroke` | solid = blocks movement |
| Grid | one square per 8 m border-wall segment | `grid` | the game's own distance unit |

The palette is Okabe-Ito based. Keep the map layer desaturated (`mapFill`, `structFill`) so the
semantic markers pop. Tokens live in `ui/theme.py` only, and QML never hard-codes a colour.

## Pane layout (380 x 452 logical px, top-right by default, draggable)
```
┌──────────────────────────────────────┐
│ COAL TOWER                    ◆ 5    │  header: map name (caps, 12 px DemiBold), status pip + word, gens left
│ ● LOCKED                             │
│┌────────────────────────────────────┐│
││            map canvas              ││  north-up, metres, fit to bbox, 8 m grid
││   ◆        ◇          ▬            ││
││        ◎◆ (next)    ·             ││
││    ◆          ◉→  ((killer band))  ││
│└────────────────────────────────────┘│
│ Next gen ringed — breaks the tightest 3-gen │  ONE line of guidance (hint())
└──────────────────────────────────────┘
```
- Opacity 92 % on `bgPane`, with no blur and no acrylic. Blur makes text fuzzy, and crisp is a rule.
- Corner radius 14 px; 1 px hairline border.
- Header and footer text sizes are 12 px and 11 px minimum. This assumes 100 % scale, and Windows
  scaling applies on top.

## Dashboard (outside trials)
A nav rail, then cards in this order:
1. **Game link** hero: status chips for Process, Window, Log, Input, Capture and Audio, each
   ok/warn/down/off with a one-line detail.
2. **Calibration**: mouse deg/count, keybinds, HUD scale.
3. **Map library**: maps with their status badges (stub/partial/verified).
4. **Generator plan**: the post-match route review, plus the strategy explainer.
5. Footer: the fair-play statement plus a link to docs/FAIR_PLAY.md.

The dashboard may be denser, but still uses the same tokens and type ramp:

| Element | Size |
|---|---|
| Display title | 22 |
| Card title | 15 |
| Body | 13 |
| Caption | 11 |

## Crispness checklist (run before every UI merge; screenshots in qa/screens/)
- [ ] Renders at 100 %, 150 % and 200 % scale (`scripts/qa_screens.ps1`, or
      `--screenshot` with `QT_SCALE_FACTOR`).
- [ ] No blurry text: native text rendering, integer font pixel sizes, and
      `HighDpiScaleFactorRoundingPolicy.PassThrough` with pixel-snapped positions.
- [ ] Thin strokes: 1 px lines sit on half-pixel centres (`crisp()` helper in MapCanvas), and shapes
      use CurveRenderer.
- [ ] No raster icons scaled up. Icons are SVG or shapes, and `app.ico` is rendered per size by
      `tools/make_icon.py` (16-256 px, each size rendered natively).
- [ ] Zero QML warnings in the log (tests/test_ui_smoke.py enforces this).
- [ ] Contrast:
      - text on `bgPane` is at least 7:1 for `text` and at least 4.5:1 for `textDim`;
      - every marker colour is at least 3:1 against `mapFill`.
- [ ] Colour-blind check:
      - simulate deuteranopia, protanopia and tritanopia on the screenshot;
      - every state is still distinguishable by shape.
- [ ] 60 fps: QML profiler shows no binding loops and no per-frame JS allocation in MapCanvas.
- [ ] No AI-generated-looking art: no gradients-for-the-sake-of-it, no glow soup, no random
      decorative blobs, no stock illustrations. Flat, functional, aligned to a 4 px grid.

## Interaction (M7)
- **Tap** the map: "I am here" (`UserFix`). **Tap a ghost ring**: confirm that hypothesis.
- **Drag from the dot**: heading fix. **Long-press a generator**: mark it completed or not spawned.
- **Hold Alt over the pane** to make it interactive while click-through is on (configurable).
  Release to return to click-through.
- **Hotkeys work without touching the pane:**
  - F9 = I'm at a generator;
  - F10 = I'm at an exit gate;
  - F8 = note this moment.

## Visual QA workflow for agents
```
uv run python -m deadbymapper --demo --screenshot qa/screens/<name> --screenshot-delay 45
```
This writes `pane.png` and `dashboard.png`. Look at them with your image tool, compare them against
docs/design/*.png (the baseline), and list every pixel-level issue you see before you claim a UI
task is done. Re-render at `QT_SCALE_FACTOR=1.5` and `2`.
