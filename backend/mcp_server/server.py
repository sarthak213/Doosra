"""
Doosra's MCP server: every analytics capability as a Model Context Protocol
tool, so any MCP client -- the app's own copilot (in-process), Claude
Desktop, Claude Code -- uses exactly the same tools.

Run standalone over stdio:

    python -m mcp_server            (from backend/)

The FastAPI app also mounts it over streamable HTTP at /mcp.

Tools take plain names ("Rohit Sharma", "IPL", "RCB") and return the
standard result envelope {title, columns, rows, filters, notes, highlights}.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from agent import stats, tools
from analytics import engine, registry
from analytics.scope import normalize_filters

INSTRUCTIONS = """Cricket analytics over Cricsheet ball-by-ball data (men's and women's Tests, ODIs, T20Is
and major leagues). Pass names as a person would write them -- the tools resolve players, competitions
(including every historical name of a tournament), teams and venues, and report what they resolved to in
`notes`. Every stats tool accepts the same optional `filters` object. Use search_metrics to discover the
100+ metrics (traditional, scoring profile, reliability, and context-adjusted ones such as true strike
rate, true average, runs above expected, match factor and era factor)."""

mcp = FastMCP("doosra", instructions=INSTRUCTIONS, log_level="WARNING")

Role = Literal["batting", "bowling"]
Filters = Annotated[dict[str, Any] | None, Field(description=(
    "Optional filters, all plain words: competition ('IPL', 'T20 World Cup', 'Ashes'), format ('Test', 'ODI', "
    "'T20I', 'T20' = all T20 incl. leagues, 'first-class', 'List A', 'international'), gender ('male', "
    "'female', 'all'), team, opposition, venue, season ('2024' or '2023/24'), from_year, to_year, phase "
    "('powerplay', 'middle', 'death'), innings (1 = batting first, 2 = chasing)."))]
Metrics = Annotated[list[str] | None, Field(description="Metric ids from search_metrics; defaults to a sensible set.")]
SplitBy = Annotated[str | None, Field(description=(
    "Break the figures down by: " + ", ".join(registry.DIMENSIONS) + "."))]


def _f(filters: dict | None) -> dict:
    return normalize_filters(filters)


@mcp.tool()
def search_metrics(query: str = "", role: Role | None = None) -> dict:
    """Find metrics by keyword (e.g. 'true', 'boundary', 'consistency', 'death') and list the dimensions
    results can be split by. Each metric comes with a plain-English definition."""
    return engine.metric_catalog(role=role, query=query)


@mcp.tool()
def lookup_entity(kind: Literal["player", "team", "venue", "competition"], name: str) -> dict:
    """Show how a name resolves in the database, with candidates. Only needed when another tool reported an
    ambiguous or unknown name."""
    return stats.lookup(kind, name)


@mcp.tool()
def player_profile(player: str, filters: Filters = None) -> dict:
    """A player's headline batting and bowling figures, overall and by format, including context-adjusted
    metrics (true strike rate, match factor, ...). Start here for 'tell me about X'."""
    return engine.player_profile(player, **_f(filters))


@mcp.tool()
def player_stats(player: str, role: Role = "batting", metrics: Metrics = None, split_by: SplitBy = None,
                 filters: Filters = None) -> dict:
    """One player's figures in any scope, optionally split (by season, format, opposition, phase, batting
    position, entry point, dismissal type, ...). Split results include `highlights`."""
    return engine.query_stats(role=role, metrics=metrics, players=[player], split_by=split_by, **_f(filters))


@mcp.tool()
def compare_players(players: list[str], role: Role = "batting", metrics: Metrics = None,
                    filters: Filters = None) -> dict:
    """Several players side by side on the same metrics and filters. Includes `highlights` naming who leads
    each metric."""
    return engine.query_stats(role=role, metrics=metrics, players=players, **_f(filters))


@mcp.tool()
def leaderboard(metric: str, role: Role = "batting", extra_metrics: Metrics = None, ascending: bool | None = None,
                min_balls: int | None = None, limit: int = 10, filters: Filters = None) -> dict:
    """Rank players by any metric ('who has the most/best/highest ...'). Rate metrics get an automatic
    minimum-balls qualification unless min_balls is given. Direction defaults to 'best first'."""
    ms = [metric] + [m for m in (extra_metrics or ["matches", "innings" if role == "batting" else "wickets"])
                     if m != metric]
    return engine.query_stats(role=role, metrics=ms, sort_by=metric, ascending=ascending, min_balls=min_balls,
                              limit=limit, **_f(filters))


@mcp.tool()
def player_form(player: str, role: Role = "batting", window: int = 10, filters: Filters = None) -> dict:
    """Innings-by-innings series with rolling last-N and career-to-date figures -- form over time. The
    `highlights` give current form, the peak and the trough windows."""
    return engine.player_form(player, role=role, window=window, **_f(filters))


@mcp.tool()
def career_arc(players: list[str], role: Role = "batting", metric: str = "average", filters: Filters = None) -> dict:
    """Career-to-date metric after each innings for several players, aligned by innings number -- compare
    careers at the same stage. Batting metric: average, strike_rate, runs, true_sr. Bowling: wickets,
    average, economy, strike_rate."""
    return engine.career_arc(players, role=role, metric=metric, **_f(filters))


@mcp.tool()
def percentiles(players: list[str], role: Role = "batting", metrics: Metrics = None, min_balls: int | None = None,
                filters: Filters = None) -> dict:
    """Where players rank (0-100 percentile, higher = better) against every qualified player in the same
    scope, per metric."""
    return engine.percentiles(players, role=role, metrics=metrics, min_balls=min_balls, **_f(filters))


@mcp.tool()
def player_matrix(x: str, y: str, role: Role = "batting", min_balls: int | None = None,
                  highlight: list[str] | None = None, filters: Filters = None) -> dict:
    """Two metrics for every qualified player (a scatter), with medians. Great for 'who is both X and Y'.
    `highlights.best_on_both_axes` names the standouts."""
    return engine.scatter(role=role, x=x, y=y, min_balls=min_balls, highlight=highlight, **_f(filters))


@mcp.tool()
def entry_points(player: str, filters: Filters = None) -> dict:
    """A batter's record by when they came in: phase of the innings x wickets already down."""
    return engine.entry_heatmap(player, **_f(filters))


