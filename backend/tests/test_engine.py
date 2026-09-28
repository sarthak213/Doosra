"""
Tests of the derived tables (analytics/build.py) and the analytics engine
(analytics/engine.py) on the synthetic fixture matches in conftest.py, run
against both database schemas. Expected values are worked out by hand from
those matches; the context metrics are also checked against invariants
that hold by construction (e.g. runs above expected sum to zero).
"""

import pytest

from analytics import db, engine, registry


def rows(res):
    assert "error" not in res, res
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


def bi(player, match_id, innings=1):
    return db.query("SELECT * FROM batting_innings WHERE player = ? AND match_id = ? AND innings_num = ?",
                    [player, match_id, innings])[0]


class TestDerivedTables:
    def test_openers_and_order_of_arrival(self):
        assert bi("S Sharma", "match_a")["position"] == 1   # on strike for ball 1
        assert bi("V Kohli", "match_a")["position"] == 2    # non-striker for ball 1
        assert bi("R Pant", "match_a")["position"] == 3

    def test_entry_point(self):
        # Pant arrives (as non-striker) after Sharma's run-out, before
        # Kohli's dismissal: 16 on the board, 1 down.
        pant = bi("R Pant", "match_a")
        assert (pant["entry_over"], pant["entry_score"], pant["entry_wkts"]) == (0, 16, 1)
        assert bi("A Patel", "match_a")["entry_wkts"] == 3

    def test_non_striker_only_innings_exists(self):
        # Kohli never faced in match_c but was at the crease: 0*, 0 balls.
        k = bi("V Kohli", "match_c")
        assert (k["runs"], k["balls"], k["out"]) == (0, 0, 0)

    def test_retired_hurt_is_not_out(self):
        assert bi("H Pandya", "match_a")["dismissal"] == "stumped"

    def test_match_context_excludes_own_runs(self, schema):
        # match_a top-7: India 11+1+0+2+1, Australia 6+0+0+0 = 21 runs and
        # 7 dismissals; minus Sharma's own 11 and 1. (The old schema loses
        # Warner's dismissal on the two-wicket ball: 6 dismissals.)
        s = bi("S Sharma", "match_a")
        assert (s["mc_runs"], s["mc_outs"]) == (10, 6 if schema == "new" else 5)

    def test_results(self):
        assert bi("S Sharma", "match_a")["result"] == "won"
        assert bi("S Sharma", "match_c")["result"] == "lost"

    def test_bowling_innings(self, schema):
        starc = db.query("SELECT * FROM bowling_innings WHERE player = 'P Starc' AND match_id = 'match_a'")[0]
        assert (starc["balls"], starc["wickets"]) == (7, 3)
        assert starc["runs"] == (14 if schema == "new" else 16)
        assert starc["result"] == "lost"

    def test_expectation_is_zero_sum(self):
        # Expected values are state means over the same data, so across all
        # batters/bowlers the "above expected" totals cancel out.
        bat = db.query("SELECT SUM(runs) - SUM(exp_runs) AS d FROM batting_innings")[0]["d"]
        bowl = db.query("SELECT SUM(exp_runs) - SUM(runs) AS r, SUM(wickets) - SUM(exp_wkts) AS w FROM bowling_innings")[0]
        assert abs(bat) < 1e-6
        assert abs(bowl["r"]) < 1e-6 and abs(bowl["w"]) < 1e-6


