"""Tests of the agent's generic tools: the run_sql guard, name search, and
the schema/stats endpoints. run_sql's guard is a security boundary -- the
external-access and forbidden-keyword tests here must not regress."""

from agent import graph, tools


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


class TestToolRegistration:
    def test_every_schema_has_an_impl(self):
        # Catches the classic regression: adding a tool schema to the LLM
        # prompt but forgetting to register its implementation (or the
        # reverse -- an impl with no schema the model can never call).
        schema_names = {t["function"]["name"] for t in graph.TOOL_SCHEMAS}
        assert schema_names == set(graph.TOOL_IMPLS)

    def test_final_answer_is_offered(self):
        assert "final_answer" in {t["function"]["name"] for t in graph.ALL_TOOLS}
