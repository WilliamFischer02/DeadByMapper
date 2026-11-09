"""Localizer service: input events + observations -> PoseEstimate.

Lifecycle per trial
    NO_MAP      no map identified: pure dead reckoning in a relative frame (breadcrumb trail)
    NO_FIX      map known, no absolute fix yet: still breadcrumb. The trail is kept so the first
                fix can be solved globally by path matching (path_matcher.py)
    SEARCHING   particle filter seeded; belief still multimodal
    CONVERGING  dominant cluster holds >= 40% mass
    LOCKED      dominant cluster >= 75% mass and sigma <= 3 m
    LOST        was locked, uncertainty grew past 12 m (keeps tracking; next fix re-locks)

Every landmark event (generator / exit gate interaction, user tap) runs the global PathMatcher
over (candidate x heading-offset) and refreshes part of the particle population from it, so a
wrong lock is corrected at the next landmark instead of persisting.
"""

from __future__ import annotations

import logging
import math

import numpy as np

from ..core.bus import EventBus
from ..core.clock import Clock, ns_to_s, s_to_ns
from ..core.events import (
    InputFocusChanged,
    InteractionKind,
    InteractionObserved,
    KeyEvent,
    MapIdentified,
    MatchState,
    MatchStateChanged,
    MotionObserved,
    MouseButton,
    MouseDelta,
    PlayerStatus,
    PlayerStatusObserved,
    PoseEstimate,
    PoseStatus,
    Tick,
    UserFix,
    UserHeadingFix,
    UserMapOverride,
    UserMark,
)
from ..core.geometry import wrap_deg
from ..input.input_state import InputState
from ..input.keymap import KeyMap
from ..world.map_model import MapData, SpawnCandidate
from ..world.map_registry import MapRegistry
from .measurements import EXIT_GATE_STANDOFF_M, GENERATOR_STANDOFF_M, heading_loglik, near_candidates_loglik
from .motion_model import Gait, SurvivorSpeeds, command_from_input
from .occupancy import Occupancy
from .particle_filter import MOVING_GATE, ParticleFilter, PFConfig
from .path_matcher import LandmarkSet, PastEvent, PathMatcher

log = logging.getLogger(__name__)

# Placeholder until the 360-degree calibration has run (UI shows a 'Calibrate mouse' chip).
UNCALIBRATED_DEG_PER_COUNT = 0.04
FLOW_STALE_S = 0.6
INCONSISTENT_EVIDENCE = 9.0  # nats below the best achievable log-likelihood
DISAGREE_M = 6.0
LANDMARK_DEDUPE_S = 3.0  # a hotkey mark and a perceived interaction for the same object count once


