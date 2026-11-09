# Localization - how the dot knows where you are

## The problem
DBD exposes no position to outside programs (and we will not read memory). What we have:
1. **Your inputs** (Raw Input): WASD/sprint/crouch keys and raw mouse counts - the same counts the game
   receives.
2. **Speeds** (datamined): run 4.0, walk 2.26, crouch 1.13, crawl 0.7-1.05 m/s.
3. **Your screen**: whether the camera is actually translating (optical flow), and when you
   *interact* with known objects (generators, exit gates).
4. **The map**: walkable area (bounds minus solid structures) and the *possible* spots of generators
   and exit gates.
Unknowns at spawn: where you are AND which way the camera faces in map terms. Mouse counts only give
*relative* yaw.

## Motion model (dead reckoning)
Survivors move relative to the camera. With yaw_rel = counts x deg_per_count:

    world heading = psi + k * yaw_rel + intent(WASD)        (degrees, clockwise from map-up)
    displacement  = s * speed(gait) * dt * [moving]

- `psi` = unknown offset between camera yaw and map north (estimated);
- `k` ~ 1 = yaw-scale correction absorbing calibration error;
- `s` ~ 1 = speed-scale correction absorbing haste/hindered;
- gait from keys + perceived status: LOCKED while interacting/hooked/in a locker, CRAWL when downed,
  CARRIED = unknown direction (persistent random heading per particle), IDLE with no keys.
- **Stuck gate:** optical flow publishes `translating_prob`; below 0.35 nobody moves (holding W into a
  wall). It is a shared decision, never a per-particle coin flip (that biases every particle's speed).
- Input only counts while the camera is controlled (`InputFocusChanged`: window focus, menu overlays).

## Particle filter (Monte Carlo Localization)
State per particle: (x, y, psi, k, s, log-weight); 5000 particles; numpy-vectorised.
- **Predict** each Tick (60 Hz) with diffusion on psi (1.5 deg/sqrt s), k, s and position.
- **Collisions:** moves into solids/out of bounds slide along walls; the lost distance costs
  log-weight (3 per metre, x translating_prob) - wrong hypotheses walking through buildings die.
- **Landmark update** on `InteractionObserved(generator|exit_gate, active=True)`: mixture of Gaussians
  over the candidate spots (sigma_eff^2 = candidate sigma^2 + ~1 m standoff^2) plus a small floor so a
  missing/mis-authored candidate cannot annihilate the truth.
- **Resample** (systematic) when ESS < 50 %, with small roughening.
- **Estimate:** clusters of particles; report the heaviest cluster, with hysteresis (keep the last
  reported cluster while it holds >= 70 % of the best's mass) so the dot does not flicker between
  hypotheses. Other clusters become tappable "ghost" rings in the UI.

## Global re-localization: PathMatcher (the clever part)
At the first landmark the unknowns are ~13 candidates x 360 degrees - random particles cover that
thinly. So at every landmark event we exhaustively score each (candidate k, psi) on a 1-degree grid:
1. the **breadcrumb** (dead-reckoned relative path, last 90 s), rotated by psi and anchored at k, must
   stay in walkable space;
2. every **earlier landmark event** on that path must land near a candidate of its kind (tolerance grows
   5 % of distance travelled since - dead-reckoning drift).
The posterior over (k, psi) seeds the filter at the first fix, and refreshes 15 % of the population at
later fixes (60-80 % if the filter disagrees or the fix is inconsistent: "sensor resetting"). Cost:
~10-30 ms per landmark, nothing between landmarks.

## User assists (one tap, no typing)
- Tap the map = `UserFix` (2-3 m): the matcher solves the heading from your breadcrumb.
- Tap a ghost ring = confirm that hypothesis.
- (M7) drag from the dot = `UserHeadingFix`.

## Status shown to the player
| Status | Meaning | Pane |
|---|---|---|
| NO_MAP | map unknown / no layout data | breadcrumb trail around a centred dot |
| NO_FIX | map known, no landmark yet | breadcrumb + "repair a generator or tap your spot" |
| SEARCHING | multimodal | dot + halo + ghost rings |
| CONVERGING | best cluster >= 40 % mass, sigma <= 6 m | dot + halo, trail |
| LOCKED | >= 75 % mass and sigma <= 3 m | crisp dot + heading cone + trail |
| LOST | was locked, sigma > 12 m | halo grows, next landmark re-locks |

## Simulator benchmark (tools/bench_localization.py, 30 unseen seeds, Test Arena, 1.5 % yaw error)
| After landmark fix | 1 | 2 | 3 | 4 |
|---|---|---|---|---|
| Within 3 m | 13 % | 17 % | 77 % | 93 % |
Final error median 1.13 m (p90 1.61 m). **False-LOCKED: 0.00 %** (never "locked" while > 5 m off).
Cost 1.15 ms per 60 Hz tick. Reality will be harder (imperfect prompts, haste, vault lunges, wall
hugging, real map data errors) - M4 tunes on recorded sessions.

## Calibration (deg per count)
DBD's sensitivity scaling is not stock UE, so we measure it: a slow 360-degree spin while standing
still; the frame that matches the start view again marks counts-per-360 (input/calibration.py).
Continuous refinement: regress optical-flow yaw rate against count rate during play. Residual error is
absorbed by `k`.

## Known limitations / roadmap
- Fixed-lag smoother: associate interactions that happened before lock (retro-mark generators).
- Floors (multi-level maps): stairs/drop detection + per-floor occupancy.
- Visual landmarks (M8): detected gens/gates/pallets as bearing measurements (ObjectSighted).
- Vault/pallet lunges, on-hit sprint (6.6 m/s for 1.8 s) as perceived events -> speed bursts.
- Killer mode (first-person, 4.4/4.6 m/s) - same machinery, different motion model.
- KLD-adaptive particle count; bench on real map data once M5 authors maps.
