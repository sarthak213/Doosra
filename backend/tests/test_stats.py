"""
Deterministic tests of the cricket scoring rules in agent/stats.py, against
the synthetic fixture matches in conftest.py. Every test runs against both
database schemas (see conftest's `schema` fixture). Expected values are
computed BY HAND from those matches -- see conftest.py's match comments for
the edge cases each one covers. Where the old schema can't be exact (it
keeps one extra type and one wicket per ball), the difference is asserted
explicitly rather than skipped.
"""

import pytest

from agent import stats


def row(result, index=0):
    """A result row as a {column: value} dict."""
    assert "error" not in result, result
    return dict(zip(result["columns"], result["rows"][index]))


def rows(result):
    assert "error" not in result, result
    return [dict(zip(result["columns"], r)) for r in result["rows"]]


class TestBatting:
    def test_non_striker_run_out_counts_as_dismissal(self):
        # S Sharma: match_a 11 off 3 then run out at the NON-striker's end
        # (the case naive batter+is_wicket SQL misses) + match_c 4 off 2, caught.
        r = row(stats.player_stats("S Sharma", role="batting"))
        assert r["matches"] == 2
        assert r["innings"] == 2
        assert r["not_outs"] == 0
        assert r["runs"] == 15
        assert r["balls"] == 5
        assert r["average"] == 7.5
        assert r["strike_rate"] == 300.0
        assert r["highest"] == "11"
        assert r["fours"] == 2
        assert r["sixes"] == 1

    def test_wide_not_faced_no_ball_faced_and_non_striker_innings(self):
        # V Kohli faced the no-ball(+byes), the run-out ball and the ball he
        # was bowled on -- not the wide. In match_c he never faced but was at
        # the crease (non-striker): an innings, not out 0.
        r = row(stats.player_stats("V Kohli", role="batting"))
        assert r["balls"] == 3
        assert r["runs"] == 1
        assert r["innings"] == 2
        assert r["not_outs"] == 1
        assert r["average"] == 1.0
        assert r["highest"] == "1"

    def test_retired_hurt_is_not_a_dismissal(self):
        # H Pandya: stumped (out) and retired hurt (NOT out).
        r = row(stats.player_stats("H Pandya", role="batting"))
        assert r["runs"] == 2
        assert r["balls"] == 4  # the leg-bye ball counts as faced
        assert r["innings"] == 1
        assert r["average"] == 2.0

    def test_gender_filter(self):
        assert row(stats.player_stats("M Lanning", role="batting"))["runs"] == 4
        assert row(stats.player_stats("M Lanning", role="batting", gender="female"))["runs"] == 4
        # Filtering to men's cricket leaves nothing -- the name can't even resolve.
        assert "error" in stats.player_stats("M Lanning", role="batting", gender="male")

    def test_competition_filter(self):
        r = row(stats.player_stats("S Sharma", role="batting", competition="Test Bash League"))
        assert r["matches"] == 1
        assert r["runs"] == 11

    def test_season_split_is_chronological(self):
        r = stats.player_stats("S Sharma", role="batting", split_by="season")
        got = rows(r)
        # Both seasons fall in calendar 2024, so the raw season labels are kept.
        assert [x["season"] for x in got] == ["2023/24", "2024/25"]
        assert [x["runs"] for x in got] == [11, 4]
        assert [x["strike_rate"] for x in got] == [366.67, 200.0]

    def test_split_highlights_are_precomputed(self):
        # The model quotes these rather than scanning rows it may misread.
        h = stats.player_stats("S Sharma", role="batting", split_by="season")["highlights"]
        assert h["best_runs"] == "11 (2023/24)"
        assert h["best_strike_rate"] == "366.67 (2023/24)"
        assert h["totals"]["runs"] == 15

    def test_opposition_filter(self):
        assert row(stats.player_stats("D Warner", role="batting", opposition="India"))["runs"] == 12
        r = stats.player_stats("D Warner", role="batting", opposition="Australia")
        assert r.get("empty")

    def test_phase_filter(self):
        # Every fixture ball is in over 0 or 1: all powerplay, no death overs.
        assert row(stats.player_stats("S Sharma", role="batting", phase="powerplay"))["runs"] == 15
        assert stats.player_stats("S Sharma", role="batting", phase="death").get("empty")

    def test_unknown_player_is_an_error_not_zeros(self):
        r = stats.player_stats("Zlatan Ibrahimovic", role="batting")
        assert "error" in r


