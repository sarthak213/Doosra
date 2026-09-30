"""
Train Doosra's ball-by-ball win-probability models (T20 and ODI), evaluate them on later seasons,
and export them for the app and for Hugging Face.

    python ml/winprob_train.py                      # both groups
    python ml/winprob_train.py --group T20

Splits are by time, so the test is a real forecast: train on matches up to 2022, calibrate and
early-stop on 2023-24, test on 2025 onwards. Training rows: matches with 6-ball overs and a
decided result, with no rain rule and a full-length chase (Match Replay still scores the rest).

Outputs:
  backend/models/winprob-<group>.json   the model the app loads (trees + isotonic calibration)
  ml/out/winprob/<group>/               lightgbm.txt, report.json, reliability.png, by_over.png
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import lightgbm as lgb
import matplotlib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from analytics import db, winprob  # noqa: E402

OUT = ROOT / "ml" / "out" / "winprob"
TRAIN_TO, VALID_TO = 2022, 2024
CLEAN = ("m.method IS NULL AND m.result IS NULL AND m.winner IS NOT NULL "
         "AND (m.target_overs IS NULL OR m.target_overs = m.overs_per_innings)")
# Rows within a match are near-duplicates, so a leaf must span many matches (an ODI alone is ~600 rows).
PARAMS = dict(objective="binary", learning_rate=0.02, num_leaves=15, min_data_in_leaf=20000, feature_fraction=0.9,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, monotone_constraints_method="advanced")
# Cricket common sense, enforced: the batting side's chance can only rise with more wickets in hand, balls left,
# runs scored or a stronger side, and only fall as runs needed or the required rate go up.
MONOTONE = {"balls_left": 1, "wickets_in_hand": 1, "score": 1, "runs_needed": -1, "required_rate": -1, "elo_diff": 1}


def load(group: str) -> dict[str, np.ndarray]:
    con = db.connect()                    # the app's settings: DuckDB won't mix two on one file
    t = time.time()
    cols = con.execute(winprob.states_sql(group, CLEAN)).fetchnumpy()
    con.close()
    print(f"{group}: {len(cols['match_id']):,} ball states from {len(set(cols['match_id'])):,} matches "
          f"({time.time() - t:.0f}s)")
    return cols


def ece(y: np.ndarray, p: np.ndarray, bins: int = 20) -> float:
    """Expected calibration error: how far predicted chances are from what happened, on average."""
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return float(sum(abs(y[idx == b].mean() - p[idx == b].mean()) * (idx == b).mean() for b in range(bins)
                     if (idx == b).any()))


def scores(y: np.ndarray, p: np.ndarray) -> dict:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return {"log_loss": round(log_loss(y, p), 4), "brier": round(brier_score_loss(y, p), 4),
            "auc": round(roc_auc_score(y, p), 4), "ece": round(ece(y, p), 4),
            "accuracy": round(float(((p > 0.5) == y).mean()), 4)}


def par_baseline(X: np.ndarray) -> np.ndarray:
    """A simple cricket heuristic: first innings, projected total vs venue par; chase, required rate vs the
    rate the side can still score at given wickets in hand. Squashed with a logistic."""
    c = {n: X[:, i] for i, n in enumerate(winprob.FEATURES)}
    balls_left = np.maximum(c["balls_left"], 1)
    bowled = np.maximum(c["overs"] * 6 - c["balls_left"], 6)
    resource = np.clip(c["wickets_in_hand"] / 10, 0.1, 1) ** 0.5
    par = np.nan_to_num(c["venue_par"], nan=np.nanmean(c["venue_par"]))
    rate = np.where(c["innings"] == 1, c["score"], np.nan_to_num(c["target"] - c["runs_needed"])) * 6 / bowled
    first = (c["score"] + rate * balls_left / 6 * resource - par) / (0.12 * par)
    chase = (rate * resource + 1.0 - np.nan_to_num(c["required_rate"])) / 1.5
    z = np.where(c["innings"] == 1, first, chase)
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def swing(cols: dict, p: np.ndarray, rows: np.ndarray) -> dict:
    """How much the prediction moves on an ordinary ball (no wicket, no boundary): the worm's jumpiness."""
    same = np.r_[False, (cols["match_id"][1:] == cols["match_id"][:-1]) & (cols["innings"][1:] == cols["innings"][:-1])]
    quiet = ~cols["is_wicket"].astype(bool) & ~np.isin(cols["runs_batter"], [4, 6])
    d = np.abs(np.diff(p, prepend=p[0]))[rows & same & quiet]
    return {"mean": round(float(d.mean()), 4), "p99": round(float(np.quantile(d, 0.99)), 4)}