@mcp.tool()
def similar_players(player: str, role: Role = "batting", limit: int = 10, filters: Filters = None) -> dict:
    """Players with the most similar statistical profile (standardised rate metrics) in the same scope."""
    return engine.similar_players(player, role=role, limit=limit, **_f(filters))


@mcp.tool()
def team_record(team: str, opposition: str | None = None,
                split_by: Literal["season", "year", "format", "competition", "opposition", "venue"] | None = None,
                filters: Filters = None) -> dict:
    """A team's results (won/lost/win %, batting first vs chasing, tosses, recent results); give an
    opposition for a head-to-head."""
    f = _f(filters)
    f.pop("team", None)
    f.pop("opposition", None)
    return stats.team_stats(team, opposition=opposition, split_by=split_by, **f)


@mcp.tool()
def team_leaderboard(metric: Literal["wins", "win_pct", "matches", "losses"] = "wins", min_matches: int | None = None,
                     limit: int = 10, filters: Filters = None) -> dict:
    """Rank teams by wins, win % or matches in any scope (e.g. most wins at a venue)."""
    return stats.leaderboard(role="team", metric=metric, limit=limit, min_matches=min_matches, **_f(filters))


@mcp.tool()
def venue_profile(venue: str, filters: Filters = None) -> dict:
    """How a ground plays, per format: average 1st/2nd innings scores, bat-first vs chasing wins (and which
    it favours), toss decisions, highest total."""
    f = _f(filters)
    f.pop("venue", None)
    return stats.venue_stats(venue, **f)


@mcp.tool()
def matchup(batter: str | None = None, bowler: str | None = None, limit: int = 10, min_balls: int | None = None,
            filters: Filters = None) -> dict:
    """Batter vs bowler: balls, runs, dismissals, strike rate. Give only a batter to list the bowlers who
    dismissed them most, or only a bowler for the batters they dismissed most."""
    return stats.matchup(batter=batter, bowler=bowler, limit=limit, min_balls=min_balls, **_f(filters))


@mcp.tool()
def records(kind: Literal["batting_innings", "bowling_figures", "team_total"], order: Literal["highest", "lowest"] = "highest",
            player: str | None = None, limit: int = 10, filters: Filters = None) -> dict:
    """Record lists: highest individual scores, best bowling figures in an innings, or highest/lowest team
    totals -- optionally for one player."""
    return stats.top_performances(kind=kind, order=order, player=player, limit=limit, **_f(filters))


@mcp.tool(description="Read-only SQL (SELECT/WITH) for anything the other tools don't cover. " + tools.SQL_GUIDE)
def run_sql(query: str) -> dict:
    return tools.run_sql(query)
