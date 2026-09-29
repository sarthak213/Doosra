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
    # Batting-role filters: apply to per-innings batting records only (see batting_clauses).
    position: tuple[int, int] | None = None
    entry_phase: str | None = None
    entry_wickets: tuple[int, int] | None = None
    # The match result from the player's side: won, lost, drawn, tied, no result.
    result: str | None = None
    applied: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def has_ball_filters(self) -> bool:
        return bool(self.phase or self.innings)

    @property
    def batting_role(self) -> bool:
        """Filters on how a batter came in (position, entry phase, wickets down)."""
        return bool(self.position or self.entry_phase or self.entry_wickets)

    @property
    def entry_filters(self) -> bool:
        return bool(self.entry_phase or self.entry_wickets)

    @property
    def per_innings_only(self) -> bool:
        """Filters that exist only on per-innings/per-phase player records (batting role, match result)."""
        return self.batting_role or bool(self.result)

    def result_clauses(self, result_sql: str) -> list[str]:
        """The match-result filter, on a per-innings or per-phase table (alias m)."""
        return [f"({result_sql}) = {lit(self.result)}"] if self.result else []

    def batting_clauses(self, entry_phase_sql: str) -> list[str]:
        """The batting-role filters, on a per-innings batting table (alias m)."""
        c = []
        if self.position:
            c.append(f"m.position BETWEEN {self.position[0]} AND {self.position[1]}")
        if self.entry_phase:
            c.append(f"({entry_phase_sql}) = {lit(self.entry_phase)}")
        if self.entry_wickets:
            c.append(f"m.entry_wkts BETWEEN {self.entry_wickets[0]} AND {self.entry_wickets[1]}")
        return c

    # -- SQL --------------------------------------------------------------

    def match_clauses(self, batting_innings: bool = False) -> list[str]:
        """Filters on the matches table (alias m). The batting-role filters exist only on per-innings
        batting records; a view built on anything else refuses them rather than ignoring them."""
        if self.per_innings_only and not batting_innings:
            which = ("Batting position, entry and match-result filters apply" if self.batting_role
                     else "The match-result filter applies")
            raise ResolutionError("result" if not self.batting_role else "position",
                                  self.applied.get("result") or self.applied.get("position") or self.applied.get("entry", ""),
                                  f"{which} to player figures (Player Hub, Compare, Query Builder, leaderboards), "
                                  "not to this view. Remove them here.")
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
    position=None,
    entry_phase: str | None = None,
    entry_wickets=None,
    result: str | None = None,
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

    if position not in (None, ""):
        s.position = parse_position(position)
        s.applied["position"] = describe_range(s.position, "batting at")
    if entry_phase not in (None, ""):
        s.entry_phase = catalog.resolve_phase(entry_phase)
        s.applied["entry"] = f"came in during the {s.entry_phase}"
        s.notes.append("Entry phase = the phase of the innings when the batter walked in (limited-overs only; "
                       "same over bands as the phase filter).")
    if entry_wickets not in (None, ""):
        s.entry_wickets = parse_range(entry_wickets, "entry_wickets", 0, 10)
        s.applied["entry_wickets"] = describe_range(s.entry_wickets, "wickets down at entry:")
    if result not in (None, ""):
        s.result = parse_result(result)
        s.applied["result"] = f"matches {s.result}" if s.result in ("won", "lost", "drawn", "tied") else "no-result matches"
        s.notes.append("Result is from the player's side: 'won' = matches their team won.")

    return s


RESULT_WORDS = {
    "won": "won", "win": "won", "wins": "won", "winning": "won", "victory": "won", "victories": "won",
    "lost": "lost", "loss": "lost", "losses": "lost", "losing": "lost", "lose": "lost", "defeat": "lost", "defeats": "lost",
    "drawn": "drawn", "draw": "drawn", "draws": "drawn", "tied": "tied", "tie": "tied", "ties": "tied",
    "no result": "no result", "nr": "no result", "abandoned": "no result", "no-result": "no result",
}


def parse_result(value) -> str:
    key = str(value).strip().lower()
    if key in RESULT_WORDS:
        return RESULT_WORDS[key]
    raise ResolutionError("result", str(value), "result must be one of: won, lost, drawn, tied, no result.")


# Batting orders in words -> positions (1 = opener).
POSITION_WORDS = {
    "opener": (1, 2), "openers": (1, 2), "opening": (1, 2), "top order": (1, 3), "top": (1, 3),
    "middle order": (4, 7), "middle": (4, 7), "lower order": (8, 11), "lower": (8, 11), "tail": (8, 11),
    "number 3": (3, 3), "no 3": (3, 3), "one down": (3, 3),
}


def parse_range(value, name: str, lo: int, hi: int) -> tuple[int, int]:
    """'4' -> (4, 4); '1-3' -> (1, 3); '5+' -> (5, hi); a (lo, hi) pair passes through."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        a, b = value
    else:
        text = str(value).strip().lower().replace(" ", "")
        m = re.fullmatch(r"(\d+)(?:(-|to)(\d+)|(\+))?", text)
        if not m:
            raise ResolutionError(name, str(value), f"{name} must be a number, a range like '1-3', or '5+'.")
        a = int(m.group(1))
        b = hi if m.group(4) else int(m.group(3)) if m.group(3) else a
    a, b = int(a), int(b)
    if not (lo <= a <= b <= hi):
        raise ResolutionError(name, str(value), f"{name} must be between {lo} and {hi}.")
    return a, b


def parse_position(value) -> tuple[int, int]:
    key = str(value).strip().lower().replace("no.", "no").replace("#", "")
    if key in POSITION_WORDS:
        return POSITION_WORDS[key]
    return parse_range(value, "position", 1, 11)


def describe_range(r: tuple[int, int], prefix: str) -> str:
    return f"{prefix} {r[0]}" if r[0] == r[1] else f"{prefix} {r[0]}-{r[1]}"


FILTER_ARGS = ("competition", "format", "gender", "team", "opposition", "venue", "season",
               "from_year", "to_year", "phase", "innings", "position", "entry_phase", "entry_wickets", "result")


# Names models and people reach for, mapped onto ours.
FILTER_ALIASES = {
    "tournament": "competition", "event": "competition", "event_name": "competition", "league": "competition",
    "series": "competition", "match_type": "format", "year": "season", "ground": "venue", "stadium": "venue",
    "against": "opposition", "vs": "opposition", "opponent": "opposition", "since": "from_year",
    "start_year": "from_year", "end_year": "to_year", "until": "to_year",
    "batting_position": "position", "batting_order": "position", "order": "position",
    "entry_wkts": "entry_wickets", "wickets_down": "entry_wickets", "came_in": "entry_phase",
    "match_result": "result", "outcome": "result",
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
