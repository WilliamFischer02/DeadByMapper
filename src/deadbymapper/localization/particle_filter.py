"""Vectorised particle filter (Monte Carlo Localization) for one player on one floor.

Particle state
    x, y   position in metres (map frame)
    psi    unknown offset between raw camera yaw and map north (deg). The game never tells us
           which way we face at spawn, so this is *estimated*, not assumed.
    k      yaw-scale correction (absorbs mouse-calibration error, ~1.0)
    s      speed-scale correction (absorbs haste/hindered effects, ~1.0)
    logw   log weight

World camera heading of a particle = psi + k * yaw_counts * deg_per_count.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ..core.geometry import circular_mean_deg
from .motion_model import Gait, MotionCommand
from .occupancy import Occupancy

F64 = NDArray[np.float64]

MOVING_GATE = 0.35  # translating_prob below this = treat as stuck (no translation)


@dataclass
class PFConfig:
    n: int = 4000
    pos_diffusion_m_per_sqrt_s: float = 0.10
    heading_diffusion_deg_per_sqrt_s: float = 1.5
    speed_scale_init_sigma: float = 0.04
    speed_scale_walk_per_sqrt_s: float = 0.01
    yaw_scale_init_sigma: float = 0.02
    yaw_scale_walk_per_sqrt_s: float = 0.001
    collision_penalty_per_m: float = 3.0
    resample_ess_frac: float = 0.5
    roughen_pos_m: float = 0.10
    roughen_psi_deg: float = 0.6
    cluster_bin_m: float = 2.0
    cluster_radius_m: float = 4.0


@dataclass(frozen=True, slots=True)
class Cluster:
    x: float
    y: float
    mass: float
    sigma_m: float
    heading_deg: float
    heading_sigma_deg: float


@dataclass(frozen=True, slots=True)
class Estimate:
    x: float
    y: float
    sigma_m: float
    heading_deg: float
    heading_sigma_deg: float
    mass: float
    hypotheses: tuple[tuple[float, float, float], ...]
    clusters: tuple[Cluster, ...] = ()  # sorted by mass, clusters[0] == the fields above


class ParticleFilter:
    def __init__(self, cfg: PFConfig, occ: Occupancy | None, rng: np.random.Generator, deg_per_count: float) -> None:
        self.cfg = cfg
        self.occ = occ
        self.rng = rng
        self.dpc = float(deg_per_count)
        n = cfg.n
        self.n = n
        self.x = np.zeros(n)
        self.y = np.zeros(n)
        self.psi = np.zeros(n)
        self.k = np.ones(n)
        self.s = np.ones(n)
        self.logw = np.zeros(n)
        self.carry_dir = np.zeros(n)
        self.carry_speed = np.zeros(n)

    # ------------------------------------------------------------------ seeding
    def _fresh_params(self, m: int, psi_mean: float | None, psi_sigma: float | None) -> tuple[F64, F64, F64]:
        rng, cfg = self.rng, self.cfg
        if psi_mean is None or psi_sigma is None:
            psi = rng.uniform(0.0, 360.0, m)
        else:
            psi = (psi_mean + rng.normal(0.0, psi_sigma, m)) % 360.0
        k = np.clip(rng.normal(1.0, cfg.yaw_scale_init_sigma, m), 0.8, 1.2)
        s = np.clip(rng.normal(1.0, cfg.speed_scale_init_sigma, m), 0.85, 1.6)
        return psi, k, s

    def sample_mixture(
        self, m: int, centers: F64, sigmas: F64, weights: F64 | None, standoff_m: float
    ) -> tuple[F64, F64]:
        rng = self.rng
        kk = centers.shape[0]
        p = np.full(kk, 1.0 / kk) if weights is None else weights / np.sum(weights)
        comp = rng.choice(kk, size=m, p=p)
        ang = rng.uniform(0.0, 2.0 * math.pi, m)
        rad = np.abs(rng.normal(standoff_m, 0.35, m))
        x = centers[comp, 0] + rng.normal(0.0, 1.0, m) * sigmas[comp] + rad * np.cos(ang)
        y = centers[comp, 1] + rng.normal(0.0, 1.0, m) * sigmas[comp] + rad * np.sin(ang)
        if self.occ is not None:  # re-draw samples that landed inside solids / outside bounds
            for _ in range(4):
                bad = ~self.occ.is_walkable(x, y)
                if not bad.any():
                    break
                nb = int(bad.sum())
                c2 = comp[bad]
                a2 = rng.uniform(0.0, 2.0 * math.pi, nb)
                r2 = np.abs(rng.normal(standoff_m, 0.35, nb))
                x[bad] = centers[c2, 0] + rng.normal(0.0, 1.0, nb) * sigmas[c2] + r2 * np.cos(a2)
                y[bad] = centers[c2, 1] + rng.normal(0.0, 1.0, nb) * sigmas[c2] + r2 * np.sin(a2)
        return x, y

    def seed_mixture(
        self,
        centers: F64,
        sigmas: F64,
        weights: F64 | None = None,
        *,
        standoff_m: float = 1.0,
        psi_mean: float | None = None,
        psi_sigma: float | None = None,
        frac: float = 1.0,
    ) -> None:
        """Replace ``frac`` of the population with samples around landmark candidates."""
        m = self.n if frac >= 1.0 else int(round(frac * self.n))
        if m <= 0:
            return
        if m < self.n:
            self.resample(roughen=False)
            idx = self.rng.choice(self.n, size=m, replace=False)
        else:
            idx = np.arange(self.n)
        x, y = self.sample_mixture(m, centers, sigmas, weights, standoff_m)
        psi, k, s = self._fresh_params(m, psi_mean, psi_sigma)
        self.x[idx], self.y[idx], self.psi[idx], self.k[idx], self.s[idx] = x, y, psi, k, s
        self.logw[idx] = 0.0 if m == self.n else float(np.max(self.logw))

    def seed_hypotheses(
        self,
        k_idx: NDArray[np.int64],
        psi: F64,
        centers: F64,
        sigmas: F64,
        *,
        standoff_m: float,
        frac: float = 1.0,
    ) -> None:
        """Seed from (candidate index, psi) samples drawn from the PathMatcher posterior."""
        m = len(k_idx)
        if m == 0:
            return
        if frac < 1.0 or m < self.n:
            self.resample(roughen=False)
            idx = self.rng.choice(self.n, size=m, replace=False)
        else:
            idx = np.arange(self.n)
        rng = self.rng
        ang = rng.uniform(0.0, 2.0 * math.pi, m)
        rad = np.abs(rng.normal(standoff_m, 0.35, m))
        x = centers[k_idx, 0] + rng.normal(0.0, 1.0, m) * sigmas[k_idx] * 0.7 + rad * np.cos(ang)
        y = centers[k_idx, 1] + rng.normal(0.0, 1.0, m) * sigmas[k_idx] * 0.7 + rad * np.sin(ang)
        if self.occ is not None:
            bad = ~self.occ.is_walkable(x, y)
            if bad.any():
                x[bad] = centers[k_idx[bad], 0] + rng.normal(0.0, 0.3, int(bad.sum()))
                y[bad] = centers[k_idx[bad], 1] + rng.normal(0.0, 0.3, int(bad.sum()))
        _, k, s = self._fresh_params(m, 0.0, 1.0)
        self.x[idx], self.y[idx], self.psi[idx], self.k[idx], self.s[idx] = x, y, psi, k, s
        self.logw[idx] = 0.0 if m == self.n else float(np.mean(self.logw))

    def seed_gaussian(
        self, pos: tuple[float, float], sigma_m: float, psi_mean: float | None, psi_sigma: float | None
    ) -> None:
        centers = np.array([pos], dtype=np.float64)
        self.seed_mixture(centers, np.array([sigma_m]), None, standoff_m=0.0, psi_mean=psi_mean, psi_sigma=psi_sigma)

    def seed_uniform(self) -> None:
        if self.occ is None:
            raise ValueError("uniform seeding needs an occupancy grid")
        self.x, self.y = self.occ.sample_uniform(self.n, self.rng)
        self.psi, self.k, self.s = self._fresh_params(self.n, None, None)
        self.logw[:] = 0.0

    # ------------------------------------------------------------------ motion
    def begin_carry(self) -> None:
        self.carry_dir = self.rng.uniform(0.0, 360.0, self.n)
        self.carry_speed = self.rng.uniform(0.0, 1.0, self.n)

    def world_heading(self, yaw_counts: int) -> F64:
        return (self.psi + self.k * (yaw_counts * self.dpc)) % 360.0

    def predict(self, cmd: MotionCommand) -> None:
        dt = cmd.dt_s
        if dt <= 0.0:
            return
        rng, cfg, n = self.rng, self.cfg, self.n
        sq = math.sqrt(dt)
        self.psi = (self.psi + rng.normal(0.0, cfg.heading_diffusion_deg_per_sqrt_s * sq, n)) % 360.0
        self.k = np.clip(self.k + rng.normal(0.0, cfg.yaw_scale_walk_per_sqrt_s * sq, n), 0.8, 1.2)
        self.s = np.clip(self.s + rng.normal(0.0, cfg.speed_scale_walk_per_sqrt_s * sq, n), 0.85, 1.6)

        if cmd.gait in (Gait.IDLE, Gait.LOCKED):
            return
        if cmd.gait == Gait.CARRIED:
            h = np.radians(self.carry_dir)
            d = self.carry_speed * cmd.speed_mps * dt
            self._apply_motion(np.sin(h) * d, -np.cos(h) * d, np.ones(n, dtype=bool), 0.0)
            return
        assert cmd.intent_deg is not None
        heading = self.world_heading(cmd.yaw_counts) + cmd.intent_deg
        dist = self.s * cmd.speed_mps * dt
        # "Stuck" (holding W into a wall, animation lock) is temporally correlated and shared by
        # every hypothesis, so it is a hard gate decided by the localizer (with hysteresis), NOT
        # an independent per-particle coin flip - that would bias every particle's speed by p.
        moving = np.full(n, cmd.translating_prob >= MOVING_GATE, dtype=bool)
        h = np.radians(heading)
        dx = np.where(moving, np.sin(h) * dist, 0.0) + rng.normal(0.0, cfg.pos_diffusion_m_per_sqrt_s * sq, n)
        dy = np.where(moving, -np.cos(h) * dist, 0.0) + rng.normal(0.0, cfg.pos_diffusion_m_per_sqrt_s * sq, n)
        self._apply_motion(dx, dy, moving, cmd.translating_prob)

    def _apply_motion(self, dx: F64, dy: F64, moving: NDArray[np.bool_], translating_prob: float) -> None:
        nx, ny = self.x + dx, self.y + dy
        occ = self.occ
        if occ is None:
            self.x, self.y = nx, ny
            return
        ok = occ.is_walkable(nx, ny)
        if not ok.all():
            ib = np.flatnonzero(~ok)
            x0, y0, ddx, ddy = self.x[ib], self.y[ib], dx[ib], dy[ib]
            okx = occ.is_walkable(x0 + ddx, y0)
            oky = occ.is_walkable(x0, y0 + ddy)
            choose_x = okx & (~oky | (np.abs(ddx) >= np.abs(ddy)))
            choose_y = oky & ~choose_x
            fx = np.where(choose_x, x0 + ddx, x0)
            fy = np.where(choose_y, y0 + ddy, y0)
            nx[ib], ny[ib] = fx, fy
            intended = np.hypot(ddx, ddy)
            achieved = np.hypot(fx - x0, fy - y0)
            lost = np.maximum(intended - achieved, 0.0)
            self.logw[ib] -= self.cfg.collision_penalty_per_m * translating_prob * lost * moving[ib]
        self.x, self.y = nx, ny

    # ------------------------------------------------------------------ measurement
    def update(self, loglik: F64) -> float:
        """Apply a measurement. Returns the log mean likelihood under the prior weights
        (the 'evidence'), used by the localizer to detect inconsistent beliefs."""
        w = self.weights()
        m = float(np.max(loglik))
        evidence = math.log(float(np.sum(w * np.exp(loglik - m))) + 1e-300) + m
        self.logw = self.logw + loglik
        self.logw -= float(np.max(self.logw))
        return evidence

    def penalize(self, delta_logw: F64) -> None:
        self.logw = self.logw + delta_logw
        self.logw -= float(np.max(self.logw))

    def weights(self) -> F64:
        w = np.exp(self.logw - float(np.max(self.logw)))
        return w / float(np.sum(w))

    def ess(self) -> float:
        w = self.weights()
        return float(1.0 / np.sum(w * w))

    def resample(self, *, roughen: bool = True) -> None:
        n, rng = self.n, self.rng
        w = self.weights()
        positions = (rng.random() + np.arange(n)) / n
        cum = np.cumsum(w)
        cum[-1] = 1.0
        idx = np.searchsorted(cum, positions)
        self.x, self.y = self.x[idx], self.y[idx]
        self.psi, self.k, self.s = self.psi[idx], self.k[idx], self.s[idx]
        self.carry_dir, self.carry_speed = self.carry_dir[idx], self.carry_speed[idx]
        self.logw = np.zeros(n)
        if roughen:
            self.x = self.x + rng.normal(0.0, self.cfg.roughen_pos_m, n)
            self.y = self.y + rng.normal(0.0, self.cfg.roughen_pos_m, n)
            self.psi = (self.psi + rng.normal(0.0, self.cfg.roughen_psi_deg, n)) % 360.0

    def maybe_resample(self) -> bool:
        if self.ess() < self.cfg.resample_ess_frac * self.n:
            self.resample()
            return True
        return False

    # ------------------------------------------------------------------ estimate
    def estimate(self, yaw_counts: int, max_hypotheses: int = 3) -> Estimate:
        w = self.weights()
        cfg = self.cfg
        remaining = np.ones(self.n, dtype=bool)
        clusters: list[tuple[float, float, float, NDArray[np.bool_]]] = []
        for _ in range(max_hypotheses):
            if not remaining.any() or float(np.sum(w[remaining])) < 0.02:
                break
            bx = np.floor(self.x[remaining] / cfg.cluster_bin_m).astype(np.int64)
            by = np.floor(self.y[remaining] / cfg.cluster_bin_m).astype(np.int64)
            keys = bx * 1_000_003 + by
            uniq, inv = np.unique(keys, return_inverse=True)
            bin_mass = np.bincount(inv, weights=w[remaining], minlength=uniq.size)
            top = int(np.argmax(bin_mass))
            sel = np.flatnonzero(remaining)[inv == top]
            cx = float(np.average(self.x[sel], weights=w[sel]))
            cy = float(np.average(self.y[sel], weights=w[sel]))
            members = remaining.copy()
            for _ in range(3):
                d2 = (self.x - cx) ** 2 + (self.y - cy) ** 2
                members = remaining & (d2 <= cfg.cluster_radius_m**2)
                if float(np.sum(w[members])) <= 0:
                    break
                cx = float(np.average(self.x[members], weights=w[members]))
                cy = float(np.average(self.y[members], weights=w[members]))
            mass = float(np.sum(w[members]))
            clusters.append((cx, cy, mass, members))
            remaining &= ~members
        if not clusters:
            return Estimate(
                float(np.average(self.x, weights=w)),
                float(np.average(self.y, weights=w)),
                float("inf"),
                0.0,
                180.0,
                0.0,
                (),
            )
        clusters.sort(key=lambda c: -c[2])  # report the heaviest cluster, not the densest bin
        headings = self.world_heading(yaw_counts)
        infos: list[Cluster] = []
        for cx, cy, mass, members in clusters:
            wm = w[members]
            d2 = (self.x[members] - cx) ** 2 + (self.y[members] - cy) ** 2
            sigma = math.sqrt(float(np.sum(wm * d2) / max(float(np.sum(wm)), 1e-12)))
            heading, hsig = circular_mean_deg(headings[members], wm)
            infos.append(Cluster(cx, cy, mass, sigma, heading, hsig))
        best = infos[0]
        hyps = tuple((round(c.x, 2), round(c.y, 2), round(c.mass, 4)) for c in infos)
        return Estimate(
            best.x, best.y, best.sigma_m, best.heading_deg, best.heading_sigma_deg, best.mass, hyps, tuple(infos)
        )
