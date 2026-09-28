"""
Tools available to the agent. Each tool is a plain Python function with a
docstring/schema the LLM uses to decide when and how to call it.

Kept framework-agnostic (no LangChain/LangGraph decorators here) so they're
easy to test standalone and easy to wrap for whichever agent framework
graph.py ends up using.
"""

import re
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "cricket.duckdb"

# Only these statement types are allowed. Anything else (INSERT, UPDATE,
# DELETE, DROP, ATTACH, COPY, PRAGMA, etc.) is rejected before it ever
# touches the database.
_ALLOWED_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|COPY|PRAGMA|EXPORT|IMPORT)\b",
    re.IGNORECASE,
)
# Matches single-quoted SQL string literals (including escaped '' inside
# them) so the forbidden-keyword check can be run against a copy of the
# query with literals blanked out -- otherwise a legitimate value like a
# venue named "Drop Zone Stadium" would falsely trip the DROP check.
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


class SQLValidationError(Exception):
    pass


def _strip_string_literals(query: str) -> str:
    return _STRING_LITERAL.sub("''", query)


def _get_connection():
    # Open read-only so even a bug elsewhere can't mutate the database, and
    # disable external file/network access so table functions like
    # read_csv()/read_text()/httpfs can't be used to read or exfiltrate
    # files on the server from within a SELECT statement.
    return duckdb.connect(
        str(DB_PATH),
        read_only=True,
        config={"enable_external_access": False},
    )


def get_schema() -> str:
    """
    Returns the database schema (tables + columns) as a string the agent can
    read to know what it's allowed to query.

    Tables are listed from the database itself rather than hard-coded, so a
    database re-ingested with the newer schema (per-type extra columns, the
    deliveries_wickets table) shows up without a code change here.
    """
    con = _get_connection()
    try:
        tables = [
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
        ]
        lines = []
        for t in tables:
            cols = con.execute(f'DESCRIBE "{t}"').fetchall()
            col_desc = ", ".join(f"{c[0]} ({c[1]})" for c in cols)
            lines.append(f"{t}: {col_desc}")
        return "\n".join(lines)
    finally:
        con.close()


def run_sql(query: str, max_rows: int = 200) -> dict:
    """
    Executes a read-only SQL query against the cricket database.

    Returns a dict: { "columns": [...], "rows": [[...], ...], "row_count": N }
    or { "error": "..." } if the query is invalid or fails.

    Only SELECT / WITH statements are permitted. Query results are capped
    at max_rows to avoid flooding the agent's context.
    """
    query = query.strip().rstrip(";")

    if not _ALLOWED_START.match(query):
        return {"error": "Only SELECT/WITH queries are allowed."}
    if _FORBIDDEN.search(_strip_string_literals(query)):
        return {"error": "Query contains a forbidden keyword (only read-only SELECT queries are allowed)."}

    con = _get_connection()
    try:
        result = con.execute(query)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchmany(max_rows)
        return {
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
        }
    except Exception as e:  # noqa: BLE001 - surface DB errors back to the agent verbatim
        return {"error": str(e)}
    finally:
        con.close()


def search_player(name: str, limit: int = 5) -> list[str]:
    """
    Best-matching player names for `name`, most likely first. Understands
    Cricsheet's initials convention ("Rohit Sharma" -> "RG Sharma") and
    ranks namesakes by how much they've played -- see catalog.py. Used by
    the frontend's autocomplete.
    """
    from . import catalog

    return [p.name for _, _, p in catalog.rank_players(name, limit=limit)]


def warm_caches() -> None:
    """Load the name catalog eagerly (called on FastAPI startup) so the
    first user-facing query isn't slowed by a cold cache."""
    from . import catalog

    catalog.get_catalog()


def invalidate_caches() -> None:
    """Call after re-ingesting the DB so the next lookup picks up fresh names."""
    from . import catalog

    catalog.invalidate()


# Handed to the model in run_sql's description: the schema plus the traps
# that make hand-written cricket SQL silently wrong.
SQL_GUIDE = """Tables (DuckDB):
- matches(match_id, match_type, event_name, match_number, gender, team_type, overs_per_innings, date TEXT 'YYYY-MM-DD', venue, city, season TEXT, team1, team2, toss_winner, toss_decision, winner, win_by_runs, win_by_wickets, player_of_match)
- deliveries(match_id, innings_num, batting_team, over_num (0-indexed), ball_in_over, batter, bowler, non_striker, runs_batter, runs_extras, runs_total, extra_type, is_wicket, wicket_kind, player_dismissed)
- players_matches(match_id, team, player)
Rules: match_type 'T20' + team_type 'international' = T20Is ('IT20' = non-official internationals); 'ODM'/'MDM' = domestic one-day/multi-day. winner NULL = tie/draw/no result. gender is 'male'/'female' and team names are the same for both (always filter gender). Innings > 2 in limited-overs matches are super overs -- exclude them. A wide is not a ball faced; wides/no-balls are not legal balls; byes/leg-byes are not charged to the bowler; 'retired hurt' is not a dismissal; run outs are not bowler wickets. Player names use Cricsheet form ('V Kohli', 'RG Sharma'), and competitions can span several event_name values -- prefer the stats tools, which handle all of this."""


def get_stats() -> dict:
    """
    Returns high-level counts about the dataset (not an agent tool -- used
    by the API's /stats endpoint to power the frontend's masthead ticker).
    """
    con = _get_connection()
    try:
        matches = con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        deliveries = con.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
        tournaments = con.execute(
            "SELECT COUNT(DISTINCT event_name) FROM matches WHERE event_name IS NOT NULL"
        ).fetchone()[0]
        return {"matches": matches, "deliveries": deliveries, "tournaments": tournaments}
    finally:
        con.close()
