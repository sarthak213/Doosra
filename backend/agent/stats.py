"""
Higher-level cricket statistics tools.

These exist because letting the LLM reconstruct correct SQL for things like
"batting average" from scratch, every time, is how we got a real bug: an
earlier query counted dismissals by grouping on `batter` and checking
`is_wicket`, which silently miscounts run-outs (a run-out can dismiss the
*non-striker*, not the batter facing that ball). These functions encode the
correct formula once, tested, so the agent calls a tool instead of
re-deriving cricket scoring rules under time pressure.

All player/tournament arguments are expected to already be resolved to their
exact database strings (via search_player / search_tournament) -- these
functions don't do fuzzy matching themselves.
"""

from .tools import _get_connection, run_sql  # noqa: F401 (run_sql re-exported for convenience)

# Dismissal kinds actually credited to the bowler. Everything else (run out,
# retired hurt/out/not out, obstructing the field, handled the ball, hit the
# ball twice) is excluded from bowling wickets.
_BOWLER_CREDITED_KINDS = ("bowled", "caught", "caught and bowled", "lbw", "stumped", "hit wicket")
_credited_sql = ", ".join(f"'{k}'" for k in _BOWLER_CREDITED_KINDS)

# The deliveries schema changed after the first release: per-type extra
# amount columns (extra_wides/noballs/byes/legbyes/penalty) and the
# deliveries_wickets table were added so a ball with multiple extra types
# (e.g. a no-ball that also runs byes) or multiple wickets isn't
# undercounted. Databases built before that change lack them, so probe the
# actual columns once per process and fall back to the older approximation
# (via the single extra_type string) against a not-yet-re-ingested database.
_delivery_columns_cache: set[str] | None = None


def _deliveries_columns() -> set[str]:
    global _delivery_columns_cache
    if _delivery_columns_cache is None:
        con = _get_connection()
        try:
            _delivery_columns_cache = {row[0] for row in con.execute("DESCRIBE deliveries").fetchall()}
        finally:
            con.close()
    return _delivery_columns_cache


def _has_wickets_table() -> bool:
    con = _get_connection()
    try:
        rows = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'deliveries_wickets'"
        ).fetchone()
        return bool(rows[0])
    finally:
        con.close()


def invalidate_schema_caches() -> None:
    """Call after re-ingesting the DB so column probes re-run."""
    global _delivery_columns_cache
    _delivery_columns_cache = None


def _filters_sql(
    tournament: str | None,
    match_type: str | None,
    gender: str | None = None,
    min_over: int | None = None,
    max_over: int | None = None,
    delivery_alias: str = "d",
):
    """Builds a WHERE clause fragment + params for the optional filters shared
    by the stat functions. `delivery_alias` names the deliveries-like table
    the over filters apply to ("d", or "dw" when filtering deliveries_wickets
    directly). Returns (sql_fragment, params)."""
    clauses = []
    params = []
    if tournament:
        clauses.append("m.event_name = ?")
        params.append(tournament)
    if match_type:
        clauses.append("m.match_type = ?")
        params.append(match_type)
    if gender:
        clauses.append("m.gender = ?")
        params.append(gender)
    if min_over is not None:
        clauses.append(f"{delivery_alias}.over_num >= ?")
        params.append(min_over)
    if max_over is not None:
        clauses.append(f"{delivery_alias}.over_num <= ?")
        params.append(max_over)
    sql = (" AND " + " AND ".join(clauses)) if clauses else ""
    return sql, params


