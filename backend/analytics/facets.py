"""
Dynamic filtering: what the filter inputs should offer, given the filters already chosen.

Pick ODI and the competition list holds only competitions with ODI matches; pick the IPL and
the venues, teams and seasons narrow to the IPL's. Each list ignores its own filter (with the
IPL chosen, the competition list still offers the other competitions of that format), and
filters that don't resolve yet (half-typed) are left out rather than failing the list.
Everything is counted from the matches table, most-played first.
"""

from __future__ import annotations

from functools import lru_cache

from analytics import catalog, db
from analytics.catalog import ResolutionError
from analytics.scope import build_scope

# The filters that narrow what exists; the rest (phase, innings, position...) don't change which
# competitions, teams or venues there are.
SCOPING = ("competition", "format", "gender", "team", "opposition", "venue", "season", "from_year", "to_year")
LIMITS = {"competitions": 150, "teams": 250, "venues": 300, "seasons": 80}


def _usable(filters: dict) -> dict:
    """Drop filters that don't resolve on their own (a half-typed name) instead of failing."""
    ok = {}
    for key, value in filters.items():
        if value in (None, "") or key not in SCOPING:
            continue
        try:
            build_scope(**{key: value})
        except (ResolutionError, ValueError, TypeError):
            continue
        ok[key] = value
    return ok


def _where(filters: dict, leave_out: tuple[str, ...]) -> str:
    kept = {k: v for k, v in filters.items() if k not in leave_out}
    try:
        clauses = build_scope(**kept).match_clauses()
    except (ResolutionError, ValueError, TypeError):
        clauses = []
    return " AND ".join(clauses) or "TRUE"


def _rows(sql: str) -> list[dict]:
    return db.query(sql)


def facets(filters: dict | None) -> dict:
    return _facets(str(db.DB_PATH), tuple(sorted(_usable(filters or {}).items())))


@lru_cache(maxsize=256)
def _facets(_db: str, key: tuple) -> dict:               # _db: a swapped database is a new cache entry
    filters = dict(key)

    # Competitions: named competitions first (several event names can make one), then the rest.
    events = _rows(f"""
        SELECT m.event_name AS name, COUNT(*) AS n FROM matches m
        WHERE m.event_name IS NOT NULL AND {_where(filters, ('competition',))}
        GROUP BY 1 ORDER BY n DESC, name""")
    counts = {r["name"]: r["n"] for r in events}
    named = sorted(((sum(counts.get(e, 0) for e in c.events), c.display) for c in catalog.COMPETITIONS
                    if any(e in counts for e in c.events)), key=lambda t: (-t[0], t[1]))
    shown = [d for _, d in named]
    covered = {e for c in catalog.COMPETITIONS if c.display in shown for e in c.events}
    competitions = shown + [r["name"] for r in events if r["name"] not in covered and r["name"] not in shown]

    # Teams: each side of a match; the team list ignores `team`, the opposition list `opposition`
    # (and neither offers the side already picked in the other).
    def teams(leave_out: str, other: str) -> list[str]:
        rows = _rows(f"""
            SELECT t AS name, COUNT(*) AS n FROM (
                SELECT m.team1 AS t FROM matches m WHERE {_where(filters, (leave_out,))}
                UNION ALL
                SELECT m.team2 FROM matches m WHERE {_where(filters, (leave_out,))}
            ) GROUP BY 1 ORDER BY n DESC, name""")
        picked = str(filters.get(other) or "").lower()
        return [r["name"] for r in rows if r["name"] and r["name"].lower() != picked]

    # Venues: grounds by their first name part ("Eden Gardens, Kolkata" and "Eden Gardens" are one)
    venue_rows = _rows(f"""
        SELECT split_part(m.venue, ',', 1) AS name, COUNT(*) AS n FROM matches m
        WHERE m.venue IS NOT NULL AND {_where(filters, ('venue',))}
        GROUP BY 1 ORDER BY n DESC, name""")

    season_rows = _rows(f"""
        SELECT m.season AS name FROM matches m
        WHERE m.season IS NOT NULL AND {_where(filters, ('season', 'from_year', 'to_year'))}
        GROUP BY 1 ORDER BY name DESC""")

    matches = _rows(f"SELECT COUNT(*) AS n FROM matches m WHERE {_where(filters, ())}")[0]["n"]
    return {
        "competitions": competitions[:LIMITS["competitions"]],
        "teams": teams("team", "opposition")[:LIMITS["teams"]],
        "opposition": teams("opposition", "team")[:LIMITS["teams"]],
        "venues": [r["name"] for r in venue_rows][:LIMITS["venues"]],
        "seasons": [r["name"] for r in season_rows][:LIMITS["seasons"]],
        "matches": matches,
        "applied": sorted(filters),
    }


def clear_cache() -> None:
    _facets.cache_clear()
