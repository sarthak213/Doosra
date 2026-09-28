"""
FIBS -- Fielding-Independent Bowling Statistics: which parts of a player's
record are repeatable skill, and which are fielding and luck.

FIBS is an attempt at a cricket framework analogous to baseball's DIPS
(Defense-Independent Pitching Statistics). Voros McCracken (2001) found
pitchers have little control over whether a ball in play becomes a hit, so
ERA is part luck and part fielding. FIBS asks the same question of cricket
-- for bowlers first, and for batters as the mirror image -- as a study run
over the whole database every build (build.py calls run()). Nothing is
assumed from baseball: each outcome's reliability is measured, and the
findings -- whatever they are -- go into the tables the methodology page and
the copilot read.

The unit is a player-season: calendar year x format group x gender. For
each metric, a player-season's value is its rate ABOVE EXPECTED (observed
minus what an average player would have produced in the same ball states;
see components.py), per delivery bowled or ball faced.

  Stability  True-talent spread from split halves: a season's matches are
             split in two by a hash of the match id, and the covariance of
             the two halves' rates across players estimates the variance of
             true talent (the noise in the two halves is independent, so it
             drops out). Then
                 K = per-ball variance / true-talent variance
             -- the number of balls at which a rate is half signal, half
             noise; a rate over n balls has reliability n / (n + K).
             Split-half correlation and year-to-year correlation are the
             model-free cross-checks.
  Prediction Does a component predict NEXT season better than the headline
             number does? Out-of-sample (two folds by year parity), fitting
             next season's economy / wickets above expected from this
             season's raw figure, its regressed version, and its
             Fielding-Independent (FIB) version.

The two outcomes fielding and luck affect most are the runs off scoring
shots in play and catches taken in the field; everything else (dots,
boundaries, extras, bowled, lbw, caught behind, stumpings) is the bowler's
own. The study tests two ways of neutralising them:
  DIPS-style replacement            baseball's approach: replace them with what
                                    an average bowler got in the same
                                    situations (fib_runs / fib_wkts columns).
  luck-adjusted                     replace them with the bowler's own rate
                                    regressed by its measured K -- the
                                    version the FIB metrics in registry.py
                                    use, since the study finds these
                                    outcomes carry real skill in cricket.

Tables written:
  fibs_stability   fgroup, gender, role, metric, label, grp, unit, scale,
                   league_rate, true_sd, k_balls, typical_balls,
                   reliability_typical, split_half_r, yoy_r, n_seasons, n_pairs
  fibs_prediction  fgroup, gender, target, predictor, rmse, r, n_pairs, unit
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .components import BAT_COMPONENTS, BOWL_COMPONENTS, INPLAY_SQL

# A player-season counts toward the study with at least this many
# deliveries bowled / balls faced.
MIN_SEASON = {
    "bowling": {"T20": 120, "ODI": 300, "MULTI": 600},
    "batting": {"T20": 60, "ODI": 150, "MULTI": 300},
}
# Fewer qualifying player-seasons (or season pairs) than this -> no estimate.
MIN_SAMPLES = 20

TABLES = ("fibs_stability", "fibs_prediction")


@dataclass(frozen=True)
class Spec:
    id: str
    label: str
    num: str                 # observed count (SQL over the innings table)
    exp: str                 # expected count for the same balls
    den: str                 # denominator (deliveries / balls / scoring shots)
    var: str | None = None   # per-ball value (SQL over the ball table) for its variance; None = binomial
    var_where: str = "TRUE"
    scale: float = 100.0     # display multiplier
    unit: str = "per 100 balls"
    grp: str = "outcome"     # summary | outcome


def _specs(role: str) -> list[Spec]:
    if role == "bowling":
        comps, den = BOWL_COMPONENTS, "deliveries"
        out = [
            Spec("runs", "Economy (runs conceded)", "runs", "exp_runs", den, "bowler_runs",
                 scale=6, unit="runs per over", grp="summary"),
            Spec("fib_runs", "FIB economy", "fib_runs", "exp_runs", den,
                 "bowler_runs - inplay_runs + x_inplay_runs", scale=6, unit="runs per over", grp="summary"),
            Spec("wickets", "Wickets", "wickets", "exp_wkts", den, grp="summary"),
            Spec("fib_wickets", "FIB wickets", "fib_wkts", "exp_wkts", den, grp="summary"),
        ]
    else:
        comps, den = BAT_COMPONENTS, "balls"
        out = [
            Spec("runs", "Scoring rate (runs)", "runs", "exp_runs", den, "runs_batter", var_where="faced",
                 grp="summary", unit="runs per 100 balls"),
            Spec("fib_runs", "FIB scoring rate", "fib_runs", "exp_runs", den,
                 "runs_batter - inplay_runs + x_inplay_runs", var_where="faced", grp="summary",
                 unit="runs per 100 balls"),
            Spec("outs", "Dismissals", "out", "exp_outs", den, grp="summary"),
            Spec("fib_outs", "FIB dismissals", "fib_outs", "exp_outs", den, grp="summary"),
        ]
    out.append(Spec("boundary", "Boundaries" if role == "batting" else "Boundaries conceded",
                    "n_four + n_six", "x_four + x_six", den))
    out += [Spec(c, label, f"n_{c}", f"x_{c}", den) for c, (label, _) in comps.items()]
    out.append(Spec("inplay_runs", "Runs per scoring shot in play", "inplay_runs", "x_inplay_runs", "n_inplay",
                    "runs_batter", var_where=INPLAY_SQL, scale=1, unit="runs per scoring shot"))
    return out


# ---------------------------------------------------------------------------
# Estimators (pure functions; tests/test_fibs.py checks them on simulated data)
# ---------------------------------------------------------------------------

def _wmean(x, w):
    return float(np.sum(w * x) / np.sum(w))


def wcov(x, y, w) -> float:
    return float(np.sum(w * (x - _wmean(x, w)) * (y - _wmean(y, w))) / np.sum(w))


def wcorr(x, y, w) -> float | None:
    vx, vy = wcov(x, x, w), wcov(y, y, w)
    if vx <= 0 or vy <= 0:
        return None
    return wcov(x, y, w) / math.sqrt(vx * vy)


def split_half(a_a, a_b, n_a, n_b, sigma2: float) -> dict:
    """True-talent variance, K and split-half correlation from the two halves
    of each player-season (rates a_*, sample sizes n_*)."""
    a_a, a_b, n_a, n_b = map(np.asarray, (a_a, a_b, n_a, n_b))
    w = n_a * n_b / (n_a + n_b)
    tau2 = wcov(a_a, a_b, w)
    r = wcorr(a_a, a_b, w)
    return {
        "tau2": tau2,
        "true_sd": math.sqrt(tau2) if tau2 > 0 else 0.0,
        "k": sigma2 / tau2 if tau2 > 0 and sigma2 > 0 else None,
        "split_half_r": None if r is None else 2 * r / (1 + r) if r > -1 else None,  # Spearman-Brown
    }


def regress(rate, n, k, prior=0.0):
    """Shrink an observed rate over n balls toward `prior` by K."""
    if k is None:
        return prior
    return (np.asarray(rate) * n + prior * k) / (n + k)


def _shrink(n, x, d, k):
    """Per-unit rate n/d regressed toward x/d by K (x/d when K is unknown)."""
    d = np.asarray(d, dtype=float)
    prior = np.divide(x, d, out=np.zeros_like(d), where=d > 0)
    if k is None:
        return prior
    return (np.asarray(n) + k * prior) / (d + k)


def _fit_predict(x_tr, y_tr, w_tr, x_te):
    """Weighted least squares y = a + b x; a constant if x is None."""
    if x_tr is None:
        return np.full(len(x_te), _wmean(y_tr, w_tr))
    xm, ym = _wmean(x_tr, w_tr), _wmean(y_tr, w_tr)
    vx = np.sum(w_tr * (x_tr - xm) ** 2)
    b = np.sum(w_tr * (x_tr - xm) * (y_tr - ym)) / vx if vx > 0 else 0.0
    return ym + b * (x_te - xm)


def out_of_sample(x, y, w, fold) -> tuple[float, float | None]:
    """RMSE of two-fold out-of-sample predictions of y from x (None = the
    mean only), and the in-sample correlation."""
    y, w, fold = map(np.asarray, (y, w, fold))
    pred = np.zeros(len(y))
    for f in (0, 1):
        tr, te = fold != f, fold == f
        if not tr.any() or not te.any():
            continue
        pred[te] = _fit_predict(None if x is None else np.asarray(x)[tr], y[tr], w[tr],
                                np.zeros(te.sum()) if x is None else np.asarray(x)[te])
    rmse = math.sqrt(float(np.sum(w * (y - pred) ** 2) / np.sum(w)))
    return rmse, (None if x is None else wcorr(np.asarray(x), y, w))


# ---------------------------------------------------------------------------
# The study
# ---------------------------------------------------------------------------

def _seasons(con, role: str, specs: list[Spec]) -> pd.DataFrame:
    table = "bowling_innings" if role == "bowling" else "batting_innings"
    aggs = ", ".join(f"SUM({s.num}) AS \"{s.id}__n\", SUM({s.exp}) AS \"{s.id}__x\", SUM({s.den}) AS \"{s.id}__d\""
                     for s in specs)
    return con.execute(f"""
        SELECT player, fgroup, COALESCE(gender, 'unknown') AS gender, yr,
               CAST(hash(match_id) % 2 AS INTEGER) AS half, {aggs}
        FROM {table} GROUP BY ALL
    """).df()


def _ball_variance(con, specs: list[Spec]) -> dict:
    """Per-ball variance of each non-binomial metric, by format and gender."""
    out = {}
    for s in specs:
        if s.var is None:
            continue
        for fg, g, v in con.execute(f"""
                SELECT fgroup, COALESCE(gender, 'unknown'), var_pop({s.var}) FROM bx WHERE {s.var_where} GROUP BY ALL
                """).fetchall():
            out[(s.id, fg, g)] = v or 0.0
    return out


def _metric_rows(role, fg, g, halves, seasons, specs, ball_var, min_n) -> list[dict]:
    rows = []
    for s in specs:
        n, x, d = f"{s.id}__n", f"{s.id}__x", f"{s.id}__d"
        league = seasons[n].sum() / seasons[d].sum() if seasons[d].sum() > 0 else None
        row = {"fgroup": fg, "gender": g, "role": role, "metric": s.id, "label": s.label, "grp": s.grp,
               "unit": s.unit, "scale": s.scale, "league_rate": league, "true_sd": None, "k_balls": None,
               "typical_balls": None, "reliability_typical": None, "split_half_r": None, "yoy_r": None,
               "n_seasons": 0, "n_pairs": 0}
        rows.append(row)
        # Qualification is on the role's main denominator, not the metric's
        # own (scoring shots for inplay_runs).
        qual = seasons[seasons["_den"] >= min_n]
        qual = qual[qual[d] > 0]
        row["n_seasons"] = len(qual)
        if league is None or len(qual) < MIN_SAMPLES:
            continue
        sigma2 = ball_var.get((s.id, fg, g)) if s.var else league * (1 - league)
        # Split halves of the qualifying seasons.
        h = halves.merge(qual[["player", "yr"]], on=["player", "yr"])
        wide = h.pivot_table(index=["player", "yr"], columns="half", values=[n, x, d], aggfunc="sum").dropna()
        wide = wide[(wide[(d, 0)] > 0) & (wide[(d, 1)] > 0)]
        if len(wide) >= MIN_SAMPLES and sigma2:
            a0 = (wide[(n, 0)] - wide[(x, 0)]) / wide[(d, 0)]
            a1 = (wide[(n, 1)] - wide[(x, 1)]) / wide[(d, 1)]
            est = split_half(a0, a1, wide[(d, 0)], wide[(d, 1)], sigma2)
            row.update(true_sd=est["true_sd"], k_balls=est["k"], split_half_r=est["split_half_r"])
            typical = float(qual[d].median())
            row["typical_balls"] = typical
            if est["k"]:
                row["reliability_typical"] = typical / (typical + est["k"])
        # Year to year.
        q = qual.assign(a=(qual[n] - qual[x]) / qual[d])[["player", "yr", "a", d]]
        pairs = q.merge(q.assign(yr=q["yr"] - 1), on=["player", "yr"], suffixes=("", "_next"))
        row["n_pairs"] = len(pairs)
        if len(pairs) >= MIN_SAMPLES:
            w = pairs[d] * pairs[f"{d}_next"] / (pairs[d] + pairs[f"{d}_next"])
            row["yoy_r"] = wcorr(pairs["a"].to_numpy(), pairs["a_next"].to_numpy(), w.to_numpy())
    return rows


def _prediction_rows(fg, g, seasons, stab, min_n) -> list[dict]:
    """Bowling: predicting next season's economy and wickets above expected."""
    k = {r["metric"]: r["k_balls"] for r in stab if r["fgroup"] == fg and r["gender"] == g and r["role"] == "bowling"}
    q = seasons[seasons["_den"] >= min_n].copy()
    for m in ("runs", "fib_runs", "wickets", "fib_wickets"):
        q[m] = (q[f"{m}__n"] - q[f"{m}__x"]) / q[f"{m}__d"]
    q["n"] = q["runs__d"]
    # Luck-adjusted: in-play runs and outfield catches regressed by their K
    # rather than replaced outright (what Doosra's FIB metrics use).
    ir_n, ir_x, ir_d = q["inplay_runs__n"], q["inplay_runs__x"], q["inplay_runs__d"]
    ct_n, ct_x, den = q["ct_field__n"], q["ct_field__x"], q["n"]
    skill_ir = ir_d * _shrink(ir_n, ir_x, ir_d, k.get("inplay_runs"))
    skill_ct = den * _shrink(ct_n, ct_x, den, k.get("ct_field"))
    q["adj_runs"] = (q["runs__n"] - ir_n + skill_ir - q["runs__x"]) / den
    q["adj_wickets"] = (q["wickets__n"] - ct_n + skill_ct - q["wickets__x"]) / den
    pairs = q.merge(q.assign(yr=q["yr"] - 1), on=["player", "yr"], suffixes=("", "_next"))
    if len(pairs) < MIN_SAMPLES:
        return []
    w, fold = pairs["n_next"].to_numpy(), (pairs["yr"] % 2).to_numpy()
    rows = []
    for target, fib, scale, unit in (("runs", "fib_runs", 6, "runs per over"),
                                     ("wickets", "fib_wickets", 100, "wickets per 100 balls")):
        y = pairs[f"{target}_next"].to_numpy()
        predictors = {
            "league average": None,
            "this season": pairs[target].to_numpy(),
            "this season, regressed": regress(pairs[target].to_numpy(), pairs["n"].to_numpy(), k.get(target)),
            "this season, DIPS-style (league-average replacement)": pairs[fib].to_numpy(),
            "this season, luck-adjusted": pairs[f"adj_{target}"].to_numpy(),
        }
        for name, x in predictors.items():
            if x is not None and np.allclose(x, 0):
                continue
            rmse, r = out_of_sample(x, y, w, fold)
            rows.append({"fgroup": fg, "gender": g, "target": target, "predictor": name,
                         "rmse": rmse * scale, "r": r, "n_pairs": len(pairs), "unit": unit})
    return rows


