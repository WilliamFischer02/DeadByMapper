"""PyInstaller entry script (top-level, so the package's relative imports work when frozen)."""

from deadbymapper.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
