"""
A resolved set of filters ("scope") shared by every analytics tool, plus
the SQL fragments that implement it.

Every tool accepts the same human-readable filters (competition="T20 World
Cup", format="T20I", team="RCB", venue="Chinnaswamy", phase="death", ...).
build_scope() resolves them all through catalog.py once, records what each
one resolved to (so the answer can state its assumptions), and emits SQL.

Resolved values come from the database itself, and are inlined as escaped
SQL literals rather than bound parameters -- list filters (several event
names, a franchise's former names) make positional parameters error-prone,
and nothing user-typed reaches the SQL without first being mapped onto a
known database value (years/innings are validated as integers).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import catalog
from .catalog import ResolutionError

# Dismissal kinds credited to the bowler.
BOWLER_CREDITED_KINDS = ("bowled", "caught", "caught and bowled", "lbw", "stumped", "hit wicket")
# Wicket-table entries that are NOT dismissals of the batter.
NOT_DISMISSALS = ("retired hurt", "retired not out")


def lit(value) -> str:
    """SQL literal for a resolved value."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def lit_list(values) -> str:
    return "(" + ", ".join(lit(v) for v in values) + ")"


CREDITED_SQL = lit_list(BOWLER_CREDITED_KINDS)
NOT_DISMISSALS_SQL = lit_list(NOT_DISMISSALS)

PHASE_SQL = (
    "CASE WHEN m.match_type IN ('T20', 'IT20') THEN "
    "(CASE WHEN d.over_num < 6 THEN 'powerplay' WHEN d.over_num < 15 THEN 'middle' ELSE 'death' END) "
    "WHEN m.match_type IN ('ODI', 'ODM') THEN "
    "(CASE WHEN d.over_num < 10 THEN 'powerplay' WHEN d.over_num < 40 THEN 'middle' ELSE 'death' END) END"
)

FORMAT_LABEL_SQL = (
    "CASE WHEN m.match_type = 'T20' AND m.team_type = 'international' THEN 'T20I' "
    "WHEN m.match_type = 'T20' THEN 'T20 (domestic/franchise)' "
    "WHEN m.match_type = 'IT20' THEN 'T20 (other international)' "
    "WHEN m.match_type = 'ODM' THEN 'List A (domestic)' "
    "WHEN m.match_type = 'MDM' THEN 'First-class (domestic)' "
    "ELSE m.match_type END"
)

YEAR_SQL = "CAST(substr(m.date, 1, 4) AS INTEGER)"

# Cricsheet stores super overs as extra innings (3, 4, ...) of a limited-overs
# match. They aren't part of anyone's batting/bowling record.
NOT_SUPER_OVER_SQL = "(m.match_type IN ('Test', 'MDM') OR d.innings_num <= 2)"


@dataclass
class Scope:
    events: list[str] | None = None
    match_types: tuple[str, ...] | None = None
    team_type: str | None = None
    gender: str | None = None
    team: list[str] | None = None
    opposition: list[str] | None = None
    venues: list[str] | None = None
    season: str | None = None
    from_year: int | None = None
    to_year: int | None = None
    phase: str | None = None
    innings: int | None = None
    applied: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def has_ball_filters(self) -> bool:
        return bool(self.phase or self.innings)

    # -- SQL --------------------------------------------------------------

    def match_clauses(self) -> list[str]:
        """Filters on the matches table (alias m)."""
        c = []
        if self.events:
            c.append(f"m.event_name IN {lit_list(self.events)}")
        if self.match_types:
            c.append(f"m.match_type IN {lit_list(self.match_types)}")
        if self.team_type:
            c.append(f"m.team_type = {lit(self.team_type)}")
        if self.gender:
            c.append(f"m.gender = {lit(self.gender)}")
        if self.venues:
            c.append(f"m.venue IN {lit_list(self.venues)}")
        if self.season:
            if re.fullmatch(r"\d{4}", self.season):
                # "2024" means the 2024 season *or* a split season played in
                # calendar 2024 (IPL 2008 is stored as season "2007/08").
                y = int(self.season)
                c.append(
                    f"(m.season = {lit(self.season)} OR (m.season LIKE '%/%' AND {YEAR_SQL} = {y} "
                    f"AND (m.season LIKE '{y}/%' OR m.season LIKE '{y - 1}/%')))"
                )
            else:
                c.append(f"m.season = {lit(self.season)}")
        if self.from_year is not None:
            c.append(f"{YEAR_SQL} >= {int(self.from_year)}")
        if self.to_year is not None:
            c.append(f"{YEAR_SQL} <= {int(self.to_year)}")
        if self.team:
            c.append(f"(m.team1 IN {lit_list(self.team)} OR m.team2 IN {lit_list(self.team)})")
        if self.opposition:
            c.append(f"(m.team1 IN {lit_list(self.opposition)} OR m.team2 IN {lit_list(self.opposition)})")
        return c

    def ball_clauses(self, role: str) -> list[str]:
        """Filters on deliveries (alias d) for a batting or bowling view:
        `team` is the side the player/team was batting for (batting) or
        fielding for (bowling); `opposition` is the other side."""
        c = []
        if self.phase:
            c.append(f"({PHASE_SQL}) = {lit(self.phase)}")
        if self.innings:
            c.append(f"d.innings_num = {int(self.innings)}")
        if self.team:
            op = "IN" if role == "batting" else "NOT IN"
            c.append(f"d.batting_team {op} {lit_list(self.team)}")
        if self.opposition:
            op = "NOT IN" if role == "batting" else "IN"
            c.append(f"d.batting_team {op} {lit_list(self.opposition)}")
        return c

    def describe(self) -> dict:
        return dict(self.applied)


