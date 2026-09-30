"""
Doosra win probability: the batting side's chance of winning a T20 or ODI from the match state.
Standalone (NumPy only). The models are the JSON files next to this one.

    from predict import WinProbability
    wp = WinProbability("winprob-t20.json")
    wp.predict(innings=2, score=151, wickets=5, balls_left=24, target=177)        # -> 0.92...

Features are built exactly as in Doosra (backend/analytics/winprob.py); a test in the Doosra repo
checks that this file and the app agree.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FEATURES = ["innings", "balls_left", "wickets_in_hand", "score", "target", "runs_needed", "required_rate",
            "venue_par", "female", "overs", "elo_diff"]


class TreeModel:
    """A LightGBM model from its dump_model() JSON, each tree flattened into arrays and evaluated with NumPy."""

    def __init__(self, dump: dict):
        self.trees = [self._flatten(t["tree_structure"]) for t in dump["tree_info"]]

    @staticmethod
    def _flatten(root: dict) -> dict:
        cols = {k: [] for k in ("feat", "thr", "left", "right", "value", "default_left", "nan_as_zero")}

        def add(node: dict) -> int:
            i = len(cols["feat"])
            for k, v in (("feat", -1), ("thr", 0.0), ("left", i), ("right", i), ("value", 0.0), ("default_left", True),
                         ("nan_as_zero", False)):
                cols[k].append(v)
            if "leaf_value" in node:
                cols["value"][i] = node["leaf_value"]
                return i
            cols["feat"][i], cols["thr"][i] = node["split_feature"], node["threshold"]
            cols["default_left"][i] = node.get("default_left", True)
            cols["nan_as_zero"][i] = node.get("missing_type") == "None"
            cols["left"][i] = add(node["left_child"])
            cols["right"][i] = add(node["right_child"])
            return i

        add(root)
        return {k: np.asarray(v) for k, v in cols.items()}

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
            v = np.where(np.isnan(v) & t["nan_as_zero"][n], 0.0, v)
            go_left = np.where(np.isnan(v), t["default_left"][n], v <= t["thr"][n])
            node[inner] = np.where(go_left, t["left"][n], t["right"][n])

    def raw(self, X: np.ndarray) -> np.ndarray:
        return sum(self._eval(t, X) for t in self.trees)


class WinProbability:
    def __init__(self, path: str | Path):
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
        self.spec = spec
        self.innings = {}
        for k, v in spec["innings"].items():
            lin = v["linear"]
            self.innings[int(k)] = (v["features"], TreeModel(v["trees"]),
                                    (np.asarray(lin["mean"]), np.asarray(lin["scale"]), np.asarray(lin["coef"]),
                                     float(lin["intercept"])))

    def predict_rows(self, X: np.ndarray) -> np.ndarray:
        """X: rows of FEATURES (NaN where one doesn't apply). Returns the batting side's chance of winning."""
        p = np.full(len(X), np.nan)
        for inn, (names, trees, (mean, scale, coef, b)) in self.innings.items():
            rows = X[:, 0] == inn
            if rows.any():
                Xs = X[rows][:, [FEATURES.index(n) for n in names]]
                filled = np.where(np.isnan(Xs), mean, Xs)            # a missing value counts as the training average
                z = (trees.raw(Xs) + ((filled - mean) / scale) @ coef + b) / 2
                p[rows] = np.clip(1 / (1 + np.exp(-z)), 0.001, 0.999)
        return p

    def predict(self, innings: int, score: int, wickets: int, balls_left: int, target: int | None = None,
                venue_par: float | None = None, female: bool = False, elo_diff: float = 0.0,
                overs: int | None = None) -> float:
        """One match state. `wickets` = wickets fallen; `venue_par` = the ground's typical first-innings total
        (None if unknown); `elo_diff` = batting side's Elo minus the bowling side's (0 = evenly matched)."""
        overs = overs or (50 if self.spec["group"] == "ODI" else 20)
        chase = innings == 2 and target is not None
        need = target - score if chase else np.nan
        req = need * 6 / max(balls_left, 1) if chase else np.nan
        row = [innings, balls_left, 10 - wickets, score, target if chase else np.nan, need, req,
               np.nan if venue_par is None else venue_par, 1.0 if female else 0.0, overs, elo_diff]
        return float(self.predict_rows(np.asarray([row], dtype=np.float64))[0])
