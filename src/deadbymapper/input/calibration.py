"""Mouse yaw calibration: degrees of in-game camera yaw per raw mouse count.

Primary method - automatic "spin" calibration (M2):
  1. Player stands still (custom game is ideal), presses the calibrate hotkey.
  2. Player turns the camera smoothly one full turn (or a bit more) and stops.
  3. We keep a downscaled reference frame of the start view and track, for every captured frame,
     (a) cumulative raw X counts and (b) similarity to the reference (normalised cross-correlation
     of the upper part of the frame - sky / far geometry - with the character masked out).
  4. The count value where similarity peaks again (after leaving the start view) = counts per 360.
     Repeat 2-3 spins, take the median; reject if spins disagree by > 2 %.
Secondary: ``yaw_rate_dps`` from optical flow (perception/motion_flow.py) regressed against the
count rate during normal play refines the value continuously (the particle filter's per-particle
yaw scale k also absorbs small residual error).

This module holds the pure maths so it is unit-testable with synthetic similarity curves.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def counts_per_360_from_similarity(
    counts: NDArray[np.float64],
    similarity: NDArray[np.float64],
    *,
    leave_thresh: float = 0.5,
    return_thresh: float = 0.6,
) -> float | None:
    """``counts``: cumulative |dx| per frame (monotonic for a one-direction spin).
    ``similarity``: NCC of each frame vs the start frame (~1.0 at the start)."""
    if len(counts) < 10 or len(counts) != len(similarity):
        return None
    rel = np.asarray(counts, dtype=np.float64) - float(counts[0])
    sim = np.asarray(similarity, dtype=np.float64)
    low = np.flatnonzero(sim < leave_thresh)
    if low.size == 0:
        return None
    i0 = int(low[0])
    back = np.flatnonzero(sim[i0:] > return_thresh)
    if back.size == 0:
        return None
    j = i0 + int(back[0])
    k = j
    while k + 1 < len(sim) and sim[k + 1] >= return_thresh:
        k += 1
    window = np.arange(j, k + 1)
    best = int(window[np.argmax(sim[window])])
    # parabolic refinement around the peak (sub-frame accuracy)
    if 0 < best < len(sim) - 1:
        y0, y1, y2 = sim[best - 1], sim[best], sim[best + 1]
        denom = y0 - 2 * y1 + y2
        if abs(denom) > 1e-12:
            off = 0.5 * (y0 - y2) / denom
            off = float(np.clip(off, -1.0, 1.0))
            nb = best + 1 if off > 0 else best - 1
            return float(rel[best] + abs(off) * (rel[nb] - rel[best]))
    return float(rel[best])


def deg_per_count(counts_per_360: float) -> float:
    return 360.0 / counts_per_360


def combine_spins(values: list[float], max_spread: float = 0.02) -> float | None:
    if not values:
        return None
    arr = np.array(values, dtype=np.float64)
    med = float(np.median(arr))
    if float(np.max(np.abs(arr - med))) / med > max_spread:
        return None
    return med
