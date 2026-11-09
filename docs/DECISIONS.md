# DECISIONS - lightweight decision log (newest first)

Big, long-lived choices get an ADR in `docs/adr/`. Everything else gets one entry here:
- **what** was decided;
- **why**;
- **alternatives** considered;
- **revisit when** (the condition that would reopen it).

| Date | Decision | Why | Alternatives | Revisit when |
|---|---|---|---|---|
| 2026-11-08 | Map loads are idempotent: re-identifying or confirming the SAME map mid-trial keeps the estimate, gen states and trail. New trials still reset through `MatchStateChanged(IN_MATCH)`. | Confirming the map from the ambiguity chip used to wipe the particle filter and the gen states. | Reset on every identification. | Multi-floor maps need a per-floor reload. |
| 2026-11-08 | Hotkey marks (F9 generator / F10 exit gate / F8 note) are both landmark fixes AND ground-truth labels. The localizer de-dupes a mark and a perceived interaction of the same kind within 3 s. | Gives a working live minimap before vision exists (M1-M3), and labels recordings for M3 evaluation for free. | Labels only; separate labelling tool. | When prompt detection hits at least 95 % recall, consider making marks labels-only by default. |
| 2026-11-08 | Raw Input is privacy-gated at the source (`InputGate`): only `KeyMap.allowed_vks()` and only while the DBD window is foreground. Mouse is gated the same way, re-checked every 20 ms or less. | INPUTSINK is system-wide, so without the gate `--record` would have logged keystrokes typed in other apps. | Filter at the recorder; filter by focus event only (250 ms lag). | Never widen it without the user's explicit OK. |
| 2026-11-08 | Audio resampling is stateful (`MonoResampler16k`, triangle anti-alias, carried phase). | Per-block resampling glitched at the callback rate, and the old comment claimed anti-aliasing that did not exist. | soxr / scipy dependency. | If CPU or quality are insufficient, use soxr. |
| 2026-11-08 | QML may not contain colour literals. All colours, including alpha variants, are tokens in `ui/theme.py`, and this is enforced by `tests/test_ui_tokens.py`. | One source of truth for theming and colour-blind review. | A convention without a test. | - |
| 2026-11-08 | Roadmap order: map editor + verified maps (M4) **before** localization tuning on real data (M5). | Tuning needs real map geometry. | Tune on the Test Arena only. | - |
| 2026-11-08 | Estimate = heaviest particle cluster, with hysteresis: keep reporting the cluster within 5 m of the last reported position while it still holds at least 70 % of the heaviest cluster's mass. | The densest-bin estimate flickered between hypotheses, which a glance UI cannot use. | MAP bin; weighted mean (it averages two modes into a wall). | - |
| 2026-11-08 | "Stuck" (holding W into a wall) is ONE shared gate from optical flow, not per-particle coin flips. | Per-particle Bernoulli movement biases the effective speed and spreads the cloud. | Soft weighting. | When flow quality is measured on real frames (M3). |
| 2026-11-08 | Trail is drawn from the breadcrumb, transformed through the current hypothesis, and only when CONVERGING/LOCKED. Jumps over 8 m clear it. | Raw estimate history looked like spaghetti whenever the hypothesis switched. | Smooth the estimate history. | - |
| 2026-11-08 | "Near vs far generators" = generator spacing (3-gen avoidance), not distance to exit gates. The strategy flags the tightest triple (pairwise max of 48 m or less) and edge gens. | This matches how the community teaches gen efficiency. Exit gates only matter at endgame. | Gate-distance heuristic. | A user preference for alternate strategies. |
| 2026-11-08 | Python + PySide6/QML; event bus + record/replay; MCL + PathMatcher; external read-only sensing; authored map data. | See ADRs 0001-0005. | See ADRs. | See ADRs. |
