"""
The result envelope every analytics function returns, plus the small
formatting helpers around it:

    {title, columns, rows, filters?, notes?, highlights?, empty?, ...extra}

`columns`/`rows` render directly as a table in the UI; the agent loop sends
them to the model as records. `highlights` are best/worst values computed
here so a small model quotes them instead of scanning wide tables.
"""

from __future__ import annotations

import math

from . import catalog
from .scope import Scope, lit, lit_list


def num(v, digits: int = 2):
    """Round floats for display; NaN/inf become None."""
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, digits)
    if hasattr(v, "is_integer") and not isinstance(v, (int, bool)):  # Decimal/HUGEINT
        f = float(v)
        return int(f) if f.is_integer() else round(f, digits)
    return v


def overs(balls) -> float:
    """Cricket notation: 57 balls -> 9.3 overs."""
    balls = int(balls or 0)
    return float(f"{balls // 6}.{balls % 6}")


def result(title: str, columns: list[str], rows: list[list], scope: Scope | None = None,
           notes: list[str] | None = None, **extra) -> dict:
    out = {"title": title, "columns": columns, "rows": [[num(v) for v in r] for r in rows]}
    if scope is not None and scope.applied:
        out["filters"] = scope.describe()
    all_notes = (scope.notes if scope is not None else []) + (notes or [])
    if all_notes:
        out["notes"] = all_notes
    if not rows:
        out["empty"] = True
    out.update({k: v for k, v in extra.items() if v not in (None, {}, [])})
    return out


def season_labels(rows: list[dict], key: str = "k") -> list[str]:
    """IPL 2008 is stored as season '2007/08'; show the calendar year when
    every match in a season fell in one year -- unless two seasons would then
    share a label (e.g. a '2023/24' and a '2024/25' both played in 2024).
    Rows need first_date/last_date."""
    labels = []
    for r in rows:
        first, last = (r.get("first_date") or "")[:4], (r.get("last_date") or "")[:4]
        labels.append(first if first and first == last else r[key])
    if len(set(labels)) < len(labels):
        return [r[key] for r in rows]
    return labels


def franchise_case(expr: str) -> str:
    """Merge renamed franchises into one label when grouping by team."""
    parts = [f"WHEN {expr} IN {lit_list(group)} THEN {lit(' / '.join(group))}" for group in catalog.FRANCHISE_GROUPS]
    return f"(CASE {' '.join(parts)} ELSE {expr} END)"


def highlights(columns: list[str], rows: list[list], label_col: str, specs: list[tuple],
               rate_metrics: set[str] = frozenset(), sample_col: str | None = None,
               totals: tuple[str, ...] = ()) -> dict:
    """Best/worst row per metric. `specs` is [(metric, "max"|"min")]; the
    key is best_<metric> for the good direction. Rate metrics skip rows whose
    sample (sample_col) is under a quarter of the median, so a 3-ball cameo
    can't "lead" a split. A spec may carry a third element to name the key
    (e.g. ("win_pct", "min", "worst_win_pct"))."""
    if len(rows) < 2:
        return {}
    idx = {c: i for i, c in enumerate(columns)}
    sample_col = sample_col or ("balls" if "balls" in idx else "matches" if "matches" in idx else None)
    out = {}
    for spec in specs:
        metric, direction = spec[0], spec[1]
        key = spec[2] if len(spec) > 2 else f"best_{metric}"
        if metric not in idx:
            continue
        cand = []
        for r in rows:
            v = r[idx[metric]]
            if isinstance(v, str) and v.rstrip("*").isdigit():  # "124*"
                v = int(v.rstrip("*"))
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            if metric in rate_metrics and sample_col in idx:
                sizes = sorted(x[idx[sample_col]] or 0 for x in rows)
                if (r[idx[sample_col]] or 0) < 0.25 * sizes[len(sizes) // 2]:
                    continue
            cand.append((v, r))
        if not cand:
            continue
        v, r = (max if direction == "max" else min)(cand, key=lambda t: t[0])
        out[key] = f"{num(r[idx[metric]])} ({r[idx[label_col]]})"
    present = [c for c in totals if c in idx]
    if present:
        out["totals"] = {c: num(sum((r[idx[c]] or 0) for r in rows)) for c in present}
    return out


def is_table(output) -> bool:
    return isinstance(output, dict) and isinstance(output.get("columns"), list) and isinstance(output.get("rows"), list)


def to_records(output, table_id: str | None = None, max_rows: int = 25, tail: bool = False):
    """Compact, model-friendly view of a result: rows as {column: value}
    records (far less error-prone for a small model than parallel arrays),
    capped at max_rows -- the first rows, or the last with tail=True (for
    chronological series, where the latest matters most)."""
    if not is_table(output):
        return output
    cols, rows = output["columns"], output["rows"]
    view = {"table_id": table_id} if table_id else {}
    for k in ("title", "filters", "highlights", "notes", "error"):
        if output.get(k):
            view[k] = output[k]
    shown = rows[-max_rows:] if tail else rows[:max_rows]
    view["rows"] = [dict(zip(cols, r)) for r in shown]
    if len(rows) > max_rows:
        view["rows_not_shown"] = f"{len(rows) - max_rows} {'earlier' if tail else 'more'} rows not shown"
    if not rows:
        view["empty"] = True
    for k in ("recent_results", "player", "medians", "axes"):
        if output.get(k):
            view[k] = output[k]
    return view
