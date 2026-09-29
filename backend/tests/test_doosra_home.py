"""Where the app keeps its data: a checkout, the installed desktop app, or DOOSRA_HOME."""

import sys
from pathlib import Path

import doosra_home


def test_a_checkout_keeps_using_backend_data(monkeypatch):
    monkeypatch.delenv("DOOSRA_HOME", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert doosra_home.home() == Path(doosra_home.__file__).resolve().parent / "data"


def test_the_desktop_app_uses_local_app_data(monkeypatch, tmp_path):
    monkeypatch.delenv("DOOSRA_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert doosra_home.home() == tmp_path / "Doosra"
    assert doosra_home.models_dir() == tmp_path / "Doosra" / "models" and (tmp_path / "Doosra" / "models").is_dir()
    assert doosra_home.settings_path() == tmp_path / "Doosra" / "settings.json"


def test_doosra_home_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("DOOSRA_HOME", str(tmp_path / "elsewhere"))
    assert doosra_home.logs_dir() == tmp_path / "elsewhere" / "logs" and doosra_home.logs_dir().is_dir()
