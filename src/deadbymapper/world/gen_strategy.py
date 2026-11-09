"""Generator strategy analysis - the 'which gen next?' brain.

Community consensus (docs/RESEARCH.md): the main survivor macro mistake is leaving a *3-gen*
- three remaining generators close enough together for the killer to patrol. Advice: work the
gens that could form the tightest cluster early (often the middle of the map), leave isolated
corner/edge gens for last. This module turns that into explainable, glanceable suggestions.
It only uses generator positions the player has legitimately learned (public spawn-location
data + what they have seen/done); it never infers hidden state.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

GenPos = tuple[str, float, float]  # (id, x, y)

TIGHT_TRIPLE_M = 48.0  # max pairwise distance for a triple to count as a defensible 3-gen
EDGE_MARGIN_M = 16.0


@dataclass(frozen=True, slots=True)
class Triple:
    ids: tuple[str, str, str]
    max_pair_m: float
    perimeter_m: float


@dataclass(frozen=True, slots=True)
class Suggestion:
    gen_id: str
    score: float
    reasons: tuple[str, ...]  # player-facing, no ids (shown on the pane/dashboard)
    partners: tuple[str, ...] = ()  # gens it would leave/break a 3-gen with (for the review screen)


@dataclass(frozen=True, slots=True)
class StrategyReport:
    tightest: Triple | None
    tight_triples: tuple[Triple, ...]
    ranking: tuple[Suggestion, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


def _d(a: GenPos, b: GenPos) -> float:
    return math.hypot(a[1] - b[1], a[2] - b[2])


def triples(gens: list[GenPos]) -> list[Triple]:
    out: list[Triple] = []
    for a, b, c in itertools.combinations(gens, 3):
        ab, bc, ca = _d(a, b), _d(b, c), _d(c, a)
        out.append(Triple((a[0], b[0], c[0]), max(ab, bc, ca), ab + bc + ca))
    out.sort(key=lambda t: (t.max_pair_m, t.perimeter_m))
    return out


def analyse(
    remaining: list[GenPos],
    map_w: float | None = None,
    map_h: float | None = None,
    tight_m: float = TIGHT_TRIPLE_M,
) -> StrategyReport:
    """Rank remaining generators. Higher score = do it sooner."""
    if len(remaining) < 3:
        ranking = tuple(Suggestion(g[0], 1.0, ("finishes the last gens",)) for g in remaining)
        return StrategyReport(None, (), ranking, ("fewer than 3 gens remain: no 3-gen possible",))

    all_triples = triples(remaining)
    tight = tuple(t for t in all_triples if t.max_pair_m <= tight_m)
    tightest = all_triples[0]

    cx = (map_w / 2) if map_w else sum(g[1] for g in remaining) / len(remaining)
    cy = (map_h / 2) if map_h else sum(g[2] for g in remaining) / len(remaining)
    half_diag = math.hypot(cx, cy) or 1.0

    involvement: dict[str, float] = {g[0]: 0.0 for g in remaining}
    for t in tight:
        w = 1.0 - (t.max_pair_m / tight_m) * 0.5  # tighter -> heavier
        for gid in t.ids:
            involvement[gid] += w

    suggestions: list[Suggestion] = []
    for gid, x, y in remaining:
        reasons: list[str] = []
        partners: tuple[str, ...] = ()
        score = 0.0
        inv = involvement[gid]
        if gid in tightest.ids and tightest.max_pair_m <= tight_m:
            score += 1.0
            reasons.append("breaks the tightest 3-gen")
        if inv > 0:
            score += 2.0 * inv
            partners = tuple(sorted({o for t in tight if gid in t.ids for o in t.ids if o != gid}))
            reasons.append("breaks a potential 3-gen")
        centrality = 1.0 - math.hypot(x - cx, y - cy) / half_diag
        score += 1.0 * centrality
        if map_w and map_h:
            edge = min(x, y, map_w - x, map_h - y)
            if edge <= EDGE_MARGIN_M:
                score -= 0.5
                reasons.append("is an edge gen (safe to leave for later)")
        if centrality > 0.6:
            reasons.append("is central")
        suggestions.append(Suggestion(gid, round(score, 3), tuple(reasons) or ("is isolated",), partners))
    suggestions.sort(key=lambda s: -s.score)
    notes: list[str] = []
    if tight:
        notes.append(f"{len(tight)} defensible triple(s) within {tight_m:.0f} m")
    return StrategyReport(tightest, tight, tuple(suggestions), tuple(notes))