def get_batting_stats(
    player: str,
    tournament: str | None = None,
    match_type: str | None = None,
    gender: str | None = None,
    min_over: int | None = None,
    max_over: int | None = None,
) -> dict:
    """
    Correct batting figures for a player: runs, balls faced, dismissals,
    average, strike rate, fours, sixes, matches played.

    - Balls faced excludes wides (a batter doesn't "face" a wide) but
      includes no-balls, matching standard cricket scoring convention.
    - Dismissals are counted via the dismissed player, NOT via `batter` +
      `is_wicket` -- a run-out can dismiss the non-striker, so counting by
      the facing batter alone undercounts some players' dismissals.
    - `average` is null when the player has never been dismissed in the
      filtered data (undefined, not infinite).
    - `gender` ("male"/"female") filters on matches.gender -- the database
      mixes men's and women's cricket, so set it unless the question is
      explicitly about both.
    """
    filt_sql, filt_params = _filters_sql(tournament, match_type, gender, min_over, max_over)
    wickets_filt_sql, wickets_filt_params = _filters_sql(
        tournament, match_type, gender, min_over, max_over, delivery_alias="dw"
    )

    # With the new per-type extra columns, "was a wide" is exactly
    # extra_wides IS NULL; on an older schema fall back to extra_type.
    new_schema = "extra_wides" in _deliveries_columns()
    balls_faced_expr = "d.extra_wides IS NULL" if new_schema else "(d.extra_type IS NULL OR d.extra_type != 'wides')"

    con = _get_connection()
    try:
        row = con.execute(
            f"""
            SELECT
                SUM(CASE WHEN d.batter = ? THEN d.runs_batter ELSE 0 END) AS runs,
                COUNT(CASE WHEN d.batter = ? AND {balls_faced_expr} THEN 1 END) AS balls_faced,
                COUNT(CASE WHEN d.is_wicket AND d.player_dismissed = ? THEN 1 END) AS dismissals,
                COUNT(CASE WHEN d.batter = ? AND d.runs_batter = 4 THEN 1 END) AS fours,
                COUNT(CASE WHEN d.batter = ? AND d.runs_batter = 6 THEN 1 END) AS sixes,
                COUNT(DISTINCT CASE WHEN d.batter = ? THEN d.match_id END) AS matches
            FROM deliveries d
            JOIN matches m ON d.match_id = m.match_id
            WHERE (d.batter = ? OR d.player_dismissed = ?){filt_sql}
            """,
            [player, player, player, player, player, player, player, player] + filt_params,
        ).fetchone()

        # On the new schema, deliveries_wickets has every dismissal on a ball
        # (deliveries.player_dismissed only records the first), so count
        # dismissals there instead when it's available.
        if _has_wickets_table():
            dismissals = con.execute(
                f"""
                SELECT COUNT(*)
                FROM deliveries_wickets dw
                JOIN matches m ON dw.match_id = m.match_id
                WHERE dw.player_out = ?{wickets_filt_sql}
                """,
                [player] + wickets_filt_params,
            ).fetchone()[0]
        else:
            dismissals = row[2]
    finally:
        con.close()

    runs, balls_faced, _, fours, sixes, matches = row
    runs = runs or 0
    balls_faced = balls_faced or 0
    dismissals = dismissals or 0

    return {
        "player": player,
        "runs": runs,
        "balls_faced": balls_faced,
        "dismissals": dismissals,
        "average": round(runs / dismissals, 2) if dismissals > 0 else None,
        "strike_rate": round(100.0 * runs / balls_faced, 2) if balls_faced > 0 else None,
        "fours": fours or 0,
        "sixes": sixes or 0,
        "matches": matches or 0,
        "filters": {"tournament": tournament, "match_type": match_type, "gender": gender, "min_over": min_over, "max_over": max_over},
    }


