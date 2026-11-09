# ADR 0002 - Strictly external, read-only sensing (fair play)

**Status:** accepted (M0) · **Date:** 2026-11-08

## Context
DBD uses Easy Anti-Cheat. BHVR's rules forbid third-party software that gives an unfair advantage.
Memory reading would make localization trivial, and it would also get the owner banned and break
the trust model. The owner wants a learning tool: learn loops, learn generator spacing.

## Decision
Observe only what the player already perceives:
- their own screen pixels (OS capture);
- the audio they hear (WASAPI loopback);
- their own keyboard and mouse (Raw Input, gated: `input/privacy.py`);
- the log and config files DBD writes (shared read).

Never:
- read or write memory, inject, hook DirectX, sniff packets, or modify files;
- synthesize input;
- show information the player could not perceive.

The full table is in docs/FAIR_PLAY.md, and the `fair-play-reviewer` agent audits every milestone.

## Alternatives considered
- **Memory reading (exact position).** Rejected: ban risk and unfair advantage.
- **Packet inspection.** Rejected: same reasons, plus encryption.
- **Pure manual tracking (user clicks their position).** Kept only as an assist (tap to fix),
  because it is not glanceable on its own.

## Consequences
Position has to be *estimated* (ADR 0003), map data has to be authored (ADR 0005), and every
feature carries a "can the player perceive this?" check. The ban risk is never zero; the README
says so.
