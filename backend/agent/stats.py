"""
The analytics engine behind the agent's tools.

Why this exists: letting an LLM derive cricket scoring rules in raw SQL on
every question is how the app produced wrong numbers (dismissals counted via
the facing batter miss non-striker run-outs; "retired hurt" isn't a
dismissal; byes aren't the bowler's fault; a wide isn't a ball faced...).
Every rule is encoded once here, in shared SQL building blocks, and every
tool -- single-player figures, comparisons, splits, leaderboards, records --
is assembled from the same blocks, so they can't disagree with each other.

Each public function:
  * takes plain human names (player="Rohit Sharma", competition="IPL") and
    resolves them via catalog/scope -- no exact-string lookups for the LLM;
  * returns a uniform envelope {title, columns, rows, filters, notes} that
    the agent loop can show to the user as a table and to the model as
    records;
  * returns {"error": ..., "candidates": [...]} instead of raising when a
    name is ambiguous or unknown, so the model can recover.

Works against both database schemas: the current one (per-type extras +
deliveries_wickets, exact) and the older one (single extra_type string,
first wicket per ball only), probing which one is present.
"""

from __future__ import annotations

import functools
import math

from analytics import catalog, db, results
from analytics.results import franchise_case as _franchise_case
from analytics.results import num as _num
from analytics.results import overs as _overs
from analytics.results import result as _result
from analytics.results import season_labels as _season_labels
from analytics.catalog import ResolutionError
from analytics.scope import (
    ball_exprs,
    CREDITED_SQL,
    FORMAT_LABEL_SQL,
    NOT_DISMISSALS_SQL,
    NOT_SUPER_OVER_SQL,
    PHASE_SQL,
    YEAR_SQL,
    Scope,
    build_scope,
    lit,
    lit_list,
)

MAX_LIMIT = 50


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

def _tool(fn):
    """Turn resolution/validation failures into {"error": ...} results the
    model can read and act on, instead of exceptions."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ResolutionError as e:
            return e.to_dict()
        except ValueError as e:
            return {"error": str(e)}

    return wrapper


def _query(sql: str) -> list[dict]:
    con = db.connect()
    try:
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


# Metrics worth calling out in a split/comparison, and which direction is best.
_HIGHLIGHT_METRICS = {
    "batting": [("runs", "max"), ("average", "max"), ("strike_rate", "max"), ("highest", "max"), ("sixes", "max")],
    "bowling": [("wickets", "max"), ("economy", "min"), ("average", "min"), ("strike_rate", "min")],
    "fielding": [("catches", "max"), ("dismissals", "max")],
    "team": [("won", "max"), ("win_pct", "max"), ("win_pct", "min", "worst_win_pct")],
}


def _highlights(columns: list[str], rows: list[list], label_col: str, kind: str) -> dict:
    return results.highlights(columns, rows, label_col, _HIGHLIGHT_METRICS[kind],
                              rate_metrics=_RATE_METRICS | {"average", "strike_rate"},
                              totals=("runs", "wickets", "won", "matches") if label_col != "player" else ())




def _exprs() -> dict:
    return ball_exprs(catalog.get_catalog().has_new_schema)


_OTHER_SIDE = "(CASE WHEN d.batting_team = m.team1 THEN m.team2 ELSE m.team1 END)"

SPLITS = ("season", "year", "format", "competition", "opposition", "team", "venue", "phase", "innings")


def _key_expr(split_by: str | None, role: str) -> str:
    if not split_by:
        return "'all'"
    split_by = split_by.lower().strip()
    fielding_side = _OTHER_SIDE
    exprs = {
        "season": "m.season",
        "year": YEAR_SQL,
        "format": FORMAT_LABEL_SQL,
        "competition": "COALESCE(m.event_name, 'Bilateral / other')",
        "venue": "trim(regexp_replace(split_part(m.venue, ',', 1), '\\.\\s*', ' ', 'g'))",
        "phase": PHASE_SQL,
        "innings": "d.innings_num",
        "opposition": _franchise_case(_OTHER_SIDE if role == "batting" else "d.batting_team"),
        "team": _franchise_case("d.batting_team" if role == "batting" else fielding_side),
    }
    if split_by not in exprs:
        raise ValueError(f"split_by must be one of: {', '.join(SPLITS)}")
    return exprs[split_by]


def _ball_ctes(scope: Scope, role: str, key: str = "'all'", where: str = "") -> str:
    """The two CTEs every ball-level query starts from:
        b -- scoped deliveries with derived columns (faced, legal,
             bowler_runs) and a grouping key k
        w -- one row per wicket on a scoped delivery (every wicket, when the
             database has deliveries_wickets; else the first per ball)"""
    e = _exprs()
    clauses = scope.match_clauses() + scope.ball_clauses(role) + [NOT_SUPER_OVER_SQL]
    if where:
        clauses.append(where)
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    b = f"""
b AS MATERIALIZED (
    SELECT d.match_id, d.innings_num, d.over_num, d.ball_in_over, d.batting_team,
           d.batter, d.bowler, d.non_striker, d.runs_batter, d.runs_total,
           d.is_wicket, d.wicket_kind, d.player_dismissed,
           {e['faced']} AS faced, {e['legal']} AS legal, {e['bowler_runs']} AS bowler_runs,
           {_OTHER_SIDE} AS fielding_team,
           m.date, m.season, m.team1, m.team2, m.venue, m.event_name, m.match_type,
           COALESCE(CAST(({key}) AS VARCHAR), 'n/a') AS k
    FROM deliveries d JOIN matches m ON d.match_id = m.match_id
    {where_sql}
)"""
    if catalog.get_catalog().has_wickets_table:
        w = """
w AS (
    SELECT b.*, dw.kind, dw.player_out, dw.fielders
    FROM deliveries_wickets dw
    JOIN b ON dw.match_id = b.match_id AND dw.innings_num = b.innings_num
          AND dw.over_num = b.over_num AND dw.ball_in_over = b.ball_in_over
)"""
    else:
        w = """