def get_bowling_stats(
    player: str,
    tournament: str | None = None,
    match_type: str | None = None,
    gender: str | None = None,
    min_over: int | None = None,
    max_over: int | None = None,
) -> dict:
    """
    Correct bowling figures for a player: wickets, runs conceded, balls
    bowled, economy, average, strike rate, matches played.

    - Runs conceded excludes byes/leg-byes (not the bowler's fault) but
      includes wides/no-balls (are the bowler's fault).
    - Balls bowled excludes wides/no-balls (they're re-bowled, don't count
      toward the over).
    - Wickets only count dismissal kinds actually credited to the bowler
      (bowled, caught, caught and bowled, lbw, stumped, hit wicket) --
      run out, retired hurt/out/not out, obstructing the field, handled the
      ball, and hit the ball twice are all excluded.
    - `gender` ("male"/"female") filters on matches.gender -- the database
      mixes men's and women's cricket, so set it unless the question is
      explicitly about both.
    """
    filt_sql, filt_params = _filters_sql(tournament, match_type, gender, min_over, max_over)

    new_schema = "extra_wides" in _deliveries_columns()
    if new_schema:
        # Exact even when a ball carries more than one extra type (e.g. a
        # no-ball that also runs byes): charge the bowler everything except
        # byes, leg-byes, and penalties (none of those debit the bowler), and
        # a ball is legal iff it's neither a wide nor a no-ball.
        runs_conceded_expr = (
            "d.runs_total - COALESCE(d.extra_byes, 0) - COALESCE(d.extra_legbyes, 0) - COALESCE(d.extra_penalty, 0)"
        )
        balls_bowled_expr = "d.extra_wides IS NULL AND d.extra_noballs IS NULL"
    else:
        runs_conceded_expr = "CASE WHEN d.extra_type IN ('byes', 'legbyes') THEN d.runs_total - d.runs_extras ELSE d.runs_total END"
        balls_bowled_expr = "d.extra_type IS NULL OR d.extra_type NOT IN ('wides', 'noballs')"

    con = _get_connection()
    try:
        row = con.execute(
            f"""
            SELECT
                SUM({runs_conceded_expr}) AS runs_conceded,
                COUNT(CASE WHEN {balls_bowled_expr} THEN 1 END) AS balls_bowled,
                COUNT(CASE WHEN d.is_wicket AND d.wicket_kind IN ({_credited_sql}) THEN 1 END) AS wickets,
                COUNT(DISTINCT d.match_id) AS matches
            FROM deliveries d
            JOIN matches m ON d.match_id = m.match_id
            WHERE d.bowler = ?{filt_sql}
            """,
            [player] + filt_params,
        ).fetchone()

        # On the new schema, count wickets from deliveries_wickets (which has
        # every wicket on a ball -- deliveries.wicket_kind only records the
        # first) joined back to deliveries for the bowler.
        if _has_wickets_table():
            wickets = con.execute(
                f"""
                SELECT COUNT(*)
                FROM deliveries_wickets dw
                JOIN deliveries d
                  ON dw.match_id = d.match_id AND dw.innings_num = d.innings_num
                 AND dw.over_num = d.over_num AND dw.ball_in_over = d.ball_in_over
                JOIN matches m ON d.match_id = m.match_id
                WHERE d.bowler = ? AND dw.kind IN ({_credited_sql}){filt_sql}
                """,
                [player] + filt_params,
            ).fetchone()[0]
        else:
            wickets = row[2]
    finally:
        con.close()

    runs_conceded, balls_bowled, _, matches = row
    runs_conceded = runs_conceded or 0
    balls_bowled = balls_bowled or 0
    wickets = wickets or 0
    overs = balls_bowled / 6.0 if balls_bowled else 0

    return {
        "player": player,
        "wickets": wickets,
        "runs_conceded": runs_conceded,
        "balls_bowled": balls_bowled,
        "overs": round(overs, 1),
        "economy": round(runs_conceded / overs, 2) if overs > 0 else None,
        "average": round(runs_conceded / wickets, 2) if wickets > 0 else None,
        "strike_rate": round(balls_bowled / wickets, 2) if wickets > 0 else None,
        "matches": matches or 0,
        "filters": {"tournament": tournament, "match_type": match_type, "gender": gender, "min_over": min_over, "max_over": max_over},
    }


