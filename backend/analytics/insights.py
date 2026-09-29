"""
Facts about a chart's data, computed deterministically, so the model explains
and interprets numbers it was handed instead of doing arithmetic or guessing.

`digest(card, result)` covers one board card (a result table plus how it is
drawn): leaders and laggards, spread, outliers, trend, correlation, thin
samples, and, for raw rate metrics, how far each player's figure moves once
it is regressed toward the league (Doosra's skill-versus-luck lens).
`board_digest` adds links across cards. Everything is JSON-safe and sized to
fit a prompt: `detail` 2 (full), 1 (medium) or 0 (headline only).
"""

from __future__ import annotations

import json
import math
import statistics
from typing import Any

from . import registry

TIME_SPLITS = {"season", "year"}
# A row with fewer than this many of the size column is a thin sample.
THIN = {"balls": 120, "overs": 20, "innings": 8, "matches": 5}
SKIP = {"rank"}
# raw rate metric -> its regressed counterpart (per role), where the registry has one
REGRESSED = {
    ("batting", "strike_rate"): "regressed_sr", ("batting", "average"): "regressed_average",
    ("batting", "dot_pct"): "regressed_dot_pct", ("batting", "boundary_pct"): "regressed_boundary_pct",
    ("bowling", "economy"): "regressed_economy", ("bowling", "strike_rate"): "regressed_strike_rate",
    ("bowling", "average"): "regressed_average", ("bowling", "dot_pct"): "regressed_dot_pct",
    ("bowling", "boundary_pct"): "regressed_boundary_pct",
}
REGRESSED_GAP = 0.05         # flag a player whose regressed figure differs from the raw one by more than 5%
_N = {2: 5, 1: 3, 0: 1}      # how many names to list at each detail level


def _r(v: Any, nd: int = 2) -> Any:
    return round(v, nd) if isinstance(v, float) and math.isfinite(v) else v


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _records(result: dict) -> list[dict]:
    cols = result.get("columns") or []
    return [dict(zip(cols, row)) for row in result.get("rows") or []]


def _kinds(result: dict) -> tuple[list[str], list[str]]:
    """(numeric columns, text columns), in table order."""
    rows, numeric, text = result.get("rows") or [], [], []
    for i, c in enumerate(result.get("columns") or []):
        if c in SKIP:
            continue
        vals = [r[i] for r in rows if r[i] is not None]
        (numeric if vals and all(_is_num(v) for v in vals) else text).append(c)
    return numeric, text


def _higher_is_better(role: str | None, metric: str) -> bool:
    try:
        return registry.get(role or "batting", metric).higher_is_better
    except (ValueError, KeyError):
        return True


def _is_count(role: str | None, metric: str) -> bool:
    try:
        return registry.get(role or "batting", metric).kind == "int"
    except (ValueError, KeyError):
        return False


def column_facts(labels: list[str], values: list[Any], higher_better: bool = True, count: bool = False,
                 detail: int = 2) -> dict | None:
    """Who leads and trails, how spread out the rows are, and any outliers."""
    pairs = [(str(l), float(v)) for l, v in zip(labels, values) if _is_num(v)]
    if not pairs:
        return None
    order = sorted(pairs, key=lambda p: p[1], reverse=higher_better)
    vs = [v for _, v in pairs]
    out: dict = {"n": len(pairs), "best": {"label": order[0][0], "value": _r(order[0][1])},
                 "worst": {"label": order[-1][0], "value": _r(order[-1][1])},
                 "median": _r(statistics.median(vs)), "mean": _r(statistics.fmean(vs)),
                 "spread": _r(max(vs) - min(vs))}
    if len(pairs) > 2:
        out["ranked"] = [{"label": l, "value": _r(v)} for l, v in order[: _N[detail]]]
    if count and sum(vs) > 0:
        out["leader_share_of_listed_total_pct"] = _r(100 * order[0][1] / sum(vs), 1) if higher_better else None
    if len(pairs) >= 6 and detail >= 1:
        sd, mu = statistics.pstdev(vs), statistics.fmean(vs)
        if sd > 0:
            odd = sorted(((l, v, (v - mu) / sd) for l, v in pairs), key=lambda t: -abs(t[2]))
            out["outliers"] = [{"label": l, "value": _r(v), "z": _r(z, 1)} for l, v, z in odd[:3] if abs(z) >= 2]
    if out.get("leader_share_of_listed_total_pct") is None:
        out.pop("leader_share_of_listed_total_pct", None)
    return {k: v for k, v in out.items() if v not in (None, [])}


