"""
Deterministic tests of the cricket scoring rules encoded in agent/stats.py,
against the synthetic fixture matches in conftest.py. All expected values
are computed by hand from those matches -- see conftest.py's match comments
for the edge cases each one covers.
"""

from agent import stats


class TestBattingStats:
    def test_basic_figures_with_run_out_of_non_striker(self):
        # S Sharma: match_a 11 runs off 3 balls (dismissed via a run-out
        # while NOT facing -- the exact case naive batter+is_wicket SQL
        # miscounts) + match_c 4 runs off 2 balls, caught.
        r = stats.get_batting_stats("S Sharma")
        assert r["runs"] == 15
        assert r["balls_faced"] == 5
        assert r["dismissals"] == 2
        assert r["average"] == 7.5
        assert r["strike_rate"] == 300.0
        assert r["fours"] == 2
        assert r["sixes"] == 1
        assert r["matches"] == 2

    def test_wide_not_faced_but_no_ball_faced(self):
        # V Kohli faced: the no-ball(+byes) ball, the run-out ball, the
        # ball he was bowled on -- NOT the wide before them.
        r = stats.get_batting_stats("V Kohli")
        assert r["balls_faced"] == 3
        assert r["runs"] == 1
        assert r["dismissals"] == 1

    def test_gender_filter(self):
        # M Lanning only exists in the women's match.
        assert stats.get_batting_stats("M Lanning")["runs"] == 4
        assert stats.get_batting_stats("M Lanning", gender="female")["runs"] == 4
        male = stats.get_batting_stats("M Lanning", gender="male")
        assert male["matches"] == 0
        assert male["runs"] == 0

    def test_tournament_filter(self):
        # Only match_a carries an event_name.
        r = stats.get_batting_stats("S Sharma", tournament="Test Bash League")
        assert r["matches"] == 1
        assert r["runs"] == 11


class TestBowlingStats:
    def test_multi_extra_ball_charged_correctly(self):
        # P Starc, match_a over 0: conceded 1+4+6 (legal) +1 (wide) +1
        # (no-ball, NOT the 2 byes that rode on it) +1+0+0+0 = 14;
        # 7 legal balls (wides/no-balls re-bowled); 3 wickets
        # (bowled, caught, stumped) -- the run-out is NOT his wicket.
        r = stats.get_bowling_stats("P Starc")
        # match_c adds 4 conceded, 2 balls, 1 wicket (caught).
        assert r["runs_conceded"] == 18
        assert r["balls_bowled"] == 9
        assert r["wickets"] == 4
        assert r["overs"] == 1.5
        assert r["economy"] == 12.0
        assert r["average"] == 4.5
        assert r["strike_rate"] == 2.25
        assert r["matches"] == 2

    def test_legbyes_and_retired_hurt(self):
        # A Zampa: leg-bye (0 charged), retired hurt (dismissal but not his
        # wicket), caught and bowled (his wicket AND his catch).
        r = stats.get_bowling_stats("A Zampa")
        assert r["runs_conceded"] == 3
        assert r["balls_bowled"] == 5
        assert r["wickets"] == 1

    def test_penalty_and_multi_wicket_ball(self):
        # J Bumrah, match_a: 6 (six) + 0 (ball with TWO wickets -- only the
        # caught counts to him) + 0 (penalty not charged); match_c adds a six
        # and a single. 13 conceded off 5 legal balls -> economy 15.6.
        r = stats.get_bowling_stats("J Bumrah")
        assert r["runs_conceded"] == 13
        assert r["balls_bowled"] == 5
        assert r["wickets"] == 1
        assert r["economy"] == 15.6
        assert r["matches"] == 2

    def test_gender_filter(self):
        assert stats.get_bowling_stats("R Gayakwad", gender="female")["wickets"] == 1
        assert stats.get_bowling_stats("R Gayakwad", gender="male")["matches"] == 0