def get_fielding_stats(
    player: str,
    tournament: str | None = None,
    match_type: str | None = None,
    gender: str | None = None,
) -> dict:
    """
    Fielding figures for a player: catches, stumpings, run-outs (as a fielder,
    including keeper), matches played.

    Requires the deliveries_wickets table (added in the current schema); if
    the database predates it, returns an error explaining it must be
    re-ingested rather than silently returning zeros.
    """
    if not _has_wickets_table():
        return {
            "error": "Fielding stats need the deliveries_wickets table, which this database "
            "lacks -- it was built with an older ingest. Re-run backend/ingest/build_db.py "
            "against the raw Cricsheet JSON to rebuild it."
        }

    filt_sql, filt_params = _filters_sql(tournament, match_type, gender)
    # fielders is a JSON-encoded array of names, e.g. '["A de Villiers"]' --
    # cast it to a real array and test exact membership.
    fielder_match = "list_contains(CAST(dw.fielders AS VARCHAR[]), ?)"

    con = _get_connection()
    try:
        row = con.execute(
            f"""
            SELECT
                COUNT(CASE WHEN dw.kind IN ('caught', 'caught and bowled') AND {fielder_match} THEN 1 END) AS catches,
                COUNT(CASE WHEN dw.kind = 'stumped' AND {fielder_match} THEN 1 END) AS stumpings,
                COUNT(CASE WHEN dw.kind = 'run out' AND {fielder_match} THEN 1 END) AS run_outs,
                COUNT(DISTINCT CASE WHEN {fielder_match} THEN dw.match_id END) AS matches
            FROM deliveries_wickets dw
            JOIN matches m ON dw.match_id = m.match_id
            WHERE dw.fielders IS NOT NULL{filt_sql}
            """,
            [player, player, player, player] + filt_params,
        ).fetchone()
    finally:
        con.close()

    catches, stumpings, run_outs, matches = row
    return {
        "player": player,
        "catches": catches or 0,
        "stumpings": stumpings or 0,
        "run_outs": run_outs or 0,
        "matches": matches or 0,
        "filters": {"tournament": tournament, "match_type": match_type, "gender": gender},
    }


def get_head_to_head(
    team1: str,
    team2: str,
    tournament: str | None = None,
    match_type: str | None = None,
    gender: str | None = None,
) -> dict:
    """
    Head-to-head record between two teams: matches played, each side's wins,
    and draws/no-results, from the matches table.

    Team names must be the exact strings used in the database (e.g. "India",
    "Australia"); resolve them with run_sql on DISTINCT team1/team2 if unsure.
    """
    filt_sql, filt_params = _filters_sql(tournament, match_type, gender)

    con = _get_connection()
    try:
        row = con.execute(
            f"""
            SELECT
                COUNT(*) AS played,
                COUNT(CASE WHEN m.winner = ? THEN 1 END) AS team1_wins,
                COUNT(CASE WHEN m.winner = ? THEN 1 END) AS team2_wins,
                COUNT(CASE WHEN m.winner IS NULL OR m.winner = '' THEN 1 END) AS no_result,
                MAX(m.date) AS last_meeting
            FROM matches m
            WHERE ((m.team1 = ? AND m.team2 = ?) OR (m.team1 = ? AND m.team2 = ?)){filt_sql}
            """,
            [team1, team2, team1, team2, team2, team1] + filt_params,
        ).fetchone()
    finally:
        con.close()

    played, team1_wins, team2_wins, no_result, last_meeting = row
    return {
        "team1": team1,
        "team2": team2,
        "played": played or 0,
        "team1_wins": team1_wins or 0,
        "team2_wins": team2_wins or 0,
        "no_result_or_draw": no_result or 0,
        "last_meeting": last_meeting,
        "filters": {"tournament": tournament, "match_type": match_type, "gender": gender},
    }


