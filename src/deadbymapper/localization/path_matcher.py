"""Global re-localization by matching the dead-reckoned breadcrumb against the map.

Why: the particle filter tracks well once it holds the right hypothesis, but at the first
landmark fix the unknowns are (which candidate am I at?) x (which way did I start facing?) -
roughly 13 candidates x 360 degrees. Random particles cover that space thinly. Instead, at every
landmark event we *exhaustively* score each (candidate k, heading offset psi) on a 1-degree grid:

    * the breadcrumb (relative path), rotated by psi and anchored at candidate k, must stay in
      walkable space (bounds minus solid structures);
    * every earlier landmark event on that path must land near a candidate of its kind
      (tolerance grows with distance travelled since, to absorb dead-reckoning drift).

The resulting posterior over (k, psi) seeds / refreshes the particle filter. Cost per event is a
few hundred thousand vectorised lookups (~10-30 ms) - only paid when something is learned.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .occupancy import Occupancy

F64 = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class LandmarkSet:
    centers: F64  # (K, 2)
    sigmas: F64  # (K,)
    standoff_m: float


@dataclass(frozen=True, slots=True)
class PastEvent:
    rel: tuple[float, float]  # breadcrumb position when it happened
    path_len_m: float  # cumulative breadcrumb length at that time
    landmarks: LandmarkSet


@dataclass(frozen=True, slots=True)
class MatchResult:
    k: NDArray[np.int64]  # (H,) candidate index of each scored hypothesis
    psi: F64  # (H,)
    logp: F64  # (H,) normalised log posterior
    centers: F64

    def sample(self, n: int, rng: np.random.Generator) -> tuple[NDArray[np.int64], F64]:
        p = np.exp(self.logp - np.max(self.logp))
        p /= p.sum()
        idx = rng.choice(len(p), size=n, p=p)
        return self.k[idx], (self.psi[idx] + rng.uniform(-0.5, 0.5, n)) % 360.0

    def top(self, n: int = 3) -> list[tuple[int, float, float]]:
        order = np.argsort(-self.logp)[:n]
        return [(int(self.k[i]), float(self.psi[i]), float(self.logp[i])) for i in order]


class PathMatcher:
    def __init__(
        self,
        occ: Occupancy,
        *,
        psi_step_deg: float = 1.0,
        path_samples: int = 40,
        window_s: float = 90.0,
        wall_penalty: float = 0.5,
        drift_frac: float = 0.05,
    ) -> None:
        self.occ = occ
        self.psi = np.arange(0.0, 360.0, psi_step_deg)
        self.path_samples = path_samples
        self.window_s = window_s
        self.wall_penalty = wall_penalty
        self.drift_frac = drift_frac

    def match(
        self,
        current: LandmarkSet,
        now_rel: tuple[float, float],
        now_len_m: float,
        rel_path: list[tuple[float, float, float]],  # (t, x, y)
        now_t: float,
        past: list[PastEvent],
        psi_prior: tuple[float, float] | None = None,
    ) -> MatchResult:
        K = current.centers.shape[0]
        P = self.psi.size
        r = np.radians(self.psi)
        c, s = np.cos(r), np.sin(r)  # (P,)
        logp = np.zeros((K, P))

        # --- 1. walkability of the rotated breadcrumb
        pts = np.array([(x, y) for t, x, y in rel_path if now_t - t <= self.window_s], dtype=np.float64)
        if len(pts) >= 4:
            idx = np.linspace(0, len(pts) - 1, min(self.path_samples, len(pts))).astype(int)
            off = pts[idx] - np.asarray(now_rel)
            off = off[np.hypot(off[:, 0], off[:, 1]) > 2.0]
            if len(off):
                ox = c[:, None] * off[None, :, 0] - s[:, None] * off[None, :, 1]  # (P, S)
                oy = s[:, None] * off[None, :, 0] + c[:, None] * off[None, :, 1]
                wx = current.centers[:, 0][:, None, None] + ox[None]  # (K, P, S)
                wy = current.centers[:, 1][:, None, None] + oy[None]
                free = self.occ.is_walkable(wx, wy).mean(axis=-1)
                logp -= self.wall_penalty * (1.0 - free) * len(off)

        # --- 2. earlier landmark events must sit on candidates of their kind
        for ev in past:
            d = np.asarray(ev.rel) - np.asarray(now_rel)
            dx = c * d[0] - s * d[1]  # (P,)
            dy = s * d[0] + c * d[1]
            ex = current.centers[:, 0][:, None] + dx[None, :]  # (K, P)
            ey = current.centers[:, 1][:, None] + dy[None, :]
            drift = self.drift_frac * max(now_len_m - ev.path_len_m, 0.0)
            lm = ev.landmarks
            se2 = lm.sigmas**2 + lm.standoff_m**2 + current.standoff_m**2 + drift**2  # (J,)
            ddx = ex[..., None] - lm.centers[:, 0]
            ddy = ey[..., None] - lm.centers[:, 1]
            dens = np.sum(np.exp(-(ddx * ddx + ddy * ddy) / (2.0 * se2)) / (2.0 * math.pi * se2), axis=-1)
            peak = float(np.max(1.0 / (2.0 * math.pi * se2)))
            logp += np.log(dens + 1e-4 * peak) - math.log(peak)

        if psi_prior is not None:
            mu, sig = psi_prior
            dd = (self.psi - mu + 180.0) % 360.0 - 180.0
            logp += (-(dd * dd) / (2.0 * sig * sig))[None, :]

        flat = logp.ravel()
        flat = flat - np.max(flat)
        flat = flat - math.log(float(np.sum(np.exp(flat))))
        kk, _ = np.meshgrid(np.arange(K), np.arange(P), indexing="ij")
        return MatchResult(kk.ravel(), np.tile(self.psi, K), flat, current.centers)