class TestBowling:
    def test_multi_extra_ball(self, schema):
        # P Starc, match_a over 0: 1+4+6 + wide 1 + no-ball 1 (NOT the 2 byes
        # that rode on it) + 1 = 14 off 7 legal balls; wickets bowled, caught,
        # stumped (the run-out isn't his). match_c: 4 off 2, one caught.
        r = row(stats.player_stats("P Starc", role="bowling"))
        assert r["balls"] == 9
        assert r["overs"] == 1.3  # cricket notation: 1 over 3 balls
        assert r["wickets"] == 4
        assert r["matches"] == 2
        if schema == "new":
            assert r["runs"] == 18
            assert r["best"] == "3/14"
            assert r["economy"] == 12.0
            assert r["average"] == 4.5
        else:
            # The old schema kept only "noballs" for that ball, so the byes
            # are charged to the bowler.
            assert r["runs"] == 20
            assert r["best"] == "3/16"
        assert r["strike_rate"] == 2.25

    def test_legbyes_retired_hurt_and_dots(self):
        # A Zampa: leg-bye (not charged, but a dot), 2, retired hurt (dot, not
        # his wicket), 1, caught and bowled.
        r = row(stats.player_stats("A Zampa", role="bowling"))
        assert r["runs"] == 3
        assert r["balls"] == 5
        assert r["wickets"] == 1
        assert r["dot_pct"] == 60.0

    def test_penalty_and_two_wicket_ball(self, schema):
        # J Bumrah: six, the ball with TWO wickets (run out + caught -- only the
        # caught is his), a 5-run penalty (not charged); match_c six + single.
        r = row(stats.player_stats("J Bumrah", role="bowling"))
        assert r["runs"] == 13
        assert r["balls"] == 5
        assert r["economy"] == 15.6
        # The old schema records only the first wicket on a ball (the run-out).
        assert r["wickets"] == (1 if schema == "new" else 0)

    def test_gender_filter(self):
        assert row(stats.player_stats("R Gayakwad", role="bowling", gender="female"))["wickets"] == 1


class TestFielding:
    def test_needs_new_schema(self, schema):
        r = stats.player_stats("S Smith", role="fielding")
        if schema == "old":
            assert "error" in r and "re-run" in r["error"].lower()
            return
        assert row(r)["catches"] == 2

    def test_caught_and_bowled_stumping_run_outs(self, schema):
        if schema == "old":
            pytest.skip("fielding needs the new schema")
        assert row(stats.player_stats("A Zampa", role="fielding"))["catches"] == 1
        wade = row(stats.player_stats("M Wade", role="fielding"))
        assert (wade["stumpings"], wade["catches"]) == (1, 0)
        # Both wickets on the two-wicket ball: a run-out AND a catch for Kohli.
        kohli = row(stats.player_stats("V Kohli", role="fielding"))
        assert (kohli["run_outs"], kohli["catches"]) == (1, 1)
        assert row(stats.player_stats("G Maxwell", role="fielding"))["run_outs"] == 1


class TestCompare:
    def test_compare(self):
        got = rows(stats.compare_players(["S Sharma", "V Kohli"], role="batting"))
        assert [(r["player"], r["runs"]) for r in got] == [("S Sharma", 15), ("V Kohli", 1)]

    def test_needs_two_players(self):
        assert "error" in stats.compare_players(["S Sharma"], role="batting")


class TestLeaderboard:
    def test_most_runs_defaults_to_mens_cricket(self):
        r = stats.leaderboard(role="batting", metric="runs", limit=2)
        got = rows(r)
        assert [(x["player"], x["runs"]) for x in got] == [("S Sharma", 15), ("D Warner", 12)]
        assert r["filters"]["gender"] == "male"
        assert any("defaulted to male" in n for n in r["notes"])

    def test_most_wickets(self, schema):
        got = rows(stats.leaderboard(role="bowling", metric="wickets"))
        assert got[0]["player"] == "P Starc" and got[0]["wickets"] == 4

    def test_rate_metric_applies_qualification(self):
        r = stats.leaderboard(role="batting", metric="strike_rate", min_balls=3)
        got = rows(r)
        assert all(x["balls"] >= 3 for x in got)
        assert (got[0]["player"], got[0]["strike_rate"]) == ("D Warner", 400.0)  # 12 off 3
        assert any("at least 3 balls" in n for n in r["notes"])

    def test_team_wins(self):
        got = rows(stats.leaderboard(role="team", metric="wins"))
        assert {(x["team"], x["wins"]) for x in got} == {("India", 1), ("Australia", 1)}

    def test_bad_metric(self):
        assert "error" in stats.leaderboard(role="batting", metric="wickets")


