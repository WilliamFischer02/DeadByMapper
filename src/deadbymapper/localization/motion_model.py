"""Turns observed inputs + perceived player state into a motion command for the filter.

Survivors move relative to the CAMERA (third person): pressing W moves toward where the camera
looks; the character turns to face the movement direction. So world movement heading is
    heading = psi (unknown camera offset) + yaw_rel (from mouse counts) + intent (WASD angle).
Speeds are datamined values from the DBD wiki (data/game/movement.json, docs/RESEARCH.md).
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from pathlib import Path

from ..config.paths import data_dir
from ..core.events import PlayerStatus
from ..core.geometry import intent_angle_deg
from ..input.input_state import InputSnapshot


class Gait(enum.StrEnum):
    IDLE = "idle"
    WALK = "walk"
    RUN = "run"
    CROUCH = "crouch"
    CRAWL = "crawl"
    LOCKED = "locked"  # interacting, hooked, in locker: inputs do not translate
    CARRIED = "carried"  # moved by the killer: direction unknown -> diffuse


@dataclass(frozen=True, slots=True)
class SurvivorSpeeds:
    run: float = 4.0
    walk: float = 2.26
    crouch: float = 1.13
    crawl: float = 0.7
    crawl_eventual: float = 1.05
    carried: float = 3.68  # killer carry speed, used only for diffusion magnitude

    @classmethod
    def load(cls, path: Path | None = None) -> SurvivorSpeeds:
        path = path or data_dir() / "game" / "movement.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))["survivor"]
        except (OSError, KeyError, json.JSONDecodeError):
            return cls()
        return cls(
            run=raw["running_mps"],
            walk=raw["walking_mps"],
            crouch=raw["crouching_mps"],
            crawl=raw["crawling_initial_mps"],
            crawl_eventual=raw["crawling_eventual_mps"],
            carried=raw.get("carried_by_killer_mps", 3.68),
        )


@dataclass(frozen=True, slots=True)
class MotionCommand:
    dt_s: float
    gait: Gait
    speed_mps: float
    intent_deg: float | None  # camera-relative, None when no movement keys
    yaw_counts: int  # cumulative raw mouse counts (camera yaw)
    translating_prob: float  # optical-flow belief that we are really moving (1.0 = no info)


LOCKED_STATUSES = {PlayerStatus.HOOKED, PlayerStatus.IN_LOCKER, PlayerStatus.DEAD, PlayerStatus.ESCAPED}


def command_from_input(
    snap: InputSnapshot,
    dt_s: float,
    *,
    status: PlayerStatus,
    interacting: bool,
    translating_prob: float,
    speeds: SurvivorSpeeds,
) -> MotionCommand:
    intent = intent_angle_deg(snap.forward, snap.back, snap.left, snap.right) if snap.camera_control else None
    if status in LOCKED_STATUSES or interacting:
        return MotionCommand(dt_s, Gait.LOCKED, 0.0, None, snap.yaw_counts, translating_prob)
    if status == PlayerStatus.CARRIED:
        return MotionCommand(dt_s, Gait.CARRIED, speeds.carried, None, snap.yaw_counts, translating_prob)
    if intent is None:
        return MotionCommand(dt_s, Gait.IDLE, 0.0, None, snap.yaw_counts, translating_prob)
    if status == PlayerStatus.DOWNED:
        crawl = 0.5 * (speeds.crawl + speeds.crawl_eventual)
        return MotionCommand(dt_s, Gait.CRAWL, crawl, intent, snap.yaw_counts, translating_prob)
    if snap.crouch:
        return MotionCommand(dt_s, Gait.CROUCH, speeds.crouch, intent, snap.yaw_counts, translating_prob)
    if snap.sprint:
        return MotionCommand(dt_s, Gait.RUN, speeds.run, intent, snap.yaw_counts, translating_prob)
    return MotionCommand(dt_s, Gait.WALK, speeds.walk, intent, snap.yaw_counts, translating_prob)
