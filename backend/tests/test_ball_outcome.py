"""The ball-outcome model's training rows (ml/ball_outcome_train.py): outcomes, the situation before each
ball, and career form from earlier matches only."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from analytics import db

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def bo():
    spec = importlib.util.spec_from_file_location("ball_outcome_train", ROOT / "ml" / "ball_outcome_train.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as e:                       # lightgbm / matplotlib are training-only dependencies
        pytest.skip(f"training dependencies not installed: {e}")
    return mod


def test_one_row_per_ball_faced_with_its_outcome(bo):
    c = bo.load("T20")
    faced = db.query("SELECT COUNT(*) AS n FROM deliveries d JOIN matches m USING (match_id) "
                     "WHERE m.match_type IN ('T20', 'IT20') AND d.innings_num IN (1, 2)")[0]["n"]
    assert 0 < len(c["y"]) <= faced
    assert set(np.unique(c["y"])) <= set(range(len(bo.CLASSES)))
    runs = c["runs_batter"].astype(int)
    fours = (c["y"] == bo.CLASSES.index("4"))
    assert (runs[fours] >= 4).all() and (runs[c["y"] == 0] == 0).all()


def test_career_form_comes_from_earlier_matches_only(bo):
    c = bo.load("T20")
    order = {r["match_id"]: r["date"] for r in db.query("SELECT match_id, date FROM matches")}
    for player in set(c["batter"]):
        rows = [i for i in range(len(c["y"])) if c["batter"][i] == player]
        by_match = {}
        for i in rows:
            by_match.setdefault(c["match_id"][i], []).append(i)
        matches = sorted(by_match, key=lambda m: order[m])
        assert all(c["bat_balls"][i] == 0 for i in by_match[matches[0]])        # nothing before the first match
        for earlier, later in zip(matches, matches[1:]):
            faced_before = sum(len(by_match[m]) for m in matches[:matches.index(later)])
            assert all(c["bat_balls"][i] == faced_before for i in by_match[later])


def test_features_and_shrinkage(bo):
    c = bo.load("T20")
    X, means = bo.features(c)
    assert X.shape == (len(c["y"]), len(bo.STATE + bo.FORM))
    rate = X[:, (bo.STATE + bo.FORM).index("bat_runs_rate")]
    new = c["bat_balls"] == 0
    assert np.allclose(rate[new], means["bat_runs"])                            # no history: the average player
