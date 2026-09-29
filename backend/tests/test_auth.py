"""Hosted mode: invite-only sign-in, per-user isolation, AI quotas, admin
routes. The OAuth round trip itself needs the providers, so these tests craft
the signed session cookie the callback would have set and check everything
that follows from it."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

import main
from agent import cancellation, graph
from api import auth, workspace


def cookie_for(uid: str) -> str:
    signer = TimestampSigner(auth.session_secret())
    return signer.sign(base64.b64encode(json.dumps({"uid": uid}).encode())).decode()


@pytest.fixture
def hosted(workspace_db, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "oauth")
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.delenv("AI_DAILY_QUESTIONS", raising=False)
    monkeypatch.delenv("AI_GLOBAL_DAILY_QUESTIONS", raising=False)
    main._request_log.clear()
    workspace.add_invite("alice@example.com", "boss@example.com")
    workspace.add_invite("bob@example.com", None)
    return SimpleNamespace(alice=auth.admit("Alice@Example.com", "Alice"), bob=auth.admit("bob@example.com", "Bob"),
                           boss=auth.admit("boss@example.com", "Boss"))


def as_user(user: dict) -> TestClient:
    c = TestClient(main.app)          # no `with`: the MCP session manager belongs to the shared app client
    c.cookies.set("session", cookie_for(user["id"]))
    return c


def fake_llm(monkeypatch):
    async def fake_complete(messages, tools):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[]))])
    monkeypatch.setattr(graph, "_complete", fake_complete)


class TestAdmission:
    def test_only_invited_or_admin_emails_get_in(self, hosted):
        assert auth.admit("stranger@example.com", "S") is None
        assert auth.admit("", None) is None
        assert hosted.alice["email"] == "alice@example.com" and hosted.alice["role"] == "member"   # case-insensitive
        assert hosted.boss["role"] == "admin"
        assert auth.admit("alice@example.com", "Alice B")["name"] == "Alice B"      # later sign-ins refresh the profile
        assert [i["accepted"] for i in workspace.list_invites() if i["email"] == "alice@example.com"] == [True]

    def test_revoking_an_invite_blocks_sign_in_and_ends_the_session(self, hosted):
        bob = as_user(hosted.bob)
        assert bob.get("/api/projects").status_code == 200
        workspace.remove_invite("bob@example.com")
        assert auth.admit("bob@example.com", "Bob") is None
        assert bob.get("/api/projects").status_code == 401

    def test_a_missing_or_short_secret_is_refused_at_startup(self, hosted, monkeypatch):
        monkeypatch.delenv("SESSION_SECRET", raising=False)
        with pytest.raises(RuntimeError):
            auth.check_config()
        monkeypatch.setenv("SESSION_SECRET", "x" * 32)
        auth.check_config()


class TestSessions:
    def test_api_needs_a_session_but_health_and_me_do_not(self, hosted):
        anon = TestClient(main.app)
        assert anon.get("/api/projects").status_code == 401
        assert anon.post("/api/query", json={"metrics": ["runs"]}).status_code == 401
        assert anon.post("/api/chat/stream", json={"question": "hi"}).status_code == 401
        assert anon.get("/health").status_code == 200
        assert anon.get("/api/me").json() == {"mode": "oauth", "user": None}

    def test_a_forged_or_unknown_session_is_refused(self, hosted):
        forged = TestClient(main.app)
        forged.cookies.set("session", "not-a-real-cookie")
        assert forged.get("/api/projects").status_code == 401
        ghost = TestClient(main.app)
        ghost.cookies.set("session", cookie_for("u_nobody"))
        assert ghost.get("/api/projects").status_code == 401

    def test_me_reports_the_user_and_quota(self, hosted):
        me = as_user(hosted.alice).get("/api/me").json()
        assert me["user"]["email"] == "alice@example.com"
        assert me["quota"] == {"used": 0, "limit": auth.DEFAULT_HOSTED_DAILY_QUESTIONS}

    def test_sign_out_clears_the_session(self, hosted):
        c = as_user(hosted.alice)
        assert c.get("/api/projects").status_code == 200
        r = c.post("/auth/logout")
        cleared = r.headers["set-cookie"]                     # the session cookie is expired, so the browser drops it
        assert cleared.startswith("session=null") and "Max-Age=0" in cleared or "expires=" in cleared.lower()

    def test_local_mode_is_open(self, workspace_db, monkeypatch):
        monkeypatch.delenv("AUTH_MODE", raising=False)
        c = TestClient(main.app)
        assert c.get("/api/projects").status_code == 200
        assert c.get("/api/me").json()["mode"] == "none"

    def test_providers_lists_only_configured_ones(self, hosted, monkeypatch):
        for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
            monkeypatch.delenv(k, raising=False)
        c = TestClient(main.app)
        assert c.get("/auth/providers").json()["providers"] == []
        assert c.get("/auth/login/google", follow_redirects=False).status_code == 404
        monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
        monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
        assert [p["id"] for p in c.get("/auth/providers").json()["providers"]] == ["github"]


class TestIsolation:
    def test_users_cannot_reach_each_others_work_through_the_api(self, hosted, monkeypatch):
        fake_llm(monkeypatch)
        a, b = as_user(hosted.alice), as_user(hosted.bob)
        p = a.post("/api/projects", json={"name": "Alice scouting", "instructions": "secret plan"}).json()
        n = a.post(f"/api/projects/{p['id']}/notes", json={"title": "t", "body": "private"}).json()
        chat = a.post("/api/chats", json={"project_id": p["id"]}).json()
        board = a.post("/api/boards", json={"name": "B", "project_id": p["id"], "cards": []}).json()
        a.post("/api/chat/stream", json={"question": "hello", "chat_id": chat["id"]})
        gets = [f"/api/projects/{p['id']}", f"/api/projects/{p['id']}/export", f"/api/chats/{chat['id']}", f"/api/boards/{board['id']}"]
        assert [b.get(u).status_code for u in gets] == [404] * 4
        assert b.patch(f"/api/notes/{n['id']}", json={"body": "hacked"}).status_code == 404
        assert b.delete(f"/api/notes/{n['id']}").status_code == 404
        assert b.patch(f"/api/chats/{chat['id']}", json={"title": "x"}).status_code == 404
        assert b.delete(f"/api/boards/{board['id']}").status_code == 404
        assert b.delete(f"/api/projects/{p['id']}").status_code == 404
        assert b.post(f"/api/projects/{p['id']}/notes", json={"title": "t", "body": "b"}).status_code == 404
        assert b.post("/api/chats", json={"project_id": p["id"]}).status_code == 404
        assert b.post("/api/chats", json={"board_id": board["id"]}).status_code == 404        # can't explain her board
        assert b.post("/api/chat/stream", json={"question": "hi", "chat_id": chat["id"]}).status_code == 404
        assert b.get("/api/chats").json()["chats"] == [] and b.get("/api/projects").json()["projects"] == []
        assert a.get(f"/api/projects/{p['id']}").json()["notes"][0]["body"] == "private"       # untouched

    def test_watchlists_and_saved_views_are_private(self, hosted):
        a, b = as_user(hosted.alice), as_user(hosted.bob)
        a.put("/api/watchlist", json={"players": ["V Kohli"]})
        a.post("/api/views", json={"name": "mine", "kind": "query", "state": {}})
        assert b.get("/api/watchlist").json() == {"players": []} and b.get("/api/views").json() == {"views": []}


class TestQuotas:
    def test_the_next_question_after_the_daily_limit_is_refused(self, hosted, monkeypatch):
        fake_llm(monkeypatch)
        monkeypatch.setenv("AI_DAILY_QUESTIONS", "2")
        a, b = as_user(hosted.alice), as_user(hosted.bob)
        assert [a.post("/api/chat/stream", json={"question": "q"}).status_code for _ in range(2)] == [200, 200]
        r = a.post("/api/chat/stream", json={"question": "q"})
        assert r.status_code == 429 and "2 AI questions" in r.json()["detail"]
        assert b.post("/api/chat/stream", json={"question": "q"}).status_code == 200            # the allowance is per user
        assert a.get("/api/me").json()["quota"] == {"used": 2, "limit": 2}
        assert a.post("/api/projects", json={"name": "other routes are not metered"}).status_code == 200

    def test_the_whole_instance_has_a_ceiling_too(self, hosted, monkeypatch):
        fake_llm(monkeypatch)
        monkeypatch.setenv("AI_GLOBAL_DAILY_QUESTIONS", "2")
        a, b = as_user(hosted.alice), as_user(hosted.bob)
        assert a.post("/api/chat/stream", json={"question": "q"}).status_code == 200
        assert b.post("/api/chat/stream", json={"question": "q"}).status_code == 200
        assert a.post("/api/chat/stream", json={"question": "q"}).status_code == 429

    def test_local_mode_is_unmetered_unless_asked(self, workspace_db, monkeypatch):
        fake_llm(monkeypatch)
        monkeypatch.delenv("AUTH_MODE", raising=False)
        monkeypatch.delenv("AI_DAILY_QUESTIONS", raising=False)
        main._request_log.clear()
        assert auth.daily_limit() == 0
        assert all(TestClient(main.app).post("/api/chat/stream", json={"question": "q"}).status_code == 200 for _ in range(3))


class TestAdmin:
    def test_only_admins_manage_invites(self, hosted):
        assert as_user(hosted.alice).get("/api/admin/invites").status_code == 403
        assert TestClient(main.app).get("/api/admin/invites").status_code == 401
        boss = as_user(hosted.boss)
        assert boss.post("/api/admin/invites", json={"email": "New@Example.com"}).json()["email"] == "new@example.com"
        listed = boss.get("/api/admin/invites").json()
        assert "new@example.com" in [i["email"] for i in listed["invites"]] and listed["admins"] == ["boss@example.com"]
        assert boss.post("/api/admin/invites", json={"email": "not-an-email"}).status_code == 422
        assert boss.delete("/api/admin/invites/new@example.com").status_code == 200
        assert boss.delete("/api/admin/invites/new@example.com").status_code == 404


def test_only_the_owner_can_cancel_a_request(hosted):
    cancellation.set_owner("req-1", hosted.alice["id"])
    assert as_user(hosted.bob).post("/query/cancel/req-1").status_code == 403
    assert not cancellation.is_cancelled("req-1")
    assert as_user(hosted.alice).post("/query/cancel/req-1").status_code == 200
    assert cancellation.is_cancelled("req-1")
    cancellation.clear("req-1")


def test_a_hosted_instance_does_not_serve_the_mcp_endpoint():
    """The MCP mount is decided at import, so check it in a fresh interpreter."""
    code = "import main; print(sum(type(r).__name__ == 'Mount' and r.name != 'frontend' for r in main.app.routes))"
    env = {**os.environ, "AUTH_MODE": "oauth", "SESSION_SECRET": "s" * 40}
    out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().endswith("0"), out.stderr[-500:]
    assert sum(type(r).__name__ == "Mount" and r.name != "frontend" for r in main.app.routes) == 1   # local mode still serves it
