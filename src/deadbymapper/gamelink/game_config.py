"""Reads DBD's Unreal Engine .ini files (read-only) for settings that matter to us.

UE ini quirks handled: section headers like ``[/Script/Engine.InputSettings]``, duplicate keys,
array prefixes (``+Key=``, ``-Key=``, ``.Key=``, ``!Key=``), case-insensitive keys, BOMs.
We search by pattern rather than hard-coding key names, because DBD's exact keys are not
documented and change across patches (community posts mention e.g. ``KillerMouseSensitivity``
in GameUserSettings.ini and axis ``Scale=`` values in Input.ini).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config.paths import dbd_config_dirs

_SECTION = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*$")
_KV = re.compile(r"^\s*(?P<prefix>[+\-.!]?)(?P<key>[^=;#]+?)\s*=\s*(?P<val>.*?)\s*$")


@dataclass
class IniFile:
    path: Path | None
    sections: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def get(self, section: str, key: str) -> str | None:
        vals = self.sections.get(section.lower(), {}).get(key.lower())
        return vals[-1] if vals else None

    def find(self, key_pattern: str) -> list[tuple[str, str, str]]:
        rx = re.compile(key_pattern, re.IGNORECASE)
        out = []
        for sec, kv in self.sections.items():
            for k, vals in kv.items():
                if rx.search(k):
                    out.extend((sec, k, v) for v in vals)
        return out


def parse_ini(text: str, path: Path | None = None) -> IniFile:
    ini = IniFile(path)
    current = ini.sections.setdefault("", {})
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if not line or line[0] in ";#":
            continue
        m = _SECTION.match(line)
        if m:
            current = ini.sections.setdefault(m.group("name").strip().lower(), {})
            continue
        kv = _KV.match(line)
        if kv:
            current.setdefault(kv.group("key").strip().lower(), []).append(kv.group("val"))
    return ini


def read_ini(path: Path) -> IniFile:
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8", errors="replace")
    return parse_ini(text, path)


@dataclass(frozen=True, slots=True)
class DbdDisplaySettings:
    resolution: tuple[int, int] | None
    fullscreen_mode: str | None  # "fullscreen" | "windowed_fullscreen" | "windowed"
    sensitivity_candidates: tuple[tuple[str, str, str], ...]  # (section, key, value) for the calibration UI
    source: Path | None


_FS_MODES = {"0": "fullscreen", "1": "windowed_fullscreen", "2": "windowed"}


def read_display_settings(config_dir: Path | None = None) -> DbdDisplaySettings:
    dirs = [config_dir] if config_dir else dbd_config_dirs()
    for d in dirs:
        gus = d / "GameUserSettings.ini"
        if not gus.exists():
            continue
        ini = read_ini(gus)
        rx = ini.find(r"^resolutionsizex$")
        ry = ini.find(r"^resolutionsizey$")
        res = (int(rx[-1][2]), int(ry[-1][2])) if rx and ry and rx[-1][2].isdigit() and ry[-1][2].isdigit() else None
        fs = ini.find(r"^fullscreenmode$")
        mode = _FS_MODES.get(fs[-1][2]) if fs else None
        sens = tuple(ini.find(r"sensitiv"))
        return DbdDisplaySettings(res, mode, sens, gus)
    return DbdDisplaySettings(None, None, (), None)
