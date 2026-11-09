# PyInstaller spec - one-folder, windowed build of DeadByMapper.
#   uv run pyinstaller packaging/deadbymapper.spec --noconfirm --clean
# Output: dist/DeadByMapper/DeadByMapper.exe  (install with packaging/install.ps1)
#
# One-folder (not one-file) on purpose: instant startup (no temp unpacking), fewer AV false
# positives, and Qt's QML plugins load from disk normally.
# ruff: noqa
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is injected by PyInstaller)
ICON = ROOT / "packaging" / "build" / "app.ico"

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=[
        (str(ROOT / "data" / "maps"), "data/maps"),
        (str(ROOT / "data" / "game"), "data/game"),
        (str(ROOT / "data" / "schemas"), "data/schemas"),
        (str(ROOT / "src" / "deadbymapper" / "ui" / "qml"), "deadbymapper/ui/qml"),
    ],
    hiddenimports=collect_submodules("deadbymapper")
    + ["PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtSvg", "PySide6.QtQuickControls2"],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DeadByMapper",
    console=False,
    icon=str(ICON) if ICON.exists() else None,
    upx=False,  # UPX-packed binaries trigger antivirus heuristics
)
coll = COLLECT(exe, a.binaries, a.datas, name="DeadByMapper", upx=False)  # noqa: F821