w AS (
    SELECT b.*, b.wicket_kind AS kind, b.player_dismissed AS player_out, CAST(NULL AS VARCHAR) AS fielders
    FROM b WHERE b.is_wicket
)"""
    return b + "," + w


# ---------------------------------------------------------------------------
# Core aggregations (per player x key)
# ---------------------------------------------------------------------------

def _batting_innings_ctes(scope: Scope, key: str = "'all'", players: list[str] | None = None) -> str:
    """CTE chain ending in `inn`: one row per player per innings (per key),
    counting non-striker appearances so a batter run out without facing
    still has an innings."""
    where = ""
    if players:
        pl = lit_list(players)
        where = f"(d.batter IN {pl} OR d.non_striker IN {pl})"
    return f"""
WITH {_ball_ctes(scope, 'batting', key, where)},
apps AS (
    SELECT batter AS player, match_id, innings_num, k, date, batting_team FROM b
    UNION
    SELECT non_striker, match_id, innings_num, k, date, batting_team FROM b WHERE non_striker IS NOT NULL
),
bat AS (
    SELECT batter AS player, match_id, innings_num, k,
           SUM(runs_batter) AS runs, SUM(CAST(faced AS INTEGER)) AS balls,
           SUM(CAST(runs_batter = 4 AS INTEGER)) AS fours, SUM(CAST(runs_batter = 6 AS INTEGER)) AS sixes,
           SUM(CAST(faced AND runs_batter = 0 AS INTEGER)) AS dots
    FROM b GROUP BY batter, match_id, innings_num, k
),
outs AS (
    SELECT player_out AS player, match_id, innings_num, k, COUNT(*) AS outs
    FROM w WHERE player_out IS NOT NULL AND kind NOT IN {NOT_DISMISSALS_SQL}
    GROUP BY player_out, match_id, innings_num, k
),
inn AS (
    SELECT a.player, a.match_id, a.innings_num, a.k, a.date, a.batting_team,
           COALESCE(bat.runs, 0) AS runs, COALESCE(bat.balls, 0) AS balls,
           COALESCE(bat.fours, 0) AS fours, COALESCE(bat.sixes, 0) AS sixes,
           COALESCE(bat.dots, 0) AS dots, COALESCE(o.outs, 0) AS outs
    FROM apps a
    LEFT JOIN bat ON bat.player = a.player AND bat.match_id = a.match_id
                 AND bat.innings_num = a.innings_num AND bat.k = a.k
    LEFT JOIN outs o ON o.player = a.player AND o.match_id = a.match_id
                    AND o.innings_num = a.innings_num AND o.k = a.k
)"""


def _batting_sql(scope: Scope, key: str = "'all'", players: list[str] | None = None) -> str:
    final_where = f"WHERE player IN {lit_list(players)}" if players else ""
    return f"""{_batting_innings_ctes(scope, key, players)},
agg AS (
    SELECT player, k,
           mode(batting_team) AS team,
           COUNT(DISTINCT match_id) AS matches, COUNT(*) AS innings,
           COUNT(*) - SUM(outs) AS not_outs, SUM(outs) AS outs, SUM(outs) AS dismissals,
           SUM(runs) AS runs, SUM(balls) AS balls,
           arg_max(CAST(runs AS VARCHAR) || CASE WHEN outs = 0 THEN '*' ELSE '' END,
                   runs * 2 + CAST(outs = 0 AS INTEGER)) AS highest,
           SUM(CAST(runs >= 100 AS INTEGER)) AS hundreds,
           SUM(CAST(runs >= 50 AND runs < 100 AS INTEGER)) AS fifties,
           SUM(CAST(runs = 0 AND outs > 0 AS INTEGER)) AS ducks,
           SUM(fours) AS fours, SUM(sixes) AS sixes, SUM(dots) AS dots,
           MIN(date) AS first_date, MAX(date) AS last_date
    FROM inn GROUP BY player, k
)
SELECT *,
       CAST(runs AS DOUBLE) / NULLIF(outs, 0) AS average,
       100.0 * runs / NULLIF(balls, 0) AS strike_rate,
       100.0 * dots / NULLIF(balls, 0) AS dot_pct,
       100.0 * (fours + sixes) / NULLIF(balls, 0) AS boundary_pct,
       CAST(balls AS DOUBLE) / NULLIF(sixes, 0) AS balls_per_six
FROM agg {final_where}
"""


def _bowling_innings_ctes(scope: Scope, key: str = "'all'", players: list[str] | None = None) -> str:
    """CTE chain ending in `inn` (one row per bowler per innings) plus `mdn`
    (maidens per bowler per key)."""
    where = f"d.bowler IN {lit_list(players)}" if players else ""
    return f"""
WITH {_ball_ctes(scope, 'bowling', key, where)},
bowl AS (
    SELECT bowler AS player, match_id, innings_num, k, MIN(date) AS date, any_value(fielding_team) AS team,
           SUM(CAST(legal AS INTEGER)) AS balls, SUM(bowler_runs) AS runs,
           SUM(CAST(legal AND bowler_runs = 0 AS INTEGER)) AS dots,
           SUM(CAST(runs_batter = 4 AS INTEGER)) AS fours, SUM(CAST(runs_batter = 6 AS INTEGER)) AS sixes
    FROM b GROUP BY bowler, match_id, innings_num, k
),
wk AS (
    SELECT bowler AS player, match_id, innings_num, k, COUNT(*) AS wkts
    FROM w WHERE kind IN {CREDITED_SQL}
    GROUP BY bowler, match_id, innings_num, k
),
ovr AS (
    SELECT bowler AS player, k, match_id, innings_num, over_num,
           SUM(CAST(legal AS INTEGER)) AS lb, SUM(bowler_runs) AS r
    FROM b GROUP BY bowler, k, match_id, innings_num, over_num
),
mdn AS (SELECT player, k, COUNT(*) AS maidens FROM ovr WHERE lb >= 6 AND r = 0 GROUP BY player, k),
inn AS (
    SELECT bowl.*, COALESCE(wk.wkts, 0) AS wkts
    FROM bowl LEFT JOIN wk ON wk.player = bowl.player AND wk.match_id = bowl.match_id
                          AND wk.innings_num = bowl.innings_num AND wk.k = bowl.k
)"""


def _bowling_sql(scope: Scope, key: str = "'all'", players: list[str] | None = None) -> str:
    return f"""{_bowling_innings_ctes(scope, key, players)},
