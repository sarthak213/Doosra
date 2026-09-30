"""
Doosra ball-outcome model: the chance of each result of the next ball faced (dot, 1, 2, 3, 4, 6, out),
from the match situation and both players' career form.

    pip install lightgbm numpy duckdb

    from predict_ball import BallOutcome
    bo = BallOutcome("T20")                                   # files for T20 in this folder
    bo.predict(batter="V Kohli", bowler="JJ Bumrah", innings=2, over=18, ball=1, wickets=4,
               score=150, balls_left=12, target=177, venue_par=165)
    # {'0': 0.36, '1': 0.33, '2': 0.07, '3': 0.00, '4': 0.10, '6': 0.06, 'out': 0.08, 'expected_runs': 1.3}

Players are looked up by their Cricsheet name ("V Kohli", "JJ Bumrah") in players-<group>.parquet, which
holds each player's career totals up to the dataset's build date; an unknown name counts as an average
player. `over` is 0-based (the 19th over is 18), as in Cricsheet.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import lightgbm as lgb
import numpy as np

HERE = Path(__file__).resolve().parent


class BallOutcome:
    def __init__(self, group: str = "T20", folder: str | Path = HERE):
        folder = Path(folder)
        g = group.lower()
        self.spec = json.loads((folder / f"spec-{g}.json").read_text(encoding="utf-8"))
        self.model = lgb.Booster(model_file=str(folder / f"lightgbm-{g}.txt"))
        self.players = folder / f"players-{g}.parquet"

    def form(self, name: str | None, gender: str, prefix: str) -> dict:
        cols = ["balls", "runs", "outs" if prefix == "bat" else "wkts", "dots", "fours", "sixes"]
        row = None
        if name:
            row = duckdb.execute(
                f"SELECT {', '.join(f'{prefix}_{c}' for c in cols)} FROM '{self.players.as_posix()}' "
                "WHERE player = ? AND gender = ?", [name, gender]).fetchone()
        return dict(zip((f"{prefix}_{c}" for c in cols), row or [0] * len(cols)))

    def predict(self, batter: str | None, bowler: str | None, innings: int, over: int, ball: int, wickets: int,
                score: int, balls_left: int, target: int | None = None, venue_par: float | None = None,
                female: bool = False, bat_inn_balls: int = 0, bat_inn_runs: int = 0) -> dict:
        gender = "female" if female else "male"
        f = {**self.form(batter, gender, "bat"), **self.form(bowler, gender, "bowl")}
        k, means = self.spec["shrink_balls"], self.spec["form_means"]
        for prefix, names in (("bat", ("runs", "outs", "dots", "fours", "sixes")),
                              ("bowl", ("runs", "wkts", "dots", "fours", "sixes"))):
            for n in names:
                key = f"{prefix}_{n}"
                f[f"{key}_rate"] = (f[key] + k * means[key]) / (f[f"{prefix}_balls"] + k)
        chase = innings == 2 and target is not None
        need = target - score if chase else np.nan
        values = {"innings": innings, "over": over, "ball": ball, "wickets": wickets, "score": score,
                  "balls_left": balls_left, "runs_needed": need,
                  "required_rate": need * 6 / max(balls_left, 1) if chase else np.nan,
                  "venue_par": np.nan if venue_par is None else venue_par, "female": float(female),
                  "bat_inn_balls": bat_inn_balls, "bat_inn_runs": bat_inn_runs, **f}
        x = np.array([[values[n] for n in self.spec["features"]]], dtype=float)
        p = self.model.predict(x)[0]
        out = {c: round(float(v), 4) for c, v in zip(self.spec["classes"], p)}
        out["expected_runs"] = round(float(p @ np.array(self.spec["runs"])), 3)
        return out
