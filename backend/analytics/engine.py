"""
The analytics engine: every question about players is answered by composing
registry metrics over the derived per-innings tables (see build.py).

    query_stats      metrics x (players | everyone) x optional split, with
                     filters, qualification, sorting -- the Query Builder,
                     leaderboards, comparisons and splits are all this.
    player_profile   headline batting/bowling figures + context metrics
    player_form      innings-by-innings series with rolling windows
    career_arc       several players' careers aligned by innings number
    percentiles      where players rank among qualified peers, per metric
    scatter          two metrics for every qualified player (Player Matrix)
    entry_heatmap    a batter's record by when they came in
    similar_players  nearest players on standardised rate metrics

Inputs are plain names (resolved through catalog/scope); outputs are the
standard envelope from results.py, so the UI, the MCP tools and the agent
all consume the same thing. Errors come back as {"error": ...}.
"""

from __future__ import annotations

import functools
import math

from . import catalog, db, registry
from .catalog import ResolutionError
from .registry import BAT, BOWL
from .results import franchise_case, highlights, num, overs, result, season_labels
from .scope import Scope, build_scope, lit, lit_list

MAX_LIMIT = 200

_TABLES = {(BAT, False): "batting_innings", (BAT, True): "batting_phase",
           (BOWL, False): "bowling_innings", (BOWL, True): "bowling_phase"}

_ENTRY_PHASE = ("CASE WHEN m.fgroup = 'T20' THEN (CASE WHEN m.entry_over < 6 THEN 'powerplay' "
                "WHEN m.entry_over < 15 THEN 'middle' ELSE 'death' END) "
                "WHEN m.fgroup = 'ODI' THEN (CASE WHEN m.entry_over < 10 THEN 'powerplay' "
                "WHEN m.entry_over < 40 THEN 'middle' ELSE 'death' END) "
                "ELSE (CASE WHEN m.entry_over < 20 THEN 'multi-day: overs 1-20' "
                "WHEN m.entry_over < 60 THEN 'multi-day: overs 21-60' ELSE 'multi-day: overs 61+' END) END")

_DIMENSION_SQL = {
    "season": "m.season",
    "year": "CAST(m.yr AS VARCHAR)",
    "format": "m.fmt",
    "competition": "COALESCE(m.event_name, 'Bilateral / other')",
    "team": franchise_case("m.team"),
    "opposition": franchise_case("m.opposition"),
    "venue": "trim(regexp_replace(split_part(m.venue, ',', 1), '\\.\\s*', ' ', 'g'))",
    "innings": "CAST(m.innings_num AS VARCHAR)",
    "chase": ("CASE WHEN m.fgroup = 'MULTI' THEN 'innings ' || m.innings_num "
              "WHEN m.innings_num = 1 THEN 'setting' ELSE 'chasing' END"),
    "result": "m.result",
    "phase": "m.phase",
    "position": "CAST(m.position AS VARCHAR)",
    "entry_wickets": "CASE WHEN m.entry_wkts >= 5 THEN '5+' ELSE CAST(CAST(m.entry_wkts AS INTEGER) AS VARCHAR) END",
    "entry_phase": _ENTRY_PHASE,
    "dismissal": "COALESCE(m.dismissal, 'not out')",
}
_PHASE_ORDER = {"powerplay": 0, "middle": 1, "death": 2}


class EngineError(ValueError):
    pass


