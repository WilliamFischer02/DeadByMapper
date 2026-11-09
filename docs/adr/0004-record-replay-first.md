# ADR 0004 - Event bus + record/replay first

**Status:** accepted (M0) · **Date:** 2026-11-08

## Context
The agents building this can't run the game, and the owner can only occasionally play test
sessions. Bugs in perception and localization have to be reproducible and benchmarkable offline.

## Decision
- Every sensor publishes small, typed, frozen events with `ts_ns` from one monotonic clock onto an
  `EventBus`.
- Large data (frames, audio blocks) stays out of the bus: frames go to a latest-frame mailbox, audio
  goes to callbacks.
- `--record` writes every event to JSONL.
- `--replay` and the test harness feed recordings back through the same pipeline with `ManualClock`
  and a synchronous bus.
- A simulator generates the same event types with ground truth (`--demo`, the bench).

## Alternatives considered
- **Direct method calls between modules.** Faster to write, but impossible to record or replay,
  and the lanes would be tightly coupled.
- **A full message broker / multiprocess design from day one.** Unnecessary at this scale. The
  event contracts allow moving perception into a worker process later.

## Consequences
- Contract discipline: event changes go through the lead, need defaults on new fields, and need a
  schema-version bump on renames.
- One recorded session serves several lanes.
- Offline regression tests become possible as soon as the user records sessions.
