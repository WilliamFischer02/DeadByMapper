"""Typed events - THE contracts between modules.

Rules
-----
* Events are small, immutable, timestamped (``ts_ns`` from the shared monotonic clock).
* Heavy payloads (video frames, audio blocks) never travel on the bus; they go through
  ``capture.base.FrameMailbox`` / audio callbacks. Only *derived observations* are events.
* Every event is JSON-serialisable via :func:`to_record` / :func:`from_record` so sessions can
  be recorded and replayed bit-for-bit (see recording/).
* Adding a field: give it a default so old recordings still load. Renaming/removing a field
  is a breaking change -> bump ``RECORD_SCHEMA_VERSION`` and add a migration in recording/.
* Changing this file is a *lead-only* change during parallel work (see CLAUDE.md).
"""

from __future__ import annotations

import dataclasses
import enum
import types
import typing
from dataclasses import dataclass, field
from typing import Any

RECORD_SCHEMA_VERSION = 1

_REGISTRY: dict[str, type[Event]] = {}


# --------------------------------------------------------------------------- enums
class MatchState(enum.StrEnum):
    OFFLINE = "offline"  # game process not running
    MENU = "menu"  # main menu / store / bloodweb
    LOBBY = "lobby"
    LOADING = "loading"  # offering reveal + loading screen
    IN_MATCH = "in_match"
    POST_MATCH = "post_match"  # tally / scoreboard


class Role(enum.StrEnum):
    SURVIVOR = "survivor"
    KILLER = "killer"
    UNKNOWN = "unknown"


class InteractionKind(enum.StrEnum):
    GENERATOR = "generator"
    EXIT_GATE = "exit_gate"
    HATCH = "hatch"
    CHEST = "chest"
    TOTEM = "totem"
    LOCKER = "locker"
    WINDOW = "window"
    PALLET = "pallet"
    HOOK = "hook"
    HEAL = "heal"
    OTHER = "other"


class PlayerStatus(enum.StrEnum):
    HEALTHY = "healthy"
    INJURED = "injured"
    DOWNED = "downed"
    CARRIED = "carried"
    HOOKED = "hooked"
    IN_LOCKER = "in_locker"
    DEAD = "dead"
    ESCAPED = "escaped"
    UNKNOWN = "unknown"


class MapSource(enum.StrEnum):
    MANUAL = "manual"
    LOG = "log"
    OCR_INTRO = "ocr_intro"  # map title shown when the trial starts
    OCR_MENU = "ocr_menu"  # pause / TAB menu shows the map name
    VISUAL = "visual"  # image classifier (future)
    REPLAY = "replay"
    DEMO = "demo"


class PoseStatus(enum.StrEnum):
    NO_MAP = "no_map"  # no map identified - relative breadcrumb only
    NO_FIX = "no_fix"  # map known, waiting for first absolute fix
    SEARCHING = "searching"  # multimodal belief
    CONVERGING = "converging"
    LOCKED = "locked"
    LOST = "lost"


class TrSource(enum.StrEnum):
    AUDIO = "audio"
    VISUAL_HEART = "visual_heart"  # DBD "Visual Terror Radius" accessibility heart
    SIM = "sim"


# --------------------------------------------------------------------------- base
@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    ts_ns: int

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # dataclass(slots=True) re-creates the class; the last registration wins (the slotted one).
        _REGISTRY[cls.__name__] = cls


# --------------------------------------------------------------------------- system / ticks
@dataclass(frozen=True, slots=True, kw_only=True)
class Tick(Event):
    """Fixed-rate heartbeat that drives estimators (live: ticker thread, replay: synthesised)."""

    hz: float = 60.0


@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceHealth(Event):
    service: str
    status: str  # "ok" | "degraded" | "down" | "disabled"
    detail: str = ""