def get_venue_stats(
    venue: str,
    tournament: str | None = None,
    match_type: str | None = None,
    gender: str | None = None,
) -> dict:
    """
    Venue record: matches hosted, highest innings total (with team), and
    average first-innings total.

    The venue string must match how the database spells it (e.g. "Wankhede
    Stadium"); resolve it first with run_sql on DISTINCT venue if unsure.
    """
    filt_sql, filt_params = _filters_sql(tournament, match_type, gender)

    con = _get_connection()
    try:
        matches_row = con.execute(
            f"""
            SELECT COUNT(*) FROM matches m WHERE m.venue = ?{filt_sql}
            """,
            [venue] + filt_params,
        ).fetchone()
        matches_played = matches_row[0] or 0

        highest = con.execute(
            f"""
            SELECT t.total, t.batting_team, m.date
            FROM (
                SELECT match_id, innings_num, batting_team, SUM(runs_total) AS total
                FROM deliveries GROUP BY 1, 2, 3
            ) t
            JOIN matches m ON t.match_id = m.match_id
            WHERE m.venue = ?{filt_sql}
            ORDER BY t.total DESC
            LIMIT 1
            """,
            [venue] + filt_params,
        ).fetchone()

        avg_first = con.execute(
            f"""
            SELECT AVG(t.total)
            FROM (
                SELECT match_id, SUM(runs_total) AS total
                FROM deliveries WHERE innings_num = 1 GROUP BY 1
            ) t
            JOIN matches m ON t.match_id = m.match_id
            WHERE m.venue = ?{filt_sql}
            """,
            [venue] + filt_params,
        ).fetchone()[0]
    finally:
        con.close()

    return {
        "venue": venue,
        "matches_played": matches_played,
        "highest_total": {
            "runs": highest[0],
            "team": highest[1],
            "date": highest[2],
        } if highest else None,
        "average_first_innings_total": round(avg_first, 1) if avg_first is not None else None,
        "filters": {"tournament": tournament, "match_type": match_type, "gender": gender},
    }


