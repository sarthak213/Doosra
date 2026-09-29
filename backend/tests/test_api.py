"""HTTP tests of the UI endpoints (api/routes.py, main.py) against the
fixture databases. One app client for the module: the MCP session manager
mounted in the app can only be started once per process."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from agent import graph


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def test_profile(client):
    r = client.get("/api/players/S Sharma/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["player"] == "S Sharma" and "batting" in body


def test_unknown_player_is_400_with_reason(client):
    r = client.get("/api/players/Zlatan Ibrahimovic/profile")
    assert r.status_code == 400
    assert r.json()["unresolved"] == "player"


def test_form_and_splits(client):
    assert client.get("/api/players/S Sharma/form", params={"window": 2}).json()["rows"][-1][6] == 7.5
    r = client.get("/api/players/S Sharma/splits", params={"split_by": "dismissal", "metrics": "runs"})
    assert {tuple(row) for row in r.json()["rows"]} == {("run out", 11), ("caught", 4)}


def test_query_with_filters(client):
    r = client.post("/api/query", json={"metrics": ["runs"], "limit": 1, "filters": {"tournament": "Test Bash League"}})
    assert r.status_code == 200
    assert r.json()["rows"][0][1:4] == ["S Sharma", "India", 11]


def test_unknown_filter_is_400(client):
    r = client.post("/api/query", json={"filters": {"bogus": 1}})
    assert r.status_code == 400 and "Unknown filter" in r.json()["error"]


def test_compare(client):
    body = client.post("/api/compare", json={"players": ["S Sharma", "V Kohli"], "metrics": ["runs"],
                                             "arc_metric": "runs"}).json()
    assert body["table"]["rows"] == [["S Sharma", 15], ["V Kohli", 1]]
    assert body["arc"]["rows"] == [[1, 11, 1], [2, 15, 1]]
    assert body["percentiles"]["columns"] == ["metric", "player", "value", "percentile"]


def test_matrix(client):
    body = client.post("/api/matrix", json={"x": "average", "y": "strike_rate", "min_balls": 3}).json()
    assert body["columns"][:4] == ["player", "team", "average", "strike_rate"]
    assert "medians" in body


def test_views_and_watchlist(client):
    saved = client.post("/api/views", json={"name": "My query", "kind": "query", "state": {"metrics": ["runs"]}}).json()
    assert any(v["id"] == saved["id"] for v in client.get("/api/views").json()["views"])
    client.delete(f"/api/views/{saved['id']}")
    assert all(v["id"] != saved["id"] for v in client.get("/api/views").json()["views"])
    assert client.put("/api/watchlist", json={"players": ["S Sharma", "S Sharma", " "]}).json() == {"players": ["S Sharma"]}


def test_options_and_metrics(client):
    opts = client.get("/api/options").json()
    assert "T20I" in opts["formats"] and "Test Bash League" in opts["competitions"]
    ids = {m["id"] for m in client.get("/api/metrics", params={"role": "batting"}).json()["metrics"]}
    assert {"true_sr", "match_factor"} <= ids


def test_fibs_endpoints(client):
    r = client.get("/api/fibs/report", params={"format": "T20", "gender": "male", "role": "bowling"})
    assert r.status_code == 200 and r.json()["findings"]
    r = client.get("/api/fibs/pairs", params={"metric": "dot", "role": "batting"})
    assert r.status_code == 200 and r.json()["columns"][:2] == ["player", "yr"]
    assert client.get("/api/fibs/pairs", params={"metric": "nope"}).status_code == 400
    r = client.get("/api/fibs/luck", params={"role": "bowling", "min_balls": 1, "season": "latest"})
    assert r.status_code == 200
    body = r.json()
    assert "wicket_luck" in body["columns"] and body["title"].startswith("Luckiest bowlers")


def test_coverage_endpoints_explain_missing_tables(client):
    # The fixture databases are built without Cricsheet's coverage pages.
    r = client.get("/api/coverage")
    assert r.status_code == 400 and "ingest.update" in r.json()["error"]
    assert client.get("/api/coverage/missing", params={"format": "Test"}).status_code == 400


def test_chat_stream_with_context(client, monkeypatch):
    seen = {}

    async def fake_complete(messages, tools):
        seen["system"] = messages[0]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Looks good.", tool_calls=[]))])

    monkeypatch.setattr(graph, "_complete", fake_complete)
    r = client.post("/api/chat/stream", json={"question": "What am I looking at?",
                                              "context": {"view": "matrix", "x": "average"}})
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ") and line != "data: {}"]
    assert events[-1] == {"type": "final_answer", "content": "Looks good.", "chart_data": None, "table_data": None}
    assert '"view": "matrix"' in seen["system"]


# -- workspace routes (repository behaviour is in test_workspace.py) ---------------

@pytest.fixture
def ws(workspace_db):
    main._request_log.clear()        # many chat streams in one module would trip the burst limiter



def test_project_notes_chats_boards(client, ws):
    p = client.post("/api/projects", json={"name": "Scouting", "instructions": "focus on death overs"}).json()
    n = client.post(f"/api/projects/{p['id']}/notes", json={"title": "Brief", "body": "T20 only"}).json()
    client.patch(f"/api/notes/{n['id']}", json={"enabled": False})
    chat = client.post("/api/chats", json={"project_id": p["id"]}).json()
    board = client.post("/api/boards", json={"name": "B", "project_id": p["id"], "cards": [
        {"id": "c1", "source": {"kind": "matrix", "state": {"x": "average", "y": "strike_rate", "min_balls": 3}},
         "chart": {"type": "scatter"}}]}).json()
    full = client.get(f"/api/projects/{p['id']}").json()
    assert full["notes"][0]["enabled"] is False and full["chats"][0]["id"] == chat["id"]
    assert full["boards"][0]["cards"] == 1
    assert client.delete(f"/api/projects/{p['id']}").status_code == 200
    assert client.get(f"/api/projects/{p['id']}").status_code == 404
    assert client.get(f"/api/boards/{board['id']}").status_code == 404
    assert client.get(f"/api/chats/{chat['id']}").json()["project_id"] is None

def test_unknown_ids_and_bad_cards(client, ws):
    assert client.get("/api/chats/nope").status_code == 404
    assert client.post("/api/projects/nope/notes", json={"title": "t", "body": "b"}).status_code == 404
    bad = {"name": "B", "cards": [{"id": "c", "source": {"kind": "sql"}, "chart": {}}]}
    assert client.post("/api/boards", json=bad).status_code == 400
    assert client.post("/api/projects/import", json={"format": "other"}).status_code == 400

def test_render_card_matches_the_live_endpoints(client, ws):
    q = {"role": "batting", "metrics": ["runs"], "limit": 5}
    live = client.post("/api/query", json=q).json()
    assert client.post("/api/boards/render-card", json={"source": {"kind": "query", "state": q}}).json() == live
    m = {"x": "average", "y": "strike_rate", "min_balls": 3}
    card = client.post("/api/boards/render-card", json={"source": {"kind": "matrix", "state": m}}).json()
    direct = client.post("/api/matrix", json=m).json()
    for r in (card, direct):
        r["rows"] = sorted(r["rows"], key=str)          # players tied on the sort key may come back in either order
    assert card == direct
    comp = client.post("/api/boards/render-card", json={"source": {"kind": "compare", "state": {"players": ["S Sharma"], "metrics": ["runs"]}}})
    assert comp.status_code == 200 and "columns" in comp.json()
    snap = {"kind": "snapshot", "saved_at": "2026-01-01", "table": {"columns": ["a"], "rows": [[1]]}}
    assert client.post("/api/boards/render-card", json={"source": snap}).json()["snapshot"] is True
    assert client.post("/api/boards/render-card", json={"source": {"kind": "query", "state": {"limit": "x"}}}).status_code == 400
    assert client.post("/api/boards/render-card", json={"source": {"kind": "nope"}}).status_code == 400


def _stream(client, **body):
    r = client.post("/api/chat/stream", json=body)
    assert r.status_code != 429, "rate limited"
    return r, [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ") and line != "data: {}"]


def test_saved_chat_persists_turns_and_loads_history_server_side(client, ws, monkeypatch):
    by_question = {}

    async def fake_complete(messages, tools):
        question = next(m["content"] for m in reversed(messages) if m["role"] == "user" and not m["content"].startswith("["))
        by_question[question] = messages       # the agent may call the model more than once per turn
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=f"answer to {question}", tool_calls=[]))])

    monkeypatch.setattr(graph, "_complete", fake_complete)
    chat = client.post("/api/chats", json={}).json()
    _stream(client, question="first question", chat_id=chat["id"])
    _stream(client, question="second question", chat_id=chat["id"], history=[{"role": "user", "content": "ignored"}])
    roles = [(m["role"], m["content"]) for m in by_question["second question"] if m["role"] != "system"]
    assert roles[:2] == [("user", "first question"), ("assistant", "answer to first question")]   # from the store
    assert ("user", "ignored") not in roles                                                        # not from the client
    saved = client.get(f"/api/chats/{chat['id']}").json()
    assert [(m["role"], m["content"]) for m in saved["messages"]] == [
        ("user", "first question"), ("assistant", "answer to first question"),
        ("user", "second question"), ("assistant", "answer to second question")]
    assert saved["title"] == "first question"


def test_saved_chat_keeps_tables_charts_and_earlier_table_summaries(client, ws, monkeypatch):
    from api import chat_persist

    def call(name, **args):
        fn = SimpleNamespace(name=name, arguments=json.dumps(args))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=None, tool_calls=[SimpleNamespace(id=f"c-{name}", type="function", function=fn)]))])

    replies = iter([call("leaderboard", metric="runs", limit=3), call("final_answer", answer="Top scorers are in T1."),
                    call("leaderboard", metric="runs", limit=3), call("final_answer", answer="Split done.")])
    seen = []

    async def fake_complete(messages, tools):
        seen.append(list(messages))
        return next(replies)

    monkeypatch.setattr(graph, "_complete", fake_complete)
    chat = client.post("/api/chats", json={}).json()
    _stream(client, question="top scorers", chat_id=chat["id"])
    stored = client.get(f"/api/chats/{chat['id']}").json()["messages"][1]
    types = [e["type"] for e in stored["events"]["events"]]
    assert stored["content"] == "Top scorers are in T1." and "table" in types and "tool_call" in types
    _stream(client, question="split that by phase", chat_id=chat["id"])
    assistant = next(m for m in seen[2] if m["role"] == "assistant")
    assert "Data shown with this answer" in assistant["content"] and "runs" in assistant["content"]
    assert chat_persist.table_hint(None) == ""


def test_saved_chat_unknown_id_is_404_and_stopped_turn_is_kept(client, ws, monkeypatch):
    r, _ = _stream(client, question="hi", chat_id="nope")
    assert r.status_code == 404

    async def boom(messages, tools):
        raise RuntimeError("provider down")

    monkeypatch.setattr(graph, "_complete", boom)
    chat = client.post("/api/chats", json={}).json()
    _, events = _stream(client, question="will fail", chat_id=chat["id"])
    assert events[-1]["type"] == "error"
    msgs = client.get(f"/api/chats/{chat['id']}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["content"] == ""
    assert msgs[1]["events"]["events"][-1]["type"] == "error"


def test_legacy_browser_chat_import(client, ws):
    chat = client.post("/api/chats/import", json={"turns": [
        {"question": "who scored most?", "finalAnswer": "S Sharma.", "steps": [{"type": "tool_call", "name": "leaderboard"}],
         "tables": [{"id": "T1", "columns": ["player"], "rows": [["S Sharma"]]}], "charts": []},
        {"question": "  ", "finalAnswer": "skipped"}]}).json()
    got = client.get(f"/api/chats/{chat['id']}").json()
    assert [m["role"] for m in got["messages"]] == ["user", "assistant"] and got["title"] == "Imported chat"
    kinds = [e["type"] for e in got["messages"][1]["events"]["events"]]
    assert kinds == ["tool_call", "table"]


def test_project_brief_and_enabled_notes_reach_the_agent(client, ws, monkeypatch):
    seen = []

    async def fake_complete(messages, tools):
        seen.append(messages[0]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[]))])

    monkeypatch.setattr(graph, "_complete", fake_complete)
    p = client.post("/api/projects", json={"name": "Scouting", "instructions": "Focus on death overs."}).json()
    on = client.post(f"/api/projects/{p['id']}/notes", json={"title": "On", "body": "we value economy"}).json()
    off = client.post(f"/api/projects/{p['id']}/notes", json={"title": "Off", "body": "secret idea"}).json()
    client.patch(f"/api/notes/{off['id']}", json={"enabled": False})
    chat = client.post("/api/chats", json={"project_id": p["id"]}).json()
    _stream(client, question="hello", chat_id=chat["id"])
    assert "Focus on death overs." in seen[0] and "we value economy" in seen[0] and "secret idea" not in seen[0]
    client.patch(f"/api/notes/{on['id']}", json={"body": "we now value wickets"})     # read fresh every turn
    _stream(client, question="again", chat_id=chat["id"])
    assert "we now value wickets" in seen[-1]
    loose = client.post("/api/chats", json={}).json()                                  # no project, no brief
    _stream(client, question="hello", chat_id=loose["id"])
    assert "Focus on death overs." not in seen[-1]


def test_explaining_a_board_builds_the_digest_on_the_server(client, ws, monkeypatch):
    seen = []

    async def fake_complete(messages, tools):
        seen.append(messages[0]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[]))])

    monkeypatch.setattr(graph, "_complete", fake_complete)
    p = client.post("/api/projects", json={"name": "Scouting", "instructions": "Focus on the top order."}).json()
    cards = [
        {"id": "c1", "title": "Top scorers", "note": "volume only", "chart": {},
         "source": {"kind": "query", "state": {"role": "batting", "metrics": ["runs", "balls"], "sort_by": "runs", "limit": 5}}},
        {"id": "c2", "title": "Matrix", "chart": {},
         "source": {"kind": "matrix", "state": {"x": "average", "y": "strike_rate", "min_balls": 3}}},
        {"id": "c3", "title": "Old picture", "chart": {}, "source": {
            "kind": "snapshot", "saved_at": "2026-01-02T00:00:00", "table": {"columns": ["player", "runs"], "rows": [["Z", 5], ["Y", 4]]}}}]
    board = client.post("/api/boards", json={"name": "Overview", "project_id": p["id"], "cards": cards}).json()
    chat = client.post("/api/chats", json={"project_id": p["id"], "board_id": board["id"]}).json()
    assert chat["board_id"] == board["id"]

    forged = {"kind": "board", "cards": [{"title": "FORGED 999"}], "card_id": None}
    _stream(client, question="Explain this view", chat_id=chat["id"], context=forged)
    system = seen[-1]
    assert '"kind": "board"' in system and "Overview" in system and "Top scorers" in system
    assert "FORGED 999" not in system                                     # client-sent numbers are never used
    assert "Explaining a board or chart" in system and "Focus on the top order." in system   # recipe + project brief
    assert "saved 2026-01-02" in system and "volume only" in system      # snapshot flagged, user's card note carried

    _stream(client, question="and this card?", chat_id=chat["id"], context={"card_id": "c1"})
    focused = seen[-1]
    assert '"focus_card": "Top scorers"' in focused and "Old picture" not in focused

    _stream(client, question="explain that table", chat_id=chat["id"],
            context={"view": "ask", "focus": {"panel": "Runs", "data": [{"player": "Z"}]}, "cards": "FORGED 999"})
    assert '"focus": {"panel": "Runs"' in seen[-1] and "Overview" in seen[-1] and "FORGED 999" not in seen[-1]

    client.delete(f"/api/boards/{board['id']}")                            # the chat survives, unlinked
    assert client.get(f"/api/chats/{chat['id']}").json()["board_id"] is None


def test_leaving_a_saved_chat_mid_answer_still_saves_the_answer(client, ws, monkeypatch):
    import asyncio
    from api import chat_persist, workspace

    async def slow(messages, tools):
        await asyncio.sleep(0.4)                       # a slow local model
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Finished anyway.", tool_calls=[]))])

    monkeypatch.setattr(graph, "_complete", slow)
    chat = client.post("/api/chats", json={}).json()
    workspace.add_message("local", chat["id"], "user", "slow one")

    async def go():
        resp = main._sse("slow one", [], None, None, chat_persist.Recorder("local", chat["id"]), None)
        task = chat_persist.ANSWERING[chat["id"]]
        await resp.body_iterator.aclose()              # the browser leaves before any answer arrives
        assert chat_persist.answering(chat["id"]) and not task.cancelled()
        await asyncio.wait_for(task, 10)               # ...the answer is still written
    asyncio.run(go())

    got = client.get(f"/api/chats/{chat['id']}").json()
    assert got["answering"] is False and [m["content"] for m in got["messages"]] == ["slow one", "Finished anyway."]


def test_a_chat_that_is_still_answering_refuses_another_question(client, ws, monkeypatch):
    from api import chat_persist
    chat = client.post("/api/chats", json={}).json()
    chat_persist.ANSWERING[chat["id"]] = SimpleNamespace(done=lambda: False)     # a stand-in for a running answer
    try:
        assert client.get(f"/api/chats/{chat['id']}").json()["answering"] is True
        assert client.post("/api/chat/stream", json={"question": "again", "chat_id": chat["id"]}).status_code == 409
    finally:
        chat_persist.ANSWERING.pop(chat["id"], None)