# --------------------------------------------------------------------------- game link
@dataclass(frozen=True, slots=True, kw_only=True)
class GameProcessChanged(Event):
    running: bool
    pid: int | None = None
    exe: str | None = None
    platform: str | None = None  # "steam" | "epic" | "msstore" | "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class GameWindowChanged(Event):
    hwnd: int | None
    client_rect: tuple[int, int, int, int] | None  # x, y, w, h in physical screen pixels
    focused: bool
    display_mode: str = "unknown"  # "windowed_fullscreen" | "fullscreen" | "windowed" | "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class LogLine(Event):
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LogEvent(Event):
    kind: str  # e.g. "map_hint", "match_start", "match_end", "lobby" (from data/game/log_rules.json)
    data: dict[str, Any] = field(default_factory=dict)
    rule_id: str = ""
    verified: bool = False  # rules start unverified until confirmed against a real log


# --------------------------------------------------------------------------- raw input (observed, never sent)
@dataclass(frozen=True, slots=True, kw_only=True)
class KeyEvent(Event):
    vk: int  # Windows virtual-key code, left/right resolved (VK_LSHIFT, VK_LCONTROL, ...)
    down: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class MouseDelta(Event):
    dx: int  # raw counts (same units the game receives)
    dy: int


@dataclass(frozen=True, slots=True, kw_only=True)
class MouseButton(Event):
    button: str  # "left" | "right" | "middle" | "x1" | "x2"
    down: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class InputFocusChanged(Event):
    """Whether input currently drives the in-game camera (game focused, no menu/cursor)."""

    camera_control: bool
    reason: str = ""


# --------------------------------------------------------------------------- perception
@dataclass(frozen=True, slots=True, kw_only=True)
class ScreenObserved(Event):
    """Output of the screen-state classifier (perception/state_classifier.py)."""

    screen: str  # "menu" | "lobby" | "loading" | "in_match_survivor" | "in_match_killer" | "tally" | "unknown"
    confidence: float = 1.0
    menu_overlay: bool = False  # pause / TAB / settings open on top of the match (camera not controlled)


@dataclass(frozen=True, slots=True, kw_only=True)
class MatchStateChanged(Event):
    state: MatchState
    role: Role = Role.UNKNOWN


@dataclass(frozen=True, slots=True, kw_only=True)
class MapCandidate(Event):
    map_id: str
    confidence: float  # 0..1
    source: MapSource
    raw_text: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class MapIdentified(Event):
    map_id: str
    confidence: float
    source: MapSource


@dataclass(frozen=True, slots=True, kw_only=True)
class MapAmbiguous(Event):
    """UI shows a one-tap confirm chip with these options."""

    candidates: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class InteractionObserved(Event):
    kind: InteractionKind
    active: bool  # True when the interaction starts, False when it ends
    confidence: float = 1.0
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class PlayerStatusObserved(Event):
    status: PlayerStatus
    confidence: float = 1.0


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionObserved(Event):
    """Optical-flow summary of the camera image (character + HUD masked out)."""

    translating_prob: float  # P(the camera is translating, i.e. we are actually moving)
    yaw_rate_dps: float | None = None  # measured rotation (deg/s), used for auto-calibration
    quality: float = 1.0


@dataclass(frozen=True, slots=True, kw_only=True)
class GenCounterObserved(Event):
    remaining: int  # HUD generator counter


@dataclass(frozen=True, slots=True, kw_only=True)
class TerrorRadiusObserved(Event):
    """Distance band from the Terror Radius layer/heartbeat intensity.

    DBD splits the Terror Radius into three equal-width distance bands (layers 1-3); a chase
    replaces layer 3 with chase music. ``tier`` 0 = outside TR, 1 = outer third, 2 = middle
    third, 3 = inner third.
    """

    tier: int
    in_chase: bool = False
    bpm: float | None = None
    confidence: float = 1.0
    source: TrSource = TrSource.AUDIO
    lullaby: bool = False  # lullabies are non-directional and non-graded -> tier unreliable


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectSighted(Event):
    """Future (M8): an object detected on screen, projected to a bearing/range estimate."""

    kind: str  # "generator" | "pallet" | "window" | "wall" | "exit_gate" | ...
    bearing_deg: float  # relative to camera forward, clockwise
    range_m: float | None
    confidence: float
    state: str = ""  # e.g. "completed" | "regressing" for generators


