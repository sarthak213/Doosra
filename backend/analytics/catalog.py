"""
Entity resolution: turns what a user types ("Rohit Sharma", "T20 World Cup",
"RCB", "Chinnaswamy") into the exact strings stored in the database.

This is the layer the agent used to get wrong most often. Cricsheet stores
most established players by initials + surname ("RG Sharma", "SPD Smith"),
so plain fuzzy string matching picks an obscure namesake whose name happens
to be spelled out in full (the "Rohit Sharma" in the data is an 8-match
domestic player). Competitions are split across naming eras ("ICC World
Twenty20" -> "World T20" -> "ICC Men's T20 World Cup"), franchises get
renamed ("Royal Challengers Bangalore" -> "Bengaluru"), and venues carry
optional city suffixes ("Eden Gardens" vs "Eden Gardens, Kolkata").

Resolution here is deterministic and happens inside the stats tools, so the
LLM passes plain names and never has to chain lookups or guess exact
strings. Every resolution is reported back (as a note) so the answer can
say what was actually queried.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import get_close_matches

from . import db


class ResolutionError(Exception):
    """Raised when a name can't be resolved confidently. `candidates` lists
    plausible matches (with context) so the agent can pick or ask."""

    def __init__(self, kind: str, query: str, message: str, candidates: list | None = None):
        super().__init__(message)
        self.kind = kind
        self.query = query
        self.candidates = candidates or []

    def to_dict(self) -> dict:
        return {
            "error": str(self),
            "unresolved": self.kind,
            "query": self.query,
            "candidates": self.candidates,
        }


def norm(text: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace(".", " ").replace("'", "").replace("’", "").replace("-", " ")
    text = re.sub(r"[^a-z0-9&/ ]", " ", text)
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Cache: everything is loaded once per database file. Keyed on DB_PATH so
# tests that point db.DB_PATH at a fixture database get a fresh catalog.
# ---------------------------------------------------------------------------

_catalog_cache: dict[str, "Catalog"] = {}


def get_catalog() -> "Catalog":
    key = str(db.DB_PATH)
    cat = _catalog_cache.get(key)
    if cat is None:
        cat = Catalog.load()
        _catalog_cache[key] = cat
    return cat


def invalidate() -> None:
    _catalog_cache.clear()


@dataclass
class Player:
    name: str               # unique display name (register unique_name, e.g. "Rashid Khan (2)")
    matches: int
    gender: str | None
    teams: list[str]
    first: str | None
    last: str | None
    international: bool
    raw_name: str = ""      # the name as written in match data (not unique across people)
    player_id: str | None = None   # Cricsheet register identifier
    cricinfo_id: str | None = None
    tokens: list[str] = field(default_factory=list)       # normalized tokens of raw_name
    initials_flags: list[bool] = field(default_factory=list)  # token is a block of initials ("RG")

    def describe(self) -> dict:
        span = f"{(self.first or '')[:4]}-{(self.last or '')[:4]}"
        out = {
            "name": self.name,
            "matches": self.matches,
            "gender": self.gender,
            "teams": self.teams,
            "span": span,
        }
        if self.cricinfo_id:
            out["cricinfo_url"] = f"https://www.espncricinfo.com/cricketers/player-{self.cricinfo_id}"
        return out


@dataclass
class Catalog:
    players: dict[str, Player]                  # keyed by unique display name
    players_by_last_token: dict[str, list[Player]]
    players_by_alt_name: dict[str, list[Player]]    # normalized register alternate name -> people
    teams: dict[str, dict[str, int]]          # team -> {gender: matches}
    events: dict[str, dict]                    # event_name -> {matches, genders, formats}
    venues: dict[str, dict]                    # raw venue -> {matches, city, base}
    has_new_schema: bool
    has_wickets_table: bool
    delivery_columns: frozenset
    has_derived_tables: bool
    date_min: str | None
    date_max: str | None

    @classmethod
    def load(cls) -> "Catalog":
        con = db.connect()
        try:
            tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
            pm_cols = {r[0] for r in con.execute("DESCRIBE players_matches").fetchall()}
            has_register = "people" in tables and "player_id" in pm_cols
            # One identity per person: the register's unique name when the
            # database has the register (namesakes then stay apart), else the
            # plain match-data name.
            if has_register:
                ident, join = "COALESCE(pe.unique_name, pm.player)", "LEFT JOIN people pe ON pe.identifier = pm.player_id"
                extra_cols, extra_aggs = ", pm.player_id, pe.key_cricinfo", "any_value(p.player_id), any_value(p.key_cricinfo)"
            else:
                ident, join, extra_cols, extra_aggs = "pm.player", "", "", "NULL, NULL"
            prow = con.execute(
                f"""
                WITH pm AS (
                    SELECT {ident} AS player, pm.player AS raw, pm.team, m.match_id, m.gender, m.date,
                           m.team_type {extra_cols}
                    FROM players_matches pm JOIN matches m ON pm.match_id = m.match_id {join}
                ),
                team_counts AS (
                    SELECT player, team, COUNT(*) AS n,
                           ROW_NUMBER() OVER (PARTITION BY player ORDER BY COUNT(*) DESC, team) AS rk
                    FROM pm GROUP BY player, team
                )
                SELECT p.player, COUNT(DISTINCT p.match_id), mode(p.gender), MIN(p.date), MAX(p.date),
                       BOOL_OR(p.team_type = 'international'),
                       (SELECT list(team ORDER BY rk) FROM team_counts t WHERE t.player = p.player AND rk <= 3),
                       any_value(p.raw), {extra_aggs}
                FROM pm p
                WHERE p.player IS NOT NULL
                GROUP BY p.player
                """
            ).fetchall()

            trow = con.execute(
                """
                SELECT team, gender, COUNT(*) FROM (
                    SELECT team1 AS team, gender FROM matches UNION ALL SELECT team2, gender FROM matches
                ) WHERE team IS NOT NULL GROUP BY ALL
                """
            ).fetchall()

            erow = con.execute(
                """
                SELECT event_name, COUNT(*), list(DISTINCT gender), list(DISTINCT match_type)
                FROM matches WHERE event_name IS NOT NULL GROUP BY 1
                """
            ).fetchall()

            vrow = con.execute(
                "SELECT venue, COUNT(*), mode(city) FROM matches WHERE venue IS NOT NULL GROUP BY 1"
            ).fetchall()

            cols = {r[0] for r in con.execute("DESCRIBE deliveries").fetchall()}
            has_wk = bool(con.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'deliveries_wickets'"
            ).fetchone()[0])
            dmin, dmax = con.execute("SELECT MIN(date), MAX(date) FROM matches").fetchone()
            # Register alternate names ("Virat Kohli" for V Kohli), for people
            # who appear in the match data.
            alt_rows = con.execute("""
                SELECT DISTINCT pn.name, COALESCE(pe.unique_name, pe.name)
                FROM people_names pn JOIN people pe ON pe.identifier = pn.identifier
                WHERE pn.identifier IN (SELECT player_id FROM players_matches)
            """).fetchall() if has_register and "people_names" in tables else []
        finally:
            con.close()

        players: dict[str, Player] = {}
        by_last: dict[str, list[Player]] = {}
        for name, n, gender, first, last, intl, teams, raw, pid, cricinfo in prow:
            raw_tokens = (raw or name).replace(".", " ").split()
            p = Player(
                name=name, matches=n, gender=gender, teams=list(teams or []),
                first=first, last=last, international=bool(intl),
                raw_name=raw or name, player_id=pid, cricinfo_id=cricinfo,
                tokens=[norm(t) for t in raw_tokens],
                initials_flags=[_is_initials_token(t) for t in raw_tokens],
            )
            p.tokens = [t for t in p.tokens if t]
            if not p.tokens:
                continue
            players[name] = p
            by_last.setdefault(p.tokens[-1], []).append(p)

        by_alt: dict[str, list[Player]] = {}
        for alt, uname in alt_rows:
            p = players.get(uname)
            if p is not None and p not in by_alt.setdefault(norm(alt), []):
                by_alt[norm(alt)].append(p)

        teams: dict[str, dict[str, int]] = {}
        for team, gender, n in trow:
            teams.setdefault(team, {})[gender] = n

        events = {
            e: {"matches": n, "genders": sorted(g for g in genders if g), "formats": sorted(f for f in fmts if f)}
            for e, n, genders, fmts in erow
        }

        venues = {}
        for venue, n, city in vrow:
            base = venue.split(",")[0]
            venues[venue] = {"matches": n, "city": city, "base": norm(base)}

        return cls(
            players=players, players_by_last_token=by_last, players_by_alt_name=by_alt,
            teams=teams, events=events, venues=venues,
            has_new_schema="extra_wides" in cols, has_wickets_table=has_wk, delivery_columns=frozenset(cols),
            has_derived_tables={"batting_innings", "bowling_innings", "batting_phase", "bowling_phase"} <= tables,
            date_min=dmin, date_max=dmax,
        )


def _is_initials_token(tok: str) -> bool:
    """'RG', 'SPD', 'MS', 'AB' are initials; 'Rohit', 'de' are not."""
    return tok.isalpha() and tok.isupper() and len(tok) <= 4


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

# Nicknames/short forms that no string rule can bridge. Keys are normalized.
PLAYER_ALIASES = {
    "sachin": "SR Tendulkar",
    "virat": "V Kohli",
    "king kohli": "V Kohli",
    "rohit": "RG Sharma",
    "msd": "MS Dhoni",
    "mahi": "MS Dhoni",
    "dhoni": "MS Dhoni",
    "abd": "AB de Villiers",
    "sky": "SA Yadav",
    "surya": "SA Yadav",
    "suryakumar": "SA Yadav",
    "bhuvi": "B Kumar",
    "bhuvneshwar": "B Kumar",
    "bhuvneshwar kumar": "B Kumar",
    "jaddu": "RA Jadeja",
    "hitman": "RG Sharma",
    "gabbar": "S Dhawan",
    "universe boss": "CH Gayle",
    "faf": "F du Plessis",
    "kl rahul": "KL Rahul",
    "hardik": "HH Pandya",
    "krunal": "KH Pandya",
    "bumrah": "JJ Bumrah",
    "ashwin": "R Ashwin",
    "shami": "Mohammed Shami",
    "siraj": "Mohammed Siraj",
    "chahal": "YS Chahal",
    "yuzi": "YS Chahal",
    "kuldeep": "Kuldeep Yadav",
    "jaiswal": "YBK Jaiswal",
    "pant": "RR Pant",
    "rishabh pant": "RR Pant",
    "boom boom afridi": "Shahid Afridi",
}

_PROMINENCE_WEIGHT = 20.0


def _unitize(tokens: list[str], flags: list[bool]) -> list[tuple[str, bool]]:
    """Expand given-name tokens into units: an initials block 'rg' becomes
    [('r', True), ('g', True)]; a full name stays one unit."""
    units: list[tuple[str, bool]] = []
    for tok, is_init in zip(tokens, flags):
        if is_init:
            units.extend((ch, True) for ch in tok)
        else:
            units.append((tok, False))
    return units


def _unit_match(a: tuple[str, bool], b: tuple[str, bool]) -> str | None:
    """'exact' when two full names match, 'initial' when at least one side is
    an initial and first letters agree, None when incompatible."""
    (ta, ia), (tb, ib) = a, b
    if ia == ib and ta == tb:
        return "exact"
    if not ia and not ib:
        return "exact" if ta == tb else None
    return "initial" if ta[0] == tb[0] else None


def _given_compat(q_units, d_units) -> float | None:
    """Score how well the query's given names fit the database name's
    given names/initials. None means incompatible."""
    if not q_units and not d_units:
        return 100.0
    if not q_units:
        return 50.0   # surname-only query ("Kohli")
    if not d_units:
        return 45.0   # db has surname only
    n = min(len(q_units), len(d_units))
    kinds = []
    for i in range(n):
        k = _unit_match(q_units[i], d_units[i])
        if k is None:
            # Initials don't always start with the name a player goes by
            # ("Lasith Malinga" is "SL Malinga", "Mahela Jayawardene" is
            # "DPMD Jayawardene"): accept the query's names appearing later,
            # in order, among the initials -- as a weaker match.
            return 60.0 if _subsequence_match(q_units, d_units) else None
        kinds.append(k)
    if len(q_units) == len(d_units):
        return 100.0 if all(k == "exact" for k in kinds) else 85.0
    if len(q_units) < len(d_units):
        return 75.0   # "Rohit" vs "RG", "Steve" vs "SPD"
    return 65.0       # query more specific than db ("Mahendra Singh" vs "M")


def _subsequence_match(q_units, d_units) -> bool:
    it = iter(d_units)
    return all(any(_unit_match(q, d) for d in it) for q in q_units)


def _score_player(q_tokens: list[str], q_flags: list[bool], p: Player) -> float | None:
    best = None
    for k in range(1, min(len(q_tokens), len(p.tokens)) + 1):
        if q_tokens[-k:] != p.tokens[-k:]:
            break
        q_units = _unitize(q_tokens[:-k], q_flags[:-k])
        d_units = _unitize(p.tokens[:-k], p.initials_flags[:-k])
        compat = _given_compat(q_units, d_units)
        if compat is not None and (best is None or compat > best):
            best = compat
    return best


def _query_flags(raw_query: str, tokens: list[str]) -> list[bool]:
    """Guess which query tokens are initials: 'MS' typed in caps, or a 1-2
    letter lowercase token ('ms dhoni')."""
    raw_tokens = [t for t in raw_query.replace(".", " ").split() if norm(t)]
    flags = []
    for i, tok in enumerate(tokens):
        raw = raw_tokens[i] if i < len(raw_tokens) else tok
        flags.append(_is_initials_token(raw) or (len(tok) <= 2 and i < len(tokens) - 1))
    return flags


def rank_players(query: str, gender: str | None = None, limit: int = 5) -> list[tuple[float, float, Player]]:
    """Return [(score, compat, player)] best first."""
    cat = get_catalog()
    qn = norm(query)
    if not qn:
        return []

    pool = cat.players.values()
    if gender:
        pool = [p for p in pool if p.gender == gender]

    # A register unique name that differs from the match-data name ("Rashid
    # Khan (2)") is an explicit pick of one namesake: take it as given.
    for p in pool:
        if p.name != p.raw_name and norm(p.name) == qn:
            return [(1000.0, 100.0, p)]

    alias = PLAYER_ALIASES.get(qn)
    if alias and alias in cat.players and (not gender or cat.players[alias].gender == gender):
        p = cat.players[alias]
        return [(1000.0, 100.0, p)]

    q_tokens = qn.split()
    q_flags = _query_flags(query, q_tokens)

    scored: list[tuple[float, float, Player]] = []
    # A register alternate name ("Virat Kohli" -> V Kohli) is an exact match;
    # prominence still ranks it against same-named people.
    for p in cat.players_by_alt_name.get(qn, []):
        if gender and p.gender != gender:
            continue
        prominence = _PROMINENCE_WEIGHT * math.log10(p.matches + 1) + (8.0 if p.international else 0.0)
        scored.append((100.0 + prominence, 100.0, p))
    candidates = cat.players_by_last_token.get(q_tokens[-1], [])
    for p in candidates:
        if gender and p.gender != gender:
            continue
        compat = _score_player(q_tokens, q_flags, p)
        if compat is None:
            continue
        prominence = _PROMINENCE_WEIGHT * math.log10(p.matches + 1) + (8.0 if p.international else 0.0)
        scored.append((compat + prominence, compat, p))

    if not scored:
        # Typos ("Virat Kohly", "Bumra"): try close-spelled surnames, still
        # requiring the given names to fit -- a correctly spelled surname with
        # incompatible initials is a different person, not a typo.
        surnames = {}
        for p in pool:
            surnames.setdefault(p.tokens[-1], []).append(p)
        close = [s for s in get_close_matches(q_tokens[-1], list(surnames), n=4, cutoff=0.8) if s != q_tokens[-1]]
        for s in close:
            fixed = q_tokens[:-1] + [s]
            for p in surnames[s]:
                compat = _score_player(fixed, q_flags, p)
                if compat is None:
                    continue
                prominence = _PROMINENCE_WEIGHT * math.log10(p.matches + 1) + (8.0 if p.international else 0.0)
                scored.append((compat - 30.0 + prominence, compat - 30.0, p))

    scored.sort(key=lambda s: (-s[0], s[2].name))
    # de-dupe (fuzzy path can add a player twice)
    seen, out = set(), []
    for s in scored:
        if s[2].name not in seen:
            seen.add(s[2].name)
            out.append(s)
    return out[:limit]


@dataclass
class ResolvedPlayer:
    name: str               # unique display name -- what the derived tables use
    gender: str | None
    note: str | None
    raw_name: str = ""      # name in the ball-by-ball data (shared by namesakes)
    player_id: str | None = None


def resolve_player(query: str, gender: str | None = None) -> ResolvedPlayer:
    ranked = rank_players(query, gender=gender, limit=6)
    if not ranked:
        raise ResolutionError(
            "player", query,
            f"No player matching '{query}' in the database"
            + (f" ({gender} cricket)" if gender else "") + ".",
        )

    top_score, top_compat, top = ranked[0]
    if len(ranked) > 1:
        second_score, second_compat, second = ranked[1]
        surname_only = top_compat <= 50.0
        ambiguous = (
            (surname_only and second.matches * 3 > top.matches)
            or (not surname_only and top_score - second_score < 4.0 and second.matches * 2 > top.matches)
        )
        if ambiguous:
            raise ResolutionError(
                "player", query,
                f"'{query}' is ambiguous -- several players match. Pick one of the candidates "
                "(or ask the user which one they mean).",
                [p.describe() for _, _, p in ranked],
            )

    note = None
    if norm(top.name) != norm(query):
        teams = ", ".join(top.teams[:2])
        note = f"'{query}' is stored as '{top.name}' ({teams}; {top.matches} matches across all cricket in the data)."
    return ResolvedPlayer(name=top.name, gender=top.gender, note=note, raw_name=top.raw_name or top.name,
                          player_id=top.player_id)


# ---------------------------------------------------------------------------
# Competitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Competition:
    key: str
    display: str
    aliases: tuple[str, ...]
    events: tuple[str, ...]
    gender: str | None = None


# Competitions whose history is split across several event names in
# Cricsheet, plus common abbreviations. Only event names actually present in
# the database are used, so listing a name here that isn't ingested is safe.
COMPETITIONS: tuple[Competition, ...] = (
    Competition("ipl", "Indian Premier League", ("ipl", "indian premier league"), ("Indian Premier League",), "male"),
    Competition("wpl", "Women's Premier League", ("wpl", "womens premier league", "womens ipl", "women ipl"),
                ("Women's Premier League",), "female"),
    Competition("bbl", "Big Bash League", ("bbl", "big bash", "big bash league"), ("Big Bash League",), "male"),
    Competition("wbbl", "Women's Big Bash League", ("wbbl", "womens big bash", "womens big bash league", "women big bash"),
                ("Women's Big Bash League",), "female"),
    Competition("psl", "Pakistan Super League", ("psl", "pakistan super league"), ("Pakistan Super League",), "male"),
    Competition("cpl", "Caribbean Premier League", ("cpl", "caribbean premier league"), ("Caribbean Premier League",), "male"),
    Competition("wcpl", "Women's Caribbean Premier League", ("wcpl", "womens cpl", "womens caribbean premier league"),
                ("Women's Caribbean Premier League",), "female"),
    Competition("bpl", "Bangladesh Premier League", ("bpl", "bangladesh premier league"), ("Bangladesh Premier League",), "male"),
    Competition("lpl", "Lanka Premier League", ("lpl", "lanka premier league"), ("Lanka Premier League",), "male"),
    Competition("sa20", "SA20", ("sa20", "sa 20", "sa t20"), ("SA20",), "male"),
    Competition("ilt20", "International League T20", ("ilt20", "ilt 20", "international league t20"),
                ("International League T20",), "male"),
    Competition("mlc", "Major League Cricket", ("mlc", "major league cricket"), ("Major League Cricket",), "male"),
    Competition("npl", "Nepal Premier League", ("npl", "nepal premier league"), ("Nepal Premier League",), "male"),
    Competition("smat", "Syed Mushtaq Ali Trophy", ("smat", "syed mushtaq ali", "syed mushtaq ali trophy", "mushtaq ali"),
                ("Syed Mushtaq Ali Trophy",), "male"),
    Competition("blast", "T20 Blast (England)", ("t20 blast", "blast", "vitality blast", "natwest t20 blast"),
                ("NatWest T20 Blast", "Vitality Blast", "Vitality Blast Men"), "male"),
    Competition("wblast", "Women's T20 Blast (England)", ("womens blast", "vitality blast women", "womens t20 blast"),
                ("Vitality Blast Women",), "female"),
    Competition("hundred", "The Hundred (men)", ("the hundred", "hundred", "the hundred mens", "hundred mens"),
                ("The Hundred Men's Competition",), "male"),
    Competition("whundred", "The Hundred (women)", ("the hundred womens", "hundred womens", "womens hundred"),
                ("The Hundred Women's Competition",), "female"),
    Competition("supersmash", "Super Smash", ("super smash",), ("Super Smash",), "male"),
    Competition("wsupersmash", "Women's Super Smash", ("womens super smash",), ("Women's Super Smash",), "female"),
    Competition("t20wc", "Men's T20 World Cup",
                ("t20 world cup", "t20 wc", "t20wc", "world t20", "world twenty20", "t20 worldcup", "icc t20 world cup",
                 "mens t20 world cup", "wt20"),
                ("ICC World Twenty20", "World T20", "ICC Men's T20 World Cup"), "male"),
    Competition("wt20wc", "Women's T20 World Cup",
                ("womens t20 world cup", "womens world t20", "womens t20 wc", "womens world twenty20"),
                ("Women's World T20", "ICC Women's World Twenty20", "ICC Women's T20 World Cup"), "female"),
    Competition("odiwc", "Men's ODI World Cup",
                ("world cup", "odi world cup", "cricket world cup", "cwc", "icc world cup", "50 over world cup",
                 "mens world cup", "mens odi world cup", "icc cricket world cup"),
                ("ICC World Cup", "ICC Cricket World Cup", "World Cup"), "male"),
    Competition("wodiwc", "Women's ODI World Cup",
                ("womens world cup", "womens odi world cup", "womens cricket world cup"),
                ("ICC Women's World Cup", "Women's World Cup"), "female"),
    Competition("ct", "ICC Champions Trophy", ("champions trophy", "ct", "icc champions trophy"), ("ICC Champions Trophy",), "male"),
    Competition("asiacup", "Asia Cup (men)", ("asia cup", "mens asia cup"), ("Asia Cup", "Men's T20 Asia Cup"), "male"),
    Competition("wasiacup", "Women's Asia Cup", ("womens asia cup",),
                ("Women's Asia Cup", "Women's Twenty20 Asia Cup", "Asian Cricket Council Women's Twenty20 Asia Cup"), "female"),
    Competition("ashes", "The Ashes", ("ashes", "the ashes"), ("The Ashes",), "male"),
    Competition("washes", "Women's Ashes", ("womens ashes",), ("Women's Ashes",), "female"),
    Competition("wtc", "ICC World Test Championship", ("wtc", "world test championship", "wtc final"),
                ("ICC World Test Championship",), "male"),
    Competition("county", "County Championship",
                ("county championship", "county cricket"),
                ("County Championship", "LV= County Championship", "Specsavers County Championship"), "male"),
    Competition("shield", "Sheffield Shield", ("sheffield shield", "shield"), ("Sheffield Shield",), "male"),
    Competition("plunket", "Plunket Shield", ("plunket shield",), ("Plunket Shield",), "male"),
    Competition("odc", "One-Day Cup (England)", ("royal london one day cup", "one day cup", "metro bank one day cup"),
                ("Royal London One-Day Cup", "One-Day Cup"), "male"),
)

_WOMEN_RE = re.compile(r"\b(women|womens|woman|ladies|female)\b")


@dataclass
class ResolvedCompetition:
    events: list[str]
    display: str
    gender: str | None
    note: str | None
    season: str | None = None  # a year embedded in the query ("IPL 2016")


def resolve_competition(query: str) -> ResolvedCompetition:
    cat = get_catalog()
    q = norm(query)

    season = None
    m = re.search(r"\b((?:19|20)\d{2}(?:/\d{2})?)\b", q)
    if m:
        season = m.group(1)
        q = " ".join(q.replace(m.group(1), " ").split())
    q = re.sub(r"\b(icc|season|edition|tournament|the)\b", " ", q)
    q = " ".join(q.split())
    wants_women = bool(_WOMEN_RE.search(q))
    q_plain = _WOMEN_RE.sub("", q).strip()
    q_key = ("womens " + q_plain) if wants_women else q_plain

    def present(events):
        return [e for e in events if e in cat.events]

    for comp in COMPETITIONS:
        names = {norm(a) for a in comp.aliases} | {norm(comp.display)}
        names |= {re.sub(r"\b(icc|the)\b", " ", n).strip() for n in names}
        names = {" ".join(n.split()) for n in names}
        if q_key in names or q in names:
            evs = present(comp.events)
            if evs:
                note = None
                if len(evs) > 1:
                    note = f"'{query}' covers {len(evs)} event names across eras: {', '.join(evs)}."
                return ResolvedCompetition(evs, comp.display, comp.gender, note, season)

    # Exact event name (case/punctuation-insensitive)
    by_norm = {norm(e): e for e in cat.events}
    for cand in (q, q_key):
        if cand in by_norm:
            e = by_norm[cand]
            return ResolvedCompetition([e], e, _single_gender(cat.events[e]), None, season)

    # Token containment: every query token appears in the event name.
    q_tokens = set(q_plain.split())
    if q_tokens:
        hits = []
        for e, info in cat.events.items():
            etoks = set(norm(e).split())
            if not q_tokens <= etoks:
                continue
            if wants_women != bool(_WOMEN_RE.search(norm(e))):
                continue
            if "qualifier" not in q_tokens and ("qualifier" in etoks or "qualifying" in etoks):
                continue
            hits.append((info["matches"], e))
        hits.sort(reverse=True)
        if len(hits) == 1 or (len(hits) > 1 and hits[0][0] >= 3 * hits[1][0]):
            e = hits[0][1]
            note = None if norm(e) == q else f"'{query}' resolved to event '{e}'."
            return ResolvedCompetition([e], e, _single_gender(cat.events[e]), note, season)
        if hits:
            raise ResolutionError(
                "competition", query,
                f"'{query}' matches several competitions. Pick the one the user means.",
                [{"event_name": e, "matches": n} for n, e in hits[:8]],
            )

    close = get_close_matches(q, list(by_norm), n=5, cutoff=0.6)
    raise ResolutionError(
        "competition", query,
        f"No competition matching '{query}'.",
        [{"event_name": by_norm[c], "matches": cat.events[by_norm[c]]["matches"]} for c in close],
    )


def _single_gender(info: dict) -> str | None:
    return info["genders"][0] if len(info["genders"]) == 1 else None


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

# Franchises that changed name: asking about either name means the whole
# franchise history.
FRANCHISE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("Royal Challengers Bangalore", "Royal Challengers Bengaluru"),
    ("Delhi Daredevils", "Delhi Capitals"),
    ("Kings XI Punjab", "Punjab Kings"),
    ("Rising Pune Supergiant", "Rising Pune Supergiants"),
)

TEAM_ALIASES = {
    "rcb": "Royal Challengers Bengaluru", "royal challengers": "Royal Challengers Bengaluru",
    "csk": "Chennai Super Kings", "chennai": "Chennai Super Kings",
    "mi": "Mumbai Indians",
    "kkr": "Kolkata Knight Riders", "kolkata": "Kolkata Knight Riders",
    "rr": "Rajasthan Royals", "rajasthan": "Rajasthan Royals",
    "srh": "Sunrisers Hyderabad", "sunrisers": "Sunrisers Hyderabad",
    "dc": "Delhi Capitals", "dd": "Delhi Daredevils",
    "pbks": "Punjab Kings", "kxip": "Kings XI Punjab",
    "gt": "Gujarat Titans", "lsg": "Lucknow Super Giants", "rps": "Rising Pune Supergiant",
    "ind": "India", "aus": "Australia", "eng": "England", "sa": "South Africa", "rsa": "South Africa",
    "proteas": "South Africa", "nz": "New Zealand", "kiwis": "New Zealand", "blackcaps": "New Zealand",
    "black caps": "New Zealand", "wi": "West Indies", "windies": "West Indies", "sl": "Sri Lanka",
    "pak": "Pakistan", "ban": "Bangladesh", "bd": "Bangladesh", "afg": "Afghanistan", "ire": "Ireland",
    "zim": "Zimbabwe", "ned": "Netherlands", "holland": "Netherlands", "sco": "Scotland",
    "uae": "United Arab Emirates", "usa": "United States of America", "us": "United States of America",
    "america": "United States of America", "nep": "Nepal",
}


@dataclass
class ResolvedTeam:
    names: list[str]
    display: str
    note: str | None


def resolve_team(query: str, gender: str | None = None) -> ResolvedTeam:
    cat = get_catalog()
    full = norm(query)
    # Cricsheet names women's sides like men's ("India", told apart by the
    # gender column), so "India Women" usually means "India" + gender filter --
    # but try an exact match first in case the data does name it that way.
    q = " ".join(re.sub(r"\b(women|womens|men|mens)\b", " ", full).split())

    def matches_for(team):
        g = cat.teams.get(team, {})
        return g.get(gender, 0) if gender else sum(g.values())

    by_norm = {norm(t): t for t in cat.teams}
    team = None
    if full in by_norm and matches_for(by_norm[full]) > 0:
        team = by_norm[full]
    elif q in TEAM_ALIASES and TEAM_ALIASES[q] in cat.teams:
        team = TEAM_ALIASES[q]
    elif q in by_norm:
        team = by_norm[q]
    else:
        hits = [t for n, t in by_norm.items() if q and q in n and matches_for(t) > 0]
        hits.sort(key=lambda t: -matches_for(t))
        if len(hits) == 1 or (len(hits) > 1 and matches_for(hits[0]) >= 3 * matches_for(hits[1])):
            team = hits[0]
        elif hits:
            raise ResolutionError(
                "team", query, f"'{query}' matches several teams. Pick one.",
                [{"team": t, "matches": matches_for(t)} for t in hits[:8]],
            )
        else:
            close = get_close_matches(q, list(by_norm), n=5, cutoff=0.75)
            if len(close) >= 1 and matches_for(by_norm[close[0]]) > 0:
                team = by_norm[close[0]]
            else:
                raise ResolutionError(
                    "team", query, f"No team matching '{query}'.",
                    [{"team": by_norm[c], "matches": matches_for(by_norm[c])} for c in close],
                )

    names = [team]
    for group in FRANCHISE_GROUPS:
        if team in group:
            names = [t for t in group if t in cat.teams]
    display = " / ".join(names)
    note = None
    if len(names) > 1:
        note = f"'{query}' includes the franchise's former names: {display}."
    elif norm(team) != norm(query):
        note = f"'{query}' resolved to team '{team}'."
    return ResolvedTeam(names, display, note)


# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------

VENUE_ALIASES = {
    "mcg": "melbourne cricket ground",
    "scg": "sydney cricket ground",
    "gabba": "brisbane cricket ground",
    "waca": "w a c a ground",
    "chepauk": "ma chidambaram stadium",
    "kotla": "feroz shah kotla|arun jaitley stadium",
    "feroz shah kotla": "feroz shah kotla|arun jaitley stadium",
    "arun jaitley stadium": "feroz shah kotla|arun jaitley stadium",
    "motera": "narendra modi stadium|sardar patel stadium",
    "the oval": "kennington oval",
    "oval": "kennington oval",
    "chinnaswamy": "m chinnaswamy stadium",
    "eden": "eden gardens",
    "wankhede": "wankhede stadium",
    "lords": "lords",
    "uppal": "rajiv gandhi international stadium",
}


@dataclass
class ResolvedVenue:
    venues: list[str]
    display: str
    note: str | None


def resolve_venue(query: str) -> ResolvedVenue:
    cat = get_catalog()
    q = norm(query)
    targets = VENUE_ALIASES.get(q, q).split("|")

    # Group raw venue strings by (base name, city) so "Eden Gardens" and
    # "Eden Gardens, Kolkata" are one ground, but "Lords, St David's" isn't Lord's.
    groups: dict[tuple[str, str], list[str]] = {}
    for v, info in cat.venues.items():
        groups.setdefault((_norm_base(info["base"]), _norm_city(info["city"])), []).append(v)

    def group_matches(key):
        return sum(cat.venues[v]["matches"] for v in groups[key])

    def merge_city_variants(keys):
        # Same base name, one entry with and one without a city: merge.
        by_base = {}
        for k in keys:
            by_base.setdefault(k[0], []).append(k)
        return by_base

    exact = [k for k in groups if any(_norm_base(t) == k[0] for t in targets)]
    contains = [k for k in groups if any(_contains_tokens(k[0], _norm_base(t)) for t in targets)]
    for keys in (exact, contains):
        if not keys:
            continue
        by_base = merge_city_variants(keys)
        ranked = sorted(by_base.items(), key=lambda kv: -sum(group_matches(k) for k in kv[1]))
        top_base, top_keys = ranked[0]
        top_n = sum(group_matches(k) for k in top_keys)
        if len(ranked) > 1 and len(targets) == 1:
            second_n = sum(group_matches(k) for k in ranked[1][1])
            if second_n * 3 > top_n:
                raise ResolutionError(
                    "venue", query, f"'{query}' matches several venues. Pick one.",
                    [{"venue": groups[kv[1][0]][0], "matches": sum(group_matches(k) for k in kv[1])} for kv in ranked[:8]],
                )
            ranked = ranked[:1]
        venues = sorted({v for _, ks in ranked for k in ks for v in groups[k]})
        # a base name shared by grounds in different cities (e.g. two "Lords"):
        # keep only the city with the most matches unless the city is blank
        cities = {}
        for v in venues:
            cities.setdefault(_norm_city(cat.venues[v]["city"]), []).append(v)
        if len(cities) > 1 and len(targets) == 1:
            real = {c: vs for c, vs in cities.items() if c}
            if real:
                best_city = max(real, key=lambda c: sum(cat.venues[v]["matches"] for v in real[c]))
                venues = sorted(real[best_city] + cities.get("", []))
        display = venues[0].split(",")[0] if len(targets) == 1 else " / ".join(sorted({v.split(",")[0] for v in venues}))
        note = None
        if len(venues) > 1 or norm(venues[0]) != q:
            note = f"'{query}' resolved to venue(s): {', '.join(venues)}."
        return ResolvedVenue(venues, display, note)

    # A city name: every ground in that city.
    city_hits = sorted(v for v, info in cat.venues.items() if _norm_city(info["city"]) == CITY_ALIASES.get(q, q))
    if city_hits:
        return ResolvedVenue(city_hits, query, f"'{query}' is a city; including all {len(city_hits)} venue name(s) there.")

    close = get_close_matches(q, sorted({info["base"] for info in cat.venues.values()}), n=5, cutoff=0.6)
    raise ResolutionError("venue", query, f"No venue matching '{query}'.", [{"venue_base": c} for c in close])


# Renamed cities, so a ground listed under both names stays one ground.
CITY_ALIASES = {"bangalore": "bengaluru", "bombay": "mumbai", "madras": "chennai", "calcutta": "kolkata",
                "gurgaon": "gurugram", "baroda": "vadodara", "trivandrum": "thiruvananthapuram"}


def _norm_city(city: str | None) -> str:
    c = norm(city or "")
    return CITY_ALIASES.get(c, c)


def _norm_base(s: str) -> str:
    # "M.Chinnaswamy" / "M Chinnaswamy" -> "m chinnaswamy"
    return " ".join(norm(s).split())


def _contains_tokens(haystack: str, needle: str) -> bool:
    return bool(needle) and set(needle.split()) <= set(haystack.split())


# ---------------------------------------------------------------------------
# Formats, genders, phases
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedFormat:
    match_types: tuple[str, ...]
    team_type: str | None
    display: str


FORMATS = {
    "test": ResolvedFormat(("Test",), None, "Test"),
    "odi": ResolvedFormat(("ODI",), None, "ODI"),
    "t20i": ResolvedFormat(("T20",), "international", "T20I"),
    "t20": ResolvedFormat(("T20", "IT20"), None, "all T20 (international + franchise/domestic)"),
    "first class": ResolvedFormat(("Test", "MDM"), None, "first-class (Tests + multi-day domestic)"),
    "list a": ResolvedFormat(("ODI", "ODM"), None, "List A (ODIs + one-day domestic)"),
    "international": ResolvedFormat(("Test", "ODI", "T20"), "international", "all international cricket"),
}
_FORMAT_ALIASES = {
    "tests": "test", "test match": "test", "test cricket": "test", "red ball": "test",
    "odis": "odi", "one day international": "odi", "one day internationals": "odi", "50 over": "odi",
    "t20is": "t20i", "t20 international": "t20i", "t20 internationals": "t20i", "t20 intl": "t20i",
    "twenty20 international": "t20i",
    "t20s": "t20", "twenty20": "t20", "t20 cricket": "t20", "all t20": "t20", "franchise": "t20",
    "fc": "first class", "firstclass": "first class", "list a cricket": "list a", "one day": "list a",
    "internationals": "international", "intl": "international", "all international": "international",
}


def resolve_format(query: str) -> ResolvedFormat:
    q = norm(query).replace("-", " ")
    q = _FORMAT_ALIASES.get(q, q)
    if q in FORMATS:
        return FORMATS[q]
    raise ResolutionError(
        "format", query,
        f"Unknown format '{query}'. Use one of: Test, ODI, T20I, T20 (all T20 incl. franchise), "
        "first-class, List A, international.",
    )


def resolve_gender(query: str | None) -> str | None:
    """'male'/'female', or None for both. Raises on garbage."""
    if query is None:
        return None
    q = norm(query)
    if q in ("male", "men", "mens", "man", "m"):
        return "male"
    if q in ("female", "women", "womens", "woman", "w", "ladies"):
        return "female"
    if q in ("all", "both", "any", ""):
        return None
    raise ResolutionError("gender", query, f"Unknown gender '{query}'. Use 'male', 'female' or 'all'.")


PHASES = ("powerplay", "middle", "death")


def resolve_phase(query: str) -> str:
    q = norm(query)
    if q in ("powerplay", "power play", "pp", "powerplays"):
        return "powerplay"
    if q in ("middle", "middle overs", "middle phase"):
        return "middle"
    if q in ("death", "death overs", "slog", "slog overs", "end overs"):
        return "death"
    raise ResolutionError("phase", query, "phase must be one of: powerplay, middle, death.")
