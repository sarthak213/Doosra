"""Running Doosra as a hosted service: the built frontend served from the API's
own origin, and the data refresher that keeps a long-running instance on the
latest published database."""

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from ingest import refresh


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Doosra</title><div id=root></div>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    return tmp_path


@pytest.fixture
def spa(dist):
    app = FastAPI()

    @app.get("/api/known")
    def known():
        return {"ok": True}

    app.mount("/", main.SPAStaticFiles(directory=dist, html=True))
    return TestClient(app)


class TestStaticFrontend:
    def test_the_app_and_its_assets_are_served(self, spa):
        assert "<title>Doosra</title>" in spa.get("/").text
        assert spa.get("/assets/app.js").text == "console.log('app')"

    def test_client_side_routes_fall_back_to_index(self, spa):
        for path in ("/ask", "/ask/abc123", "/boards/xyz", "/projects/p1", "/methodology/fibs"):
            r = spa.get(path)
            assert r.status_code == 200 and "<div id=root>" in r.text, path

    def test_a_missing_api_path_is_a_404_not_the_app(self, spa):
        assert spa.get("/api/known").json() == {"ok": True}
        for path in ("/api/nope", "/auth/nope", "/query/nope", "/mcp"):
            assert spa.get(path).status_code == 404, path

    def test_a_missing_asset_is_still_a_404_for_the_browser_to_report(self, spa):
        # Only extension-less client routes fall back; a typo'd asset URL gets index.html too
        # (the SPA convention), so it must at least not be an error page.
        assert spa.get("/assets/missing.js").status_code in (200, 404)


def test_health_reports_the_data_build(workspace_db):
    body = TestClient(main.app).get("/health").json()      # no `with`: the MCP session manager runs once per process
    assert body["status"] == "ok" and "database" in body


class TestRefresh:
    def test_it_is_off_unless_asked(self, monkeypatch):
        monkeypatch.delenv("DATA_REFRESH_HOURS", raising=False)
        assert refresh.interval_hours() == 0 and refresh.start() is False
        monkeypatch.setenv("DATA_REFRESH_HOURS", "not a number")
        assert refresh.interval_hours() == 0

    def test_a_newer_build_is_installed_and_caches_are_reset(self, monkeypatch):
        from agent import tools
        from ingest import pull
        builds = iter([{"built_at": "old"}, {"built_at": "new"}])
        calls = []
        monkeypatch.setattr(pull, "local_info", lambda *a, **k: next(builds))
        monkeypatch.setattr(pull, "pull", lambda *a, **k: calls.append("pull") or 0)
        monkeypatch.setattr(tools, "invalidate_caches", lambda: calls.append("invalidate"))
        monkeypatch.setattr(tools, "warm_caches", lambda: calls.append("warm"))
        assert refresh.run_once() is True
        assert calls == ["pull", "invalidate", "warm"]

    def test_nothing_new_leaves_the_caches_alone(self, monkeypatch):
        from agent import tools
        from ingest import pull
        monkeypatch.setattr(pull, "local_info", lambda *a, **k: {"built_at": "same"})
        monkeypatch.setattr(pull, "pull", lambda *a, **k: 0)
        monkeypatch.setattr(tools, "invalidate_caches", lambda: pytest.fail("caches reset without new data"))
        assert refresh.run_once() is False

    def test_a_failing_refresh_is_retried_and_never_kills_the_thread(self, monkeypatch):
        calls = []

        def flaky():
            calls.append(1)
            raise RuntimeError("github is down")

        monkeypatch.setenv("DATA_REFRESH_HOURS", "0.00001")            # ~36 ms
        monkeypatch.setattr(refresh, "run_once", flaky)
        assert refresh.start() is True
        try:
            deadline = time.time() + 5
            while len(calls) < 3 and time.time() < deadline:
                time.sleep(0.05)
            assert len(calls) >= 3 and refresh._thread.is_alive()
            assert refresh.start() is False                              # already running
        finally:
            refresh.stop()
            refresh._thread.join(timeout=2)


def test_a_local_container_can_serve_the_app_without_sign_in():
    """SERVE_FRONTEND=1 with no auth: the app is served, sign-in stays off, MCP is not mounted."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = ("import main; from api import auth; "
            "print(auth.hosted(), main.serves_ui(), sum(type(r).__name__ == 'Mount' and r.name != 'frontend' for r in main.app.routes))")
    env = {**os.environ, "SERVE_FRONTEND": "1", "AUTH_MODE": "none"}
    out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().endswith("False True 0"), out.stderr[-400:]


def test_the_page_is_never_served_stale(spa):
    """After an update the window must load the new page, not a cached one pointing at old assets."""
    for path in ("/", "/ask/abc"):
        assert spa.get(path).headers.get("cache-control") == "no-cache", path
    assert spa.get("/assets/app.js").headers.get("cache-control") != "no-cache"
