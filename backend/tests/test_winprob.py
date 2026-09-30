"""Win probability: the ball states and features built from the fixture matches, team ratings from
earlier results only, the NumPy tree evaluation, and scoring a whole match."""

import numpy as np
import pytest

from analytics import db, winprob


def states(where="TRUE"):
    return db.query(winprob.states_sql("T20", where))


def first_match():
    return db.query("SELECT match_id FROM matches ORDER BY date LIMIT 1")[0]["match_id"]


class TestStates:
    def test_one_row_per_ball_with_running_totals(self):
        mid = first_match()
        rows = states(f"m.match_id = '{mid}'")
        balls = db.query(f"SELECT COUNT(*) AS n FROM deliveries WHERE match_id = '{mid}' "
                         "AND innings_num IN (1, 2)")[0]["n"]
        assert len(rows) == balls
        first = [r for r in rows if r["innings"] == 1]
        assert first[-1]["score"] == db.query(f"SELECT SUM(runs_total) AS s FROM deliveries WHERE match_id = '{mid}' "
                                              "AND innings_num = 1")[0]["s"]
        chase = [r for r in rows if r["innings"] == 2]
        assert all(r["target"] == first[-1]["score"] + 1 or r["target"] is not None for r in chase)
        assert {r["won"] for r in first} == {1 - chase[0]["won"]}          # one side won

    def test_features_line_up_with_the_state(self):
        rows = [r for r in states(f"m.match_id = '{first_match()}'") if r["innings"] == 2]
        X = winprob.features(winprob.columns(rows), "T20")
        f = dict(zip(winprob.FEATURES, X[0]))
        r = rows[0]
        assert f["innings"] == 2 and f["score"] == r["score"] and f["wickets_in_hand"] == 10 - r["wickets"]
        assert f["runs_needed"] == r["target"] - r["score"] and f["balls_left"] == r["balls_total"] - r["legal"]
        first_innings = winprob.features(winprob.columns([s for s in states() if s["innings"] == 1][:1]), "T20")[0]
        assert np.isnan(first_innings[winprob.FEATURES.index("target")])      # no target before the chase


class TestNoLeakage:
    def test_ratings_come_from_earlier_results_only(self):
        ratings = winprob._elo("T20", str(db.DB_PATH))
        a, c = (db.query("SELECT match_id, winner FROM matches WHERE team1 = 'India' AND gender = 'male' "
                         "ORDER BY date")[i] for i in (0, 1))
        assert ratings[a["match_id"]] == {"India": 1500.0, "Australia": 1500.0}   # nothing before the first match
        winner_first = a["winner"]
        assert ratings[c["match_id"]][winner_first] > 1500.0                 # the earlier win counts, later ones don't

    def test_venue_par_ignores_the_match_itself(self):
        par = {r["match_id"]: r["venue_par"] for r in db.query(winprob.venue_par_sql("T20"))}
        assert par[first_match()] is None                                    # nothing earlier to average


class TestModel:
    def test_numpy_trees_follow_lightgbm_json(self):
        # a hand-written two-tree model: x0 <= 5 ? -1 : 1, plus 0.5 (NaN goes left)
        leaf = lambda v: {"leaf_value": v}                                    # noqa: E731
        spec = {"features": ["x"], "model": {"tree_info": [
            {"tree_structure": {"split_feature": 0, "threshold": 5.0, "default_left": True, "missing_type": "NaN",
                                "left_child": leaf(-1.0), "right_child": leaf(1.0)}},
            {"tree_structure": leaf(0.5)}]}}
        m = winprob.TreeModel(spec)
        assert m.raw(np.array([[3.0], [7.0], [np.nan]])).tolist() == [-0.5, 1.5, -0.5]

    @pytest.mark.skipif(not winprob.load("T20"), reason="no trained T20 model in backend/models")
    def test_scoring_a_match(self):
        # the fixture match won by chasing: once the runs are knocked off, the chasing side is the big favourite
        m = db.query("SELECT match_id, winner FROM matches WHERE win_by_wickets IS NOT NULL AND gender = 'male'")[0]
        got = winprob.predict_match(m["match_id"])
        wp = [r["wp_team1"] for r in got["rows"]]
        assert got["group"] == "T20" and all(0 < p < 1 for p in wp)
        last = got["rows"][-1]
        assert last["innings"] == 2 and last["score"] >= last["target"] and last["batting_team"] == m["winner"]
        p_winner = wp[-1] if m["winner"] == got["team1"] else 1 - wp[-1]
        assert p_winner > 0.9

    def test_not_a_limited_overs_match(self):
        assert winprob.predict_match("no-such-match") is None


@pytest.mark.skipif(not winprob.load("T20"), reason="no trained T20 model in backend/models")
def test_the_published_predictor_agrees_with_the_app():
    # ml/hf/winprob/predict.py ships to Hugging Face on its own; it must give the app's numbers
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("hf_predict", root / "ml" / "hf" / "winprob" / "predict.py")
    hf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hf)
    assert hf.FEATURES == winprob.FEATURES
    published = hf.WinProbability(winprob.MODELS / "winprob-t20.json")
    rng = np.random.default_rng(1)
    X = np.column_stack([rng.integers(1, 3, 500), rng.integers(0, 121, 500), rng.integers(1, 11, 500),
                         rng.integers(0, 230, 500), rng.integers(100, 240, 500), rng.integers(-5, 150, 500),
                         rng.uniform(0, 20, 500), rng.uniform(130, 190, 500), rng.integers(0, 2, 500),
                         np.full(500, 20), rng.uniform(-300, 300, 500)]).astype(float)
    X[X[:, 0] == 1, 4:7] = np.nan                                  # no target in the first innings
    assert np.allclose(published.predict_rows(X), winprob.load("T20").predict(X))