agg AS (
    SELECT player, k, mode(team) AS team,
           COUNT(DISTINCT match_id) AS matches, COUNT(*) AS innings,
           SUM(balls) AS balls, SUM(runs) AS runs, SUM(wkts) AS wickets, SUM(dots) AS dots,
           SUM(fours) AS fours, SUM(sixes) AS sixes,
           arg_max(CAST(wkts AS VARCHAR) || '/' || CAST(runs AS VARCHAR), wkts * 100000 - runs) AS best,
           SUM(CAST(wkts >= 4 AS INTEGER)) AS four_wkt_hauls,
           SUM(CAST(wkts >= 5 AS INTEGER)) AS five_wkt_hauls,
           MIN(date) AS first_date, MAX(date) AS last_date
    FROM inn GROUP BY player, k
)
SELECT agg.*, COALESCE(mdn.maidens, 0) AS maidens,
       CAST(agg.runs AS DOUBLE) / NULLIF(agg.wickets, 0) AS average,
       6.0 * agg.runs / NULLIF(agg.balls, 0) AS economy,
       CAST(agg.balls AS DOUBLE) / NULLIF(agg.wickets, 0) AS strike_rate,
       100.0 * agg.dots / NULLIF(agg.balls, 0) AS dot_pct
FROM agg LEFT JOIN mdn ON mdn.player = agg.player AND mdn.k = agg.k
"""


def _fielding_sql(scope: Scope, key: str = "'all'", players: list[str] | None = None) -> str:
    final_where = f"WHERE player IN {lit_list(players)}" if players else ""
    return f"""
WITH {_ball_ctes(scope, 'bowling', key)},
f AS (
    SELECT kind, match_id, k, fielding_team AS team,
           UNNEST(CASE WHEN kind = 'caught and bowled' THEN [bowler]
                       ELSE CAST(COALESCE(fielders, '[]') AS VARCHAR[]) END) AS player
    FROM w WHERE kind IN ('caught', 'caught and bowled', 'stumped', 'run out')
)
SELECT player, k, mode(team) AS team,
       SUM(CAST(kind IN ('caught', 'caught and bowled') AS INTEGER)) AS catches,
       SUM(CAST(kind = 'stumped' AS INTEGER)) AS stumpings,
       SUM(CAST(kind = 'run out' AS INTEGER)) AS run_outs,
       COUNT(*) AS dismissals, COUNT(DISTINCT match_id) AS matches_with_dismissal