# --------------------------------------------------------------------------- user intents (from UI)
@dataclass(frozen=True, slots=True, kw_only=True)
class UserFix(Event):
    pos_m: tuple[float, float]
    radius_m: float = 3.0
    floor: str = "ground"


@dataclass(frozen=True, slots=True, kw_only=True)
class UserHeadingFix(Event):
    heading_deg: float  # world heading the camera currently faces (0 = map up, clockwise)
    sigma_deg: float = 15.0


@dataclass(frozen=True, slots=True, kw_only=True)
class UserMapOverride(Event):
    map_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class UserMarkGenerator(Event):
    gen_id: str
    state: str  # "spawned" | "completed" | "regressing" | "not_spawned" | "candidate"


@dataclass(frozen=True, slots=True, kw_only=True)
class UserMark(Event):
    """Player hotkey annotation (input/hotkeys.py; F9 generator, F10 exit gate, F8 note by default).

    Generator / exit-gate marks act as landmark fixes until perception detects interactions on its
    own (the localizer de-duplicates the two), and every mark doubles as a ground-truth label in
    recordings (docs/DATA_COLLECTION.md).
    """

    kind: str  # "generator" | "exit_gate" | "note"
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class KillerTerrorRadiusSet(Event):
    radius_m: float  # 32 default (4.6 m/s killers), 24 (4.4 m/s), 40 (Mastermind) ...


# --------------------------------------------------------------------------- estimation
@dataclass(frozen=True, slots=True, kw_only=True)
class PoseEstimate(Event):
    status: PoseStatus
    x: float = 0.0  # metres, map frame (x right, y down, origin top-left of map bbox)
    y: float = 0.0
    floor: str = "ground"
    heading_deg: float | None = None  # world heading of camera forward (0 = up, clockwise)
    sigma_m: float = float("inf")
    heading_sigma_deg: float = 180.0
    confidence: float = 0.0  # probability mass of the reported cluster
    relative: bool = False  # True in breadcrumb mode (no map / no fix): coords are relative
    hypotheses: tuple[tuple[float, float, float], ...] = ()  # (x, y, weight) ghosts for the UI
    # Transform from the dead-reckoned breadcrumb frame to the map for THIS hypothesis:
    #   world = R(psi_deg) @ (p_rel - rel_xy) + (x, y)   (R = clockwise rotation, y-down frame)
    # Lets the UI draw a smooth trail that snaps as a whole when the hypothesis changes.
    psi_deg: float | None = None
    rel_xy: tuple[float, float] | None = None


# --------------------------------------------------------------------------- serialisation
def event_types() -> dict[str, type[Event]]:
    return dict(_REGISTRY)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, tuple | list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, float) and value == float("inf"):
        return "inf"
    return value


def to_record(evt: Event) -> dict[str, Any]:
    rec: dict[str, Any] = {"type": type(evt).__name__}
    for f in dataclasses.fields(evt):
        rec[f.name] = _to_jsonable(getattr(evt, f.name))
    return rec


_HINT_CACHE: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _HINT_CACHE:
        _HINT_CACHE[cls] = typing.get_type_hints(cls)
    return _HINT_CACHE[cls]


def _coerce(value: Any, hint: Any) -> Any:
    if value is None:
        return None
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _coerce(value, non_none[0])
        return value
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        return hint(value)
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_coerce(v, args[0]) for v in value)
        if args:
            return tuple(_coerce(v, a) for v, a in zip(value, args, strict=False))
        return tuple(value)
    if hint is float:
        if value == "inf":
            return float("inf")
        if isinstance(value, int):
            return float(value)
    return value


def from_record(rec: dict[str, Any]) -> Event:
    rec = dict(rec)
    name = rec.pop("type")
    cls = _REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"Unknown event type in record: {name!r}")
    hints = _hints(cls)
    names = {f.name for f in dataclasses.fields(cls)}
    kwargs = {k: _coerce(v, hints.get(k, Any)) for k, v in rec.items() if k in names}
    return cls(**kwargs)
