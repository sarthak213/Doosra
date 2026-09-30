"""The desktop app's update check: picking the newest app release, and installing it on quit
(no network: GitHub's answer and the installer launch are faked)."""

import time

import pytest
from fastapi.testclient import TestClient

import downloads
import main
import updates


def release(tag, *, draft=False, prerelease=False, exe=True, digest="sha256:" + "a" * 64):
    assets = [{"name": "notes.txt", "browser_download_url": "x", "size": 1}]
    if exe:
        v = tag.removeprefix("app-v")
        assets.append({"name": f"DoosraSetup-{v}.exe", "size": 83_000_000, "digest": digest,
                       "browser_download_url": f"https://github.com/o/r/releases/download/{tag}/DoosraSetup-{v}.exe"})
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "assets": assets,
            "body": f"What's new in {tag}", "html_url": f"https://github.com/o/r/releases/tag/{tag}"}


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "_cache", {"at": 0.0, "release": None, "error": None})
    monkeypatch.setattr(updates, "DOWNLOAD", updates.Download())
    monkeypatch.setattr(updates, "_pending", None)
    monkeypatch.setattr(updates, "quit_app", None)
    monkeypatch.setattr(updates, "VERSION", "2.4.0")
    monkeypatch.setenv("DOOSRA_HOME", str(tmp_path))


class TestNewest:
    def test_versions_compare_as_numbers(self):
        assert updates.parse("2.10.0") > updates.parse("2.9.3") and updates.parse("2.4.0-beta") == (2, 4, 0)

    def test_picks_the_highest_app_release_with_an_installer(self):
        got = updates.newest([release("data-latest", exe=False), release("app-v2.5.0"), release("app-v2.10.0"),
                              release("app-v3.0.0", draft=True), release("app-v2.11.0", prerelease=True),
                              release("app-v2.12.0", exe=False)])
        assert got["version"] == "2.10.0" and got["name"] == "DoosraSetup-2.10.0.exe" and got["sha256"] == "a" * 64

    def test_nothing_published_yet(self):
        assert updates.newest([release("data-latest", exe=False)]) is None


class TestCheck:
    def test_a_newer_release_is_offered(self, fresh, monkeypatch):
        monkeypatch.setattr(updates, "_fetch", lambda: [release("app-v2.5.0")])
        got = updates.check()
        assert got["available"] and got["latest"]["version"] == "2.5.0" and got["current"] == "2.4.0"

    def test_the_same_version_is_not(self, fresh, monkeypatch):
        monkeypatch.setattr(updates, "_fetch", lambda: [release("app-v2.4.0")])
        assert updates.check()["available"] is False

    def test_offline_says_so_and_is_cached(self, fresh, monkeypatch):
        calls = []

        def fail():
            calls.append(1)
            raise OSError("no network")
        monkeypatch.setattr(updates, "_fetch", fail)
        assert "no network" in updates.check()["error"] and not updates.check()["available"]
        assert len(calls) == 1                         # the second call used the cached answer
        updates.check(force=True)
        assert len(calls) == 2


class TestInstall:
    def test_download_then_install_on_quit(self, fresh, monkeypatch, tmp_path):
        monkeypatch.setattr(updates, "_fetch", lambda: [release("app-v2.5.0")])
        seen = {}

        def fake_download(url, dest, sha256=None, progress=None, cancel=None):
            seen.update(url=url, sha256=sha256)
            dest.write_bytes(b"installer")
            progress(9, 9)
            return dest
        monkeypatch.setattr(downloads, "download", fake_download)
        job = updates.start_download()
        end = time.time() + 5
        while job.state == "downloading" and time.time() < end:
            time.sleep(0.02)
        assert job.state == "ready" and seen["sha256"] == "a" * 64 and seen["url"].endswith("DoosraSetup-2.5.0.exe")

        closed, launched = [], []
        monkeypatch.setattr(updates, "quit_app", lambda: closed.append(True))
        monkeypatch.setattr(updates.subprocess, "Popen", lambda args, **k: launched.append(args))
        updates.install_on_quit()
        assert closed == [True]
        assert updates.run_pending_installer()
        assert launched[0][0].endswith("DoosraSetup-2.5.0.exe") and "/UPDATE=1" in launched[0] and "/SILENT" in launched[0]

    def test_nothing_to_install_until_downloaded(self, fresh):
        with pytest.raises(RuntimeError):
            updates.install_on_quit()
        assert updates.run_pending_installer() is False

    def test_no_download_when_up_to_date(self, fresh, monkeypatch):
        monkeypatch.setattr(updates, "_fetch", lambda: [release("app-v2.4.0")])
        with pytest.raises(RuntimeError, match="up to date"):
            updates.start_download()


class TestRoutes:
    def test_update_status_and_its_guard(self, fresh, monkeypatch):
        monkeypatch.setattr(updates, "_fetch", lambda: [release("app-v2.5.0")])
        monkeypatch.delenv("DOOSRA_DESKTOP", raising=False)
        assert TestClient(main.app).get("/api/desktop/update").status_code == 404
        monkeypatch.setenv("DOOSRA_DESKTOP", "1")
        got = TestClient(main.app).get("/api/desktop/update").json()
        assert got["available"] and got["download"]["state"] == "idle"
        assert TestClient(main.app).post("/api/desktop/update/install").status_code == 409
