"""
Tools available to the agent. Each tool is a plain Python function with a
docstring/schema the LLM uses to decide when and how to call it.

Kept framework-agnostic (no LangChain/LangGraph decorators here) so they're
easy to test standalone and easy to wrap for whichever agent framework
graph.py ends up using.
"""

import re
from difflib import get_close_matches
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


# In-process caches for the distinct name lists used by fuzzy matching --
# these change only when the database is re-ingested, so re-querying them on
# every search_player/search_tournament call is wasted DB work.
_player_names_cache: list[str] | None = None
_tournament_names_cache: list[str] | None = None


def _load_player_names() -> list[str]:
    global _player_names_cache
    if _player_names_cache is None:
        con = _get_connection()
        try:
            rows = con.execute("SELECT DISTINCT player FROM players_matches").fetchall()
        finally:
            con.close()
        _player_names_cache = [r[0] for r in rows if r[0]]
    return _player_names_cache


def _load_tournament_names() -> list[str]:
    global _tournament_names_cache
    if _tournament_names_cache is None:
        con = _get_connection()
        try:
            rows = con.execute(
                "SELECT DISTINCT event_name FROM matches WHERE event_name IS NOT NULL"
            ).fetchall()
        finally:
            con.close()
        _tournament_names_cache = [r[0] for r in rows if r[0]]
    return _tournament_names_cache


def invalidate_name_caches() -> None:
    """Call after re-ingesting the DB so the next lookup picks up fresh names."""
    global _player_names_cache, _tournament_names_cache
    _player_names_cache = None
    _tournament_names_cache = None


def warm_name_caches() -> None:
    """Populate both caches eagerly (called on FastAPI startup) so the first
    user-facing search_player/search_tournament call isn't slowed by a cold
    cache."""
    _load_player_names()
    _load_tournament_names()


def search_player(name: str, limit: int = 5) -> list[str]:
    """
    Fuzzy-matches a player name against names actually present in the database.
    Cricsheet spells names inconsistently across sources (e.g. "V Kohli" vs
    "Virat Kohli" vs "Kohli V"), so the agent should call this before writing
    a WHERE clause on a player name, then use the returned exact string.
    """
    names = _load_player_names()

    matches = get_close_matches(name, names, n=limit, cutoff=0.4)
    if not matches:
        # fall back to a simple substring search (handles partial/last-name queries)
        lowered = name.lower()
        matches = [n for n in names if lowered in n.lower()][:limit]
    return matches


# Common abbreviations that won't fuzzy-match their full names (e.g. "IPL"
# vs "Indian Premier League" share almost no characters, so difflib alone
# can't bridge that gap). Extend this as you notice the agent guess wrong.
_TOURNAMENT_ALIASES = {
    "ipl": "Indian Premier League",
    "bbl": "Big Bash League",
    "psl": "Pakistan Super League",
    "cpl": "Caribbean Premier League",
    "bpl": "Bangladesh Premier League",
    "t20 wc": "ICC Men's T20 World Cup",
    "t20 world cup": "ICC Men's T20 World Cup",
    "world cup": "ICC Cricket World Cup",
    "the hundred": "The Hundred",
}


def search_tournament(name: str, limit: int = 5) -> list[str]:
    """
    Resolves a tournament/competition name to the exact event_name string(s)
    used in the database. ALWAYS call this before filtering matches.event_name
    -- common abbreviations (e.g. "IPL") don't fuzzy-match their full names
    (e.g. "Indian Premier League") by string similarity, and getting this
    wrong silently returns zero rows rather than an error, which can lead to
    an incorrect "no data found" conclusion.
    """
    names = _load_tournament_names()

    lowered = name.strip().lower()
    if lowered in _TOURNAMENT_ALIASES:
        alias_target = _TOURNAMENT_ALIASES[lowered]
        if alias_target in names:
            return [alias_target]

    matches = get_close_matches(name, names, n=limit, cutoff=0.4)
    if not matches:
        matches = [n for n in names if lowered in n.lower()][:limit]
    return matches


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
