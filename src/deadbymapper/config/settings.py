"""User settings (persisted to ``user_dir()/settings.json``). Pydantic-validated, forward-compatible:
unknown keys are ignored and missing keys take defaults, so old files keep loading."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .paths import user_dir

log = logging.getLogger(__name__)


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class InputSettings(_Base):
    # Mouse yaw calibration: degrees of in-game camera yaw per raw mouse count (survivor camera).
    # None = uncalibrated -> run the 360 deg calibration wizard (docs/LOCALIZATION.md).
    survivor_deg_per_count: float | None = None
    killer_deg_per_count: float | None = None
    keybind_overrides: dict[str, list[int]] = Field(default_factory=dict)  # action -> VK codes


class CaptureSettings(_Base):
    backend: str = "auto"  # "auto" | "wgc" | "dxgi"
    perception_fps: float = 15.0  # flow / prompt reading rate
    analysis_width: int = 960  # frames are downscaled to this width for CV
    hud_scale: float = 1.0  # mirror DBD's HUD scale setting (calibrated ROIs override)
    ui_scale: float = 1.0


class AudioSettings(_Base):
    enabled: bool = True
    mode: str = "auto"  # "process" (DBD only) | "device" (system loopback) | "auto"
    device_name: str | None = None
    use_visual_heart: bool = True  # read DBD's Visual Terror Radius heart when it is enabled in-game


class LocalizationSettings(_Base):
    n_particles: int = 4000
    tick_hz: float = 60.0
    default_terror_radius_m: float = 32.0


class UiSettings(_Base):
    pane_x: int | None = None
    pane_y: int | None = None
    pane_size: int = 360
    pane_opacity: float = 0.92
    click_through: bool = True
    north_up: bool = True
    palette: str = "okabe_ito"  # colour-blind-safe default
    show_trail_s: float = 25.0
    show_hypotheses: bool = True


class Settings(_Base):
    schema_version: int = 1
    input: InputSettings = Field(default_factory=InputSettings)
    capture: CaptureSettings = Field(default_factory=CaptureSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    localization: LocalizationSettings = Field(default_factory=LocalizationSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    record_sessions: bool = False


def settings_path() -> Path:
    return user_dir() / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    if not path.exists():
        return Settings()
    try:
        return Settings.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        log.exception("settings.json unreadable - using defaults (file kept as settings.bad.json)")
        path.replace(path.with_suffix(".bad.json"))
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