def train_innings(inn: int, X: np.ndarray, y: np.ndarray, train, valid, test) -> tuple[dict, lgb.Booster]:
    """One innings' blend: gradient-boosted trees plus a logistic regression, averaged in log-odds."""
    names = winprob.INNINGS_FEATURES[inn]
    Xs = winprob.select(X, names)
    rows = X[:, 0] == inn
    tr, va = train & rows, valid & rows
    params = dict(PARAMS, monotone_constraints=[MONOTONE.get(f, 0) for f in names])
    t = time.time()
    booster = lgb.train(params, lgb.Dataset(Xs[tr], y[tr], feature_name=names), num_boost_round=4000,
                        valid_sets=[lgb.Dataset(Xs[va], y[va], feature_name=names)],
                        callbacks=[lgb.early_stopping(80, verbose=False)])
    scaler = StandardScaler().fit(np.nan_to_num(Xs[tr]))
    lr = LogisticRegression(max_iter=1000).fit(scaler.transform(np.nan_to_num(Xs[tr])), y[tr])
    print(f"  innings {inn}: {booster.best_iteration} trees + logistic regression ({time.time() - t:.0f}s)")
    spec = {"features": names, "trees": booster.dump_model(num_iteration=booster.best_iteration),
            "linear": {"mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
                       "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0])}}
    te = np.flatnonzero(test & rows)
    sample = np.random.default_rng(0).choice(te, size=min(20000, len(te)), replace=False)
    trees = winprob.TreeModel({"features": names, "model": spec["trees"]})
    diff = np.abs(trees.predict(Xs[sample], calibrated=False)
                  - booster.predict(Xs[sample], num_iteration=booster.best_iteration)).max()
    # LightGBM rounds split thresholds when writing JSON, so a value exactly on one can take the other branch
    assert diff < 1e-3, f"NumPy trees disagree with LightGBM by {diff}"
    spec["numpy_vs_lightgbm_max_diff"] = float(diff)
    spec["feature_importance"] = dict(sorted(zip(names, booster.feature_importance("gain").round(0).tolist()),
                                             key=lambda kv: -kv[1]))
    return spec, booster


def main(group: str) -> dict:
    cols = load(group)
    X = winprob.features(cols, group)
    y = cols["won"].astype(np.int64)
    year = cols["year"].astype(np.int64)
    train, valid, test = year <= TRAIN_TO, (year > TRAIN_TO) & (year <= VALID_TO), year > VALID_TO
    print(f"  train {train.sum():,}  valid {valid.sum():,}  test {test.sum():,} rows")

    out = OUT / group
    out.mkdir(parents=True, exist_ok=True)
    specs = {}
    for inn in (1, 2):
        specs[str(inn)], booster = train_innings(inn, X, y, train, valid, test)
        booster.save_model(str(out / f"lightgbm-innings{inn}.txt"), num_iteration=booster.best_iteration)
    spec = {"version": 3, "group": group, "innings": specs,
            "trained_on": {"train_to": TRAIN_TO, "early_stopped_on": f"{TRAIN_TO + 1}-{VALID_TO}",
                           "tested_on": f"{VALID_TO + 1}+", "built": date.today().isoformat(),
                           "rows": int(train.sum()), "matches": int(len(set(cols["match_id"][train])))}}
    model = winprob.WinProbModel(spec)                          # evaluated exactly as the app evaluates it
    parts = {"blend": np.full(len(X), np.nan), "trees": np.full(len(X), np.nan), "logistic": np.full(len(X), np.nan)}
    for inn in (1, 2):
        rows = test & (X[:, 0] == inn)
        Xs = winprob.select(X[rows], winprob.INNINGS_FEATURES[inn])
        _, trees, linear = model.innings[inn]
        parts["blend"][rows] = model.predict_innings(inn, Xs)
        parts["trees"][rows] = trees.predict(Xs, calibrated=False)
        parts["logistic"][rows] = 1 / (1 + np.exp(-linear.logit(Xs)))
    p_model = parts["blend"][test]
    report = {
        "group": group, "rows": {"train": int(train.sum()), "valid": int(valid.sum()), "test": int(test.sum())},
        "test_matches": int(len(set(cols["match_id"][test]))),
        "model": scores(y[test], p_model),
        "by_innings": {inn: scores(y[test & (X[:, 0] == inn)], parts["blend"][test & (X[:, 0] == inn)]) for inn in (1, 2)},
        "swing_on_ordinary_balls": swing(cols, parts["blend"], test),
        "baselines": {
            "trees_alone": {**scores(y[test], parts["trees"][test]), "swing": swing(cols, parts["trees"], test)},
            "logistic_regression_alone": {**scores(y[test], parts["logistic"][test]),
                                          "swing": swing(cols, parts["logistic"], test)},
            "par_heuristic": scores(y[test], par_baseline(X[test])),
            "base_rate": scores(y[test], np.full(test.sum(), y[train].mean())),
        },
        "feature_importance": {inn: specs[str(inn)]["feature_importance"] for inn in (1, 2)},
    }
    spec["metrics"] = report["model"]
    (ROOT / "backend" / "models").mkdir(exist_ok=True)
    (ROOT / "backend" / "models" / f"winprob-{group.lower()}.json").write_text(json.dumps(spec), encoding="utf-8")
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    plots(out, group, y[test], p_model, X[test])
    print(json.dumps({"model": report["model"], "swing": report["swing_on_ordinary_balls"], **report["baselines"]},
                     indent=1))
    return report


def plots(out: Path, group: str, y: np.ndarray, p: np.ndarray, X: np.ndarray) -> None:
    edges = np.linspace(0, 1, 11)
    mids, rates, counts = [], [], []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (p >= a) & (p < b if b < 1 else p <= b)
        if m.any():
            mids.append(p[m].mean()); rates.append(y[m].mean()); counts.append(m.sum())
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], ls="--", color="#999", lw=1, label="perfect")
    ax.plot(mids, rates, marker="o", color="#b23a2e", lw=2, label="model")
    ax.set(xlabel="Predicted win probability", ylabel="How often the batting side won",
           title=f"{group} win probability: calibration (2025+)", xlim=(0, 1), ylim=(0, 1))
    ax.legend(loc="upper left", frameon=False)
    fig.tight_layout(); fig.savefig(out / "reliability.png", dpi=150); plt.close(fig)

    names = winprob.FEATURES
    overs_bowled = (X[:, names.index("overs")] * 6 - X[:, names.index("balls_left")]) / 6
    fig, ax = plt.subplots(figsize=(7, 4))
    for inn, colour in ((1, "#8f7a4c"), (2, "#b23a2e")):
        acc = []
        for o in range(int(np.nanmax(X[:, names.index("overs")]))):
            m = (X[:, 0] == inn) & (overs_bowled >= o) & (overs_bowled < o + 1)
            acc.append(((p[m] > 0.5) == y[m]).mean() if m.sum() > 100 else np.nan)
        ax.plot(range(1, len(acc) + 1), acc, color=colour, lw=2, label=f"innings {inn}")
    ax.set(xlabel="Over", ylabel="Winner called correctly", title=f"{group}: accuracy through the match (2025+)",
           ylim=(0.4, 1))
    ax.legend(frameon=False)
    fig.tight_layout(); fig.savefig(out / "by_over.png", dpi=150); plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=sorted(winprob.GROUPS), action="append")
    for g in parser.parse_args().group or sorted(winprob.GROUPS):
        main(g)