class Localizer:
    name = "localizer"

    def __init__(
        self,
        clock: Clock,
        registry: MapRegistry,
        *,
        keymap: KeyMap | None = None,
        deg_per_count: float | None = None,
        n_particles: int = 5000,
        speeds: SurvivorSpeeds | None = None,
        seed: int | None = None,
        publish_hz: float = 30.0,
        require_match_state: bool = True,
    ) -> None:
        self.clock = clock
        self.registry = registry
        self.input = InputState(keymap or KeyMap.load())
        self.calibrated = deg_per_count is not None
        self.dpc = deg_per_count if deg_per_count is not None else UNCALIBRATED_DEG_PER_COUNT
        self.cfg = PFConfig(n=n_particles)
        self.speeds = speeds or SurvivorSpeeds.load()
        self.rng = np.random.default_rng(seed)
        self.publish_period_ns = int(1e9 / publish_hz)
        self.require_match_state = require_match_state
        self.bus: EventBus | None = None

        self.map: MapData | None = None
        self.occ: Occupancy | None = None
        self.matcher: PathMatcher | None = None
        self.gens: LandmarkSet | None = None
        self.gates: LandmarkSet | None = None
        self.pf: ParticleFilter | None = None
        self.status = PoseStatus.NO_MAP
        self.in_match = not require_match_state
        self.player_status = PlayerStatus.HEALTHY
        self.interacting: InteractionKind | None = None
        self.translating_prob = 1.0
        self._flow_ns = 0
        self._last_tick_ns: int | None = None
        self._last_pub_ns = 0
        self._was_locked = False
        self._pending_psi: tuple[float, float] | None = None
        self._last_xy: tuple[float, float] | None = None
        self._last_landmark_ns: dict[InteractionKind, int] = {}
        self._reset_breadcrumb()

    # ------------------------------------------------------------------ wiring
    def attach(self, bus: EventBus) -> None:
        self.bus = bus
        bus.subscribe(MapIdentified, lambda e: self.set_map(e.map_id))
        bus.subscribe(UserMapOverride, lambda e: self.set_map(e.map_id))
        bus.subscribe(MatchStateChanged, self._on_state)
        for t in (KeyEvent, MouseDelta, MouseButton, InputFocusChanged):
            bus.subscribe(t, self.input.apply)
        bus.subscribe(PlayerStatusObserved, self._on_status)
        bus.subscribe(MotionObserved, self._on_motion)
        bus.subscribe(InteractionObserved, self._on_interaction)
        bus.subscribe(UserMark, self._on_mark)
        bus.subscribe(UserFix, self._on_user_fix)
        bus.subscribe(UserHeadingFix, self._on_heading_fix)
        bus.subscribe(Tick, self._on_tick)

    # ------------------------------------------------------------------ map / state
    def set_map(self, map_id: str) -> None:
        if map_id not in self.registry:
            log.warning("unknown map id %s", map_id)
            return
        if self.map is not None and self.map.id == map_id:
            return  # re-identification / user confirming the same map must not wipe the estimate
        self.map = self.registry.get(map_id)
        self.occ = Occupancy.from_map(self.map)
        self.matcher = PathMatcher(self.occ) if self.occ is not None else None

        def lm(cands: list[SpawnCandidate], standoff: float) -> LandmarkSet | None:
            ground = [c for c in cands if c.floor == "ground"]
            if not ground:
                return None
            return LandmarkSet(
                np.array([c.pos_m for c in ground], dtype=np.float64),
                np.array([c.sigma_m for c in ground], dtype=np.float64),
                standoff,
            )

        self.gens = lm(self.map.generators, GENERATOR_STANDOFF_M)
        self.gates = lm(list(self.map.exit_gates), EXIT_GATE_STANDOFF_M)
        self.pf = None
        self.past_events = []
        self._last_xy = None
        self.status = PoseStatus.NO_FIX if self.occ is not None else PoseStatus.NO_MAP
        self._was_locked = False

    def _on_state(self, e: MatchStateChanged) -> None:
        if e.state == MatchState.IN_MATCH:
            if not self.in_match:
                self.input.reset_yaw()
                self._reset_breadcrumb()
                self.pf = None
                self.status = PoseStatus.NO_FIX if self.occ is not None else PoseStatus.NO_MAP
                self._was_locked = False
                self._pending_psi = None
            self.in_match = True
        else:
            self.in_match = not self.require_match_state

    def _reset_breadcrumb(self) -> None:
        self.rel_x = 0.0
        self.rel_y = 0.0
        self.rel_len = 0.0
        self._rel_t = 0.0
        self.rel_path: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
        self.past_events: list[PastEvent] = []
        self._last_xy = None
        self._last_landmark_ns = {}

    def _on_status(self, e: PlayerStatusObserved) -> None:
        if e.status == PlayerStatus.CARRIED and self.player_status != PlayerStatus.CARRIED and self.pf is not None:
            self.pf.begin_carry()
        self.player_status = e.status

    def _on_motion(self, e: MotionObserved) -> None:
        self.translating_prob = float(min(max(e.translating_prob, 0.02), 0.995))
        self._flow_ns = e.ts_ns

    # ------------------------------------------------------------------ observations
    def _on_interaction(self, e: InteractionObserved) -> None:
        if not e.active:
            if self.interacting == e.kind:
                self.interacting = None
            return
        self.interacting = e.kind
        self._landmark(e.kind, e.ts_ns, e.confidence)

    def _on_mark(self, e: UserMark) -> None:
        kind = {"generator": InteractionKind.GENERATOR, "exit_gate": InteractionKind.EXIT_GATE}.get(e.kind)
        if kind is not None:
            self._landmark(kind, e.ts_ns, 0.9)

    def _landmark(self, kind: InteractionKind, ts_ns: int, confidence: float) -> None:
        lm = {InteractionKind.GENERATOR: self.gens, InteractionKind.EXIT_GATE: self.gates}.get(kind)
        if lm is None:
            return
        last = self._last_landmark_ns.get(kind)
        if last is not None and abs(ts_ns - last) < s_to_ns(LANDMARK_DEDUPE_S):
            return
        self._last_landmark_ns[kind] = ts_ns
        self._landmark_fix(lm, confidence)

    def _psi_prior(self) -> tuple[float, float] | None:
        if self._pending_psi is not None:
            return self._pending_psi
        if self.pf is not None:
            yaw = self.input.snapshot().yaw_counts
            est = self.pf.estimate(yaw)
            if est.mass > 0.6 and est.heading_sigma_deg < 20.0:
                return (wrap_deg(est.heading_deg - yaw * self.dpc), max(3.0 * est.heading_sigma_deg, 15.0))
        return None

    def _match(self, lm: LandmarkSet, psi_prior: tuple[float, float] | None = None):  # -> MatchResult
        assert self.matcher is not None
        return self.matcher.match(
            lm,
            (self.rel_x, self.rel_y),
            self.rel_len,
            self.rel_path,
            self._rel_t,
            self.past_events,
            psi_prior=psi_prior,
        )

    def _landmark_fix(self, lm: LandmarkSet, confidence: float) -> None:
        if self.occ is None or self.matcher is None:
            return
        res = self._match(lm, self._pending_psi)
        if self.pf is None:
            self.pf = ParticleFilter(self.cfg, self.occ, self.rng, self.dpc)
            k_idx, psi = res.sample(self.pf.n, self.rng)
            self.pf.seed_hypotheses(k_idx, psi, lm.centers, lm.sigmas, standoff_m=lm.standoff_m)
            self.status = PoseStatus.SEARCHING
        else:
            pf = self.pf
            ll = near_candidates_loglik(pf.x, pf.y, lm.centers, lm.sigmas, standoff_m=lm.standoff_m)
            ll *= max(0.2, min(1.0, confidence))
            peak = float(
                np.max(
                    near_candidates_loglik(
                        lm.centers[:, 0], lm.centers[:, 1], lm.centers, lm.sigmas, standoff_m=lm.standoff_m
                    )
                )
            )
            evidence = pf.update(ll)
            est = pf.estimate(self.input.snapshot().yaw_counts)
            k_mass = np.exp(res.logp).reshape(lm.centers.shape[0], -1).sum(axis=1)
            best_k = int(np.argmax(k_mass))
            disagree = math.hypot(est.x - lm.centers[best_k, 0], est.y - lm.centers[best_k, 1]) > DISAGREE_M
            if evidence < peak - INCONSISTENT_EVIDENCE:
                frac = 0.8
            elif disagree and k_mass[best_k] > 0.5:
                frac = 0.6
            else:
                frac = 0.15
            log.debug(
                "landmark fix: evidence %.1f peak %.1f matcher best k=%d mass %.2f -> inject %.0f%%",
                evidence,
                peak,
                best_k,
                k_mass[best_k],
                100 * frac,
            )
            k_idx, psi = res.sample(int(frac * pf.n), self.rng)
            pf.seed_hypotheses(k_idx, psi, lm.centers, lm.sigmas, standoff_m=lm.standoff_m, frac=frac)
        self.pf.maybe_resample()
        self.past_events.append(PastEvent((self.rel_x, self.rel_y), self.rel_len, lm))

    def _on_user_fix(self, e: UserFix) -> None:
        if self.occ is None or self.matcher is None:
            return
        lm = LandmarkSet(np.array([e.pos_m], dtype=np.float64), np.array([max(e.radius_m / 2.0, 0.5)]), 0.0)
        res = self._match(lm, self._psi_prior())
        self.pf = ParticleFilter(self.cfg, self.occ, self.rng, self.dpc)
        k_idx, psi = res.sample(self.pf.n, self.rng)
        self.pf.seed_hypotheses(k_idx, psi, lm.centers, lm.sigmas, standoff_m=0.0)
        self.past_events.append(PastEvent((self.rel_x, self.rel_y), self.rel_len, lm))
        self.status = PoseStatus.SEARCHING

    def _on_heading_fix(self, e: UserHeadingFix) -> None:
        yaw = self.input.snapshot().yaw_counts
        if self.pf is None:
            self._pending_psi = (wrap_deg(e.heading_deg - yaw * self.dpc), e.sigma_deg)
            return
        self.pf.update(heading_loglik(self.pf.world_heading(yaw), e.heading_deg, e.sigma_deg))
        self.pf.maybe_resample()

    # ------------------------------------------------------------------ tick
    def _on_tick(self, e: Tick) -> None:
        last = self._last_tick_ns
        self._last_tick_ns = e.ts_ns
        if last is None or not self.in_match:
            return
        dt = min(max(ns_to_s(e.ts_ns - last), 0.0), 0.25)
        if e.ts_ns - self._flow_ns > FLOW_STALE_S * 1e9:
            self.translating_prob = 1.0
        snap = self.input.snapshot()
        cmd = command_from_input(
            snap,
            dt,
            status=self.player_status,
            interacting=self.interacting is not None,
            translating_prob=self.translating_prob,
            speeds=self.speeds,
        )
        self._rel_t += dt
        moving = cmd.translating_prob >= MOVING_GATE
        if moving and cmd.gait not in (Gait.IDLE, Gait.LOCKED, Gait.CARRIED) and cmd.intent_deg is not None:
            h = math.radians(snap.yaw_counts * self.dpc + cmd.intent_deg)
            d = cmd.speed_mps * dt
            self.rel_x += math.sin(h) * d
            self.rel_y -= math.cos(h) * d
            self.rel_len += d
            if self._rel_t - self.rel_path[-1][0] >= 0.1:
                self.rel_path.append((self._rel_t, self.rel_x, self.rel_y))
                if len(self.rel_path) > 4000:
                    self.rel_path = self.rel_path[-3000:]
        if self.pf is not None:
            self.pf.predict(cmd)
            self.pf.maybe_resample()
        if e.ts_ns - self._last_pub_ns >= self.publish_period_ns:
            self._last_pub_ns = e.ts_ns
            pose = self.current_pose(e.ts_ns)
            if self.bus is not None:
                self.bus.publish(pose)

    # ------------------------------------------------------------------ output
    def current_pose(self, ts_ns: int | None = None) -> PoseEstimate:
        ts = self.clock.now_ns() if ts_ns is None else ts_ns
        snap = self.input.snapshot()
        if self.pf is None:
            return PoseEstimate(
                ts_ns=ts,
                status=self.status,
                x=self.rel_x,
                y=self.rel_y,
                heading_deg=wrap_deg(snap.yaw_counts * self.dpc),
                relative=True,
            )
        est = self.pf.estimate(snap.yaw_counts)
        # Hysteresis: keep reporting the cluster we reported last unless another clearly outweighs
        # it - a marker that flips between two hypotheses every frame is unreadable at a glance.
        chosen = est.clusters[0] if est.clusters else None
        if chosen is not None and self._last_xy is not None:
            for c in est.clusters:
                if math.hypot(c.x - self._last_xy[0], c.y - self._last_xy[1]) < 5.0 and c.mass >= 0.7 * chosen.mass:
                    chosen = c
                    break
        x, y = (chosen.x, chosen.y) if chosen else (est.x, est.y)
        sigma = chosen.sigma_m if chosen else est.sigma_m
        mass = chosen.mass if chosen else est.mass
        heading = chosen.heading_deg if chosen else est.heading_deg
        hsig = chosen.heading_sigma_deg if chosen else est.heading_sigma_deg
        self._last_xy = (x, y)
        if mass >= 0.75 and sigma <= 3.0:
            status = PoseStatus.LOCKED
            self._was_locked = True
        elif self._was_locked and sigma > 12.0:
            status = PoseStatus.LOST
        elif mass >= 0.4 and sigma <= 6.0:
            status = PoseStatus.CONVERGING
        else:
            status = PoseStatus.SEARCHING
        self.status = status
        others = tuple(h for h in est.hypotheses if math.hypot(h[0] - x, h[1] - y) > 1.0)
        return PoseEstimate(
            ts_ns=ts,
            status=status,
            x=x,
            y=y,
            heading_deg=heading,
            sigma_m=sigma,
            heading_sigma_deg=hsig,
            confidence=mass,
            hypotheses=((round(x, 2), round(y, 2), round(mass, 4)), *others),
            psi_deg=wrap_deg(heading - snap.yaw_counts * self.dpc),
            rel_xy=(self.rel_x, self.rel_y),
        )