class TestFieldingStats:
    def test_catches(self):
        # S Smith caught R Pant (match_a) and S Sharma (match_c).
        assert stats.get_fielding_stats("S Smith")["catches"] == 2
        assert stats.get_fielding_stats("S Smith")["matches"] == 2

    def test_caught_and_bowled_is_a_catch(self):
        assert stats.get_fielding_stats("A Zampa")["catches"] == 1

    def test_stumping(self):
        r = stats.get_fielding_stats("M Wade")
        assert r["stumpings"] == 1
        assert r["catches"] == 0

    def test_run_out_and_catch_on_multi_wicket_ball(self):
        # The one ball with two wickets credits V Kohli with BOTH the
        # run-out (M Wade) and the catch (D Warner).
        r = stats.get_fielding_stats("V Kohli")
        assert r["run_outs"] == 1
        assert r["catches"] == 1

    def test_run_out_of_non_striker(self):
        assert stats.get_fielding_stats("G Maxwell")["run_outs"] == 1


class TestHeadToHead:
    def test_record(self):
        r = stats.get_head_to_head("India", "Australia")
        assert r["played"] == 2
        assert r["team1_wins"] == 1  # match_a: India won by 5 runs
        assert r["team2_wins"] == 1  # match_c: Australia won by 6 wickets
        assert r["no_result_or_draw"] == 0
        assert r["last_meeting"] == "2024-06-01"

    def test_gender_filter(self):
        r = stats.get_head_to_head("India Women", "Australia Women", gender="female")
        assert r["played"] == 1
        assert r["team2_wins"] == 1  # Australia Women won


class TestVenueStats:
    def test_record(self):
        r = stats.get_venue_stats("City Oval")
        assert r["matches_played"] == 1
        # Innings totals at City Oval: India 20, Australia 11.
        assert r["highest_total"]["runs"] == 20
        assert r["highest_total"]["team"] == "India"
        assert r["average_first_innings_total"] == 20.0

    def test_unknown_venue(self):
        r = stats.get_venue_stats("Nowhere Stadium")
        assert r["matches_played"] == 0
        assert r["highest_total"] is None
        assert r["average_first_innings_total"] is None


class TestSeasonTrend:
    def test_batting_trend(self):
        r = stats.get_season_trend("S Sharma", "batting")
        assert r["columns"] == ["season", "matches", "runs", "balls_faced", "average", "strike_rate"]
        assert r["rows"] == [
            ["2023/24", 1, 11, 3, 11.0, 366.67],
            ["2024/25", 1, 4, 2, 4.0, 200.0],
        ]

    def test_bowling_trend(self):
        r = stats.get_season_trend("P Starc", "bowling")
        assert r["rows"][0] == ["2023/24", 1, 3, 1.2, 12.0, 4.67]
        # match_c over 0: 4 conceded, 2 balls, 1 wicket -> 0.3 overs, 12.0 economy, 4.0 average
        assert r["rows"][1] == ["2024/25", 1, 1, 0.3, 12.0, 4.0]

    def test_bad_stat_type(self):
        assert "error" in stats.get_season_trend("S Sharma", "fielding")


class TestMatchup:
    def test_matchup(self):
        r = stats.get_matchup("S Sharma", "P Starc")
        assert r["balls_faced"] == 5
        assert r["runs"] == 15
        assert r["dismissals"] == 1
        assert r["wickets"] == 1  # the caught in match_c; the run-out isn't Starc's
        assert r["strike_rate"] == 300.0

    def test_matchup_multi_wicket_ball(self):
        # Warner faced Bumrah twice: a six, then the caught (on the
        # two-wicket ball) + a six in match_c.
        r = stats.get_matchup("D Warner", "J Bumrah")
        assert r["balls_faced"] == 3
        assert r["runs"] == 12
        assert r["wickets"] == 1
        assert r["strike_rate"] == 400.0


class TestComparePlayers:
    def test_compare(self):
        r = stats.compare_players(["S Sharma", "V Kohli"], "runs", "batting")
        assert r == {"columns": ["player", "runs"], "rows": [["S Sharma", 15], ["V Kohli", 1]]}

    def test_compare_unknown_metric(self):
        assert "error" in stats.compare_players(["S Sharma"], "wickets", "batting")