def get_season_trend(
    player: str,
    stat_type: str = "batting",
    tournament: str | None = None,
    match_type: str | None = None,
    gender: str | None = None,
) -> dict:
    """
    Per-season trend rows for a player, ready to hand to plot_chart:
    {"columns": [...], "rows": [[season, ...], ...]}.

    stat_type "batting" gives season, matches, runs, balls_faced, average,
    strike_rate; "bowling" gives season, matches, wickets, balls_bowled,
    economy, average. Uses the same correct formulas as
    get_batting_stats/get_bowling_stats, just grouped by season.
    """
    if stat_type not in ("batting", "bowling"):
        return {"error": "stat_type must be 'batting' or 'bowling'"}

    filt_sql, filt_params = _filters_sql(tournament, match_type, gender)
    new_schema = "extra_wides" in _deliveries_columns()

    if stat_type == "batting":
        balls_faced_expr = "d.extra_wides IS NULL" if new_schema else "(d.extra_type IS NULL OR d.extra_type != 'wides')"
        columns = ["season", "matches", "runs", "balls_faced", "average", "strike_rate"]
        con = _get_connection()
        try:
            rows = con.execute(
                f"""
                SELECT
                    m.season,
                    COUNT(DISTINCT d.match_id) AS matches,
                    SUM(d.runs_batter) AS runs,
                    COUNT(CASE WHEN {balls_faced_expr} THEN 1 END) AS balls_faced,
                    NULL AS average,
                    NULL AS strike_rate
                FROM deliveries d
                JOIN matches m ON d.match_id = m.match_id
                WHERE d.batter = ?{filt_sql}
                GROUP BY m.season
                ORDER BY m.season
                """,
                [player] + filt_params,
            ).fetchall()
            if _has_wickets_table():
                dismissals = dict(con.execute(
                    f"""
                    SELECT m.season, COUNT(*)
                    FROM deliveries_wickets dw
                    JOIN matches m ON dw.match_id = m.match_id
                    WHERE dw.player_out = ?{filt_sql}
                    GROUP BY m.season
                    """,
                    [player] + filt_params,
                ).fetchall())
            else:
                dismissals = dict(con.execute(
                    f"""
                    SELECT m.season, COUNT(*)
                    FROM deliveries d
                    JOIN matches m ON d.match_id = m.match_id
                    WHERE d.is_wicket AND d.player_dismissed = ?{filt_sql}
                    GROUP BY m.season
                    """,
                    [player] + filt_params,
                ).fetchall())
        finally:
            con.close()

        out_rows = []
        for season, matches, runs, balls_faced, _, _ in rows:
            d = dismissals.get(season, 0)
            out_rows.append([
                season,
                matches or 0,
                runs or 0,
                balls_faced or 0,
                round((runs or 0) / d, 2) if d else None,
                round(100.0 * (runs or 0) / balls_faced, 2) if balls_faced else None,
            ])
        return {"columns": columns, "rows": out_rows}

    # bowling
    if new_schema:
        runs_conceded_expr = (
            "d.runs_total - COALESCE(d.extra_byes, 0) - COALESCE(d.extra_legbyes, 0) - COALESCE(d.extra_penalty, 0)"
        )
        balls_bowled_expr = "d.extra_wides IS NULL AND d.extra_noballs IS NULL"
    else:
        runs_conceded_expr = "CASE WHEN d.extra_type IN ('byes', 'legbyes') THEN d.runs_total - d.runs_extras ELSE d.runs_total END"
        balls_bowled_expr = "d.extra_type IS NULL OR d.extra_type NOT IN ('wides', 'noballs')"

    con = _get_connection()
    try:
        rows = con.execute(
            f"""
            SELECT
                m.season,
                COUNT(DISTINCT d.match_id) AS matches,
                SUM({runs_conceded_expr}) AS runs_conceded,
                COUNT(CASE WHEN {balls_bowled_expr} THEN 1 END) AS balls_bowled
            FROM deliveries d
            JOIN matches m ON d.match_id = m.match_id
            WHERE d.bowler = ?{filt_sql}
            GROUP BY m.season
            ORDER BY m.season
            """,
            [player] + filt_params,
        ).fetchall()
        if _has_wickets_table():
            wickets = dict(con.execute(
                f"""
                SELECT m.season, COUNT(*)
                FROM deliveries_wickets dw
                JOIN deliveries d
                  ON dw.match_id = d.match_id AND dw.innings_num = d.innings_num
                 AND dw.over_num = d.over_num AND dw.ball_in_over = d.ball_in_over
                JOIN matches m ON d.match_id = m.match_id
                WHERE d.bowler = ? AND dw.kind IN ({_credited_sql}){filt_sql}
                GROUP BY m.season
                """,
                [player] + filt_params,
            ).fetchall())
        else:
            wickets = dict(con.execute(
                f"""
                SELECT m.season, COUNT(*)
                FROM deliveries d
                JOIN matches m ON d.match_id = m.match_id
                WHERE d.bowler = ? AND d.is_wicket AND d.wicket_kind IN ({_credited_sql}){filt_sql}
                GROUP BY m.season
                """,
                [player] + filt_params,
            ).fetchall())
    finally:
        con.close()

    columns = ["season", "matches", "wickets", "overs", "economy", "average"]
    out_rows = []
    for season, matches, runs_conceded, balls_bowled in rows:
        w = wickets.get(season, 0)
        overs = (balls_bowled or 0) / 6.0
        out_rows.append([
            season,
            matches or 0,
            w,
            round(overs, 1),
            round((runs_conceded or 0) / overs, 2) if overs else None,
            round((runs_conceded or 0) / w, 2) if w else None,
        ])
    return {"columns": columns, "rows": out_rows}


