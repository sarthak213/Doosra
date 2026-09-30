"""
Doosra's ball-outcome model: for a ball faced, the chance of each result (dot, 1, 2, 3, 4, 6, out),
given the match situation and both players' form going into the match.

    python ml/ball_outcome_train.py                 # T20 and ODI
    python ml/ball_outcome_train.py --group T20 --sample 0.2

The question it answers: how much does knowing the batter and the bowler add to the situation alone?
Doosra's metrics (true strike rate, runs above expected, FIBS) judge players against
`ball_expectation`, a lookup of expected runs and dismissal chance per situation (format, gender,
year, innings, over, wickets down). This model is scored against that table and against itself
without the player features, on matches from 2025 on (trained on matches up to 2022).

Player form is built from earlier matches only (career totals before the match, in the same format
group and gender), shrunk toward the average for players with few balls. Players are matched by
name as in Cricsheet's ball-by-ball records.

Outputs (ml/out/ball_outcome/<group>/): lightgbm.txt, spec.json (features, classes, shrinkage),
report.json, calibration.png, players.parquet (each player's latest form, for predictions).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import matplotlib
import numpy as np
from sklearn.metrics import brier_score_loss, log_loss

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from analytics import db, winprob  # noqa: E402
from analytics.scope import ball_exprs  # noqa: E402

OUT = ROOT / "ml" / "out" / "ball_outcome"
TRAIN_TO, VALID_TO = 2022, 2024
CLASSES = ["0", "1", "2", "3", "4", "6", "out"]
RUNS = np.array([0, 1, 2, 3, 4, 6, 0], dtype=float)     # runs off the bat for each class
SHRINK_BALLS = 120                                      # form counts as if it began with 120 average balls
BAT_RATES = ["bat_runs", "bat_outs", "bat_dots", "bat_fours", "bat_sixes"]
BOWL_RATES = ["bowl_runs", "bowl_wkts", "bowl_dots", "bowl_fours", "bowl_sixes"]
STATE = ["innings", "over", "ball", "wickets", "score", "balls_left", "runs_needed", "required_rate", "venue_par",
         "female"]
FORM = ["bat_balls", *[f"{c}_rate" for c in BAT_RATES], "bat_inn_balls", "bat_inn_runs",
        "bowl_balls", *[f"{c}_rate" for c in BOWL_RATES]]
PARAMS = dict(objective="multiclass", num_class=len(CLASSES), learning_rate=0.05, num_leaves=63,
              min_data_in_leaf=2000, feature_fraction=0.9, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0,
              verbose=-1)


def rows_sql(group: str, where: str = "TRUE") -> str:
    """Every ball faced in innings 1-2 of the group's matches, with the situation before it, the outcome,
    and both players' career totals from earlier matches."""
    cols = {r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'deliveries'")}
    e = ball_exprs(cols)
    types = ", ".join(f"'{t}'" for t in winprob.GROUPS[group])
    not_out_kinds = "('retired hurt', 'retired not out')"
    credited = "('run out', 'retired hurt', 'retired not out', 'retired out', 'obstructing the field')"
    return f"""
    WITH par AS ({winprob.venue_par_sql(group)}),
    m AS (
        SELECT m.match_id, m.match_type, m.date, m.gender, CAST(substr(m.date, 1, 4) AS INTEGER) AS year,
               m.overs_per_innings,
               m.target_runs, m.target_overs, p.venue_par
        FROM matches m LEFT JOIN par p USING (match_id)
        WHERE m.match_type IN ({types}) AND COALESCE(m.balls_per_over, 6) = 6
    ),
    keep AS (SELECT match_id FROM matches m WHERE {where}),
    d AS (
        SELECT d.match_id, d.innings_num AS innings, d.over_num AS over, d.ball_in_over AS ball, d.batter, d.bowler,
               d.runs_batter, d.runs_total, d.is_wicket, d.wicket_kind, {e['faced']} AS faced, {e['legal']} AS legal,
               (d.is_wicket AND d.player_dismissed = d.batter
                AND COALESCE(d.wicket_kind, '') NOT IN {not_out_kinds}) AS striker_out,
               (d.is_wicket AND COALESCE(d.wicket_kind, '') NOT IN {credited}) AS bowler_wicket,
               {e['four']} AS four, {e['six']} AS six,
               m.date, m.gender, m.year, m.venue_par, m.overs_per_innings, m.target_runs, m.target_overs,
               ROW_NUMBER() OVER (PARTITION BY d.match_id, d.innings_num ORDER BY d.over_num, d.ball_in_over, d.rowid)
                   AS seq
        FROM deliveries d JOIN m USING (match_id)
        WHERE {e['regular']} AND d.innings_num IN (1, 2)
    ),
    s AS (
        SELECT *,
               COALESCE(SUM(runs_total) OVER w, 0) AS score,
               COALESCE(SUM(CAST(legal AS INTEGER)) OVER w, 0) AS legal_before,
               COALESCE(SUM(CAST(is_wicket AND COALESCE(wicket_kind, '') NOT IN {not_out_kinds} AS INTEGER)) OVER w, 0)
                   AS wickets,
               COALESCE(SUM(CAST(faced AS INTEGER)) OVER wb, 0) AS bat_inn_balls,
               COALESCE(SUM(runs_batter) OVER wb, 0) AS bat_inn_runs
        FROM d
        WINDOW w AS (PARTITION BY match_id, innings ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
               wb AS (PARTITION BY match_id, innings, batter ORDER BY seq
                      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
    ),
    first_total AS (SELECT match_id, SUM(runs_total) AS total FROM d WHERE innings = 1 GROUP BY 1),
    bat_match AS (
        SELECT batter AS player, gender, match_id, MIN(date) AS date, COUNT(*) AS balls, SUM(runs_batter) AS runs,
               SUM(CAST(striker_out AS INTEGER)) AS outs, SUM(CAST(runs_batter = 0 AND NOT striker_out AS INTEGER)) AS dots,
               SUM(CAST(four AS INTEGER)) AS fours, SUM(CAST(six AS INTEGER)) AS sixes
        FROM d WHERE faced GROUP BY 1, 2, 3
    ),
    bat_form AS (
        SELECT player, gender, match_id,
               SUM(balls) OVER p AS bat_balls, SUM(runs) OVER p AS bat_runs, SUM(outs) OVER p AS bat_outs,
               SUM(dots) OVER p AS bat_dots, SUM(fours) OVER p AS bat_fours, SUM(sixes) OVER p AS bat_sixes
        FROM bat_match
        WINDOW p AS (PARTITION BY player, gender ORDER BY date, match_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
    ),
    bowl_match AS (
        SELECT bowler AS player, gender, match_id, MIN(date) AS date, COUNT(*) AS balls, SUM(runs_batter) AS runs,
               SUM(CAST(bowler_wicket AS INTEGER)) AS wkts, SUM(CAST(runs_batter = 0 AND NOT bowler_wicket AS INTEGER)) AS dots,
               SUM(CAST(four AS INTEGER)) AS fours, SUM(CAST(six AS INTEGER)) AS sixes
        FROM d WHERE faced GROUP BY 1, 2, 3
    ),
    bowl_form AS (
        SELECT player, gender, match_id,
               SUM(balls) OVER p AS bowl_balls, SUM(runs) OVER p AS bowl_runs, SUM(wkts) OVER p AS bowl_wkts,
               SUM(dots) OVER p AS bowl_dots, SUM(fours) OVER p AS bowl_fours, SUM(sixes) OVER p AS bowl_sixes
        FROM bowl_match
        WINDOW p AS (PARTITION BY player, gender ORDER BY date, match_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
    )
    SELECT s.match_id, s.year, s.gender, s.innings, s.over, s.ball, s.batter, s.bowler,
           CASE WHEN s.striker_out THEN 6 WHEN s.runs_batter >= 6 THEN 5 WHEN s.runs_batter >= 4 THEN 4
                ELSE s.runs_batter END AS y,
           s.runs_batter, s.striker_out, s.score, s.wickets, s.legal_before, s.bat_inn_balls, s.bat_inn_runs,
           CAST(COALESCE(CASE WHEN s.innings = 2 THEN s.target_overs END, s.overs_per_innings) * 6 AS INTEGER)
               - s.legal_before AS balls_left,
           CASE WHEN s.innings = 2 THEN COALESCE(s.target_runs, f.total + 1) END AS target,
           s.venue_par, s.overs_per_innings,
           COALESCE(bf.bat_balls, 0) AS bat_balls, COALESCE(bf.bat_runs, 0) AS bat_runs, COALESCE(bf.bat_outs, 0) AS bat_outs,
           COALESCE(bf.bat_dots, 0) AS bat_dots, COALESCE(bf.bat_fours, 0) AS bat_fours, COALESCE(bf.bat_sixes, 0) AS bat_sixes,
           COALESCE(wf.bowl_balls, 0) AS bowl_balls, COALESCE(wf.bowl_runs, 0) AS bowl_runs, COALESCE(wf.bowl_wkts, 0) AS bowl_wkts,
           COALESCE(wf.bowl_dots, 0) AS bowl_dots, COALESCE(wf.bowl_fours, 0) AS bowl_fours, COALESCE(wf.bowl_sixes, 0) AS bowl_sixes
    FROM s LEFT JOIN first_total f USING (match_id)
    LEFT JOIN bat_form bf ON bf.player = s.batter AND bf.gender = s.gender AND bf.match_id = s.match_id
    LEFT JOIN bowl_form wf ON wf.player = s.bowler AND wf.gender = s.gender AND wf.match_id = s.match_id
    WHERE s.faced AND s.match_id IN (SELECT match_id FROM keep)
    ORDER BY s.match_id, s.innings, s.seq"""


def load(group: str, where: str = "TRUE") -> dict[str, np.ndarray]:
    t = time.time()
    con = db.connect()                    # the app's settings: DuckDB won't mix two on one file
    raw = con.execute(rows_sql(group, where)).fetchnumpy()
    con.close()
    cols = {k: (np.ma.filled(v.astype(float), np.nan) if np.ma.isMaskedArray(v) and v.dtype.kind in "fiub"
                else np.ma.filled(v, None) if np.ma.isMaskedArray(v) else v) for k, v in raw.items()}
    print(f"{group}: {len(cols['y']):,} balls faced ({time.time() - t:.0f}s)")
    return cols


def players_sql(group: str) -> str:
    """Every player's career totals to date in the group (the form the model reads), by name and gender."""
    cols = {r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'deliveries'")}
    e = ball_exprs(cols)
    types = ", ".join(f"'{t}'" for t in winprob.GROUPS[group])
    credited = "('run out', 'retired hurt', 'retired not out', 'retired out', 'obstructing the field')"
    return f"""
    WITH d AS (
        SELECT d.batter, d.bowler, m.gender, d.runs_batter, m.date,
               (d.is_wicket AND d.player_dismissed = d.batter
                AND COALESCE(d.wicket_kind, '') NOT IN ('retired hurt', 'retired not out')) AS striker_out,
               (d.is_wicket AND COALESCE(d.wicket_kind, '') NOT IN {credited}) AS bowler_wicket,
               {e['four']} AS four, {e['six']} AS six
        FROM deliveries d JOIN matches m USING (match_id)
        WHERE m.match_type IN ({types}) AND COALESCE(m.balls_per_over, 6) = 6 AND {e['regular']}
          AND d.innings_num IN (1, 2) AND {e['faced']}
    ),
    bat AS (SELECT batter AS player, gender, COUNT(*) AS bat_balls, SUM(runs_batter) AS bat_runs,
                   SUM(CAST(striker_out AS INTEGER)) AS bat_outs,
                   SUM(CAST(runs_batter = 0 AND NOT striker_out AS INTEGER)) AS bat_dots,
                   SUM(CAST(four AS INTEGER)) AS bat_fours, SUM(CAST(six AS INTEGER)) AS bat_sixes, MAX(date) AS bat_last
            FROM d GROUP BY 1, 2),
    bowl AS (SELECT bowler AS player, gender, COUNT(*) AS bowl_balls, SUM(runs_batter) AS bowl_runs,
                    SUM(CAST(bowler_wicket AS INTEGER)) AS bowl_wkts,
                    SUM(CAST(runs_batter = 0 AND NOT bowler_wicket AS INTEGER)) AS bowl_dots,
                    SUM(CAST(four AS INTEGER)) AS bowl_fours, SUM(CAST(six AS INTEGER)) AS bowl_sixes, MAX(date) AS bowl_last
             FROM d GROUP BY 1, 2)
    SELECT COALESCE(bat.player, bowl.player) AS player, COALESCE(bat.gender, bowl.gender) AS gender,
           COALESCE(bat_balls, 0) AS bat_balls, COALESCE(bat_runs, 0) AS bat_runs, COALESCE(bat_outs, 0) AS bat_outs,
           COALESCE(bat_dots, 0) AS bat_dots, COALESCE(bat_fours, 0) AS bat_fours, COALESCE(bat_sixes, 0) AS bat_sixes,
           COALESCE(bowl_balls, 0) AS bowl_balls, COALESCE(bowl_runs, 0) AS bowl_runs, COALESCE(bowl_wkts, 0) AS bowl_wkts,
           COALESCE(bowl_dots, 0) AS bowl_dots, COALESCE(bowl_fours, 0) AS bowl_fours, COALESCE(bowl_sixes, 0) AS bowl_sixes,
           GREATEST(COALESCE(bat_last, ''), COALESCE(bowl_last, '')) AS last_match
    FROM bat FULL OUTER JOIN bowl ON bat.player = bowl.player AND bat.gender = bowl.gender
    ORDER BY player"""


def export_players(group: str) -> Path:
    """The player-form snapshot the published predictor looks names up in."""
    path = OUT / group / "players.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = db.connect()                    # read-only, with no file access: read here, write below
    players = con.execute(players_sql(group)).df()
    con.close()
    import duckdb
    duckdb.sql(f"COPY (SELECT * FROM players) TO '{path.as_posix()}' (FORMAT parquet, COMPRESSION zstd)")
    n = len(players)
    print(f"  {n:,} players' form -> {path}")
    return path


def form_rates(cols: dict, means: dict | None = None) -> tuple[dict, dict]:
    """Career rates per ball faced (batter) or bowled (bowler), shrunk toward the average: a player with
    few balls looks like an average player until the balls add up."""
    means = dict(means or {})
    out = {}
    for prefix, names in (("bat", BAT_RATES), ("bowl", BOWL_RATES)):
        balls = cols[f"{prefix}_balls"]
        for n in names:
            if n not in means:
                means[n] = float(cols[n].sum() / max(balls.sum(), 1))
            out[f"{n}_rate"] = (cols[n] + SHRINK_BALLS * means[n]) / (balls + SHRINK_BALLS)
    return out, means


def features(cols: dict, means: dict | None = None) -> tuple[np.ndarray, dict]:
    rates, means = form_rates(cols, means)

    def f(k):
        return np.asarray(cols[k], dtype=float)

    chase = (f("innings") == 2) & ~np.isnan(f("target"))
    need = np.where(chase, f("target") - f("score"), np.nan)
    req = need * 6 / np.maximum(f("balls_left"), 1)
    state = {"innings": f("innings"), "over": f("over"), "ball": f("ball"), "wickets": f("wickets"), "score": f("score"),
             "balls_left": f("balls_left"), "runs_needed": need, "required_rate": req, "venue_par": f("venue_par"),
             "female": (np.asarray(cols["gender"]) == "female").astype(float)}
    form = {"bat_balls": f("bat_balls"), **{k: v for k, v in rates.items() if k.startswith("bat_")},
            "bat_inn_balls": f("bat_inn_balls"), "bat_inn_runs": f("bat_inn_runs"),
            "bowl_balls": f("bowl_balls"), **{k: v for k, v in rates.items() if k.startswith("bowl_")}}
    both = {**state, **form}
    return np.column_stack([both[n] for n in STATE + FORM]), means


def situation_key(cols: dict) -> np.ndarray:
    female = (np.asarray(cols["gender"]) == "female").astype(np.int64)
    return (female * 10000 + cols["innings"].astype(np.int64) * 1000
            + np.minimum(cols["over"], 49).astype(np.int64) * 10 + np.minimum(cols["wickets"], 9).astype(np.int64))


def situation_lookup(cols: dict, y: np.ndarray, train: np.ndarray) -> np.ndarray:
    """Baseline: the mix of outcomes seen in the same situation (gender, innings, over, wickets down) in the
    training years, smoothed a little toward the overall mix."""
    k = situation_key(cols)
    overall = np.bincount(y[train], minlength=len(CLASSES)) / train.sum()
    table = {}
    for cell in np.unique(k[train]):
        rows = train & (k == cell)
        table[cell] = (np.bincount(y[rows], minlength=len(CLASSES)) + 50 * overall) / (rows.sum() + 50)
    return np.array([table.get(c, overall) for c in k])


def lookup_table_baseline(cols: dict, group: str) -> tuple[np.ndarray, np.ndarray]:
    """Doosra's ball_expectation table: expected runs and dismissal chance per situation. It's built from
    all years, the test years included, so it has seen the answers for its own cells."""
    rows = db.query(f"""SELECT gender, yr, innings_num, state_over, wkts, exp_bat_runs, exp_bat_out
                        FROM ball_expectation WHERE fgroup = '{group}'""")
    table = {(r["gender"], r["yr"], r["innings_num"], r["state_over"], r["wkts"]): (r["exp_bat_runs"], r["exp_bat_out"])
             for r in rows}
    runs, out = np.full(len(cols["y"]), np.nan), np.full(len(cols["y"]), np.nan)
    keys = zip(cols["gender"], cols["year"].astype(int), cols["innings"].astype(int),
               np.minimum(cols["over"], 49).astype(int), np.minimum(cols["wickets"], 9).astype(int))
    for i, key in enumerate(keys):
        runs[i], out[i] = table.get(key, (np.nan, np.nan))
    return runs, out


def scores(y: np.ndarray, P: np.ndarray) -> dict:
    P = np.clip(P, 1e-6, 1)
    P = P / P.sum(axis=1, keepdims=True)
    out = (y == CLASSES.index("out")).astype(int)
    return {"log_loss": round(log_loss(y, P, labels=range(len(CLASSES))), 4),
            "out_log_loss": round(log_loss(out, P[:, -1]), 4), "out_brier": round(brier_score_loss(out, P[:, -1]), 5),
            "runs_rmse": round(float(np.sqrt(np.mean((P @ RUNS - RUNS[y]) ** 2))), 4)}


def train_booster(X, y, train, valid, names):
    t = time.time()
    b = lgb.train(PARAMS, lgb.Dataset(X[train], y[train], feature_name=names), num_boost_round=3000,
                  valid_sets=[lgb.Dataset(X[valid], y[valid], feature_name=names)],
                  callbacks=[lgb.early_stopping(50, verbose=False)])
    print(f"  {len(names)} features: {b.best_iteration} rounds ({time.time() - t:.0f}s)")
    return b


def main(group: str, sample: float = 1.0) -> dict:
    where = "TRUE" if sample >= 1 else f"hash(m.match_id) % 1000 < {int(sample * 1000)}"
    cols = load(group, where)
    y = cols["y"].astype(np.int64)
    year = cols["year"].astype(int)
    train, valid, test = year <= TRAIN_TO, (year > TRAIN_TO) & (year <= VALID_TO), year > VALID_TO
    names = STATE + FORM
    X, means = features(cols)
    print(f"  train {train.sum():,}  valid {valid.sum():,}  test {test.sum():,} balls")

    full = train_booster(X, y, train, valid, names)
    state_only = train_booster(X[:, :len(STATE)], y, train, valid, STATE)
    P_full = full.predict(X[test], num_iteration=full.best_iteration)
    P_state = state_only.predict(X[test][:, :len(STATE)], num_iteration=state_only.best_iteration)
    P_lookup = situation_lookup(cols, y, train)[test]
    base_runs, base_out = lookup_table_baseline({k: v[test] for k, v in cols.items()}, group)
    have = ~np.isnan(base_out)
    yt = y[test]
    out_t = (yt == CLASSES.index("out")).astype(int)

    def rmse(pred, truth):
        return round(float(np.sqrt(np.mean((pred - truth) ** 2))), 4)

    report = {
        "group": group, "balls": {"train": int(train.sum()), "valid": int(valid.sum()), "test": int(test.sum())},
        "class_share_test": dict(zip(CLASSES, np.round(np.bincount(yt, minlength=len(CLASSES)) / len(yt), 4).tolist())),
        "model": scores(yt, P_full),
        "model_without_player_form": scores(yt, P_state),
        "situation_lookup": scores(yt, P_lookup),
        "overall_mix": scores(yt, np.tile(np.bincount(y[train], minlength=len(CLASSES)) / train.sum(), (len(yt), 1))),
        "vs_ball_expectation": {
            "balls_compared": int(have.sum()),
            "model": {"out_log_loss": round(log_loss(out_t[have], np.clip(P_full[have, -1], 1e-6, 1)), 4),
                      "runs_rmse": rmse(P_full[have] @ RUNS, RUNS[yt[have]])},
            "ball_expectation": {"out_log_loss": round(log_loss(out_t[have], np.clip(base_out[have], 1e-6, 1 - 1e-6)), 4),
                                 "runs_rmse": rmse(base_runs[have], RUNS[yt[have]])},
        },
        "feature_importance": dict(sorted(zip(names, full.feature_importance("gain").round(0).tolist()),
                                          key=lambda kv: -kv[1])),
    }
    out = OUT / group
    out.mkdir(parents=True, exist_ok=True)
    full.save_model(str(out / "lightgbm.txt"), num_iteration=full.best_iteration)
    (out / "spec.json").write_text(json.dumps({"group": group, "classes": CLASSES, "runs": RUNS.tolist(),
                                               "features": names, "shrink_balls": SHRINK_BALLS, "form_means": means,
                                               "trained_to": TRAIN_TO, "tested_on": f"{VALID_TO + 1}+"}, indent=1))
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    calibration_plot(out, group, yt, P_full)
    export_players(group)
    print(json.dumps({k: report[k] for k in ("model", "model_without_player_form", "situation_lookup",
                                              "overall_mix", "vs_ball_expectation")}, indent=1))
    return report


def calibration_plot(out: Path, group: str, y: np.ndarray, P: np.ndarray) -> None:
    fig, ax = plt.subplots(figsize=(5.5, 5))
    top = 0.6
    ax.plot([0, top], [0, top], ls="--", color="#999", lw=1)
    for c, colour in (("out", "#b23a2e"), ("4", "#3987e5"), ("6", "#b08a3a"), ("0", "#8f7a4c")):
        k = CLASSES.index(c)
        p, hit = P[:, k], (y == k)
        edges = np.quantile(p, np.linspace(0, 1, 11))
        idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, 9)
        xs = [p[idx == b].mean() for b in range(10) if (idx == b).any()]
        ys = [hit[idx == b].mean() for b in range(10) if (idx == b).any()]
        ax.plot(xs, ys, marker="o", lw=2, color=colour, label="dot" if c == "0" else c)
    ax.set(xlim=(0, top), ylim=(0, top), xlabel="Predicted chance", ylabel="How often it happened",
           title=f"{group} next-ball outcomes: calibration (2025+)")
    ax.legend(frameon=False, title="outcome")
    fig.tight_layout()
    fig.savefig(out / "calibration.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=sorted(winprob.GROUPS), action="append")
    parser.add_argument("--sample", type=float, default=1.0, help="share of matches to use (quick runs)")
    a = parser.parse_args()
    for g in a.group or sorted(winprob.GROUPS):
        main(g, a.sample)