FROM f {final_where} GROUP BY player, k
"""


BAT_COLS = ["matches", "innings", "not_outs", "dismissals", "runs", "balls", "average", "strike_rate", "highest",
            "hundreds", "fifties", "ducks", "fours", "sixes", "dot_pct", "boundary_pct"]
BOWL_COLS = ["matches", "innings", "overs", "balls", "runs", "wickets", "average", "economy", "strike_rate",
             "best", "four_wkt_hauls", "five_wkt_hauls", "maidens", "dot_pct"]
FIELD_COLS = ["catches", "stumpings", "run_outs", "dismissals"]


def _role_sql(role: str):
    if role == "batting":
        return _batting_sql, BAT_COLS
    if role == "bowling":
        return _bowling_sql, BOWL_COLS
    if role == "fielding":
        if not catalog.get_catalog().has_wickets_table:
            raise ValueError(
                "Fielding stats (catches/stumpings/run-outs) need fielder data, which this database doesn't "
                "have -- it was built with an older ingest. Re-run backend/ingest/build_db.py against the "
                "Cricsheet JSON to enable them."
            )
        return _fielding_sql, FIELD_COLS
    raise ValueError("role must be 'batting', 'bowling' or 'fielding'.")


def _row_values(r: dict, cols: list[str]) -> list:
    out = []
    for c in cols:
        if c == "overs":
            out.append(_overs(r.get("balls")))
        else:
            out.append(r.get(c))
    return out


def _order_split_rows(rows: list[dict], split_by: str, role: str) -> list[dict]:
    if split_by in ("season", "year"):
        return sorted(rows, key=lambda r: r.get("first_date") or "")
    if split_by == "phase":
        order = {"powerplay": 0, "middle": 1, "death": 2}
        return sorted(rows, key=lambda r: order.get(r["k"], 9))
    if split_by == "innings":
        return sorted(rows, key=lambda r: int(r["k"]) if str(r["k"]).isdigit() else 9)
    primary = {"batting": "runs", "bowling": "wickets", "fielding": "dismissals"}[role]
    return sorted(rows, key=lambda r: -(r.get(primary) or 0))


def _coverage_note(first_date: str | None) -> str | None:
    cat = catalog.get_catalog()
    if first_date and cat.date_min and first_date[:4] <= str(int(cat.date_min[:4]) + 1):
        return (f"The data starts in {cat.date_min[:4]} (Cricsheet coverage), so any career before that "
                "is missing -- these are not full career figures.")
    return None


def _matches_played(player: str, scope: Scope) -> int:
    """Matches in the playing XI (includes games where they didn't bat/bowl)."""
    clauses = scope.match_clauses() + [f"pm.player = {lit(player)}"]
    if scope.team:
        clauses.append(f"pm.team IN {lit_list(scope.team)}")
    if scope.opposition:
        clauses.append(f"pm.team NOT IN {lit_list(scope.opposition)}")
    rows = _query(
        "SELECT COUNT(DISTINCT pm.match_id) AS n FROM players_matches pm "
        f"JOIN matches m ON pm.match_id = m.match_id WHERE {' AND '.join(clauses)}"
    )
    return rows[0]["n"] if rows else 0


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

@_tool
def player_stats(player: str, role: str = "batting", split_by: str | None = None, **filters) -> dict:
    """A player's batting, bowling or fielding figures in any scope, optionally
    split by season/year/format/competition/opposition/team/venue/phase/innings."""
    role = (role or "batting").lower()
    scope_gender = catalog.resolve_gender(filters.get("gender")) if filters.get("gender") else None
    p = catalog.resolve_player(player, gender=scope_gender)
    scope = build_scope(**filters)
    if p.note:
        scope.notes.insert(0, p.note)

    sql_fn, cols = _role_sql(role)
    key = _key_expr(split_by, role)
    rows = _query(sql_fn(scope, key, [p.name]))

    title = f"{role.capitalize()} — {p.name}"
    if scope.applied:
        title += " — " + ", ".join(str(v) for k, v in scope.applied.items() if k != "gender")

    if not rows:
        return _result(title, cols, [], scope, notes=[
            f"No {role} records for {p.name} with these filters. Check the filters (e.g. did they play "
            "in this competition/format?) before telling the user it never happened."
        ], player=p.name)

    notes = []
    if split_by:
        split_by = split_by.lower()
        rows = _order_split_rows(rows, split_by, role)
        keys = _season_labels(rows) if split_by == "season" else [r["k"] for r in rows]
        table_rows = [[k] + _row_values(r, cols) for k, r in zip(keys, rows)]
        return _result(f"{title} — by {split_by}", [split_by] + cols, table_rows, scope, notes, player=p.name,
                       highlights=_highlights([split_by] + cols, table_rows, split_by, role))

    r = rows[0]
    if role == "batting" and not scope.has_ball_filters:
        r["matches"] = _matches_played(p.name, scope)
    note = _coverage_note(r.get("first_date"))
    if note and not (scope.from_year or scope.season):
        notes.append(note)
    return _result(title, cols, [_row_values(r, cols)], scope, notes, player=p.name)


@_tool
def compare_players(players: list[str], role: str = "batting", **filters) -> dict:
    """Full figures for several players side by side, in the same scope."""
    role = (role or "batting").lower()
    if not players or len(players) < 2:
        raise ValueError("compare_players needs at least two players.")
    scope_gender = catalog.resolve_gender(filters.get("gender")) if filters.get("gender") else None
    resolved = [catalog.resolve_player(name, gender=scope_gender) for name in players]
    scope = build_scope(**filters)
    for p in resolved:
        if p.note:
            scope.notes.append(p.note)

    sql_fn, cols = _role_sql(role)
    names = [p.name for p in resolved]
    by_name = {r["player"]: r for r in _query(sql_fn(scope, "'all'", names))}
    rows = []
    for name in names:
        r = by_name.get(name)
        if r is None:
            rows.append([name] + [None] * len(cols))
            scope.notes.append(f"{name} has no {role} records with these filters.")
        else:
            rows.append([name] + _row_values(r, cols))
    title = f"{role.capitalize()} comparison" + (
        " — " + ", ".join(str(v) for k, v in scope.applied.items() if k != "gender") if scope.applied else "")
    return _result(title, ["player"] + cols, rows, scope, highlights=_highlights(["player"] + cols, rows, "player", role))


LEADERBOARD_METRICS = {
    "batting": {
        "runs": "DESC", "average": "DESC", "strike_rate": "DESC", "sixes": "DESC", "fours": "DESC",
        "hundreds": "DESC", "fifties": "DESC", "innings": "DESC", "matches": "DESC", "ducks": "DESC",
        "boundary_pct": "DESC", "dot_pct": "ASC", "balls_per_six": "ASC", "balls": "DESC",
    },
    "bowling": {
        "wickets": "DESC", "economy": "ASC", "average": "ASC", "strike_rate": "ASC", "dot_pct": "DESC",
        "maidens": "DESC", "five_wkt_hauls": "DESC", "four_wkt_hauls": "DESC", "balls": "DESC",
        "matches": "DESC", "sixes": "DESC", "runs": "DESC",
    },
    "fielding": {"catches": "DESC", "stumpings": "DESC", "run_outs": "DESC", "dismissals": "DESC"},
    "team": {"wins": "DESC", "win_pct": "DESC", "matches": "DESC", "losses": "DESC"},
}
# Rate metrics need a qualification threshold or they're won by someone who
# faced 3 balls.
_RATE_METRICS = {"average", "strike_rate", "economy", "dot_pct", "boundary_pct", "balls_per_six", "win_pct"}


@_tool
def leaderboard(role: str, metric: str, limit: int = 10, min_balls: int | None = None,
                min_matches: int | None = None, **filters) -> dict:
    """Top players (or teams) by a metric in any scope."""
    role = (role or "").lower()
    metric = (metric or "").lower().replace(" ", "_")
    if role not in LEADERBOARD_METRICS:
        raise ValueError(f"role must be one of: {', '.join(LEADERBOARD_METRICS)}")
    metric = {"avg": "average", "sr": "strike_rate", "econ": "economy", "wkts": "wickets", "100s": "hundreds",
              "50s": "fifties", "6s": "sixes", "4s": "fours", "win_percentage": "win_pct", "5w": "five_wkt_hauls",
              "4w": "four_wkt_hauls", "five_wickets": "five_wkt_hauls", "catches_taken": "catches"}.get(metric, metric)
    if metric not in LEADERBOARD_METRICS[role]:
        raise ValueError(f"metric for role '{role}' must be one of: {', '.join(LEADERBOARD_METRICS[role])}")
    limit = max(1, min(int(limit or 10), MAX_LIMIT))

    if role == "team":
        return _team_leaderboard(metric, limit, min_matches, filters)

    scope = build_scope(default_gender="male", **filters)
    sql_fn, cols = _role_sql(role)
    direction = LEADERBOARD_METRICS[role][metric]

    qual_sql, notes = "", []
    if metric in _RATE_METRICS:
        if min_balls is not None:
            qual_sql = f"WHERE balls >= {int(min_balls)}"
            notes.append(f"Qualification: at least {int(min_balls)} balls {'faced' if role == 'batting' else 'bowled'}.")
        else:
            # Scale the bar to the scope: 10% of the busiest player's balls
            # (so an all-time IPL list needs ~700 balls, a single tournament ~30).
            qual_sql = "WHERE balls >= GREATEST(30, 0.1 * max_balls)"
            notes.append("Qualification: at least 10% of the balls of the busiest player in this scope "
                         "(min 30) -- pass min_balls to change it.")
    if role == "batting" and metric == "average":
        qual_sql += (" AND " if qual_sql else "WHERE ") + "outs > 0"

    tie = {"batting": "runs DESC", "bowling": "wickets DESC", "fielding": "dismissals DESC"}[role]
    sql = f"""
SELECT * FROM (
    SELECT *, MAX({'balls' if role != 'fielding' else 'dismissals'}) OVER () AS max_balls
    FROM ({sql_fn(scope)}) base
) x {qual_sql}
ORDER BY {metric} {direction} NULLS LAST, {tie}, player
LIMIT {limit}
"""
    rows = _query(sql)
    if qual_sql and rows and min_balls is None:
        threshold = max(30, math.ceil(0.1 * rows[0]["max_balls"]))
        notes[-1] = f"Qualification: at least {threshold} balls {'faced' if role == 'batting' else 'bowled'} " \
                    "(10% of the busiest player in this scope, min 30) -- pass min_balls to change it."

    show = {
        "batting": ["team", "matches", "innings", "runs", "average", "strike_rate", "hundreds", "fifties", "sixes", "balls"],
        "bowling": ["team", "matches", "wickets", "average", "economy", "strike_rate", "best", "overs"],
        "fielding": ["team", "catches", "stumpings", "run_outs", "dismissals"],
    }[role]
    if metric not in show:
        show = show + [metric]
    table_rows = [[i + 1, r["player"]] + _row_values(r, show) for i, r in enumerate(rows)]
    if not (scope.season or scope.from_year or scope.to_year):
        note = _coverage_note(min((r.get("first_date") or "9999") for r in rows) if rows else None)
        if note:
            notes.append(note.replace("any career before that is missing -- these are not full career figures",
                                      "players whose careers began earlier are undercounted"))
    title = f"Top {limit} by {metric.replace('_', ' ')} ({role})"
    if scope.applied:
        title += " — " + ", ".join(str(v) for k, v in scope.applied.items())
    return _result(title, ["rank", "player"] + show, table_rows, scope, notes)


def _team_leaderboard(metric: str, limit: int, min_matches: int | None, filters: dict) -> dict:
    scope = build_scope(default_gender="male", **filters)
    team_expr = _franchise_case("team")
    clauses = scope.match_clauses()
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    notes = []
    qual = ""
    if metric == "win_pct":
        n = int(min_matches) if min_matches else 10
        qual = f"WHERE decided >= {n}"
        notes.append(f"Qualification: at least {n} decided matches.")
    direction = LEADERBOARD_METRICS["team"][metric]
    sql = f"""
WITH m AS (SELECT m.* FROM matches m {where}),
t AS (
    SELECT team1 AS team, winner FROM m UNION ALL SELECT team2, winner FROM m
),
agg AS (
    SELECT {team_expr} AS team, COUNT(*) AS matches,
           SUM(CAST(winner = team AS INTEGER)) AS wins,
           SUM(CAST(winner IS NOT NULL AND winner <> team AS INTEGER)) AS losses,
           SUM(CAST(winner IS NULL AS INTEGER)) AS no_result,
           SUM(CAST(winner IS NOT NULL AS INTEGER)) AS decided
    FROM t WHERE team IS NOT NULL GROUP BY 1
)
SELECT *, 100.0 * wins / NULLIF(decided, 0) AS win_pct FROM agg {qual}
ORDER BY {metric} {direction} NULLS LAST, wins DESC, team LIMIT {limit}
"""
    rows = _query(sql)
    cols = ["matches", "wins", "losses", "no_result", "win_pct"]
    table_rows = [[i + 1, r["team"]] + [r[c] for c in cols] for i, r in enumerate(rows)]
    notes.append("no_result counts ties, draws and abandoned/no-result games together (win % is over decided games).")
    title = f"Teams by {metric.replace('_', ' ')}"
    if scope.applied:
        title += " — " + ", ".join(str(v) for v in scope.applied.values())
    return _result(title, ["rank", "team"] + cols, table_rows, scope, notes)


@_tool
def top_performances(kind: str, limit: int = 10, order: str = "highest", player: str | None = None, **filters) -> dict:
    """Record lists: best individual innings, best bowling figures, or
    highest/lowest team totals."""
    kind = (kind or "").lower().replace(" ", "_")
    kind = {"batting": "batting_innings", "innings": "batting_innings", "scores": "batting_innings",
            "highest_scores": "batting_innings", "bowling": "bowling_figures", "figures": "bowling_figures",
            "team_totals": "team_total", "totals": "team_total"}.get(kind, kind)
    if kind not in ("batting_innings", "bowling_figures", "team_total"):
        raise ValueError("kind must be 'batting_innings', 'bowling_figures' or 'team_total'.")
    limit = max(1, min(int(limit or 10), MAX_LIMIT))
    lowest = (order or "highest").lower().startswith("low")
    scope = build_scope(default_gender=None if player else "male", **filters)
    pname = None
    if player:
        p = catalog.resolve_player(player, gender=scope.gender)
        pname = p.name
        if p.note:
            scope.notes.insert(0, p.note)

    match_info = "m.date, m.venue, m.event_name, m.team1, m.team2"
    if kind == "batting_innings":
        sql = f"""{_batting_innings_ctes(scope, "'all'", [pname] if pname else None)}
SELECT inn.player, inn.runs, inn.balls, inn.fours, inn.sixes, inn.outs, inn.batting_team AS team,
       100.0 * inn.runs / NULLIF(inn.balls, 0) AS strike_rate, {match_info}
FROM inn JOIN matches m ON inn.match_id = m.match_id
{f"WHERE inn.player = {lit(pname)}" if pname else ""}
ORDER BY inn.runs {'ASC' if lowest else 'DESC'}, inn.balls ASC LIMIT {limit}
"""
        rows = _query(sql)
        cols = ["player", "score", "balls", "fours", "sixes", "strike_rate", "team", "opposition", "venue", "date", "competition"]
        table = [[r["player"], f"{r['runs']}{'*' if r['outs'] == 0 else ''}", r["balls"], r["fours"], r["sixes"],
                  r["strike_rate"], r["team"], _opp(r, r["team"]), r["venue"], r["date"], r["event_name"]] for r in rows]
        title = ("Lowest" if lowest else "Highest") + " individual scores"
    elif kind == "bowling_figures":
        sql = f"""{_bowling_innings_ctes(scope, "'all'", [pname] if pname else None)}
SELECT inn.player, inn.wkts, inn.runs, inn.balls, inn.team, {match_info}
FROM inn JOIN matches m ON inn.match_id = m.match_id
WHERE inn.balls > 0 {f"AND inn.player = {lit(pname)}" if pname else ""}
ORDER BY inn.wkts DESC, inn.runs ASC LIMIT {limit}
"""
        rows = _query(sql)
        cols = ["player", "figures", "overs", "economy", "team", "opposition", "venue", "date", "competition"]
        table = [[r["player"], f"{r['wkts']}/{r['runs']}", _overs(r["balls"]), 6.0 * r["runs"] / r["balls"],
                  r["team"], _opp(r, r["team"]), r["venue"], r["date"], r["event_name"]] for r in rows]
        title = "Best bowling figures in an innings"
    else:
        clauses = scope.match_clauses() + scope.ball_clauses("batting") + [NOT_SUPER_OVER_SQL]
        where = "WHERE " + " AND ".join(clauses)
        sql = f"""
WITH t AS (
    SELECT d.match_id, d.innings_num, d.batting_team, SUM(d.runs_total) AS total,
           SUM(CAST(d.is_wicket AND d.wicket_kind NOT IN {NOT_DISMISSALS_SQL} AS INTEGER)) AS wickets
    FROM deliveries d JOIN matches m ON d.match_id = m.match_id {where}
    GROUP BY d.match_id, d.innings_num, d.batting_team
)
SELECT t.*, {match_info}, m.match_type FROM t JOIN matches m ON t.match_id = m.match_id
{"WHERE t.wickets >= 10" if lowest else ""}
ORDER BY t.total {'ASC' if lowest else 'DESC'} LIMIT {limit}
"""
        rows = _query(sql)
        cols = ["team", "total", "opposition", "innings", "venue", "date", "competition", "format"]
        table = [[r["batting_team"], f"{r['total']}/{r['wickets']}" if r["wickets"] < 10 else str(r["total"]),
                  _opp(r, r["batting_team"]), r["innings_num"], r["venue"], r["date"], r["event_name"], r["match_type"]]
                 for r in rows]
        title = ("Lowest all-out" if lowest else "Highest") + " team totals"
        if lowest:
            scope.notes.append("Lowest totals only include innings where the side was bowled out.")
    if scope.applied:
        title += " — " + ", ".join(str(v) for v in scope.applied.values())
    return _result(title, cols, table, scope)


def _opp(r: dict, team: str | None) -> str | None:
    if team is None:
        return None
    return r["team2"] if r["team1"] == team else r["team1"]


@_tool
def team_stats(team: str, opposition: str | None = None, split_by: str | None = None, **filters) -> dict:
    """A team's results record (won/lost/win %, batting first vs chasing, toss),
    optionally against one opponent (head-to-head) and/or split by
    season/year/format/competition/opposition/venue."""
    split_by = split_by.lower() if split_by else None
    allowed = ("season", "year", "format", "competition", "opposition", "venue")
    if split_by and split_by not in allowed:
        raise ValueError(f"team_stats split_by must be one of: {', '.join(allowed)}")
    scope = build_scope(team=team, opposition=opposition, default_gender="male", **filters)
    T = lit_list(scope.team)
    other = f"(CASE WHEN m.team1 IN {T} THEN m.team2 ELSE m.team1 END)"
    key = {
        None: "'all'", "season": "m.season", "year": YEAR_SQL, "format": FORMAT_LABEL_SQL,
        "competition": "COALESCE(m.event_name, 'Bilateral / other')", "opposition": _franchise_case(other),
        "venue": "trim(regexp_replace(split_part(m.venue, ',', 1), '\\.\\s*', ' ', 'g'))",
    }[split_by]
    clauses = scope.match_clauses()
    sql = f"""
WITH m AS MATERIALIZED (SELECT m.* FROM matches m WHERE {' AND '.join(clauses)}),
fb AS (
    SELECT d.match_id, arg_min(d.batting_team, d.innings_num) AS first_bat
    FROM deliveries d WHERE d.match_id IN (SELECT match_id FROM m) GROUP BY d.match_id
),
x AS (SELECT m.*, fb.first_bat, COALESCE(CAST(({key}) AS VARCHAR), 'n/a') AS k FROM m LEFT JOIN fb USING (match_id))
SELECT k, COUNT(*) AS matches,
       SUM(CAST(winner IN {T} AS INTEGER)) AS won,
       SUM(CAST(winner IS NOT NULL AND winner NOT IN {T} AS INTEGER)) AS lost,
       SUM(CAST(winner IS NULL AS INTEGER)) AS no_result,
       SUM(CAST(first_bat IN {T} AS INTEGER)) AS batted_first,
       SUM(CAST(first_bat IN {T} AND winner IN {T} AS INTEGER)) AS won_batting_first,
       SUM(CAST(first_bat IS NOT NULL AND first_bat NOT IN {T} AS INTEGER)) AS chased,
       SUM(CAST(first_bat IS NOT NULL AND first_bat NOT IN {T} AND winner IN {T} AS INTEGER)) AS won_chasing,
       SUM(CAST(toss_winner IN {T} AS INTEGER)) AS tosses_won,
       MIN(date) AS first_date, MAX(date) AS last_date
FROM x GROUP BY k
"""
    rows = _query(sql)
    cols = ["matches", "won", "lost", "no_result", "win_pct", "won_batting_first", "batted_first",
            "won_chasing", "chased", "tosses_won"]
    for r in rows:
        decided = (r["won"] or 0) + (r["lost"] or 0)
        r["win_pct"] = 100.0 * r["won"] / decided if decided else None
    notes = ["no_result counts ties, draws and abandoned games; win_pct is won / (won + lost)."]

    label = f"{scope.applied['team']}" + (f" vs {scope.applied['opposition']}" if opposition else "")
    rest = ", ".join(str(v) for k, v in scope.applied.items() if k not in ("team", "opposition", "gender"))
    title = f"Results — {label}" + (f" — {rest}" if rest else "")
    if not rows:
        return _result(title, cols, [], scope, ["No matches found with these filters."])

    if split_by:
        if split_by in ("season", "year"):
            rows = sorted(rows, key=lambda r: r["first_date"] or "")
        else:
            rows = sorted(rows, key=lambda r: -r["matches"])
        keys = _season_labels(rows) if split_by == "season" else [r["k"] for r in rows]
        table = [[k] + [r[c] for c in cols] for k, r in zip(keys, rows)]
        return _result(f"{title} — by {split_by}", [split_by] + cols, table, scope, notes,
                       highlights=_highlights([split_by] + cols, table, split_by, "team"))

    recent = _query(f"""
SELECT m.date, {other} AS opposition, m.winner, m.win_by_runs, m.win_by_wickets, m.event_name, m.venue
FROM matches m WHERE {' AND '.join(clauses)} ORDER BY m.date DESC LIMIT 5
""")
    recent_rows = []
    for r in recent:
        if r["winner"] is None:
            res = "no result / tie / draw"
        else:
            won = r["winner"] in scope.team
            margin = (f"by {int(r['win_by_runs'])} runs" if r["win_by_runs"] else
                      f"by {int(r['win_by_wickets'])} wickets" if r["win_by_wickets"] else "")
            res = f"{'won' if won else 'lost'} {margin}".strip()
        recent_rows.append({"date": r["date"], "opposition": r["opposition"], "result": res,
                            "competition": r["event_name"], "venue": r["venue"]})
    return _result(title, cols, [[r[c] for c in cols] for r in rows], scope, notes, recent_results=recent_rows)


@_tool
def venue_stats(venue: str, **filters) -> dict:
    """How a ground plays, per format: average 1st/2nd-innings scores, bat-first
    vs chasing wins, toss decisions, highest total."""
    scope = build_scope(venue=venue, default_gender="male", **filters)
    clauses = scope.match_clauses()
    sql = f"""
WITH m AS MATERIALIZED (SELECT m.*, {FORMAT_LABEL_SQL} AS fmt FROM matches m WHERE {' AND '.join(clauses)}),
inns AS (
    SELECT d.match_id, d.innings_num, d.batting_team, SUM(d.runs_total) AS total
    FROM deliveries d WHERE d.match_id IN (SELECT match_id FROM m)
    GROUP BY d.match_id, d.innings_num, d.batting_team
),
fb AS (SELECT match_id, arg_min(batting_team, innings_num) AS first_bat FROM inns GROUP BY match_id),
x AS (SELECT m.*, fb.first_bat FROM m LEFT JOIN fb USING (match_id))
SELECT x.fmt AS format, COUNT(*) AS matches,
       SUM(CAST(winner IS NOT NULL AND winner = first_bat AS INTEGER)) AS won_batting_first,
       SUM(CAST(winner IS NOT NULL AND winner <> first_bat AS INTEGER)) AS won_chasing,
       SUM(CAST(winner IS NULL AS INTEGER)) AS no_result,
       SUM(CAST(toss_decision = 'bat' AS INTEGER)) AS toss_chose_bat,
       SUM(CAST(toss_winner = winner AS INTEGER)) AS toss_winner_won,
       (SELECT AVG(total) FROM inns i JOIN m m2 USING (match_id) WHERE i.innings_num = 1 AND m2.fmt = x.fmt) AS avg_first_innings,
       (SELECT AVG(total) FROM inns i JOIN m m2 USING (match_id) WHERE i.innings_num = 2 AND m2.fmt = x.fmt) AS avg_second_innings,
       (SELECT arg_max(CAST(total AS VARCHAR) || ' — ' || batting_team || ' (' || m2.date || ')', total)
          FROM inns i JOIN m m2 USING (match_id) WHERE m2.fmt = x.fmt) AS highest_total
FROM x GROUP BY x.fmt ORDER BY matches DESC
"""
    rows = _query(sql)
    for r in rows:
        decided = (r["won_batting_first"] or 0) + (r["won_chasing"] or 0)
        r["bat_first_win_pct"] = 100.0 * r["won_batting_first"] / decided if decided else None
        r["favours"] = (None if not decided else "batting first" if r["bat_first_win_pct"] > 55
                        else "chasing" if r["bat_first_win_pct"] < 45 else "neither (roughly even)")
    cols = ["format", "matches", "won_batting_first", "won_chasing", "bat_first_win_pct", "favours",
            "avg_first_innings", "avg_second_innings", "no_result", "toss_chose_bat", "toss_winner_won",
            "highest_total"]
    title = f"Venue — {scope.applied['venue']}"
    rest = ", ".join(str(v) for k, v in scope.applied.items() if k not in ("venue", "gender"))
    if rest:
        title += f" — {rest}"
    notes = [
        "Judge batting first vs chasing by won_batting_first vs won_chasing (bat_first_win_pct; 'favours' "
        "uses a 55/45 split). avg_second_innings is always lower because chasing sides stop once they pass "
        "the target -- it says nothing about which side is favoured.",
        "Figures are per format; averages include rain-affected innings.",
    ]
    if not rows:
        notes = ["No matches at this venue with these filters."]
    return _result(title, cols, [[r[c] for c in cols] for r in rows], scope, notes)


@_tool
def matchup(batter: str | None = None, bowler: str | None = None, limit: int = 10, min_balls: int | None = None,
            **filters) -> dict:
    """Batter vs bowler head-to-head. Give both for one matchup; give only a
    batter to list the bowlers who've troubled them most (or only a bowler
    to list the batters they've dismissed most)."""
    if not batter and not bowler:
        raise ValueError("matchup needs a batter, a bowler, or both.")
    g = catalog.resolve_gender(filters.get("gender")) if filters.get("gender") else None
    bat = catalog.resolve_player(batter, gender=g) if batter else None
    bowl = catalog.resolve_player(bowler, gender=g) if bowler else None
    scope = build_scope(**filters)
    for p in (bat, bowl):
        if p and p.note:
            scope.notes.insert(0, p.note)

    where = []
    if bat:
        where.append(f"d.batter = {lit(bat.name)}")
    if bowl:
        where.append(f"d.bowler = {lit(bowl.name)}")
    group = "batter, bowler"
    sql = f"""
WITH {_ball_ctes(scope, 'batting', "'all'", ' AND '.join(where))},
agg AS (
    SELECT {group}, COUNT(DISTINCT match_id) AS matches,
           SUM(CAST(faced AS INTEGER)) AS balls, SUM(runs_batter) AS runs,
           SUM(CAST(faced AND runs_batter = 0 AS INTEGER)) AS dots,
           SUM(CAST(runs_batter = 4 AS INTEGER)) AS fours, SUM(CAST(runs_batter = 6 AS INTEGER)) AS sixes
    FROM b GROUP BY {group}
),
outs AS (
    SELECT {group}, COUNT(*) AS dismissals FROM w
    WHERE kind IN {CREDITED_SQL} AND player_out = batter GROUP BY {group}
)
SELECT agg.*, COALESCE(outs.dismissals, 0) AS dismissals
FROM agg LEFT JOIN outs USING (batter, bowler)
"""
    rows = _query(sql)
    for r in rows:
        r["strike_rate"] = 100.0 * r["runs"] / r["balls"] if r["balls"] else None
        r["average"] = r["runs"] / r["dismissals"] if r["dismissals"] else None
        r["dot_pct"] = 100.0 * r["dots"] / r["balls"] if r["balls"] else None
    cols = ["balls", "runs", "dismissals", "strike_rate", "average", "dot_pct", "fours", "sixes", "matches"]
    notes = ["Dismissals count only wickets credited to the bowler (run-outs excluded)."]

    if bat and bowl:
        title = f"{bat.name} vs {bowl.name}"
        if not rows:
            return _result(title, cols, [], scope, notes + [
                f"{bat.name} never faced {bowl.name} with these filters."])
        return _result(title, cols, [[rows[0][c] for c in cols]], scope, notes)

    limit = max(1, min(int(limit or 10), MAX_LIMIT))
    min_b = int(min_balls) if min_balls is not None else 12
    rows = [r for r in rows if (r["balls"] or 0) >= min_b]
    rows.sort(key=lambda r: (-r["dismissals"], r["average"] if r["average"] is not None else 1e9, -r["balls"]))
    rows = rows[:limit]
    other = "bowler" if bat else "batter"
    title = f"{bat.name}: toughest bowlers" if bat else f"{bowl.name}: batters dismissed most"
    notes.append(f"Only pairs with at least {min_b} balls; ranked by dismissals, then average.")
    return _result(title, [other] + cols, [[r[other]] + [r[c] for c in cols] for r in rows], scope, notes)


@_tool
def lookup(kind: str, name: str) -> dict:
    """See how a name resolves, with candidates -- for checking an ambiguous
    player/team/venue/competition before answering."""
    kind = (kind or "player").lower()
    try:
        if kind == "player":
            ranked = catalog.rank_players(name, limit=8)
            return {"kind": kind, "query": name,
                    "candidates": [p.describe() for _, _, p in ranked]}
        if kind in ("competition", "tournament"):
            c = catalog.resolve_competition(name)
            return {"kind": kind, "query": name, "resolved": c.events, "note": c.note}
        if kind == "team":
            t = catalog.resolve_team(name)
            return {"kind": kind, "query": name, "resolved": t.names, "note": t.note}
        if kind == "venue":
            v = catalog.resolve_venue(name)
            return {"kind": kind, "query": name, "resolved": v.venues, "note": v.note}
    except ResolutionError as e:
        return e.to_dict()
    raise ValueError("kind must be player, competition, team or venue.")