def _tool(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ResolutionError as e:
            return e.to_dict()
        except (EngineError, ValueError) as e:
            return {"error": str(e)}
    return wrapper


def _role(role: str | None) -> str:
    role = (role or BAT).lower()
    if role not in (BAT, BOWL):
        raise EngineError("role must be 'batting' or 'bowling'.")
    return role


def _check_built():
    if not catalog.get_catalog().has_derived_tables:
        raise EngineError("The analytics tables haven't been built yet. Run: python -m analytics.build")


def _where(scope: Scope, players: list[str] | None = None, extra: list[str] | None = None) -> str:
    c = scope.match_clauses()
    if scope.team:
        c.append(f"m.team IN {lit_list(scope.team)}")
    if scope.opposition:
        c.append(f"m.opposition IN {lit_list(scope.opposition)}")
    if scope.innings:
        c.append(f"m.innings_num = {int(scope.innings)}")
    if scope.phase:
        c.append(f"m.phase = {lit(scope.phase)}")
    if players:
        c.append(f"m.player IN {lit_list(players)}")
    c += extra or []
    return ("WHERE " + " AND ".join(c)) if c else ""


def _resolve_players(names, gender=None) -> tuple[list[str], list[str]]:
    if isinstance(names, str):
        names = [names]
    out, notes = [], []
    for n in names or []:
        p = catalog.resolve_player(n, gender=gender)
        out.append(p.name)
        if p.note:
            notes.append(p.note)
    return out, notes


def _metric_list(role: str, metrics, phase_mode: bool) -> list[registry.Metric]:
    if isinstance(metrics, str):
        metrics = [m.strip() for m in metrics.split(",") if m.strip()]
    ms = [registry.get(role, m) for m in (metrics or [])]
    if phase_mode:
        bad = [m.id for m in ms if not m.phase_ok]
        if bad:
            raise EngineError(f"{', '.join(bad)} can't be computed within a single phase; drop the phase "
                              "filter/split or pick other metrics.")
    return ms


def _order_keys(rows: list[dict], split_by: str) -> list[dict]:
    if split_by in ("season", "year"):
        return sorted(rows, key=lambda r: r.get("first_date") or "")
    if split_by == "phase":
        return sorted(rows, key=lambda r: _PHASE_ORDER.get(r["k"], 9))
    if split_by in ("position", "innings"):
        return sorted(rows, key=lambda r: int(r["k"]) if str(r["k"]).isdigit() else 99)
    if split_by == "entry_wickets":
        return sorted(rows, key=lambda r: 99 if r["k"] == "5+" else int(r["k"]))
    return sorted(rows, key=lambda r: -(r.get("_innings") or 0))


def _title(prefix: str, scope: Scope) -> str:
    rest = ", ".join(str(v) for k, v in scope.applied.items())
    return f"{prefix} — {rest}" if rest else prefix


def _values(r: dict, ms: list[registry.Metric]) -> list:
    return [r.get(m.id) for m in ms]


# ---------------------------------------------------------------------------
# query_stats: the general-purpose query
# ---------------------------------------------------------------------------

DEFAULT_METRICS = {
    BAT: ["matches", "innings", "runs", "average", "strike_rate", "true_sr", "hundreds", "fifties"],
    BOWL: ["matches", "wickets", "average", "economy", "strike_rate", "true_economy", "best"],
}


@_tool
def query_stats(role: str = BAT, metrics=None, players=None, split_by: str | None = None,
                sort_by: str | None = None, ascending: bool | None = None, min_balls: int | None = None,
                limit: int = 25, **filters) -> dict:
    """Metrics for players (all qualifying players, or the named ones),
    optionally split by a dimension. With several players and a split,
    rows are player x split value."""
    _check_built()
    role = _role(role)
    split_by = (split_by or "").strip().lower() or None
    if split_by and split_by not in registry.DIMENSIONS:
        raise EngineError(f"split_by must be one of: {', '.join(registry.DIMENSIONS)}")
    if split_by and role not in registry.DIMENSIONS[split_by]["roles"]:
        raise EngineError(f"split_by '{split_by}' is only available for batting.")

    scope_gender = catalog.resolve_gender(filters.get("gender")) if filters.get("gender") else None
    names, name_notes = _resolve_players(players, scope_gender)
    scope = build_scope(default_gender=None if names else "male", **filters)
    scope.notes[:0] = name_notes

    phase_mode = bool(scope.phase) or split_by == "phase"
    if phase_mode and split_by in ("entry_wickets", "entry_phase", "dismissal"):
        raise EngineError(f"split_by '{split_by}' can't be combined with a phase filter.")
    ms = _metric_list(role, metrics or DEFAULT_METRICS[role], phase_mode)
    sort = registry.get(role, sort_by) if sort_by else (ms[0] if not names else None)
    if sort and phase_mode and not sort.phase_ok:
        raise EngineError(f"Can't sort by {sort.id} within a phase.")
    limit = max(1, min(int(limit or 25), MAX_LIMIT))

    key = _DIMENSION_SQL[split_by] if split_by else "'all'"
    select_ms = ms + ([sort] if sort and sort not in ms else [])
    metric_sql = ",\n       ".join(f"{m.sql} AS {m.id}" for m in select_ms)
    group_player = not (names and len(names) == 1 and split_by)
    sql = f"""
SELECT COALESCE(CAST(({key}) AS VARCHAR), 'n/a') AS k, {'m.player,' if group_player else ''}
       {metric_sql},
       SUM(m.balls) AS _balls, COUNT(*) AS _innings, mode(m.team) AS _team,
       MIN(m.date) AS first_date, MAX(m.date) AS last_date
FROM {_TABLES[(role, phase_mode)]} m
{_where(scope, names)}
GROUP BY k{', m.player' if group_player else ''}
"""
    notes = []
    qual = ""
    if min_balls is not None:
        qual = f"WHERE _balls >= {int(min_balls)}"
        notes.append(f"Qualification: at least {int(min_balls)} balls {'faced' if role == BAT else 'bowled'}.")
    elif not names and sort and sort.rate:
        qual = "WHERE _balls >= GREATEST(30, 0.1 * _max_balls)"
    direction = "ASC" if (ascending if ascending is not None else (sort and not sort.higher_is_better)) else "DESC"
    order = f"ORDER BY {sort.id} {direction} NULLS LAST, _balls DESC" if sort else "ORDER BY _innings DESC"
    full = f"""
SELECT * FROM (SELECT *, MAX(_balls) OVER () AS _max_balls FROM ({sql}) q) x
{qual} {order} {'' if names else f'LIMIT {limit}'}
"""
    rows = db.query(full)
    if rows and qual and min_balls is None:
        threshold = max(30, math.ceil(0.1 * rows[0]["_max_balls"]))
        notes.append(f"Qualification: at least {threshold} balls {'faced' if role == BAT else 'bowled'} "
                     "(10% of the busiest player in this scope, min 30) -- set min_balls to change it.")

    label = [m.label for m in ms]
    if split_by and names and len(names) == 1:
        rows = _order_keys(rows, split_by)
        keys = season_labels(rows) if split_by == "season" else [r["k"] for r in rows]
        cols = [split_by] + [m.id for m in ms]
        table = [[k] + _values(r, ms) for k, r in zip(keys, rows)]
        title = _title(f"{role.capitalize()} — {names[0]} by {registry.DIMENSIONS[split_by]['label'].lower()}", scope)
        hl = highlights(cols, table, split_by, [(m.id, "max" if m.higher_is_better else "min") for m in ms
                                                  if m.kind != "text" and m.id not in ("matches", "innings", "balls")],
                        rate_metrics={m.id for m in ms if m.rate}, totals=("runs", "wickets"))
        return result(title, cols, table, scope, notes, labels=label, player=names[0], highlights=hl)

    if names:
        by = {(r.get("player"), r["k"]): r for r in rows}
        keys = sorted({r["k"] for r in rows}, key=str) if split_by else ["all"]
        table = []
        for n in names:
            for k in keys:
                r = by.get((n, k))
                if r is None and not split_by:
                    notes.append(f"{n} has no {role} records with these filters.")
                    table.append([n] + [None] * len(ms))
                elif r is not None:
                    table.append([n] + ([k] if split_by else []) + _values(r, ms))
        cols = ["player"] + ([split_by] if split_by else []) + [m.id for m in ms]
        title = _title(f"{role.capitalize()} — {names[0]}" if len(names) == 1 else f"{role.capitalize()} comparison", scope)
        hl = highlights(cols, table, "player", [(m.id, "max" if m.higher_is_better else "min") for m in ms
                                                 if m.kind != "text"], rate_metrics={m.id for m in ms if m.rate}) \
            if not split_by else {}
        return result(title, cols, table, scope, notes, labels=label, highlights=hl)

    cols = ["rank", "player", "team"] + ([split_by] if split_by else []) + [m.id for m in ms]
    table = [[i + 1, r["player"], r["_team"]] + ([r["k"]] if split_by else []) + _values(r, ms)
             for i, r in enumerate(rows)]
    sort_label = sort.label.lower() if sort else "innings"
    title = _title(f"{'Lowest' if direction == 'ASC' and sort and sort.higher_is_better else 'Top'} "
                   f"{role} by {sort_label}", scope)
    return result(title, cols, table, scope, notes, labels=["Rank", "Player", "Team"] + label)


# ---------------------------------------------------------------------------
# Player views
# ---------------------------------------------------------------------------

PROFILE_METRICS = {
    BAT: ["matches", "innings", "runs", "average", "strike_rate", "highest", "hundreds", "fifties",
          "true_sr", "true_average", "match_factor", "era_factor", "first5_sr", "avg_position", "dot_pct",
          "boundary_pct", "conversion_pct"],
    BOWL: ["matches", "innings", "wickets", "average", "economy", "strike_rate", "best", "five_wkt_hauls",
           "true_economy", "true_wickets", "match_factor", "dot_pct", "boundary_pct"],
}


@_tool
def player_profile(player: str, **filters) -> dict:
    """Headline figures for both disciplines (where the player has them),
    split by format, plus the context-adjusted numbers."""
    _check_built()
    g = catalog.resolve_gender(filters.get("gender")) if filters.get("gender") else None
    p = catalog.resolve_player(player, gender=g)
    info = catalog.get_catalog().players[p.name]
    out = {"player": p.name, "gender": info.gender, "teams": info.teams, "matches_all_cricket": info.matches,
           "span": info.describe()["span"]}
    if p.note:
        out["note"] = p.note
    for role in (BAT, BOWL):
        summary = query_stats(role=role, metrics=PROFILE_METRICS[role], players=[p.name], **filters)
        if "error" in summary:
            return summary
        row = dict(zip(summary["columns"], summary["rows"][0])) if summary["rows"] else {}
        if not row.get("innings"):
            continue
        by_format = query_stats(role=role, metrics=PROFILE_METRICS[role][:8], players=[p.name],
                                split_by="format", **filters)
        out[role] = {"summary": summary, "by_format": by_format}
    out["primary_role"] = _primary_role(out)
    if BAT not in out and BOWL not in out:
        out["notes"] = [f"No records for {p.name} with these filters."]
    return out


def _primary_role(profile: dict) -> str:
    def get(role, col):
        t = profile.get(role, {}).get("summary")
        if not t or not t["rows"]:
            return 0
        return dict(zip(t["columns"], t["rows"][0])).get(col) or 0
    bat_inns, bowl_inns = get(BAT, "innings"), get(BOWL, "innings")
    if bat_inns and bowl_inns and bowl_inns >= 0.5 * bat_inns and bat_inns >= 0.5 * bowl_inns:
        return "all-rounder"
    return "bowler" if bowl_inns > bat_inns else "batter"


@_tool
def player_form(player: str, role: str = BAT, window: int = 10, **filters) -> dict:
    """Every innings in order, with rolling (last-N) and career-to-date
    figures -- the data behind a form chart."""
    _check_built()
    role = _role(role)
    window = max(2, min(int(window or 10), 100))
    g = catalog.resolve_gender(filters.get("gender")) if filters.get("gender") else None
    p = catalog.resolve_player(player, gender=g)
    scope = build_scope(**filters)
    if p.note:
        scope.notes.insert(0, p.note)
    table = _TABLES[(role, bool(scope.phase))]
    w = f"(ORDER BY m.date, m.match_id, m.innings_num ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW)"
    c = "(ORDER BY m.date, m.match_id, m.innings_num ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
    if role == BAT:
        sql = f"""
SELECT ROW_NUMBER() OVER (ORDER BY m.date, m.match_id, m.innings_num) AS n, m.date, m.opposition,
       COALESCE(m.event_name, m.fmt) AS competition, m.runs, m.balls, m.out,
       CAST(SUM(m.runs) OVER {w} AS DOUBLE) / NULLIF(SUM(m.out) OVER {w}, 0) AS rolling_average,
       100.0 * SUM(m.runs) OVER {w} / NULLIF(SUM(m.balls) OVER {w}, 0) AS rolling_strike_rate,
       CAST(SUM(m.runs) OVER {c} AS DOUBLE) / NULLIF(SUM(m.out) OVER {c}, 0) AS career_average,
       100.0 * SUM(m.runs) OVER {c} / NULLIF(SUM(m.balls) OVER {c}, 0) AS career_strike_rate
FROM {table} m {_where(scope, [p.name])}
ORDER BY n"""
        cols = ["innings_no", "date", "opposition", "competition", "score", "balls", "rolling_average",
                "rolling_strike_rate", "career_average", "career_strike_rate"]
        rows = db.query(sql)
        table_rows = [[r["n"], r["date"], r["opposition"], r["competition"],
                       f"{int(r['runs'])}{'' if r['out'] else '*'}", r["balls"], r["rolling_average"],
                       r["rolling_strike_rate"], r["career_average"], r["career_strike_rate"]] for r in rows]
        main = "rolling_average"
    else:
        sql = f"""
SELECT ROW_NUMBER() OVER (ORDER BY m.date, m.match_id, m.innings_num) AS n, m.date, m.opposition,
       COALESCE(m.event_name, m.fmt) AS competition, m.wickets, m.runs, m.balls,
       6.0 * SUM(m.runs) OVER {w} / NULLIF(SUM(m.balls) OVER {w}, 0) AS rolling_economy,
       CAST(SUM(m.runs) OVER {w} AS DOUBLE) / NULLIF(SUM(m.wickets) OVER {w}, 0) AS rolling_average,
       6.0 * SUM(m.runs) OVER {c} / NULLIF(SUM(m.balls) OVER {c}, 0) AS career_economy,
       CAST(SUM(m.runs) OVER {c} AS DOUBLE) / NULLIF(SUM(m.wickets) OVER {c}, 0) AS career_average
FROM {table} m {_where(scope, [p.name])}
ORDER BY n"""
        cols = ["innings_no", "date", "opposition", "competition", "figures", "overs", "rolling_economy",
                "rolling_average", "career_economy", "career_average"]
        rows = db.query(sql)
        table_rows = [[r["n"], r["date"], r["opposition"], r["competition"], f"{int(r['wickets'])}/{int(r['runs'])}",
                       overs(r["balls"]), r["rolling_economy"], r["rolling_average"], r["career_economy"],
                       r["career_average"]] for r in rows]
        main = "rolling_economy"

    notes = [f"Rolling figures cover the last {window} innings at each point."]
    hl = {}
    if len(table_rows) >= window:
        idx = cols.index(main)
        valid = [(r[idx], r) for r in table_rows[window - 1:] if r[idx] is not None]
        if valid:
            best = (min if role == BOWL else max)(valid, key=lambda t: t[0])[1]
            worst = (max if role == BOWL else min)(valid, key=lambda t: t[0])[1]
            last = table_rows[-1]
            hl = {
                f"current_{main}": f"{num(last[idx])} (last {window} innings to {last[1]})",
                f"peak_{main}": f"{num(best[idx])} (innings {best[0] - window + 1}-{best[0]}, ending {best[1]})",
                f"lowest_{main}" if role == BAT else f"worst_{main}":
                    f"{num(worst[idx])} (innings {worst[0] - window + 1}-{worst[0]}, ending {worst[1]})",
                "career": f"{cols[-2].replace('career_', '').replace('_', ' ')} {num(last[-2])}, "
                          f"{cols[-1].replace('career_', '').replace('_', ' ')} {num(last[-1])} after {last[0]} innings",
            }
    else:
        notes.append(f"Fewer than {window} innings -- rolling values start once there are {window}.")
    title = _title(f"{role.capitalize()} form — {p.name} (rolling {window})", scope)
    return result(title, cols, table_rows, scope, notes, player=p.name, highlights=hl,
                  chart={"type": "line", "x": "innings_no", "series": [main, cols[-2]]})


@_tool
def career_arc(players, role: str = BAT, metric: str = "average", **filters) -> dict:
    """Career-to-date value of a metric after each innings, for several
    players aligned by innings number (1st innings, 2nd, ...)."""
    _check_built()
    role = _role(role)
    metric = (metric or "average").lower()
    exprs = {
        BAT: {"average": "CAST(SUM(m.runs) OVER c AS DOUBLE) / NULLIF(SUM(m.out) OVER c, 0)",
              "strike_rate": "100.0 * SUM(m.runs) OVER c / NULLIF(SUM(m.balls) OVER c, 0)",
              "runs": "SUM(m.runs) OVER c",
              "true_sr": "100.0 * (SUM(m.runs) OVER c - SUM(m.exp_runs) OVER c) / NULLIF(SUM(m.balls) OVER c, 0)"},
        BOWL: {"wickets": "SUM(m.wickets) OVER c",
               "average": "CAST(SUM(m.runs) OVER c AS DOUBLE) / NULLIF(SUM(m.wickets) OVER c, 0)",
               "economy": "6.0 * SUM(m.runs) OVER c / NULLIF(SUM(m.balls) OVER c, 0)",
               "strike_rate": "CAST(SUM(m.balls) OVER c AS DOUBLE) / NULLIF(SUM(m.wickets) OVER c, 0)"},
    }[role]
    if metric not in exprs:
        raise EngineError(f"career_arc metric for {role} must be one of: {', '.join(exprs)}")
    g = catalog.resolve_gender(filters.get("gender")) if filters.get("gender") else None
    names, notes = _resolve_players(players, g)
    if not names:
        raise EngineError("career_arc needs at least one player.")
    scope = build_scope(**filters)
    scope.notes[:0] = notes
    sql = f"""
SELECT m.player, ROW_NUMBER() OVER (PARTITION BY m.player ORDER BY m.date, m.match_id, m.innings_num) AS n,
       {exprs[metric]} AS v
FROM {_TABLES[(role, bool(scope.phase))]} m {_where(scope, names)}
WINDOW c AS (PARTITION BY m.player ORDER BY m.date, m.match_id, m.innings_num
             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
ORDER BY n
"""
    rows = db.query(sql)
    series = {n: {} for n in names}
    for r in rows:
        series[r["player"]][r["n"]] = r["v"]
    longest = max((len(s) for s in series.values()), default=0)
    table = [[i] + [series[n].get(i) for n in names] for i in range(1, longest + 1)]
    hl = {n: f"{num(series[n][len(series[n])])} after {len(series[n])} innings" for n in names if series[n]}
    title = _title(f"Career arc — {metric.replace('_', ' ')} by innings number", scope)
    return result(title, ["innings_no"] + names, table, scope, highlights=hl,
                  chart={"type": "line", "x": "innings_no", "series": names})


@_tool
def percentiles(players, role: str = BAT, metrics=None, min_balls: int | None = None, **filters) -> dict:
    """Each player's value and percentile (0-100, higher = better) for each
    metric, among all players qualifying in the same scope."""
    _check_built()
    role = _role(role)
    g = catalog.resolve_gender(filters.get("gender")) if filters.get("gender") else None
    names, notes = _resolve_players(players, g)
    if not names:
        raise EngineError("percentiles needs at least one player.")
    default = {BAT: ["runs", "average", "strike_rate", "true_sr", "true_average", "boundary_pct", "dot_pct",
                     "match_factor"],
               BOWL: ["wickets", "average", "economy", "strike_rate", "true_economy", "true_wickets", "dot_pct"]}
    ms = _metric_list(role, metrics or default[role], False)
    scope = build_scope(default_gender=None if g else catalog.get_catalog().players[names[0]].gender, **filters)
    scope.notes[:0] = notes
    metric_sql = ",\n       ".join(f"{m.sql} AS {m.id}" for m in ms)
    rows = db.query(f"""
SELECT m.player, {metric_sql}, SUM(m.balls) AS _balls
FROM {_TABLES[(role, bool(scope.phase))]} m {_where(scope)}
GROUP BY m.player
""")
    max_balls = max((r["_balls"] or 0 for r in rows), default=0)
    threshold = int(min_balls) if min_balls is not None else max(60, math.ceil(0.1 * max_balls))
    qualified = [r for r in rows if (r["_balls"] or 0) >= threshold]
    by_name = {r["player"]: r for r in rows}
    table = []
    for m in ms:
        vals = [r[m.id] for r in qualified if isinstance(r[m.id], (int, float))]
        for n in names:
            v = (by_name.get(n) or {}).get(m.id)
            pct = None
            if isinstance(v, (int, float)) and vals:
                below = sum(1 for x in vals if x < v)
                equal = sum(1 for x in vals if x == v)
                pct = 100.0 * (below + 0.5 * equal) / len(vals)
                if not m.higher_is_better:
                    pct = 100.0 - pct
            table.append([m.id, n, v, pct])
    notes = [f"Percentile among {len(qualified)} players with at least {threshold} balls "
             f"{'faced' if role == BAT else 'bowled'} in this scope; 100 = best."]
    title = _title(f"{role.capitalize()} percentiles", scope)
    return result(title, ["metric", "player", "value", "percentile"], table, scope, notes,
                  labels={m.id: m.label for m in ms})


@_tool
def scatter(role: str = BAT, x: str = "average", y: str = "strike_rate", min_balls: int | None = None,
            highlight=None, limit: int = 1500, **filters) -> dict:
    """Two metrics for every qualifying player -- the Player Matrix."""
    _check_built()
    role = _role(role)
    mx, my = registry.get(role, x), registry.get(role, y)
    scope = build_scope(default_gender="male", **filters)
    phase_mode = bool(scope.phase)
    _metric_list(role, [mx.id, my.id], phase_mode)
    rows = db.query(f"""
SELECT m.player, mode(m.team) AS team, {mx.sql} AS x, {my.sql} AS y, SUM(m.balls) AS balls, COUNT(*) AS innings
FROM {_TABLES[(role, phase_mode)]} m {_where(scope)}
GROUP BY m.player
""")
    max_balls = max((r["balls"] or 0 for r in rows), default=0)
    threshold = int(min_balls) if min_balls is not None else max(60, math.ceil(0.1 * max_balls))
    pts = [r for r in rows if (r["balls"] or 0) >= threshold and r["x"] is not None and r["y"] is not None]
    pts.sort(key=lambda r: -(r["balls"] or 0))
    total = len(pts)
    pts = pts[:max(10, min(int(limit or 1500), 3000))]

    def median(vals):
        vals = sorted(vals)
        return vals[len(vals) // 2] if vals else None

    hl_names = []
    if highlight:
        hl_names, _ = _resolve_players(highlight, scope.gender)
    table = [[r["player"], r["team"], r["x"], r["y"], r["balls"], r["innings"]] for r in pts]
    notes = [f"{len(pts)} players with at least {threshold} balls {'faced' if role == BAT else 'bowled'}"
             + (f" (the {len(pts)} busiest of {total})." if total > len(pts) else ".")]
    extra = {
        "axes": {"x": mx.public(), "y": my.public()},
        "medians": {"x": num(median([r["x"] for r in pts])), "y": num(median([r["y"] for r in pts]))},
        "highlighted": hl_names,
    }
    # Standouts: the players best on both axes at once (for the AI to cite).
    if pts:
        def z(vals, v, good_high):
            mu = sum(vals) / len(vals)
            sd = (sum((a - mu) ** 2 for a in vals) / len(vals)) ** 0.5 or 1
            return (v - mu) / sd * (1 if good_high else -1)
        xs, ys = [r["x"] for r in pts], [r["y"] for r in pts]
        ranked = sorted(pts, key=lambda r: -(z(xs, r["x"], mx.higher_is_better) + z(ys, r["y"], my.higher_is_better)))
        extra["highlights"] = {"best_on_both_axes": ", ".join(
            f"{r['player']} ({mx.label} {num(r['x'])}, {my.label} {num(r['y'])})" for r in ranked[:5])}
        extra["standouts"] = [r["player"] for r in ranked[:5]]
    title = _title(f"Player matrix — {mx.label} vs {my.label} ({role})", scope)
    return result(title, ["player", "team", mx.id, my.id, "balls", "innings"], table, scope, notes, **extra)


@_tool
def entry_heatmap(player: str, **filters) -> dict:
    """A batter's record by entry point: phase of the innings they came in x
    wickets already down."""
    _check_built()
    g = catalog.resolve_gender(filters.get("gender")) if filters.get("gender") else None
    p = catalog.resolve_player(player, gender=g)
    scope = build_scope(**filters)
    if p.note:
        scope.notes.insert(0, p.note)
    rows = db.query(f"""
SELECT {_ENTRY_PHASE} AS entry_phase, {_DIMENSION_SQL['entry_wickets']} AS entry_wickets,
       COUNT(*) AS innings, SUM(m.runs) AS runs, SUM(m.balls) AS balls,
       CAST(SUM(m.runs) AS DOUBLE) / NULLIF(SUM(m.out), 0) AS average,
       100.0 * SUM(m.runs) / NULLIF(SUM(m.balls), 0) AS strike_rate,
       100.0 * (SUM(m.runs) - SUM(m.exp_runs)) / NULLIF(SUM(m.balls), 0) AS true_sr
FROM batting_innings m {_where(scope, [p.name])}
GROUP BY 1, 2
""")
    phase_rank = {"powerplay": 0, "middle": 1, "death": 2, "multi-day: overs 1-20": 3, "multi-day: overs 21-60": 4,
                  "multi-day: overs 61+": 5}
    rows.sort(key=lambda r: (phase_rank.get(r["entry_phase"], 9), 99 if r["entry_wickets"] == "5+" else int(r["entry_wickets"])))
    cols = ["entry_phase", "entry_wickets", "innings", "runs", "balls", "average", "strike_rate", "true_sr"]
    table = [[r[c] for c in cols] for r in rows]
    top = max(rows, key=lambda r: r["innings"]) if rows else None
    hl = {"most_common_entry": f"{top['entry_phase']}, {top['entry_wickets']} down ({top['innings']} innings)"} if top else {}
    title = _title(f"Entry points — {p.name}", scope)
    return result(title, cols, table, scope, ["Entry phase = the phase of the innings when they walked in "
                                              "(powerplay/middle/death for limited overs; over bands for multi-day)."],
                  player=p.name, highlights=hl)


_SIMILARITY_FEATURES = {
    BAT: ["strike_rate", "average", "boundary_pct", "dot_pct", "true_sr", "avg_position", "first5_sr",
          "balls_per_six"],
    BOWL: ["economy", "strike_rate", "dot_pct", "boundary_pct", "true_economy", "wickets_per_innings"],
}


@_tool
def similar_players(player: str, role: str = BAT, limit: int = 10, min_balls: int | None = None, **filters) -> dict:
    """Players whose statistical profile (standardised rate metrics) is
    closest to this player's, in the same scope."""
    _check_built()
    role = _role(role)
    g = catalog.resolve_gender(filters.get("gender")) if filters.get("gender") else None
    p = catalog.resolve_player(player, gender=g)
    scope = build_scope(default_gender=catalog.get_catalog().players[p.name].gender, **filters)
    if p.note:
        scope.notes.insert(0, p.note)
    feats = [registry.get(role, f) for f in _SIMILARITY_FEATURES[role]]
    rows = db.query(f"""
SELECT m.player, mode(m.team) AS team, SUM(m.balls) AS balls, {', '.join(f'{f.sql} AS {f.id}' for f in feats)}
FROM {_TABLES[(role, False)]} m {_where(scope)}
GROUP BY m.player
""")
    me = next((r for r in rows if r["player"] == p.name), None)
    if me is None:
        raise EngineError(f"{p.name} has no {role} records with these filters.")
    threshold = int(min_balls) if min_balls is not None else max(120, math.ceil(0.25 * (me["balls"] or 0)))
    pool = [r for r in rows if (r["balls"] or 0) >= threshold]
    if me not in pool:
        pool.append(me)
    # A rate can be undefined (never out -> no average; no sixes -> no balls
    # per six). Fill it with the pool's worst value rather than dropping the
    # player -- "never hits sixes" is itself part of a profile.
    for f in feats:
        vals = [r[f.id] for r in pool if r[f.id] is not None]
        if not vals:
            raise EngineError(f"Not enough data in this scope to compare on {f.label}.")
        worst = min(vals) if f.higher_is_better else max(vals)
        for r in pool:
            if r[f.id] is None:
                r[f.id] = worst
    stats_ = {}
    for f in feats:
        vals = [r[f.id] for r in pool]
        mu = sum(vals) / len(vals)
        sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        stats_[f.id] = (mu, sd)

    def vec(r):
        return [(r[f.id] - stats_[f.id][0]) / stats_[f.id][1] for f in feats]

    mv = vec(me)
    scored = []
    for r in pool:
        if r["player"] == p.name:
            continue
        d = math.sqrt(sum((a - b) ** 2 for a, b in zip(mv, vec(r))))
        scored.append((d, r))
    scored.sort(key=lambda t: t[0])
    limit = max(1, min(int(limit or 10), 50))
    cols = ["player", "team", "similarity"] + [f.id for f in feats]
    table = [[p.name, me["team"], 100.0] + [me[f.id] for f in feats]]
    table += [[r["player"], r["team"], 100.0 / (1.0 + d)] + [r[f.id] for f in feats] for d, r in scored[:limit]]
    notes = [f"Compared on {', '.join(f.label for f in feats)} (standardised), among {len(pool)} players with "
             f"at least {threshold} balls in this scope. Similarity 100 = identical profile."]
    title = _title(f"Players similar to {p.name} ({role})", scope)
    return result(title, cols, table, scope, notes, player=p.name)


def metric_catalog(role: str | None = None, query: str = "") -> dict:
    return {"metrics": registry.search(query, role), "dimensions": registry.DIMENSIONS}