def get_matchup(
    batter: str,
    bowler: str,
    tournament: str | None = None,
    match_type: str | None = None,
    gender: str | None = None,
) -> dict:
    """
    Batter-vs-bowler matchup across all deliveries where this exact pair met:
    runs scored off the bat, balls faced (excluding wides), strike rate,
    dismissals of the batter, and bowler-credited wickets.
    """
    filt_sql, filt_params = _filters_sql(tournament, match_type, gender)
    new_schema = "extra_wides" in _deliveries_columns()
    balls_faced_expr = "d.extra_wides IS NULL" if new_schema else "(d.extra_type IS NULL OR d.extra_type != 'wides')"

    con = _get_connection()
    try:
        row = con.execute(
            f"""
            SELECT
                SUM(d.runs_batter) AS runs,
                COUNT(CASE WHEN {balls_faced_expr} THEN 1 END) AS balls_faced,
                COUNT(CASE WHEN d.is_wicket AND d.player_dismissed = ? THEN 1 END) AS dismissals,
                COUNT(CASE WHEN d.is_wicket AND d.player_dismissed = ?
                          AND d.wicket_kind IN ({_credited_sql}) THEN 1 END) AS wickets,
                COUNT(CASE WHEN {balls_faced_expr} AND d.runs_batter = 0 THEN 1 END) AS dots
            FROM deliveries d
            JOIN matches m ON d.match_id = m.match_id
            WHERE d.batter = ? AND d.bowler = ?{filt_sql}
            """,
            [batter, batter, batter, bowler] + filt_params,
        ).fetchone()

        # On the new schema, use deliveries_wickets for dismissals/wickets:
        # deliveries only records the FIRST wicket on a ball, so a caught
        # behind a run-out on the same ball would otherwise be invisible here.
        if _has_wickets_table():
            dismissals, wickets = con.execute(
                f"""
                SELECT
                    COUNT(CASE WHEN dw.player_out = ? THEN 1 END) AS dismissals,
                    COUNT(CASE WHEN dw.player_out = ? AND dw.kind IN ({_credited_sql}) THEN 1 END) AS wickets
                FROM deliveries_wickets dw
                JOIN deliveries d
                  ON dw.match_id = d.match_id AND dw.innings_num = d.innings_num
                 AND dw.over_num = d.over_num AND dw.ball_in_over = d.ball_in_over
                JOIN matches m ON d.match_id = m.match_id
                WHERE d.batter = ? AND d.bowler = ?{filt_sql}
                """,
                [batter, batter, batter, bowler] + filt_params,
            ).fetchone()
        else:
            dismissals, wickets = row[2], row[3]
    finally:
        con.close()

    runs, balls_faced, _, _, dots = row
    runs = runs or 0
    balls_faced = balls_faced or 0
    return {
        "batter": batter,
        "bowler": bowler,
        "balls_faced": balls_faced,
        "runs": runs,
        "strike_rate": round(100.0 * runs / balls_faced, 2) if balls_faced else None,
        "dismissals": dismissals or 0,
        "wickets": wickets or 0,
        "dots": dots or 0,
        "filters": {"tournament": tournament, "match_type": match_type, "gender": gender},
    }


_BATTING_METRICS = {"runs", "average", "strike_rate", "fours", "sixes", "matches", "balls_faced", "dismissals"}
_BOWLING_METRICS = {"wickets", "economy", "runs_conceded", "balls_bowled", "overs"}
# "average" and "strike_rate" exist for both batting and bowling with different
# meanings -- when ambiguous, compare_players needs an explicit `stat_type`.


def compare_players(
    players: list[str],
    metric: str,
    stat_type: str = "batting",
    tournament: str | None = None,
    match_type: str | None = None,
    gender: str | None = None,
) -> dict:
    """
    Computes one metric across multiple players, ready to hand straight to
    plot_chart. stat_type is "batting" or "bowling" -- required because
    metrics like "average" and "strike_rate" mean different things for each.

    Returns {"columns": ["player", metric], "rows": [[name, value], ...]} or
    {"error": "..."} if the metric/stat_type combination isn't recognized.
    """
    if stat_type == "batting":
        if metric not in _BATTING_METRICS:
            return {"error": f"Unknown batting metric '{metric}'. Valid: {sorted(_BATTING_METRICS)}"}
        stats_fn = get_batting_stats
    elif stat_type == "bowling":
        if metric not in _BOWLING_METRICS and metric not in {"average", "strike_rate"}:
            return {"error": f"Unknown bowling metric '{metric}'. Valid: {sorted(_BOWLING_METRICS | {'average', 'strike_rate'})}"}
        stats_fn = get_bowling_stats
    else:
        return {"error": "stat_type must be 'batting' or 'bowling'"}

    rows = []
    for player in players:
        result = stats_fn(player, tournament=tournament, match_type=match_type, gender=gender)
        rows.append([player, result.get(metric)])

    return {"columns": ["player", metric], "rows": rows}
