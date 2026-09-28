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

from . import catalog, db
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



def ball_exprs(columns) -> dict:
    """Per-delivery scoring rules as SQL over `deliveries d` (joined to
    `matches m`), using the best columns the database has:
        faced       -- the batter faced it (a wide isn't faced; a no-ball is)
        legal       -- counts toward the over (not a wide or no-ball)
        bowler_runs -- runs charged to the bowler (byes, leg-byes, penalties aren't)
        four / six  -- a boundary (a "4" that was run isn't one)
        regular     -- not a super over (super overs aren't in anyone's record)
    `columns` is the set of deliveries columns (a bool is accepted for the
    old "has per-type extras" flag). Databases from older ingests lack some
    columns and fall back to approximations: a single extra type per ball
    (a no-ball that also ran byes charges the byes to the bowler), every
    4 counted as a boundary, and super overs inferred as limited-overs
    innings beyond the second."""
    if isinstance(columns, bool):
        columns = {"extra_wides"} if columns else set()
    e = {}
    if "extra_wides" in columns:
        e["faced"] = "(d.extra_wides IS NULL)"
        e["legal"] = "(d.extra_wides IS NULL AND d.extra_noballs IS NULL)"
        # extra_* columns may be DOUBLE in databases from older ingests; keep runs integral.
        e["bowler_runs"] = ("CAST(d.runs_total - COALESCE(d.extra_byes, 0) - COALESCE(d.extra_legbyes, 0) "
                            "- COALESCE(d.extra_penalty, 0) AS BIGINT)")
    else:
        e["faced"] = "(d.extra_type IS NULL OR d.extra_type <> 'wides')"
        e["legal"] = "(d.extra_type IS NULL OR d.extra_type NOT IN ('wides', 'noballs'))"
        e["bowler_runs"] = ("(d.runs_total - CASE WHEN d.extra_type IN ('byes', 'legbyes', 'penalty') "
                            "THEN d.runs_extras ELSE 0 END)")
    if "non_boundary" in columns:
        e["four"] = "(d.runs_batter = 4 AND NOT COALESCE(d.non_boundary, FALSE))"
        e["six"] = "(d.runs_batter = 6 AND NOT COALESCE(d.non_boundary, FALSE))"
    else:
        e["four"] = "(d.runs_batter = 4)"
        e["six"] = "(d.runs_batter = 6)"
    e["regular"] = ("(NOT COALESCE(d.is_super_over, FALSE))" if "is_super_over" in columns
                    else "(m.match_type IN ('Test', 'MDM') OR d.innings_num <= 2)")
    return e


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


LATEST_SEASON = ("latest", "current", "this season", "most recent")


def _latest_season(s: Scope) -> str | None:
    """The most recent season among matches that pass the filters resolved
    so far (competition, format, gender, teams, venue)."""
    clauses = s.match_clauses()
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.query(f"SELECT m.season FROM matches m {where} ORDER BY m.date DESC LIMIT 1")
    return rows[0]["season"] if rows and rows[0]["season"] else None


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
        if fmt.display == "T20I":
            s.notes.append("T20Is include associate nations (every T20 between ICC members has been a T20I since "
                           "2019); filter by team or competition to focus on the leading sides.")

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
        if season.lower() in LATEST_SEASON:
            season = _latest_season(s)
            if season is None:
                raise ResolutionError("season", "latest", "No matches with these filters, so there's no latest season.")
            s.notes.append(f"Latest season with these filters: {season}.")
        if not re.fullmatch(r"\d{4}(/\d{2})?", season):
            raise ResolutionError("season", season, "season must look like '2024', '2023/24' or 'latest'.")
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
    if s.innings is not None and not 1 <= s.innings <= 4:
        raise ResolutionError("innings", str(innings), "innings must be 1-4 (3 and 4 exist only in Tests and "
                                                     "first-class matches).")
    if s.innings:
        s.applied["innings"] = s.innings
        if s.innings > 2 and s.match_types and not set(s.match_types) & {"Test", "MDM"}:
            s.notes.append(f"Innings {s.innings} only exists in multi-day matches, so this {s.applied.get('format')} "
                           "filter matches nothing.")

    return s


FILTER_ARGS = ("competition", "format", "gender", "team", "opposition", "venue", "season",
               "from_year", "to_year", "phase", "innings")


# Names models and people reach for, mapped onto ours.
FILTER_ALIASES = {
    "tournament": "competition", "event": "competition", "event_name": "competition", "league": "competition",
    "series": "competition", "match_type": "format", "year": "season", "ground": "venue", "stadium": "venue",
    "against": "opposition", "vs": "opposition", "opponent": "opposition", "since": "from_year",
    "start_year": "from_year", "end_year": "to_year", "until": "to_year",
}


def normalize_filters(filters: dict | None) -> dict:
    """Clean a filters object from a client: map alias keys, drop empty
    values and "all" (which means no filter -- except gender, where 'all'
    turns off the men's-cricket default). Unknown keys are an error."""
    out = {}
    for k, v in (filters or {}).items():
        key = FILTER_ALIASES.get(k, k)
        if key not in FILTER_ARGS:
            raise ResolutionError("filter", k, f"Unknown filter '{k}'. Valid filters: {', '.join(FILTER_ARGS)}.")
        if v is None or v == "" or (isinstance(v, str) and v.strip().lower() in ("null", "none")):
            continue
        if isinstance(v, str) and v.strip().lower() in ("all", "both", "any", "overall", "n/a"):
            if key == "gender":
                out[key] = "all"
            continue
        out.setdefault(key, v)
    return out


def pop_filters(kwargs: dict) -> dict:
    """Split the shared filter arguments out of a tool call's kwargs."""
    return {k: kwargs.pop(k) for k in list(kwargs) if k in FILTER_ARGS and kwargs[k] not in (None, "")}
