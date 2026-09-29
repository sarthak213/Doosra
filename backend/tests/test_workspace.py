"""The user workspace: repository behaviour (scoping, cascades, search,
migration from the v0 file) and its REST routes."""

import sqlite3

import pytest
from sqlalchemy import inspect

from api import workspace as w


@pytest.fixture(autouse=True)
def fresh_workspace(workspace_db):
    yield


class TestProjectsAndNotes:
    def test_project_lifecycle(self):
        p = w.create_project("u1", "IPL auction", "scout death bowlers")
        assert w.get_project("u1", p["id"])["instructions"] == "scout death bowlers"
        assert w.update_project("u1", p["id"], name="IPL 2027")["name"] == "IPL 2027"
        assert [x["id"] for x in w.list_projects("u1")] == [p["id"]]

    def test_notes_are_capped_and_toggle(self):
        p = w.create_project("u1", "P")
        n = w.create_note("u1", p["id"], "Brief", "x" * (w.MAX_NOTE_CHARS + 500))
        assert len(n["body"]) == w.MAX_NOTE_CHARS
        w.update_note("u1", n["id"], enabled=False)
        assert w.list_notes("u1", p["id"], enabled_only=True) == []
        assert len(w.list_notes("u1", p["id"])) == 1

    def test_delete_project_cascades_but_keeps_chats(self):
        p = w.create_project("u1", "P")
        w.create_note("u1", p["id"], "n", "b")
        board = w.create_board("u1", "B", p["id"])
        chat = w.create_chat("u1", "kept", p["id"])
        assert w.delete_project("u1", p["id"])
        assert w.list_notes("u1", p["id"]) == [] and w.get_board("u1", board["id"]) is None
        assert w.get_chat("u1", chat["id"])["project_id"] is None


class TestChats:
    def test_messages_persist_in_order_with_events(self):
        c = w.create_chat("u1")
        w.add_message("u1", c["id"], "user", "who is the best death bowler?")
        w.add_message("u1", c["id"], "assistant", "Bumrah.", {"tables": [{"id": "T1"}]})
        got = w.get_chat("u1", c["id"])
        assert [m["role"] for m in got["messages"]] == ["user", "assistant"]
        assert got["messages"][1]["events"] == {"tables": [{"id": "T1"}]}
        assert got["title"] == "who is the best death bowler?"          # titled from the first question
        assert w.history("u1", c["id"])[0] == {"role": "user", "content": "who is the best death bowler?"}

    def test_rename_move_and_search(self):
        p = w.create_project("u1", "P")
        c = w.create_chat("u1")
        w.add_message("u1", c["id"], "user", "compare Kohli and Babar")
        assert w.update_chat("u1", c["id"], title="Kohli v Babar", project_id=p["id"], move=True)["project_id"] == p["id"]
        assert [x["id"] for x in w.list_chats("u1", q="babar")] == [c["id"]]        # message text
        assert [x["id"] for x in w.list_chats("u1", q="v Babar")] == [c["id"]]      # title
        assert w.list_chats("u1", q="100%") == []                                    # wildcards are literal
        assert w.list_chats("u1", unassigned=True) == []
        assert w.update_chat("u1", c["id"], project_id=None, move=True)["project_id"] is None

    def test_delete_removes_messages(self):
        c = w.create_chat("u1")
        w.add_message("u1", c["id"], "user", "hello")
        assert w.delete_chat("u1", c["id"]) and w.get_chat("u1", c["id"]) is None
        with w.engine().connect() as con:
            assert con.exec_driver_sql("SELECT COUNT(*) FROM messages").scalar() == 0


class TestBoards:
    def test_cards_round_trip_and_chat_link(self):
        card = {"id": "c1", "title": "Top scorers", "note": "", "source": {"kind": "query", "state": {"metrics": ["runs"]}},
                "chart": {"type": "bar", "x": "player", "y": "runs"}}
        b = w.create_board("u1", "B", cards=[card])
        assert w.get_board("u1", b["id"])["cards"] == [card]
        chat = w.create_chat("u1", "explain", board_id=b["id"])
        w.delete_board("u1", b["id"])
        assert w.get_chat("u1", chat["id"])["board_id"] is None