def series_facts(labels: list[str], values: list[Any]) -> dict | None:
    """Direction, peak and trough, and where the latest point sits, for a time series."""
    pts = [(str(l), float(v)) for l, v in zip(labels, values) if _is_num(v)]
    if len(pts) < 2:
        return None
    n = len(pts)
    xs, ys = list(range(n)), [v for _, v in pts]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    change = slope * (n - 1)
    flat = abs(change) < 0.05 * abs(my) if my else abs(change) < 1e-9
    peak, trough = max(pts, key=lambda p: p[1]), min(pts, key=lambda p: p[1])
    return {"points": n, "first": {"label": pts[0][0], "value": _r(pts[0][1])}, "last": {"label": pts[-1][0], "value": _r(pts[-1][1])},
            "trend": "flat" if flat else "rising" if slope > 0 else "falling", "change_over_period": _r(change),
            "peak": {"label": peak[0], "value": _r(peak[1])}, "trough": {"label": trough[0], "value": _r(trough[1])},
            "latest_vs_mean_pct": _r(100 * (ys[-1] - my) / my, 1) if my else None}


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def scatter_facts(recs: list[dict], xk: str, yk: str, role: str | None, medians: dict | None, detail: int = 2) -> dict | None:
    pts = [r for r in recs if _is_num(r.get(xk)) and _is_num(r.get(yk))]
    if len(pts) < 3:
        return None
    r = pearson([p[xk] for p in pts], [p[yk] for p in pts])
    xhb, yhb = _higher_is_better(role, xk), _higher_is_better(role, yk)
    mdx = (medians or {}).get("x", statistics.median(p[xk] for p in pts))
    mdy = (medians or {}).get("y", statistics.median(p[yk] for p in pts))
    good_x = lambda p: (p[xk] >= mdx) == xhb          # noqa: E731
    good_y = lambda p: (p[yk] >= mdy) == yhb          # noqa: E731
    both = [p for p in pts if good_x(p) and good_y(p)]
    label = "player" if "player" in pts[0] else next(iter(pts[0]))
    both.sort(key=lambda p: (-(p[xk] if xhb else -p[xk]) / (abs(mdx) or 1)) - (p[yk] if yhb else -p[yk]) / (abs(mdy) or 1))
    out = {"points": len(pts), "correlation": _r(r), "x_better": "higher" if xhb else "lower", "y_better": "higher" if yhb else "lower",
           "medians": {"x": _r(mdx), "y": _r(mdy)}, "better_than_median_on_both": len(both),
           "best_on_both": [str(p[label]) for p in both[: _N[detail]]]}
    if r is not None:
        out["strength"] = "strong" if abs(r) >= 0.7 else "moderate" if abs(r) >= 0.4 else "weak"
    return out


def thin_samples(result: dict, labels: list[str]) -> dict | None:
    """Rows built on few balls, innings or matches, whose rates are mostly noise."""
    cols = result.get("columns") or []
    for c in ("balls", "overs", "innings", "matches"):
        if c in cols:
            i = cols.index(c)
            small = [labels[j] for j, row in enumerate(result.get("rows") or []) if _is_num(row[i]) and row[i] < THIN[c]]
            return {"measure": c, "under": THIN[c], "count": len(small), "examples": small[:4]} if small else None
    return None


def chart_spec(card: dict, result: dict) -> dict:
    """Which columns and chart a card draws (the same defaults as the UI)."""
    numeric, text = _kinds(result)
    cfg, src = card.get("chart") or {}, card.get("source") or {}
    state, kind = src.get("state") or {}, src.get("kind")
    ctype = cfg.get("type") or ("scatter" if kind == "matrix" else "line" if state.get("split_by") in TIME_SPLITS else "bar")
    x = cfg.get("x") if cfg.get("x") in text + numeric else (
        state.get("split_by") if state.get("split_by") in text else "player" if "player" in text else (text or numeric or [None])[0])
    ys = [c for c in (cfg.get("y") if isinstance(cfg.get("y"), list) else [cfg.get("y")] if cfg.get("y") else []) if c in numeric]
    if not ys:
        pref = [state.get("sort_by"), *(state.get("metrics") or [])] if kind == "query" else []
        first = next((m for m in pref if m in numeric), None) or next((c for c in numeric if c != x), None)
        ys = [first] if first else []
    return {"type": ctype, "x": x, "ys": ys[:3], "role": state.get("role")}


