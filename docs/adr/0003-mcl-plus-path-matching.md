# ADR 0003 - Monte Carlo Localization + global path matching for the player's dot

**Status:** accepted (M0) · **Date:** 2026-11-08

## Context
With no memory access, the player's position must come from:
- inputs: WASD intent, sprint/crouch gait, raw mouse yaw;
- perceived motion (optical flow: moving or stuck);
- sparse landmarks (interacting with a generator or exit gate whose *possible* spots are known);
- optional user taps.

At spawn, both the position and the camera-yaw offset (psi) are unknown. Dead reckoning drifts.

## Decision
A particle filter over (x, y, psi, k_yaw, s_speed):
- predict with a camera-relative motion model, wall sliding plus a collision penalty, and a shared
  "stuck" gate from optical flow;
- landmark updates use a mixture of Gaussians over candidate spots;
- the estimate is the heaviest cluster, with hysteresis.

It is paired with a **PathMatcher**. At every landmark, the matcher exhaustively scores every
(candidate, psi) by whether the dead-reckoned breadcrumb stays in walkable space and past landmarks
line up. It seeds the filter at the first fix and re-injects particles later (sensor resetting).
Status levels make uncertainty honest: NO_FIX, SEARCHING, CONVERGING, LOCKED, LOST.

## Alternatives considered
- **Pure dead reckoning.** Drifts without bound, and the heading offset is unknown.
- **Kalman / EKF.** Unimodal, so it cannot represent "one of 13 generators" ambiguity.
- **Visual SLAM / place recognition from frames.** Heavy, fragile in DBD's fog and darkness, and a
  dataset problem. Kept as a future cue (M8 `ObjectSighted`).
- **Grid/histogram filter.** Fine for x and y, but psi, k and s blow up the state space.

## Consequences
- Simulator benchmark (30 seeds, 4 generator visits, 1.5 % yaw error):
  - within 3 m after the 3rd fix: 77 %; after the 4th: 93 %;
  - false-LOCKED: 0 %;
  - cost: 1.15 ms per tick.
- The real-world numbers depend on M3 perception and M4 map data, and are tuned on recordings in
  M5.
- The UI must show ghost rings and the halo instead of pretending certainty.
