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
