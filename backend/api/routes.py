"""
REST endpoints behind the UI views (Player Hub, Comparison Studio, Query
Builder, Player Matrix). They call the same analytics engine the MCP tools
use, so a number on screen and a number the copilot quotes are the same
number.

Errors from the engine ({"error": ...}) and unknown filter names come back
as HTTP 400 with the error object as the body.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from analytics import catalog, coverage, engine, facets, registry
from analytics.scope import normalize_filters

from .auth import current_user

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])   # a session when hosted; nothing locally

Role = Literal["batting", "bowling"]


def _ok(result: Any):
    if isinstance(result, dict) and result.get("error"):
        return JSONResponse(status_code=400, content=result)
    return result


def filter_params(
    competition: str | None = None, format: str | None = None, gender: str | None = None,  # noqa: A002
    team: str | None = None, opposition: str | None = None, venue: str | None = None,
    season: str | None = None, from_year: int | None = None, to_year: int | None = None,
    phase: str | None = None, innings: int | None = None,
    position: str | None = None, entry_phase: str | None = None, entry_wickets: str | None = None,
    result: str | None = None,
) -> dict:
    return normalize_filters({
        "competition": competition, "format": format, "gender": gender, "team": team, "opposition": opposition,
        "venue": venue, "season": season, "from_year": from_year, "to_year": to_year, "phase": phase,
        "innings": innings, "position": position, "entry_phase": entry_phase, "entry_wickets": entry_wickets,
        "result": result,
    })


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

@router.get("/metrics")
def metrics(role: Role | None = None, q: str = ""):
    return engine.metric_catalog(role=role, query=q)


@router.get("/options")
def options():
    """Suggestions for filter inputs: curated competitions plus the most-played
    events, teams and venues."""
    cat = catalog.get_catalog()
    curated = [c.display for c in catalog.COMPETITIONS if any(e in cat.events for e in c.events)]
    top_events = sorted(cat.events, key=lambda e: -cat.events[e]["matches"])[:150]
    teams = sorted(cat.teams, key=lambda t: -sum(cat.teams[t].values()))[:250]
    venues = sorted({v.split(",")[0] for v in sorted(cat.venues, key=lambda v: -cat.venues[v]["matches"])[:300]})
    return {
        "competitions": curated + [e for e in top_events if e not in curated],
        "formats": ["Test", "ODI", "T20I", "T20", "first-class", "List A", "international"],
        "teams": teams,
        "venues": venues,
        "phases": ["powerplay", "middle", "death"],
        "dimensions": registry.DIMENSIONS,
        "coverage": {"from": cat.date_min, "to": cat.date_max},
    }


@router.get("/options/scoped")
def scoped_options(competition: str | None = None, format: str | None = None, gender: str | None = None,  # noqa: A002
                   team: str | None = None, opposition: str | None = None, venue: str | None = None,
                   season: str | None = None, from_year: int | None = None, to_year: int | None = None):
    """Dynamic filtering: the competitions, teams, opposition, venues and seasons that exist within
    the other filters chosen (ODI -> ODI competitions; the IPL -> its venues and seasons)."""
    return facets.facets({"competition": competition, "format": format, "gender": gender, "team": team,
                          "opposition": opposition, "venue": venue, "season": season,
                          "from_year": from_year, "to_year": to_year})


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = 8):
    """Player search with context (teams, span, matches) for pickers."""
    ranked = catalog.rank_players(q, limit=limit)
    return {"players": [p.describe() for _, _, p in ranked]}


# ---------------------------------------------------------------------------
# Player Hub
# ---------------------------------------------------------------------------

@router.get("/players/{name}/profile")
def profile(name: str, filters: dict = Depends(filter_params)):
    return _ok(engine.player_profile(name, **filters))


@router.get("/players/{name}/form")
def form(name: str, role: Role = "batting", window: int = 10, filters: dict = Depends(filter_params)):
    return _ok(engine.player_form(name, role=role, window=window, **filters))


@router.get("/players/{name}/splits")
def splits(name: str, split_by: str, role: Role = "batting", metrics: str | None = None,
           filters: dict = Depends(filter_params)):
    # The Player Hub's fixed column set: within a phase (or split by phase), leave out whole-innings
    # columns and say so, rather than failing the panel.
    left_out = None
    if metrics and (filters.get("phase") or split_by == "phase"):
        ids = [m.strip() for m in metrics.split(",") if m.strip()]
        kept, left_out = engine._phase_safe(role, ids, filters.get("phase"), split=split_by == "phase")
        metrics = ",".join(kept) or None
    result = engine.query_stats(role=role, metrics=metrics, players=[name], split_by=split_by, **filters)
    if left_out and isinstance(result, dict) and not result.get("error"):
        result.setdefault("notes", []).append(left_out)
    return _ok(result)


@router.get("/players/{name}/entry-heatmap")
def entry(name: str, filters: dict = Depends(filter_params)):
    return _ok(engine.entry_heatmap(name, **filters))


@router.get("/players/{name}/similar")
def similar(name: str, role: Role = "batting", limit: int = 8, filters: dict = Depends(filter_params)):
    return _ok(engine.similar_players(name, role=role, limit=limit, **filters))


@router.get("/players/{name}/percentiles")
def player_percentiles(name: str, role: Role = "batting", metrics: str | None = None,
                       filters: dict = Depends(filter_params)):
    return _ok(engine.percentiles([name], role=role, metrics=metrics, **filters))


# ---------------------------------------------------------------------------
# Comparison Studio, Query Builder, Player Matrix
# ---------------------------------------------------------------------------

_NOT_RANKED = {"matches", "innings", "not_outs", "balls", "highest", "best"}


class CompareBody(BaseModel):
    players: list[str] = Field(min_length=1, max_length=4)
    role: Role = "batting"
    metrics: list[str] | None = None
    arc_metric: str = "average"
    filters: dict | None = None


# ---------------------------------------------------------------------------
# Data coverage (the Data page)
# ---------------------------------------------------------------------------

@router.get("/coverage")
def data_coverage(gender: str | None = None):
    return _ok(coverage.data_coverage(gender=gender))


@router.get("/coverage/missing")
def missing(format: str | None = None, competition: str | None = None, gender: str | None = None,  # noqa: A002
            team: str | None = None, from_year: int | None = None, to_year: int | None = None, limit: int = 500):
    return _ok(coverage.missing_matches(format=format, competition=competition, gender=gender, team=team,
                                        from_year=from_year, to_year=to_year, limit=limit))


# ---------------------------------------------------------------------------
# FIBS (the methodology page)
# ---------------------------------------------------------------------------

@router.get("/fibs/report")
def fibs_report(format: str = "T20", gender: str = "male", role: Role = "bowling"):  # noqa: A002
    return _ok(engine.fibs_report(format=format, gender=gender, role=role))


@router.get("/fibs/pairs")
def fibs_pairs(metric: str = "dot", format: str = "T20", gender: str = "male", role: Role = "bowling",  # noqa: A002
               limit: int = 1500):
    return _ok(engine.fibs_pairs(metric=metric, format=format, gender=gender, role=role, limit=limit))


@router.get("/fibs/luck")
def fibs_luck(role: Role = "bowling", unlucky: bool = False, by: str | None = None, limit: int = 15,
              min_balls: int | None = None, filters: dict = Depends(filter_params)):
    return _ok(engine.luck_leaderboard(role=role, unlucky=unlucky, by=by, limit=limit, min_balls=min_balls, **filters))


@router.post("/compare")
def compare(body: CompareBody):
    f = normalize_filters(body.filters)
    table = engine.query_stats(role=body.role, metrics=body.metrics, players=body.players, **f)
    if table.get("error"):
        return _ok(table)
    return {
        "table": table,
        # Percentiles only make sense for performance metrics, not sample
        # sizes (matches, innings...) or text (best figures).
        "percentiles": engine.percentiles(body.players, role=body.role, metrics=[
            m for m in (body.metrics or []) if m not in _NOT_RANKED] or None, **f),
        "arc": engine.career_arc(body.players, role=body.role, metric=body.arc_metric, **f),
        "by_phase": by_phase([r[0] for r in table["rows"]] or body.players, body.role, f),   # resolved names
    }


PHASE_ORDER = ("powerplay", "middle", "death")
PHASE_METRICS = {
    "batting": ["innings", "runs", "balls", "average", "strike_rate", "true_sr", "dot_pct", "boundary_pct"],
    "bowling": ["innings", "wickets", "economy", "average", "strike_rate", "true_economy", "dot_pct", "boundary_pct"],
}


def by_phase(players: list[str], role: str, f: dict) -> dict:
    """The players side by side in each phase: rows grouped powerplay, middle, death, and within a
    phase in the order the players were picked, so the comparison is always next to each other."""
    if f.get("phase"):
        return {"error": f"The page is filtered to the {f['phase']} phase; clear the Phase filter to see all three."}
    if f.get("entry_phase") or f.get("entry_wickets"):
        return {"error": "Entry filters are per innings, so they can't be broken down by phase; clear them to see this."}
    table = engine.query_stats(role=role, metrics=PHASE_METRICS[role], players=players, split_by="phase", **f)
    if table.get("error"):
        return table
    cols = table["columns"]
    ph = cols.index("phase")
    pi = cols.index("player") if "player" in cols else None          # absent for a single player
    order = {name: i for i, name in enumerate(players)}
    table["rows"] = sorted(table["rows"], key=lambda r: (
        PHASE_ORDER.index(r[ph]) if r[ph] in PHASE_ORDER else 9, order.get(r[pi], 99) if pi is not None else 0))
    if pi is not None:
        # phase first: the table reads as "in the powerplay, A vs B; in the middle..."
        table["columns"] = [cols[ph], cols[pi]] + [c for i, c in enumerate(cols) if i not in (pi, ph)]
        table["rows"] = [[r[ph], r[pi]] + [v for i, v in enumerate(r) if i not in (pi, ph)] for r in table["rows"]]
    if not table["rows"]:
        table.setdefault("notes", []).append(
            "No phase data here: phases (powerplay, middle, death) exist in limited-overs cricket only.")
    return table


class QueryBody(BaseModel):
    role: Role = "batting"
    metrics: list[str] | None = None
    players: list[str] | None = None
    split_by: str | None = None
    sort_by: str | None = None
    ascending: bool | None = None
    min_balls: int | None = None
    limit: int = 50
    filters: dict | None = None


@router.post("/query")
def query(body: QueryBody):
    f = normalize_filters(body.filters)
    return _ok(engine.query_stats(role=body.role, metrics=body.metrics, players=body.players or None,
                                  split_by=body.split_by, sort_by=body.sort_by, ascending=body.ascending,
                                  min_balls=body.min_balls, limit=body.limit, **f))


class MatrixBody(BaseModel):
    role: Role = "batting"
    x: str = "average"
    y: str = "strike_rate"
    min_balls: int | None = None
    highlight: list[str] | None = None
    filters: dict | None = None


@router.post("/matrix")
def matrix(body: MatrixBody):
    f = normalize_filters(body.filters)
    return _ok(engine.scatter(role=body.role, x=body.x, y=body.y, min_balls=body.min_balls,
                              highlight=body.highlight, **f))