def regressed_check(card: dict, result: dict, detail: int = 2) -> list[dict] | None:
    """For a raw rate metric over players, the same players' regressed figure:
    where the gap is large, the raw number is mostly a small sample."""
    src = card.get("source") or {}
    state = src.get("state") or {}
    if src.get("kind") != "query" or state.get("split_by") or "player" not in (result.get("columns") or []):
        return None
    role = state.get("role") or "batting"
    raw = [m for m in (state.get("metrics") or []) if (role, m) in REGRESSED and m in (result.get("columns") or [])]
    if not raw or detail < 1:
        return None
    from . import engine                                    # late: the engine imports the registry, not this module
    from .scope import normalize_filters
    names = [r["player"] for r in _records(result)][:12]
    found = []
    for m in raw[:2]:
        try:
            reg = engine.query_stats(role=role, metrics=[REGRESSED[(role, m)]], players=names, limit=len(names),
                                     **normalize_filters(state.get("filters")))
        except Exception:                                   # noqa: BLE001 - insight is best-effort, never fatal
            continue
        if not isinstance(reg, dict) or reg.get("error") or REGRESSED[(role, m)] not in (reg.get("columns") or []):
            continue
        by_name = {r["player"]: r[REGRESSED[(role, m)]] for r in _records(reg)}
        for rec in _records(result):
            a, b = rec.get(m), by_name.get(rec["player"])
            if _is_num(a) and _is_num(b) and b and abs(a - b) / abs(b) > REGRESSED_GAP:
                found.append({"player": rec["player"], "metric": m, "raw": _r(a), "regressed": _r(b),
                              "gap_pct": _r(100 * (a - b) / abs(b), 1)})
    found.sort(key=lambda f: -abs(f["gap_pct"]))
    return found[: _N[detail]] or None


def digest(card: dict, result: dict, detail: int = 2) -> dict:
    """Everything worth saying about one card, from its numbers."""
    src = card.get("source") or {}
    out: dict = {"title": card.get("title") or result.get("title") or "Untitled card", "source": src.get("kind"),
                 "rows": len(result.get("rows") or [])}
    if card.get("note"):
        out["user_note"] = card["note"][:400]
    if src.get("kind") == "snapshot":
        out["static_snapshot"] = f"saved {str(src.get('saved_at') or 'earlier')[:10]}; not refreshed with new data"
    if result.get("filters"):
        out["scope"] = result["filters"]
    if not out["rows"]:
        out["empty"] = True
        return out
    spec = chart_spec(card, result)
    out["chart"] = spec["type"]
    recs = _records(result)
    label_col = spec["x"] if spec["type"] not in ("scatter",) and spec["x"] else ("player" if "player" in result["columns"] else result["columns"][0])
    labels = [str(r.get(label_col)) for r in recs]
    out["labels"] = label_col
    role = spec["role"]
    if spec["type"] == "scatter":
        state = src.get("state") or {}
        xk, yk = state.get("x") or spec["x"], state.get("y") or (spec["ys"] or [None])[0]
        s = scatter_facts(recs, xk, yk, role, result.get("medians"), detail) if xk and yk else None
        if s:
            out["scatter"] = {"x": xk, "y": yk, **s}
    elif spec["type"] == "line" and spec["ys"]:
        out["series"] = {y: f for y in spec["ys"] if (f := series_facts(labels, [r.get(y) for r in recs]))}
    else:
        cols = spec["ys"] or [c for c in _kinds(result)[0] if c != spec["x"]][:2]
        out["metrics"] = {c: f for c in cols if (f := column_facts(
            labels, [r.get(c) for r in recs], _higher_is_better(role, c), _is_count(role, c), detail))}
    if detail >= 1:
        if (thin := thin_samples(result, labels)):
            out["thin_samples"] = thin
        if (reg := regressed_check(card, result, detail)):
            out["regressed_vs_raw"] = reg
    if result.get("notes") and detail >= 1:
        out["data_notes"] = [str(n)[:160] for n in result["notes"][:2]]
    return out


def board_digest(board: dict, results: list[tuple[dict, dict | None, str | None]], budget: int = 7000) -> dict:
    """The whole board: each card's digest, the players that recur across cards,
    and a size that fits a prompt (detail is lowered until it does)."""
    for detail in (2, 1, 0):
        cards = []
        for card, result, error in results:
            if error or result is None:
                cards.append({"title": card.get("title") or "Untitled card", "error": error or "no result"})
            else:
                cards.append(digest(card, result, detail))
        recur: dict[str, int] = {}
        for card, result, _ in results:
            if result:
                names = {str(r.get("player")) for r in _records(result) if r.get("player")} if "player" in result["columns"] else set()
                for n in names:
                    recur[n] = recur.get(n, 0) + 1
        out = {"kind": "board", "name": board.get("name"), "description": (board.get("description") or "")[:600],
               "cards": cards, "players_in_several_cards": sorted([n for n, c in recur.items() if c > 1])[:8]}
        if not out["players_in_several_cards"]:
            out.pop("players_in_several_cards")
        if len(json.dumps(out, default=str)) <= budget:
            return out
    return out
