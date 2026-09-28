"""FIBS: the outcome components (build.py + components.py), the
study's estimators (fibs.py), and the FIB / luck / regressed metrics."""

import json
import zipfile

import duckdb
import numpy as np
import pytest

from analytics import build as analytics_build
from analytics import catalog, components, db, engine, fibs
from ingest import build_db


# --- estimators on simulated data --------------------------------------------

def test_split_half_recovers_k():
    """Bowlers with known true rates plus binomial noise: the split-half
    estimator recovers the true-talent spread and K."""
    rng = np.random.default_rng(7)
    players, p, tau = 3000, 0.05, 0.01
    true = np.clip(rng.normal(p, tau, players), 0.001, None)
    n_a = rng.integers(100, 400, players)
    n_b = rng.integers(100, 400, players)
    a = rng.binomial(n_a, true) / n_a - p
    b = rng.binomial(n_b, true) / n_b - p
    est = fibs.split_half(a, b, n_a, n_b, sigma2=p * (1 - p))
    k_true = p * (1 - p) / tau ** 2          # 475 balls
    assert est["true_sd"] == pytest.approx(tau, rel=0.1)
    assert est["k"] == pytest.approx(k_true, rel=0.2)


def test_split_half_with_no_talent_spread_gives_no_k():
    rng = np.random.default_rng(1)
    n = np.full(2000, 300)
    a, b = rng.binomial(n, 0.05) / n, rng.binomial(n, 0.05) / n
    est = fibs.split_half(a, b, n, n, sigma2=0.05 * 0.95)
    assert est["k"] is None or est["k"] > 20 * 475   # pure noise: no (or a huge) K


def test_regress_shrinks_by_k():
    assert fibs.regress(0.10, 100, 100, prior=0.05) == pytest.approx(0.075)
    assert fibs.regress(0.10, 100, 0, prior=0.05) == pytest.approx(0.10)
    assert fibs.regress(0.10, 100, None, prior=0.05) == 0.05


def test_out_of_sample_prefers_the_real_signal():
    rng = np.random.default_rng(3)
    x = rng.normal(0, 1, 2000)
    y = 0.5 * x + rng.normal(0, 1, 2000)
    w, fold = np.ones(2000), np.arange(2000) % 2
    rmse_x, r = fibs.out_of_sample(x, y, w, fold)
    rmse_none, _ = fibs.out_of_sample(None, y, w, fold)
    assert rmse_x < rmse_none and r > 0.3


# --- components on the shared fixture (both schemas) -------------------------

@pytest.mark.parametrize("table,cols", [("bowling_innings", components.BOWL_COMPONENTS),
                                        ("batting_innings", components.BAT_COMPONENTS)])
def test_expected_components_balance_observed(table, cols):
    """Expected rates are per-ball-state averages, so across everyone the
    expected count of every outcome equals the observed count."""
    for c in cols:
        r = db.query(f"SELECT SUM(n_{c}) AS n, SUM(x_{c}) AS x FROM {table}")[0]
        assert r["x"] == pytest.approx(r["n"], abs=1e-9), c
    r = db.query(f"SELECT SUM(inplay_runs) AS n, SUM(x_inplay_runs) AS x FROM {table}")[0]
    assert r["x"] == pytest.approx(r["n"])


def test_fixture_dismissal_components(schema):
    """match_a: M Wade stumped H Pandya; A Zampa caught-and-bowled A Patel."""
    got = {r["player"]: r for r in db.query(
        "SELECT player, SUM(n_stumped) AS st, SUM(n_ct_bowler) AS cab, SUM(n_ct_keeper) AS keeper "
        "FROM bowling_innings GROUP BY player")}
    assert sum(r["st"] for r in got.values()) == 1
    assert got["A Zampa"]["cab"] == 1
    assert sum(r["keeper"] for r in got.values()) == 0   # no catch by a keeper in the fixture


