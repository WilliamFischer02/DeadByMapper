"""QObject view-model between the App and QML.

Performance rules
    * One signal per property group, emitted ONLY when the value changed - so QML Repeaters do not
      rebuild delegates every frame.
    * Fast-moving data (player pose) refreshes at 30 Hz; QML smooths it with short animations.
    * Slow data (map geometry, generators, chips) is compared and only re-emitted on change.
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ..app import TEST_MAP_ID, App, AppSnapshot
from ..core.events import (
    MatchState,
    PoseStatus,
    Role,
    UserFix,
    UserHeadingFix,
    UserMapOverride,
    UserMarkGenerator,
    UserTrialControl,
)

# Sensor chips in reading order (left to right, top to bottom on the dashboard). Keys are the
# ``Service.name`` strings. "Demo" only appears in demo mode; "Map data" is always appended last.
CHIP_LABELS: list[tuple[str, str]] = [
    ("gamelink.process", "Process"),
    ("gamelink.window", "Window"),
    ("gamelink.log", "Log"),
    ("input.raw", "Input"),
    ("capture.wgc", "Capture"),
    ("audio.heartbeat", "Audio"),
    ("demo", "Demo"),
    ("ticker", "Clock"),
]
# Internal plumbing: no chip while healthy, a chip only when it fails (the user can do nothing about it
# otherwise, and a healthy clock is noise).
QUIET_UNLESS_FAILING = frozenset({"ticker"})
# Plain-language detail for services that report "ok" without one.
OK_DETAIL = {
    "gamelink.log": "Following the game log",
    "input.raw": "Keyboard + mouse, read-only",
    "capture.wgc": "Capturing the game window",
    "audio.heartbeat": "Listening for the heartbeat",
    "demo": "Simulated survivor on the Test Arena",
    "ticker": "Running",
}
MAP_STATUS_DETAIL = {
    "synthetic": "test layout",
    "verified": "verified layout",
    "partial": "partial layout",
    "stub": "no layout yet",
}
TEST_GROUP = "TEST"
# Player-facing words for ids that come from the game link (never show raw ids).
PLATFORM_LABEL = {"steam": "Steam", "epic": "Epic Games", "msstore": "Microsoft Store", "simulated": "Simulated"}
DISPLAY_LABEL = {
    "fullscreen": "Exclusive fullscreen",
    "windowed_fullscreen": "Windowed fullscreen",
    "windowed": "Windowed",
}


def _realm_key(realm: str) -> str:
    return realm.removeprefix("The ").casefold()


def _r(v: float, q: float = 0.05) -> float:
    return round(v / q) * q if math.isfinite(v) else -1.0


class AppBridge(QObject):
    modeChanged = Signal()
    linkChanged = Signal()
    chipsChanged = Signal()
    mapChanged = Signal()
    gensChanged = Signal()
    gatesChanged = Signal()
    playerChanged = Signal()
    trailChanged = Signal()
    crumbsChanged = Signal()
    hypothesesChanged = Signal()
    terrorChanged = Signal()
    hudChanged = Signal()
    libraryChanged = Signal()
    uiChanged = Signal()

    def __init__(self, app: App, *, refresh_hz: float = 30.0) -> None:
        super().__init__()
        self._app = app
        self._s: dict[str, Any] = {
            "mode": "idle",
            "link": {},
            "chips": [],
            "map": {},
            "gens": [],
            "gates": [],
            "player": {},
            "trail": [],
            "crumbs": [],
            "hyp": [],
            "terror": {},
            "hud": {},
            "library": {},
        }
        self._click_through = app.mode != "demo" and app.settings.ui.click_through
        self._pickable_maps = self._pickable()  # the registry is static for the app's lifetime
        self._tick = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(int(1000 / refresh_hz))
        self.refresh()

    # ------------------------------------------------------------------ refresh
    def _set(self, key: str, value: Any, signal: Signal) -> None:  # type: ignore[valid-type]
        if self._s.get(key) != value:
            self._s[key] = value
            signal.emit()  # type: ignore[attr-defined]

    @Slot()
    def refresh(self) -> None:
        snap = self._app.snapshot()
        self._tick += 1
        m = snap.match
        mode = {MatchState.IN_MATCH: "match", MatchState.POST_MATCH: "review"}.get(m.match_state, "idle")
        self._set("mode", mode, self.modeChanged)
        self._set("link", self._link(snap), self.linkChanged)
        self._set("chips", self._chips(snap), self.chipsChanged)
        self._set("map", self._map(snap), self.mapChanged)
        self._set(
            "gens",
            [
                {
                    "id": g.id,
                    "x": g.x,
                    "y": g.y,
                    "state": g.state,
                    "sigma": g.sigma_m,
                    "fixed": g.fixed,
                    "rank": g.recommended_rank or 0,
                }
                for g in m.gens
            ],
            self.gensChanged,
        )
        self._set(
            "gates",
            [
                {
                    "id": g.id,
                    "x": g.x,
                    "y": g.y,
                    "state": g.state,
                    "facing": g.facing_deg if g.facing_deg is not None else -1,
                }
                for g in m.gates
            ],
            self.gatesChanged,
        )
        self._set("player", self._player(snap), self.playerChanged)
        if self._tick % 3 == 0:
            self._set("trail", self._trail(m), self.trailChanged)
            pose = m.pose
            if pose is not None and pose.relative:
                path = list(self._app.localizer.rel_path[-300:])  # slice copy: safe across threads (GIL)
                self._set("crumbs", [{"x": round(x, 2), "y": round(y, 2)} for _t, x, y in path], self.crumbsChanged)
            elif self._s["crumbs"]:
                self._set("crumbs", [], self.crumbsChanged)
        if self._tick % 6 == 0:
            p = m.pose
            hyp = []
            if p is not None and p.status in (PoseStatus.SEARCHING, PoseStatus.CONVERGING, PoseStatus.LOST):
                hyp = [{"x": _r(x, 0.5), "y": _r(y, 0.5), "w": round(w, 2)} for x, y, w in p.hypotheses[1:] if w > 0.06]
            self._set("hyp", hyp, self.hypothesesChanged)
        t = m.terror
        terror = (
            {
                "active": True,
                "tier": t.tier,
                "inner": t.inner_m,
                "outer": t.outer_m,
                "chase": t.in_chase,
                "bpm": t.bpm or 0.0,
                "lullaby": t.lullaby,
            }
            if t is not None and t.age_s < 3.0
            else {"active": False}
        )
        self._set("terror", terror, self.terrorChanged)
        top = m.strategy.ranking[0] if m.strategy and m.strategy.ranking else None
        self._set(
            "hud",
            {
                "gensRemaining": m.gens_remaining if m.gens_remaining is not None else -1,
                "elapsed": int(m.match_elapsed_s),
                "next": top.gen_id if top else "",
                "nextReason": top.reasons[0] if top else "",
                "interaction": m.active_interaction.value if m.active_interaction else "",
                "calibrated": snap.calibrated,
                "ambiguous": list(snap.ambiguous_maps),
                "trialManual": m.manual_trial,
                "inTrial": m.match_state == MatchState.IN_MATCH,
            },
            self.hudChanged,
        )
        if self._tick % 30 == 1:
            self._set("library", self._library(snap), self.libraryChanged)

    def _trail(self, m: Any) -> list[dict[str, float]]:
        """Recent route = the dead-reckoned breadcrumb mapped through the CURRENT hypothesis.
        Smooth by construction (no estimator jitter) and it snaps as a whole if the lock changes."""
        p = m.pose
        if (
            p is None
            or p.relative
            or p.psi_deg is None
            or p.rel_xy is None
            or p.status not in (PoseStatus.LOCKED, PoseStatus.CONVERGING, PoseStatus.LOST)
        ):
            return []
        path = list(self._app.localizer.rel_path[-400:])  # ~40 s of movement at 10 Hz
        if len(path) < 2:
            return []
        c, s = math.cos(math.radians(p.psi_deg)), math.sin(math.radians(p.psi_deg))
        rx, ry = p.rel_xy
        step = max(1, len(path) // 160)
        out = [
            {"x": round(c * (x - rx) - s * (y - ry) + p.x, 2), "y": round(s * (x - rx) + c * (y - ry) + p.y, 2)}
            for _t, x, y in path[::step]
        ]
        out.append({"x": round(p.x, 2), "y": round(p.y, 2)})
        return out

    def _link(self, snap: AppSnapshot) -> dict[str, Any]:
        w = snap.window
        return {
            "running": snap.game_running,
            "platform": snap.platform or "",
            "platformLabel": PLATFORM_LABEL.get(snap.platform or "", ""),
            "mode": snap.mode,
            "resolution": f"{w[2]}×{w[3]}" if w else "",
            "display": snap.display_mode,
            "displayLabel": DISPLAY_LABEL.get(snap.display_mode, ""),
            "version": snap.version,
        }

    def _chips(self, snap: AppSnapshot) -> list[dict[str, str]]:
        """One chip per sensor with a human-readable reason, so a failure is visible at a glance."""
        out = []
        demo = snap.mode == "demo"
        for key, label in CHIP_LABELS:
            if key == "demo" and not demo:
                continue
            if key in snap.health:
                status, detail = snap.health[key]
                if key in QUIET_UNLESS_FAILING and status in ("ok", "disabled"):
                    continue
                detail = detail or self._default_detail(key, status, snap)
            elif key in QUIET_UNLESS_FAILING:
                continue
            else:
                status, detail = "off", "Not used in demo mode" if demo else "Not started"
            out.append({"key": key, "label": label, "status": status, "detail": detail})
        mp = snap.match.map
        if mp is None:
            status, detail = "off", "No map picked yet"
        else:
            status = "ok" if mp.status in ("verified", "synthetic") else "degraded"
            detail = f"{mp.name} · {MAP_STATUS_DETAIL.get(mp.status, mp.status)}"
        out.append({"key": "map", "label": "Map data", "status": status, "detail": detail})
        return out

    @staticmethod
    def _default_detail(key: str, status: str, snap: AppSnapshot) -> str:
        if status == "down":
            return "Stopped"
        if status == "degraded":
            return "Degraded"
        if status == "disabled":
            return "Disabled"
        if key == "gamelink.process":
            if not snap.game_running:
                return "Game not running"
            platform = PLATFORM_LABEL.get(snap.platform or "", "")
            return f"Game running · {platform}" if platform else "Game running"
        if key == "gamelink.window":
            w = snap.window
            if not w:
                return "No game window yet"
            mode = DISPLAY_LABEL.get(snap.display_mode, "")
            return f"{w[2]}×{w[3]} · {mode}" if mode else f"{w[2]}×{w[3]}"
        return OK_DETAIL.get(key, "Running")

    def _pickable(self) -> list[dict[str, str]]:
        """Maps the player can pick by hand: the Test Arena first (the only map with layout data
        today), then every live map sorted by realm and name."""
        reg = self._app.registry
        out = []
        if TEST_MAP_ID in reg:
            t = reg.get(TEST_MAP_ID)
            out.append({"id": t.id, "name": t.name, "realm": t.realm, "status": t.status, "group": TEST_GROUP})
        for m in sorted(reg.available(), key=lambda m: (_realm_key(m.realm), m.name.casefold())):
            if m.id != TEST_MAP_ID:
                out.append({"id": m.id, "name": m.name, "realm": m.realm, "status": m.status, "group": m.realm})
        return out

    def _map(self, snap: AppSnapshot) -> dict[str, Any]:
        mp = snap.match.map
        if mp is None:
            return {"id": "", "name": "", "realm": "", "status": "", "w": 0.0, "h": 0.0, "bounds": [], "structures": []}
        return {
            "id": mp.id,
            "name": mp.name,
            "realm": mp.realm,
            "status": mp.status,
            "w": mp.width_m or 0.0,
            "h": mp.height_m or 0.0,
            "bounds": [{"x": x, "y": y} for x, y in (mp.bounds_polygon_m or [])],
            "structures": [
                {
                    "type": s.type,
                    "solid": s.solid,
                    "name": s.name or "",
                    "points": [{"x": x, "y": y} for x, y in s.polygon_m],
                }
                for s in mp.structures
                if s.floor == "ground"
            ],
        }

    def _player(self, snap: AppSnapshot) -> dict[str, Any]:
        p = snap.match.pose
        if p is None:
            return {
                "status": "no_map",
                "x": 0.0,
                "y": 0.0,
                "heading": -1.0,
                "sigma": -1.0,
                "conf": 0.0,
                "relative": True,
            }
        return {
            "status": p.status.value,
            "x": round(p.x, 2),
            "y": round(p.y, 2),
            "heading": round(p.heading_deg, 1) if p.heading_deg is not None and p.heading_sigma_deg < 60 else -1.0,
            "sigma": round(p.sigma_m, 2) if math.isfinite(p.sigma_m) else -1.0,
            "conf": round(p.confidence, 2),
            "relative": p.relative,
        }

    def _library(self, snap: AppSnapshot) -> dict[str, Any]:
        reg = self._app.registry
        maps = sorted(reg.available(), key=lambda m: (m.realm, m.name))
        return {
            "counts": snap.map_counts,
            "maps": [{"id": m.id, "name": m.name, "realm": m.realm, "status": m.status} for m in maps],
        }

    # ------------------------------------------------------------------ properties
    mode = Property(str, lambda self: self._s["mode"], notify=modeChanged)
    link = Property("QVariantMap", lambda self: self._s["link"], notify=linkChanged)
    chips = Property("QVariantList", lambda self: self._s["chips"], notify=chipsChanged)
    map = Property("QVariantMap", lambda self: self._s["map"], notify=mapChanged)
    gens = Property("QVariantList", lambda self: self._s["gens"], notify=gensChanged)
    gates = Property("QVariantList", lambda self: self._s["gates"], notify=gatesChanged)
    player = Property("QVariantMap", lambda self: self._s["player"], notify=playerChanged)
    trail = Property("QVariantList", lambda self: self._s["trail"], notify=trailChanged)
    crumbs = Property("QVariantList", lambda self: self._s["crumbs"], notify=crumbsChanged)
    hypotheses = Property("QVariantList", lambda self: self._s["hyp"], notify=hypothesesChanged)
    terror = Property("QVariantMap", lambda self: self._s["terror"], notify=terrorChanged)
    hud = Property("QVariantMap", lambda self: self._s["hud"], notify=hudChanged)
    library = Property("QVariantMap", lambda self: self._s["library"], notify=libraryChanged)
    pickableMaps = Property("QVariantList", lambda self: self._pickable_maps, constant=True)
    clickThrough = Property(bool, lambda self: self._click_through, notify=uiChanged)
    isDemo = Property(bool, lambda self: self._app.mode == "demo", constant=True)
    # The dashboard stays up during demo trials and during trials the player started by hand
    # (that is where the Stop button lives); detected trials hide it so only the pane remains.
    showDashboardInMatch = Property(
        bool, lambda self: self._app.mode == "demo" or bool(self._s["hud"].get("trialManual")), notify=hudChanged
    )
    genHotkey = Property(str, lambda self: self._hotkey_name("generator"), constant=True)

    def _hotkey_name(self, kind: str) -> str:
        from ..input.keymap import VK_NAME

        for vk, k in self._app.keymap.hotkeys.items():
            if k == kind:
                return VK_NAME.get(vk, f"0x{vk:02X}")
        return ""

    # ------------------------------------------------------------------ intents (QML -> app)
    def _now(self) -> int:
        return self._app.clock.now_ns()

    @Slot(float, float)
    def tapMap(self, x_m: float, y_m: float) -> None:
        self._app.bus.publish(UserFix(ts_ns=self._now(), pos_m=(float(x_m), float(y_m)), radius_m=3.0))

    @Slot(float)
    def setHeading(self, heading_deg: float) -> None:
        self._app.bus.publish(UserHeadingFix(ts_ns=self._now(), heading_deg=float(heading_deg) % 360.0))

    @Slot(str)
    def confirmMap(self, map_id: str) -> None:
        self._app.bus.publish(UserMapOverride(ts_ns=self._now(), map_id=map_id))

    @Slot()
    def startTrial(self) -> None:
        """Dashboard "Start trial": the pane appears now, before automatic detection exists (M3)."""
        self._app.bus.publish(UserTrialControl(ts_ns=self._now(), action="start", role=Role.SURVIVOR))

    @Slot()
    def stopTrial(self) -> None:
        self._app.bus.publish(UserTrialControl(ts_ns=self._now(), action="stop"))

    @Slot(str, str)
    def markGen(self, gen_id: str, state: str) -> None:
        self._app.bus.publish(UserMarkGenerator(ts_ns=self._now(), gen_id=gen_id, state=state))

    @Slot(bool)
    def setClickThrough(self, on: bool) -> None:
        if on != self._click_through:
            self._click_through = on
            self.uiChanged.emit()