class TestQueryStats:
    def test_leaderboard(self):
        got = rows(engine.query_stats(role="batting", metrics=["runs", "average"], limit=2))
        assert [(r["player"], r["runs"]) for r in got] == [("S Sharma", 15), ("D Warner", 12)]

    def test_match_factor(self, schema):
        # Sharma averages 7.5; the other top-7 batters in his matches made
        # 10 (match_a) + 7 (match_c: Warner 6*, Maxwell 1*) runs for 6 outs
        # (5 on the old schema -- see above).
        r = rows(engine.query_stats(role="batting", metrics=["average", "match_factor"], players=["S Sharma"]))[0]
        outs = 6 if schema == "new" else 5
        assert r["average"] == 7.5
        assert r["match_factor"] == round(7.5 / (17 / outs), 2)

    def test_split_for_one_player(self):
        got = rows(engine.query_stats(role="batting", metrics=["innings", "runs"], players=["S Sharma"],
                                      split_by="dismissal"))
        assert {(r["dismissal"], r["runs"]) for r in got} == {("run out", 11), ("caught", 4)}

    def test_comparison(self):
        got = rows(engine.query_stats(role="batting", metrics=["runs"], players=["S Sharma", "V Kohli"]))
        assert [(r["player"], r["runs"]) for r in got] == [("S Sharma", 15), ("V Kohli", 1)]

    def test_phase_filter_uses_phase_table(self):
        got = rows(engine.query_stats(role="batting", metrics=["runs"], players=["S Sharma"], phase="powerplay"))
        assert got[0]["runs"] == 15

    def test_phase_rejects_innings_only_metrics(self):
        assert "error" in engine.query_stats(role="batting", metrics=["hundreds"], phase="death")

    def test_bowling(self, schema):
        got = rows(engine.query_stats(role="bowling", metrics=["wickets", "economy"], players=["J Bumrah"]))
        assert got[0]["wickets"] == (1 if schema == "new" else 0)
        assert got[0]["economy"] == 15.6

    def test_unknown_metric_is_an_error(self):
        assert "error" in engine.query_stats(role="batting", metrics=["wickets"])


class TestPlayerViews:
    def test_profile(self):
        p = engine.player_profile("S Sharma")
        assert p["player"] == "S Sharma" and p["primary_role"] == "batter"
        summary = dict(zip(p["batting"]["summary"]["columns"], p["batting"]["summary"]["rows"][0]))
        assert summary["runs"] == 15 and summary["highest"] == "11"
        assert "bowling" not in p

    def test_form(self):
        f = engine.player_form("S Sharma", role="batting", window=2)
        got = rows(f)
        assert [r["score"] for r in got] == ["11", "4"]
        assert got[-1]["rolling_average"] == 7.5
        assert got[-1]["career_strike_rate"] == 300.0

    def test_career_arc(self):
        got = engine.career_arc(["S Sharma", "V Kohli"], role="batting", metric="runs")
        assert got["rows"] == [[1, 11, 1], [2, 15, 1]]

    def test_percentiles(self):
        got = rows(engine.percentiles(["S Sharma"], role="batting", metrics=["runs"], min_balls=1))
        # 7 men faced a ball; Sharma's 15 beats the other 6.
        assert got[0]["percentile"] == round(100 * 6.5 / 7, 2)

    def test_lower_is_better_percentiles_flip(self):
        got = rows(engine.percentiles(["S Sharma"], role="batting", metrics=["dot_pct"], min_balls=1))
        # Sharma never played a dot ball in match_a; 1 of 5 in total.
        assert got[0]["percentile"] > 50

    def test_scatter(self, schema):
        res = engine.scatter(role="batting", x="average", y="strike_rate", min_balls=3)
        got = rows(res)
        expected = {"S Sharma", "V Kohli", "H Pandya"}
        if schema == "new":
            expected.add("D Warner")  # never dismissed on the old schema: no average to plot
        assert {r["player"] for r in got} == expected
        assert res["medians"]["x"] is not None

    def test_entry_heatmap(self):
        got = rows(engine.entry_heatmap("H Pandya"))
        assert (got[0]["entry_phase"], got[0]["entry_wickets"], got[0]["innings"]) == ("powerplay", "2", 1)

    def test_similar_players(self):
        got = rows(engine.similar_players("S Sharma", role="batting", min_balls=1))
        assert got[0]["player"] == "S Sharma" and got[0]["similarity"] == 100.0
        assert len(got) > 1


class TestRegistry:
    @pytest.mark.parametrize("m", registry.METRICS, ids=lambda m: f"{m.role}:{m.id}")
    def test_every_metric_runs(self, m):
        res = engine.query_stats(role=m.role, metrics=[m.id], min_balls=0, gender="all")
        assert "error" not in res, res

    def test_search(self):
        ids = {m["id"] for m in registry.search("true", role="batting")}
        assert {"true_sr", "true_average"} <= ids