def test_findings_survive_a_missing_year_to_year_estimate():
    """Enough seasons for K but too few season pairs for year-to-year r."""
    from analytics.engine import _fibs_findings

    stab = [
        {"metric": "dot", "label": "Dot balls", "grp": "outcome", "k_balls": 150.0, "yoy_r": None,
         "reliability_typical": 0.6, "typical_balls": 220.0, "n_seasons": 40},
        {"metric": "wickets", "label": "Wickets", "grp": "summary", "k_balls": 1700.0, "yoy_r": None,
         "reliability_typical": 0.11, "typical_balls": 220.0, "n_seasons": 40},
    ]
    out = _fibs_findings(stab, [], "bowling", "T20")
    assert any("n/a (too few season pairs)" in line for line in out)


def test_study_tables_exist_but_are_empty_of_estimates_on_a_tiny_db():
    rows = db.query("SELECT k_balls FROM fibs_stability")
    assert rows and all(r["k_balls"] is None for r in rows)
    r = engine.fibs_report("T20", "male", "bowling")
    assert "error" not in r and "too few" in r["findings"][0]


# --- keeper inference and the FIB metrics on a purpose-built database ---------

def _d(batter, bowler, rb=0, wickets=None, ns="Z"):
    d = {"batter": batter, "bowler": bowler, "non_striker": ns, "runs": {"batter": rb, "extras": 0, "total": rb}}
    if wickets:
        d["wickets"] = wickets
    return d


def _out(kind, who, fielder=None):
    w = {"kind": kind, "player_out": who}
    if fielder:
        w["fielders"] = [{"name": fielder}]
    return [w]


def _match(date, deliveries):
    return {
        "meta": {"data_version": "1.1.0", "revision": 1},
        "info": {
            "match_type": "T20", "gender": "male", "team_type": "club", "overs": 20, "dates": [date],
            "venue": "Ground", "season": "2025", "teams": ["Aces", "Bees"], "outcome": {"winner": "Bees",
                                                                                      "by": {"runs": 1}},
            "players": {"Aces": ["P One", "P Two", "P Three", "Z"],
                        "Bees": ["K Keeper", "B Bowler", "F Fielder", "S Slip"]},
        },
        "innings": [{"team": "Aces", "overs": [{"over": 0, "deliveries": deliveries}]}],
    }


# Match 1: the keeper makes a stumping, so is the keeper for the match.
M1 = _match("2025-01-01", [
    _d("P One", "B Bowler", wickets=_out("stumped", "P One", "K Keeper")),
    _d("P Two", "B Bowler", rb=2),
    _d("P Two", "B Bowler", wickets=_out("caught", "P Two", "K Keeper")),       # caught behind
    _d("P Three", "B Bowler", rb=1),
    _d("Z", "B Bowler", wickets=_out("caught", "Z", "F Fielder")),              # caught in the field
])
# Match 2: no stumping, so the keeper is whoever has career stumpings.
M2 = _match("2025-02-01", [
    _d("P One", "B Bowler", wickets=_out("caught", "P One", "K Keeper")),       # caught behind
    _d("P Two", "B Bowler", rb=3),
    _d("P Two", "B Bowler", wickets=_out("caught", "P Two", "S Slip")),         # caught in the field
    _d("P Three", "B Bowler", wickets=_out("caught and bowled", "P Three", "B Bowler")),
])


@pytest.fixture
def keeper_db(tmp_path, monkeypatch):
    zpath = tmp_path / "m.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("m1.json", json.dumps(M1))
        zf.writestr("m2.json", json.dumps(M2))
    out = tmp_path / "k.duckdb"
    build_db.build([zpath], out, progress=lambda *_: None)
    analytics_build.build(out, progress=lambda *_: None)
    monkeypatch.setattr(db, "DB_PATH", out)
    catalog.invalidate()
    yield out
    catalog.invalidate()


