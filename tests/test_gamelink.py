from __future__ import annotations

import codecs
from pathlib import Path

from deadbymapper.core.bus import EventBus
from deadbymapper.core.clock import ManualClock
from deadbymapper.core.events import GameProcessChanged, LogEvent, LogLine
from deadbymapper.gamelink.game_config import parse_ini, read_display_settings
from deadbymapper.gamelink.log_parser import LogParser, LogRule
from deadbymapper.gamelink.log_tail import LogTailer
from deadbymapper.gamelink.process_watcher import ProcessWatcher, ProcInfo, platform_from_exe


# --------------------------------------------------------------------------- log tailing
def test_tailer_starts_at_end_and_reads_new_lines(tmp_path: Path):
    log = tmp_path / "DeadByDaylight.log"
    log.write_text("old line 1\nold line 2\n", encoding="utf-8")
    t = LogTailer(log)
    assert t.poll() == []  # starts at end: history is not replayed
    with log.open("a", encoding="utf-8") as f:
        f.write("new A\nnew B partial")
    assert t.poll() == ["new A"]
    with log.open("a", encoding="utf-8") as f:
        f.write(" done\n")
    assert t.poll() == ["new B partial done"]


def test_tailer_detects_recreated_file(tmp_path: Path):
    log = tmp_path / "DeadByDaylight.log"
    log.write_text("a" * 200 + "\n", encoding="utf-8")
    t = LogTailer(log)
    t.poll()
    log.unlink()
    log.write_text("fresh 1\nfresh 2\n", encoding="utf-8")  # new launch -> new (smaller) file
    assert t.poll() == ["fresh 1", "fresh 2"]
    assert t.rotations == 1


def test_tailer_utf16(tmp_path: Path):
    log = tmp_path / "DeadByDaylight.log"
    log.write_bytes(codecs.BOM_UTF16_LE + "first\n".encode("utf-16-le"))
    t = LogTailer(log, start_at_end=False)
    assert t.poll() == ["first"]
    with log.open("ab") as f:
        f.write("second\n".encode("utf-16-le"))
    assert t.poll() == ["second"]


def test_tailer_missing_file(tmp_path: Path):
    assert LogTailer(tmp_path / "nope.log").poll() == []


# --------------------------------------------------------------------------- parsing
def test_log_parser_rules_and_events():
    import re

    p = LogParser([LogRule("r1", "map_hint", re.compile(r"Map:\s*(?P<token>\w+)"), True, True)])
    bus = EventBus(synchronous=True)
    out: list[LogEvent] = []
    bus.subscribe(LogEvent, out.append)
    p.attach(bus, ManualClock())
    bus.publish(LogLine(ts_ns=1, text="[x] Loading Map: Ind_CoalTower"))
    assert out and out[0].data["token"] == "Ind_CoalTower" and out[0].verified


def test_default_rules_are_unverified_and_probe_works():
    p = LogParser.load()
    assert all(not r.verified for r in p.rules)
    rep = p.probe(["LogFoo: nothing", "LogLevel: LoadMap /Game/Maps/Hos_Treatment", "Theme Swp_ThePaleRose"])
    assert rep["n_keyword_hits"] >= 1
    assert ("Hos_Treatment", 1) in rep["tokens"]


# --------------------------------------------------------------------------- ini
def test_ue_ini_parser_handles_prefixes_and_duplicates():
    ini = parse_ini(
        "\ufeff[/Script/Engine.InputSettings]\n+AxisConfig=(A)\n+AxisConfig=(B)\n"
        "[ScalabilityGroups]\nsg.ResolutionQuality=100\n; comment\nKillerMouseSensitivity=-60\n"
    )
    assert ini.sections["/script/engine.inputsettings"]["axisconfig"] == ["(A)", "(B)"]
    assert ini.get("scalabilitygroups", "sg.resolutionquality") == "100"
    assert ini.find("sensitiv")[0][2] == "-60"


def test_read_display_settings(tmp_path: Path):
    d = tmp_path / "WindowsClient"
    d.mkdir()
    (d / "GameUserSettings.ini").write_text(
        "[/Script/DeadByDaylight.DBDGameUserSettings]\nResolutionSizeX=2560\nResolutionSizeY=1440\nFullscreenMode=1\n"
        "SurvivorMouseSensitivity=42\n",
        encoding="utf-8",
    )
    s = read_display_settings(d)
    assert s.resolution == (2560, 1440)
    assert s.fullscreen_mode == "windowed_fullscreen"
    assert any(k == "survivormousesensitivity" for _sec, k, _v in s.sensitivity_candidates)


# --------------------------------------------------------------------------- process
def test_platform_detection():
    assert platform_from_exe(r"C:\Program Files (x86)\Steam\steamapps\common\Dead by Daylight\x.exe") == "steam"
    assert platform_from_exe(r"C:\Program Files\Epic Games\DeadByDaylight\x.exe") == "epic"
    assert platform_from_exe(r"C:\XboxGames\Dead by Daylight\x.exe") == "msstore"


def test_process_watcher_publishes_changes():
    procs: list[ProcInfo] = []
    bus = EventBus(synchronous=True)
    out: list[GameProcessChanged] = []
    bus.subscribe(GameProcessChanged, out.append)
    w = ProcessWatcher(bus, ManualClock(), procs=lambda: list(procs))
    w.loop_once()
    assert out == []
    procs.append(ProcInfo(42, "DeadByDaylight-Win64-Shipping.exe", r"D:\steamapps\common\dbd\x.exe"))
    w.loop_once()
    w.loop_once()
    assert len(out) == 1 and out[0].running and out[0].platform == "steam"
    procs.clear()
    w.loop_once()
    assert len(out) == 2 and not out[1].running
