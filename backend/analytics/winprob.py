"""
Win probability, ball by ball, for limited-overs cricket (T20 and ODI; men's and women's).

The same code builds the training rows (ml/winprob_train.py) and scores a match for Match
Replay, so the features can't drift apart. The trained models are LightGBM tree ensembles saved
as JSON (backend/models/winprob-<group>.json) and evaluated here with NumPy, so the app doesn't
need LightGBM or SciPy; a test checks the NumPy evaluation against LightGBM itself.

The match state after every ball (legal or not) becomes one row. Each innings has its own model,
a blend of gradient-boosted trees and a logistic regression (averaged on the log-odds scale: the
trees catch the non-linear parts, the regression keeps the curve smooth ball to ball), using only
the features that matter there:
    first innings: balls left, wickets in hand, score, the venue's par, gender, overs, team strength;
    the chase:     balls left, wickets in hand, runs needed, required rate, target, the venue's par,
                   gender, overs, team strength.
The venue's par is its average first-innings score in earlier matches (never this one or later), and
team strength is the batting side's Elo rating minus the bowling side's, from earlier results only.
The label is whether the batting side went on to win.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from analytics import db
from analytics.scope import ball_exprs

MODELS = Path(__file__).resolve().parent.parent / "models"
GROUPS = {"T20": ("T20", "IT20"), "ODI": ("ODI", "ODM")}
# every column features() builds; each innings' model uses its own subset
FEATURES = ["innings", "balls_left", "wickets_in_hand", "score", "target", "runs_needed", "required_rate",
            "venue_par", "female", "overs", "elo_diff"]
INNINGS_FEATURES = {
    1: ["balls_left", "wickets_in_hand", "score", "venue_par", "female", "overs", "elo_diff"],
    2: ["balls_left", "wickets_in_hand", "runs_needed", "required_rate", "target", "venue_par", "female", "overs",
        "elo_diff"],
}
ELO_K, ELO_START = 24.0, 1500.0


def group_of(match_type: str) -> str | None:
    return next((g for g, types in GROUPS.items() if match_type in types), None)


def _types(group: str) -> str:
    return ", ".join(f"'{t}'" for t in GROUPS[group])


def _rules() -> dict:
    """Which deliveries are legal and which are super overs, for whichever schema the database has."""
    cols = {r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'deliveries'")}
    return ball_exprs(cols)


def venue_par_sql(group: str) -> str:
    """Each match's venue par: the average first-innings total in the ground's last 20 earlier matches
    of the same format group and gender (the format's last 200 when the ground has fewer than 3)."""
    e = _rules()
    return f"""
        WITH first AS (
            SELECT m.match_id, m.date, m.gender, split_part(m.venue, ',', 1) AS ground,
                   SUM(d.runs_total) AS total
            FROM matches m JOIN deliveries d USING (match_id)
            WHERE m.match_type IN ({_types(group)}) AND d.innings_num = 1 AND {e['regular']}
            GROUP BY ALL
        ),
        roll AS (
            SELECT match_id,
                   AVG(total) OVER (PARTITION BY ground, gender ORDER BY date, match_id
                                    ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS ground_avg,
                   COUNT(*) OVER (PARTITION BY ground, gender ORDER BY date, match_id
                                  ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS ground_n,
                   AVG(total) OVER (PARTITION BY gender ORDER BY date, match_id
                                    ROWS BETWEEN 200 PRECEDING AND 1 PRECEDING) AS format_avg
            FROM first
        )
        SELECT match_id, CASE WHEN ground_n >= 3 THEN ground_avg ELSE format_avg END AS venue_par
        FROM roll"""


def states_sql(group: str, where: str = "TRUE") -> str:
    """The state after every ball of the matching limited-overs matches (innings 1 and 2 only)."""
    e = _rules()
    return f"""
        WITH par AS ({venue_par_sql(group)}),
        m AS (
            SELECT m.*, CAST(substr(m.date, 1, 4) AS INTEGER) AS year, p.venue_par
            FROM matches m LEFT JOIN par p USING (match_id)
            WHERE m.match_type IN ({_types(group)}) AND COALESCE(m.balls_per_over, 6) = 6 AND ({where})
        ),
        b AS (
            SELECT d.match_id, d.innings_num AS innings, d.batting_team, d.over_num, d.ball_in_over,
                   d.runs_total, d.runs_batter, d.is_wicket, d.batter, d.bowler, d.player_dismissed, d.wicket_kind,
                   ROW_NUMBER() OVER w AS seq,
                   SUM(d.runs_total) OVER w AS score,
                   SUM(CASE WHEN {e['legal']} THEN 1 ELSE 0 END) OVER w AS legal,
                   SUM(CAST(d.is_wicket AS INTEGER)) OVER w AS wickets
            FROM deliveries d JOIN m USING (match_id)
            WHERE {e['regular']} AND d.innings_num IN (1, 2)
            WINDOW w AS (PARTITION BY d.match_id, d.innings_num ORDER BY d.over_num, d.ball_in_over, d.rowid
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        ),
        first_total AS (SELECT match_id, MAX(score) AS total FROM b WHERE innings = 1 GROUP BY 1)
        SELECT b.match_id, m.date, m.year, m.gender, m.team1, m.team2, m.winner, m.method, m.result,
               b.innings, b.batting_team, b.seq, b.over_num, b.ball_in_over, b.runs_total, b.runs_batter, b.is_wicket,
               b.batter, b.bowler, b.player_dismissed, b.wicket_kind,
               b.score, LEAST(b.wickets, 10) AS wickets,
               CAST(COALESCE(CASE WHEN b.innings = 2 THEN m.target_overs END, m.overs_per_innings) * 6 AS INTEGER)
                   AS balls_total,
               b.legal,
               CASE WHEN b.innings = 2 THEN COALESCE(m.target_runs, f.total + 1) END AS target,
               m.overs_per_innings AS overs, m.venue_par,
               CASE WHEN m.winner IS NULL THEN NULL WHEN m.winner = b.batting_team THEN 1 ELSE 0 END AS won
        FROM b JOIN m USING (match_id) LEFT JOIN first_total f USING (match_id)
        ORDER BY b.match_id, b.innings, b.seq"""


@lru_cache(maxsize=8)
def _elo(group: str, _db: str) -> dict[str, dict[str, float]]:
    """Every team's Elo rating going into each match of the group (format group x gender pools),
    from the results of earlier matches only. match_id -> {team: rating}."""
    rows = db.query(f"""
        SELECT match_id, gender, team1, team2, winner, result FROM matches
        WHERE match_type IN ({_types(group)}) ORDER BY date, match_id""")
    rating: dict[tuple, float] = {}
    before: dict[str, dict[str, float]] = {}
    for r in rows:
        a, b = (r["gender"], r["team1"]), (r["gender"], r["team2"])
        ra, rb = rating.get(a, ELO_START), rating.get(b, ELO_START)
        before[r["match_id"]] = {r["team1"]: ra, r["team2"]: rb}
        if r["result"] == "no result" or (r["winner"] is None and r["result"] != "tie"):
            continue
        score = 0.5 if r["winner"] is None else float(r["winner"] == r["team1"])
        expected = 1 / (1 + 10 ** ((rb - ra) / 400))
        rating[a], rating[b] = ra + ELO_K * (score - expected), rb - ELO_K * (score - expected)
    return before


def elo_diff(group: str, match_ids, batting_teams, team1s, team2s) -> np.ndarray:
    """Batting side's pre-match Elo minus the bowling side's, per row."""
    ratings = _elo(group, str(db.DB_PATH))
    out = np.empty(len(match_ids))
    for i, (mid, bat, t1, t2) in enumerate(zip(match_ids, batting_teams, team1s, team2s)):
        r = ratings.get(mid, {})
        other = t2 if bat == t1 else t1
        out[i] = r.get(bat, ELO_START) - r.get(other, ELO_START)
    return out


def columns(rows: list[dict]) -> dict[str, np.ndarray]:
    """Rows from db.query as columns (training reads columns straight from DuckDB instead)."""
    keys = ("innings", "balls_total", "legal", "score", "wickets", "target", "venue_par", "year", "gender", "overs",
            "match_id", "batting_team", "team1", "team2")
    text = ("gender", "match_id", "batting_team", "team1", "team2")
    return {k: np.array([r[k] for r in rows], dtype=object if k in text else np.float64) for k in keys}


def features(c: dict[str, np.ndarray], group: str) -> np.ndarray:
    """The FEATURES matrix from state columns (NaN where a feature doesn't apply)."""
    f = lambda k: np.asarray(c[k], dtype=np.float64)                     # noqa: E731 - None -> NaN
    innings, score = f("innings"), f("score")
    balls_left = np.maximum(f("balls_total") - f("legal"), 0)
    chase = (innings == 2) & ~np.isnan(f("target"))
    target = np.where(chase, f("target"), np.nan)
    need = target - score
    req = need * 6 / np.maximum(balls_left, 1)
    female = (np.asarray(c["gender"]) == "female").astype(np.float64)
    elo = elo_diff(group, c["match_id"], c["batting_team"], c["team1"], c["team2"])
    return np.column_stack([innings, balls_left, 10 - f("wickets"), score, target, need, req,
                            f("venue_par"), female, f("overs"), elo])


def select(X: np.ndarray, names: list[str]) -> np.ndarray:
    return X[:, [FEATURES.index(n) for n in names]]


# ---- the saved model: LightGBM trees evaluated with NumPy -------------------------

class TreeModel:
    """A LightGBM binary model from its dump_model() JSON, plus Platt calibration.
    Each tree is flattened into arrays and all rows walk it together, level by level."""

    def __init__(self, spec: dict):
        self.spec = spec
        self.features = spec["features"]
        self.trees = [self._flatten(t["tree_structure"]) for t in spec["model"]["tree_info"]]
        self.platt = (spec.get("calibration") or {}).get("platt")        # [a, b]: p = sigmoid(a * logit + b)

    @staticmethod
    def _flatten(root: dict) -> dict:
        feat, thr, left, right, value, default_left, nan_as_zero = [], [], [], [], [], [], []

        def add(node: dict) -> int:
            i = len(feat)
            for arr, v in ((feat, -1), (thr, 0.0), (left, i), (right, i), (value, 0.0), (default_left, True),
                           (nan_as_zero, False)):
                arr.append(v)
            if "leaf_value" in node:
                value[i] = node["leaf_value"]
                return i
            feat[i], thr[i] = node["split_feature"], node["threshold"]
            default_left[i] = node.get("default_left", True)
            nan_as_zero[i] = node.get("missing_type") == "None"      # LightGBM treats NaN as 0 here
            left[i] = add(node["left_child"])
            right[i] = add(node["right_child"])
            return i

        add(root)
        return {k: np.asarray(v) for k, v in (("feat", feat), ("thr", thr), ("left", left), ("right", right),
                                               ("value", value), ("default_left", default_left),
                                               ("nan_as_zero", nan_as_zero))}

    @staticmethod
    def _eval(t: dict, X: np.ndarray) -> np.ndarray:
        node = np.zeros(len(X), dtype=np.int64)
        rows = np.arange(len(X))
        while True:
            f = t["feat"][node]
            inner = f >= 0
            if not inner.any():
                return t["value"][node]
            r, n = rows[inner], node[inner]
            v = X[r, f[inner]]
            nan = np.isnan(v)
            v = np.where(nan & t["nan_as_zero"][n], 0.0, v)
            go_left = np.where(np.isnan(v), t["default_left"][n], v <= t["thr"][n])
            node[inner] = np.where(go_left, t["left"][n], t["right"][n])

    def raw(self, X: np.ndarray) -> np.ndarray:
        return sum(self._eval(t, X) for t in self.trees)

    def predict(self, X: np.ndarray, calibrated: bool = True) -> np.ndarray:
        z = self.raw(X)
        if calibrated and self.platt:
            z = self.platt[0] * z + self.platt[1]
        return np.clip(1.0 / (1.0 + np.exp(-z)), 0.001, 0.999)


class LinearModel:
    """A standardised logistic regression: log-odds = coef . (x - mean) / scale + intercept. A missing value
    counts as the training average (so it adds nothing either way)."""

    def __init__(self, spec: dict):
        self.mean, self.scale = np.asarray(spec["mean"]), np.asarray(spec["scale"])
        self.coef, self.intercept = np.asarray(spec["coef"]), float(spec["intercept"])

    def logit(self, X: np.ndarray) -> np.ndarray:
        return ((np.where(np.isnan(X), self.mean, X) - self.mean) / self.scale) @ self.coef + self.intercept


class WinProbModel:
    """A format group's models, one blend per innings, used together on a match's rows."""

    def __init__(self, spec: dict):
        self.spec = spec
        self.innings = {int(k): (v["features"], TreeModel({"features": v["features"], "model": v["trees"]}),
                                 LinearModel(v["linear"])) for k, v in spec["innings"].items()}

    def predict_innings(self, inn: int, Xs: np.ndarray) -> np.ndarray:
        _, trees, linear = self.innings[inn]
        z = (trees.raw(Xs) + linear.logit(Xs)) / 2
        return np.clip(1.0 / (1.0 + np.exp(-z)), 0.001, 0.999)

    def predict(self, X: np.ndarray) -> np.ndarray:
        p = np.full(len(X), np.nan)
        for inn, (names, _, _) in self.innings.items():
            rows = X[:, 0] == inn
            if rows.any():
                p[rows] = self.predict_innings(inn, select(X[rows], names))
        return p


@lru_cache(maxsize=4)
def load(group: str) -> WinProbModel | None:
    path = MODELS / f"winprob-{group.lower()}.json"
    return WinProbModel(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None


def predict_match(match_id: str) -> dict | None:
    """Win probability for team1 after every ball of a match, or None if it isn't a limited-overs
    match with 6-ball overs or no model is installed."""
    meta = db.query("SELECT match_type, team1, team2 FROM matches WHERE match_id = ?", [match_id])
    if not meta or not (group := group_of(meta[0]["match_type"])) or not (model := load(group)):
        return None
    rows = db.query(states_sql(group, f"m.match_id = '{match_id.replace(chr(39), chr(39) * 2)}'"))
    if not rows:
        return None
    p_bat = model.predict(features(columns(rows), group))
    team1 = meta[0]["team1"]
    for r, p in zip(rows, p_bat):
        r["wp_team1"] = float(p if r["batting_team"] == team1 else 1 - p)
    return {"group": group, "team1": team1, "team2": meta[0]["team2"], "rows": rows,
            "model": {k: model.spec.get(k) for k in ("trained_on", "metrics", "version")}}