class TestIsolation:
    def test_one_user_cannot_touch_anothers_rows(self):
        p = w.create_project("alice", "P")
        n = w.create_note("alice", p["id"], "n", "b")
        c = w.create_chat("alice")
        b = w.create_board("alice", "B")
        assert w.get_project("bob", p["id"]) is None and w.list_projects("bob") == []
        assert w.get_note("bob", n["id"]) is None and w.update_note("bob", n["id"], title="x") is None
        assert w.get_chat("bob", c["id"]) is None and w.add_message("bob", c["id"], "user", "hi") is None
        assert w.get_board("bob", b["id"]) is None and w.delete_board("bob", b["id"]) is False
        assert not w.delete_project("bob", p["id"]) and not w.delete_chat("bob", c["id"])
        assert w.create_note("bob", p["id"], "n", "b") is None                       # can't attach to alice's project
        assert w.create_chat("bob", project_id=p["id"]) is None
        assert w.create_chat("bob", board_id=b["id"]) is None                        # nor explain her board
        assert w.get_project("alice", p["id"])                                        # untouched
        w.set_watchlist("alice", ["V Kohli"])
        assert w.get_watchlist("bob") == [] and w.get_watchlist("alice") == ["V Kohli"]


class TestBackupAndMigration:
    def test_export_import_round_trip(self):
        p = w.create_project("u1", "P", "brief")
        w.create_note("u1", p["id"], "n", "body")
        w.create_board("u1", "B", p["id"], cards=[{"id": "c", "source": {"kind": "snapshot"}, "chart": {}}])
        c = w.create_chat("u1", project_id=p["id"])
        w.add_message("u1", c["id"], "user", "hi")
        copy = w.import_project("u2", w.export_project("u1", p["id"]))
        assert w.get_project("u2", copy["id"])["instructions"] == "brief"
        assert len(w.list_notes("u2", copy["id"])) == 1 and len(w.list_boards("u2", copy["id"])) == 1
        assert w.list_chats("u2", project_id=copy["id"])[0]["title"] == "hi"

    def test_v0_database_is_adopted(self, tmp_path):
        path = tmp_path / "workspace.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE views (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, created TEXT NOT NULL)")
        con.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("INSERT INTO views VALUES ('v1', 'Old query', 'query', '{\"metrics\": [\"runs\"]}', '2026-01-01')")
        con.execute("INSERT INTO kv VALUES ('watchlist', '[\"V Kohli\"]')")
        con.commit()
        con.close()
        assert [v["name"] for v in w.list_views("local")] == ["Old query"]
        assert w.get_watchlist("local") == ["V Kohli"]
        assert {"projects", "chats", "boards", "notes", "messages", "usage"} <= set(inspect(w.engine()).get_table_names())
        w._engines.clear()                    # re-open: migrations don't run twice
        assert [v["name"] for v in w.list_views("local")] == ["Old query"]


def test_recorder_saves_reasoning_as_steps_but_not_live_drafts():
    from api.chat_persist import Recorder
    c = w.create_chat("u1")
    r = Recorder("u1", c["id"])
    for e in [{"type": "mode", "thinking": True}, {"type": "draft", "kind": "reasoning", "text": "weigh "},
              {"type": "draft", "kind": "reasoning", "text": "it"}, {"type": "tool_call", "tool": "leaderboard"},
              {"type": "draft", "kind": "answer", "text": "Bum"}, {"type": "final_answer", "content": "Bumrah."}]:
        r.see(e)
    r.save()
    events = w.get_chat("u1", c["id"])["messages"][0]["events"]["events"]
    assert [e["type"] for e in events] == ["thought", "tool_call"] and events[0]["content"] == "weigh it"