def test_keeper_inference_splits_caught_behind(keeper_db):
    r = db.query("SELECT SUM(n_ct_keeper) AS behind, SUM(n_ct_field) AS field, SUM(n_stumped) AS st, "
                 "SUM(n_ct_bowler) AS cab, SUM(wickets) AS w FROM bowling_innings WHERE player = 'B Bowler'")[0]
    assert (r["behind"], r["field"], r["st"], r["cab"], r["w"]) == (2, 2, 1, 1, 6)
    bat = {x["player"]: x for x in db.query(
        "SELECT player, SUM(n_ct_keeper) AS behind, SUM(n_stumped) AS st, SUM(n_ct_field) AS field "
        "FROM batting_innings GROUP BY player")}
    assert (bat["P One"]["behind"], bat["P One"]["st"]) == (1, 1)
    assert bat["P Two"]["field"] == 1 and bat["P Two"]["behind"] == 1


def test_inplay_runs_are_the_non_boundary_scoring_shots(keeper_db):
    r = db.query("SELECT SUM(n_inplay) AS shots, SUM(inplay_runs) AS runs FROM bowling_innings")[0]
    assert (r["shots"], r["runs"]) == (3, 6)   # the 2, the 1 and the 3


def _set_k(path, k):
    con = duckdb.connect(str(path))
    for t in ("bowling_innings", "batting_innings"):
        con.execute(f"UPDATE {t} SET k_ct_field = {k}, k_inplay_runs = {k}, k_runs = {k}")
    con.close()


def _bowl(metrics):
    r = engine.query_stats(role="bowling", metrics=metrics, players=["B Bowler"])
    return dict(zip(r["columns"], r["rows"][0]))


def test_fib_with_k_zero_is_the_actual_record(keeper_db):
    """K = 0: the bowler's own rates are fully trusted -- no luck."""
    _set_k(keeper_db, 0)
    got = _bowl(["wickets", "fib_wickets", "wicket_luck", "runs_luck", "economy", "fib_economy"])
    assert got["fib_wickets"] == got["wickets"] == 6
    assert got["wicket_luck"] == 0 and got["runs_luck"] == 0
    assert got["fib_economy"] == got["economy"]


def test_fib_with_huge_k_is_the_textbook_replacement(keeper_db):
    """K -> infinity: outfield catches and in-play runs replaced by the
    situation-expected numbers (baseball's DIPS)."""
    _set_k(keeper_db, 1e12)
    got = _bowl(["wickets", "fib_wickets", "wicket_luck", "runs", "runs_luck"])
    raw = db.query("SELECT SUM(wickets) - SUM(n_ct_field) + SUM(x_ct_field) AS fw, "
                   "SUM(x_inplay_runs) - SUM(inplay_runs) AS rl FROM bowling_innings WHERE player = 'B Bowler'")[0]
    assert got["fib_wickets"] == pytest.approx(raw["fw"], abs=0.01)
    assert got["wicket_luck"] == pytest.approx(got["wickets"] - raw["fw"], abs=0.01)
    assert got["runs_luck"] == pytest.approx(raw["rl"], abs=0.01)


def test_regressed_economy_moves_toward_expected(keeper_db):
    _set_k(keeper_db, 1e12)
    got = _bowl(["economy", "expected_economy", "regressed_economy"])
    assert got["regressed_economy"] == pytest.approx(got["expected_economy"], abs=0.01)
    _set_k(keeper_db, 0)
    got = _bowl(["economy", "regressed_economy"])
    assert got["regressed_economy"] == pytest.approx(got["economy"], abs=0.01)


def test_luck_leaderboard_orders_by_luck(keeper_db):
    _set_k(keeper_db, 100)
    r = engine.luck_leaderboard(role="bowling", min_balls=1)
    assert "error" not in r and r["title"].startswith("Luckiest")
    assert "wicket_luck" in r["columns"]
    bad = engine.luck_leaderboard(role="bowling", by="average")
    assert "error" in bad
