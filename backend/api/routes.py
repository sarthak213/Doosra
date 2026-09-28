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

from analytics import catalog, coverage, engine, registry
from analytics.scope import normalize_filters

from . import workspace

router = APIRouter(prefix="/api")

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
) -> dict:
    return normalize_filters({
        "competition": competition, "format": format, "gender": gender, "team": team, "opposition": opposition,
        "venue": venue, "season": season, "from_year": from_year, "to_year": to_year, "phase": phase,
        "innings": innings,
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
    return _ok(engine.query_stats(role=role, metrics=metrics, players=[name], split_by=split_by, **filters))


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
    }


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


# ---------------------------------------------------------------------------
# Workspace: saved views and watchlist
# ---------------------------------------------------------------------------

class ViewBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["query", "matrix", "compare", "player"]
    state: dict


@router.get("/views")
def list_views(kind: str | None = None):
    return {"views": workspace.list_views(kind)}


@router.post("/views")
def save_view(body: ViewBody):
    return workspace.save_view(body.name, body.kind, body.state)


@router.delete("/views/{view_id}")
def delete_view(view_id: str):
    workspace.delete_view(view_id)
    return {"status": "deleted"}


class WatchlistBody(BaseModel):
    players: list[str]


@router.get("/watchlist")
def get_watchlist():
    return {"players": workspace.get_watchlist()}


@router.put("/watchlist")
def put_watchlist(body: WatchlistBody):
    return {"players": workspace.set_watchlist(body.players)}