def _to_int(value, name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip()[:4]) if name.endswith("year") else int(value)
    except (TypeError, ValueError):
        raise ResolutionError(name, str(value), f"{name} must be a number, got '{value}'.")


def build_scope(
    *,
    competition: str | None = None,
    format: str | None = None,  # noqa: A002 - matches the tool argument name
    gender: str | None = None,
    team: str | None = None,
    opposition: str | None = None,
    venue: str | None = None,
    season: str | None = None,
    from_year=None,
    to_year=None,
    phase: str | None = None,
    innings=None,
    default_gender: str | None = None,
) -> Scope:
    """Resolve human-readable filters. Raises ResolutionError (with
    candidates) when something can't be resolved confidently."""
    s = Scope()

    explicit_gender = catalog.resolve_gender(gender) if gender else None

    if competition:
        comp = catalog.resolve_competition(competition)
        if explicit_gender == "female" and comp.gender == "male" and "women" not in competition.lower():
            comp = catalog.resolve_competition("women's " + competition)
        s.events = comp.events
        s.applied["competition"] = comp.display if len(comp.events) > 1 else comp.events[0]
        if comp.note:
            s.notes.append(comp.note)
        if comp.season and not season:
            season = comp.season
        if not explicit_gender and comp.gender:
            s.gender = comp.gender

    if explicit_gender:
        s.gender = explicit_gender
    elif gender and gender.lower() in ("all", "both", "any"):
        s.gender = None
    elif s.gender is None and default_gender:
        s.gender = default_gender
        s.notes.append(f"No gender specified -- defaulted to {default_gender} cricket (pass gender='female' or 'all' to change).")
    if s.gender:
        s.applied["gender"] = s.gender

    if format:
        fmt = catalog.resolve_format(format)
        s.match_types = fmt.match_types
        s.team_type = fmt.team_type
        s.applied["format"] = fmt.display

    if team:
        t = catalog.resolve_team(team, gender=s.gender)
        s.team = t.names
        s.applied["team"] = t.display
        if t.note:
            s.notes.append(t.note)
    if opposition:
        o = catalog.resolve_team(opposition, gender=s.gender)
        s.opposition = o.names
        s.applied["opposition"] = o.display
        if o.note:
            s.notes.append(o.note)

    if venue:
        v = catalog.resolve_venue(venue)
        s.venues = v.venues
        s.applied["venue"] = v.display
        if v.note:
            s.notes.append(v.note)

    if season:
        season = str(season).strip()
        if not re.fullmatch(r"\d{4}(/\d{2})?", season):
            raise ResolutionError("season", season, "season must look like '2024' or '2023/24'.")
        s.season = season
        s.applied["season"] = season

    s.from_year = _to_int(from_year, "from_year")
    s.to_year = _to_int(to_year, "to_year")
    if s.from_year or s.to_year:
        s.applied["years"] = f"{s.from_year or '...'}-{s.to_year or '...'}"

    if phase:
        s.phase = catalog.resolve_phase(phase)
        s.applied["phase"] = s.phase
        s.notes.append(
            "Phases: T20 powerplay = overs 1-6, middle = 7-15, death = 16-20; "
            "ODI powerplay = 1-10, middle = 11-40, death = 41-50. Tests/first-class are excluded."
        )

    s.innings = _to_int(innings, "innings")
    if s.innings:
        s.applied["innings"] = s.innings

    return s


FILTER_ARGS = ("competition", "format", "gender", "team", "opposition", "venue", "season",
               "from_year", "to_year", "phase", "innings")


def pop_filters(kwargs: dict) -> dict:
    """Split the shared filter arguments out of a tool call's kwargs."""
    return {k: kwargs.pop(k) for k in list(kwargs) if k in FILTER_ARGS and kwargs[k] not in (None, "")}
