"""Tests of the agent's generic tools: the run_sql guard, name search, and
the schema/stats endpoints. run_sql's guard is a security boundary -- the
external-access and forbidden-keyword tests here must not regress."""

from agent import tools


class TestRunSqlGuard:
    def test_select_allowed(self):
        r = tools.run_sql("SELECT COUNT(*) AS n FROM matches")
        assert "error" not in r
        assert r["columns"] == ["n"]
        assert r["rows"] == [[3]]

    def test_with_allowed(self):
        r = tools.run_sql("WITH t AS (SELECT 1 AS x) SELECT x FROM t")
        assert "error" not in r

    def test_delete_rejected(self):
        assert "error" in tools.run_sql("DELETE FROM matches")

    def test_update_rejected(self):
        assert "error" in tools.run_sql("UPDATE matches SET winner = 'x'")

    def test_non_select_rejected(self):
        assert "error" in tools.run_sql("INSERT INTO matches VALUES ('x')")

    def test_forbidden_keyword_in_string_literal_allowed(self):
        # A venue named "Drop Zone Stadium" must not trip the DROP check --
        # literals are stripped before the keyword scan.
        r = tools.run_sql("SELECT COUNT(*) AS n FROM matches WHERE venue = 'Drop Zone Stadium'")
        assert "error" not in r
        assert r["rows"] == [[0]]

    def test_external_file_access_blocked(self):
        # read_csv()/read_text() inside a SELECT must fail: the connection
        # disables external access.
        r = tools.run_sql("SELECT * FROM read_csv('matches.csv')")
        assert "error" in r

    def test_bad_sql_returns_error_not_raise(self):
        r = tools.run_sql("SELECT no_such_column FROM matches")
        assert "error" in r


class TestNameSearch:
    def test_search_player(self):
        assert tools.search_player("Kohli")[0] == "V Kohli"

    def test_search_player_expands_first_name_to_initial(self):
        assert tools.search_player("Virat Kohli")[0] == "V Kohli"

    def test_search_player_no_match(self):
        assert tools.search_player("Zlatan Ibrahimovic") == []


class TestSchemaAndStats:
    def test_get_schema_lists_actual_tables(self, schema):
        text = tools.get_schema()
        expected = ["matches", "deliveries", "players_matches"]
        if schema == "new":
            expected.append("deliveries_wickets")
        for t in expected:
            assert f"{t}: " in text

    def test_get_stats(self):
        s = tools.get_stats()
        assert s["matches"] == 3
        assert s["tournaments"] == 1  # only "Test Bash League" has an event_name


class TestMcpServer:
    def test_tools_listed(self):
        import asyncio

        from mcp.shared.memory import create_connected_server_and_client_session
        from mcp_server.server import mcp

        async def names():
            async with create_connected_server_and_client_session(mcp) as session:
                return {t.name for t in (await session.list_tools()).tools}

        got = asyncio.run(names())
        assert {"player_profile", "player_stats", "compare_players", "leaderboard", "player_form", "career_arc",
                "percentiles", "player_matrix", "similar_players", "team_record", "matchup", "run_sql"} <= got

    def test_tool_call_resolves_names(self):
        import asyncio
        import json

        from mcp.shared.memory import create_connected_server_and_client_session
        from mcp_server.server import mcp

        async def call():
            async with create_connected_server_and_client_session(mcp) as session:
                res = await session.call_tool("player_stats", {"player": "Kohli", "metrics": ["runs"]})
                return json.loads(res.content[0].text)

        out = asyncio.run(call())
        assert out["rows"] == [["V Kohli", 1]]