def run(con, progress=print) -> dict:
    """Run the study (needs the build's ball table `bx` and the per-innings
    tables) and write the fibs_* tables. Returns row counts."""
    stab, pred = [], []
    for role in ("bowling", "batting"):
        specs = _specs(role)
        halves = _seasons(con, role, specs)
        main_den = "runs__d"
        halves["_den"] = halves[main_den]
        ball_var = _ball_variance(con, specs)
        for (fg, g), h in halves.groupby(["fgroup", "gender"]):
            seasons = h.drop(columns="half").groupby(["player", "fgroup", "gender", "yr"], as_index=False).sum()
            rows = _metric_rows(role, fg, g, h, seasons, specs, ball_var, MIN_SEASON[role][fg])
            stab += rows
            if role == "bowling":
                pred += _prediction_rows(fg, g, seasons, rows, MIN_SEASON[role][fg])

    stab_df = pd.DataFrame(stab, columns=[
        "fgroup", "gender", "role", "metric", "label", "grp", "unit", "scale", "league_rate", "true_sd",
        "k_balls", "typical_balls", "reliability_typical", "split_half_r", "yoy_r", "n_seasons", "n_pairs"])
    pred_df = pd.DataFrame(pred, columns=["fgroup", "gender", "target", "predictor", "rmse", "r", "n_pairs", "unit"])
    for name, df in (("fibs_stability", stab_df), ("fibs_prediction", pred_df)):
        df = df.astype({c: "float64" for c in df.columns if c in (
            "league_rate", "true_sd", "k_balls", "typical_balls", "reliability_typical", "split_half_r",
            "yoy_r", "rmse", "r", "scale")})
        con.register("_fibs_df", df)
        try:
            con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _fibs_df")
        finally:
            con.unregister("_fibs_df")
    progress(f"  FIBS study: {len(stab_df)} stability rows, {len(pred_df)} prediction rows")
    return {"fibs_stability": len(stab_df), "fibs_prediction": len(pred_df)}