class TestTopPerformances:
    def test_highest_score(self):
        got = rows(stats.top_performances(kind="batting_innings", limit=1))
        assert got[0]["player"] == "S Sharma" and got[0]["score"] == "11"
        assert got[0]["opposition"] == "Australia"

    def test_best_figures(self, schema):
        got = rows(stats.top_performances(kind="bowling_figures", limit=1))
        assert got[0]["player"] == "P Starc"
        assert got[0]["figures"] == ("3/14" if schema == "new" else "3/16")  # old schema charges the byes

    def test_team_totals(self):
        got = rows(stats.top_performances(kind="team_total", limit=1))
        # India, match_a: 16 in over 0 + 4 in over 1; five wickets (retired hurt excluded).
        assert (got[0]["team"], got[0]["total"]) == ("India", "20/5")


class TestTeamStats:
    def test_head_to_head(self):
        r = stats.team_stats("India", opposition="Australia")
        x = row(r)
        assert x["matches"] == 2
        assert (x["won"], x["lost"], x["no_result"]) == (1, 1, 0)
        assert x["win_pct"] == 50.0
        assert (x["won_batting_first"], x["batted_first"]) == (1, 2)
        assert x["tosses_won"] == 1
        assert r["recent_results"][0]["date"] == "2024-06-01"
        assert r["recent_results"][0]["result"] == "lost by 6 wickets"

    def test_womens_head_to_head(self):
        x = row(stats.team_stats("India Women", opposition="Australia Women", gender="female"))
        assert (x["matches"], x["won"], x["lost"]) == (1, 0, 1)

    def test_split_by_season(self):
        got = rows(stats.team_stats("India", split_by="season"))
        assert [(x["season"], x["won"]) for x in got] == [("2023/24", 1), ("2024/25", 0)]


class TestVenue:
    def test_venue(self):
        x = row(stats.venue_stats("City Oval"))
        assert x["format"] == "T20I"
        assert x["matches"] == 1
        assert x["avg_first_innings"] == 20.0
        assert x["avg_second_innings"] == 11.0
        assert x["won_batting_first"] == 1
        assert x["highest_total"].startswith("20 — India")

    def test_unknown_venue(self):
        assert "error" in stats.venue_stats("Nowhere Stadium")


class TestMatchup:
    def test_pair(self):
        x = row(stats.matchup(batter="S Sharma", bowler="P Starc"))
        assert (x["balls"], x["runs"]) == (5, 15)
        assert x["dismissals"] == 1  # match_c caught; the run-out wasn't off his bowling to Sharma
        assert x["strike_rate"] == 300.0

    def test_two_wicket_ball(self, schema):
        x = row(stats.matchup(batter="D Warner", bowler="J Bumrah"))
        assert (x["balls"], x["runs"]) == (3, 12)
        assert x["dismissals"] == (1 if schema == "new" else 0)

    def test_batter_only_lists_bowlers(self):
        got = rows(stats.matchup(batter="S Sharma", min_balls=1))
        assert got[0]["bowler"] == "P Starc"

    def test_needs_a_name(self):
        assert "error" in stats.matchup()


class TestSuperOvers:
    def test_super_over_innings_excluded(self, fixture_db_path, tmp_path, monkeypatch):
        """A limited-overs innings_num > 2 is a super over and must not count."""
        import duckdb

        from agent import tools

        db = tmp_path / "so.duckdb"
        con = duckdb.connect(str(db))
        con.execute(f"ATTACH '{fixture_db_path}' AS src (READ_ONLY)")
        for t in ("matches", "players_matches", "deliveries_wickets"):
            con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")
        con.execute("CREATE TABLE deliveries AS SELECT * FROM src.deliveries")
        # A super over for match_c: Warner smashes 20 off Bumrah.
        con.execute("""
            INSERT INTO deliveries (match_id, innings_num, batting_team, over_num, ball_in_over, batter, bowler,
                                    non_striker, runs_batter, runs_extras, runs_total, is_wicket)
            VALUES ('match_c', 3, 'Australia', 0, 1, 'D Warner', 'J Bumrah', 'G Maxwell', 20, 0, 20, FALSE)
        """)
        con.close()
        monkeypatch.setattr(tools, "DB_PATH", db)
        assert row(stats.player_stats("D Warner", role="batting"))["runs"] == 12
