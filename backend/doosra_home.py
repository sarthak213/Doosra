"""
Where Doosra keeps its data: the cricket database, the workspace (chats,
projects, boards), downloaded models, settings and logs.

- DOOSRA_HOME, if set, wins.
- The installed desktop app (a frozen build) uses %LOCALAPPDATA%\\Doosra, so
  data survives reinstalls and never lives inside Program Files.
- A source checkout keeps using backend/data, as it always has.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_CHECKOUT_DATA = Path(__file__).resolve().parent / "data"


def frozen() -> bool:
    """True inside the packaged desktop app (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def home() -> Path:
    env = os.environ.get("DOOSRA_HOME")
    if env:
        return Path(env)
    if frozen():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Doosra"
    return _CHECKOUT_DATA


def _dir(*parts: str) -> Path:
    path = home().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    return _dir()


def models_dir() -> Path:
    return _dir("models")


def logs_dir() -> Path:
    return _dir("logs")


def settings_path() -> Path:
    return data_dir() / "settings.json"
