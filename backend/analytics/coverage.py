"""
What the database covers, and what it doesn't: Doosra's own match counts by
format, next to Cricsheet's published coverage periods, coverage figures and
list of known-missing matches (parsed by ingest/coverage.py).

    data_coverage     formats, competitions and teams: what's in, what's missing
    missing_matches   the known-missing matches, filterable
    player_notes      coverage caveats for one player's record (used by the
                      player profile)
"""

from __future__ import annotations

from . import catalog, db
from .catalog import ResolutionError
from .engine import EngineError, _tool
from .results import result
from .scope import lit, lit_list

# Cricsheet's match-type names -> the same matches in the matches table.
MATCH_TYPES = {
    "Test Matches": ("Tests", "m.match_type = 'Test'"),
    "One-day Internationals": ("ODIs", "m.match_type = 'ODI'"),
    "T20 Internationals": ("T20Is", "m.match_type = 'T20' AND m.team_type = 'international'"),
    "International T20s": ("Other international T20s", "m.match_type = 'IT20'"),
    "Multi-day Matches": ("Other multi-day (first-class)", "m.match_type = 'MDM'"),
    "One-day matches": ("Other one-day (List A)", "m.match_type = 'ODM'"),
}
FORMAT_WORDS = {"test": "Test Matches", "tests": "Test Matches", "odi": "One-day Internationals",
                "odis": "One-day Internationals", "t20i": "T20 Internationals", "t20is": "T20 Internationals"}
WITHHELD_ARTICLE = "https://cricsheet.org/article/explanation_for_withholding_of_afghanistani_matches/"


def available() -> bool:
    return bool(db.query("SELECT COUNT(*) AS n FROM information_schema.tables "
                         "WHERE table_name IN ('missing_matches', 'coverage_counts', 'coverage_periods')")[0]["n"] == 3)


def _require():
    if not available():
        raise EngineError("Coverage data isn't loaded: rebuild the database with Cricsheet's coverage pages "
                          "(python -m ingest.update, or python -m ingest.pull for the published build).")


def withheld() -> dict:
    if not db.query("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_name = 'coverage_info'")[0]["n"]:
        return {}
    info = {r["key"]: r["value"] for r in db.query("SELECT key, value FROM coverage_info")}
    n = info.get("withheld_matches")
    return {"matches": int(n) if n else None, "reason": info.get("withheld_reason"), "article": WITHHELD_ARTICLE}


@_tool
def data_coverage(gender: str | None = None) -> dict:
    """Doosra's matches by format, against Cricsheet's coverage periods and
    known-missing matches; plus Cricsheet's coverage by competition and team."""
    _require()
    g = catalog.resolve_gender(gender) if gender else None
    rows = []
    for name, (label, cond) in MATCH_TYPES.items():
        for gen in ([g] if g else ["male", "female"]):
            per = db.query("SELECT earliest_checked, earliest_provided FROM coverage_periods "
                           "WHERE kind = 'match type' AND name = ? AND gender = ?", [name, gen])
            checked = per[0]["earliest_checked"] if per else None
            have = db.query(f"""
                SELECT COUNT(*) AS n, MIN(m.date) AS first, MAX(m.date) AS last,
                       COUNT(*) FILTER (WHERE substr(m.date, 1, 7) >= {lit(checked or '0000')}) AS since
                FROM matches m WHERE {cond} AND m.gender = {lit(gen)}""")[0]
            missing = db.query("SELECT COUNT(*) AS n FROM missing_matches WHERE kind = 'match type' "
                               "AND name = ? AND gender = ?", [name, gen])[0]["n"]
            if not have["n"] and not per:
                continue
            listed = name in ("Test Matches", "One-day Internationals")
            pct = 100.0 * have["since"] / (have["since"] + missing) if listed and have["since"] + missing else None
            rows.append([label, gen, have["n"], have["first"], have["last"], checked,
                         per[0]["earliest_provided"] if per else None, missing if listed else None, pct])
    cols = ["format", "gender", "matches", "first", "last", "checked_from", "data_from", "known_missing",
            "coverage_pct"]
    labels = ["Format", "Gender", "Matches in Doosra", "First", "Last", "Cricsheet checks from",
              "Data from", "Known missing", "Coverage %"]

    comps = db.query(f"""
        SELECT c.name, c.gender, c.have, c.total, c.pct, p.earliest_provided AS data_from
        FROM coverage_counts c
        LEFT JOIN coverage_periods p ON p.kind = 'competition' AND p.name = c.name AND p.gender = c.gender
        WHERE c.kind = 'competition' {f"AND c.gender = {lit(g)}" if g else ""}
        ORDER BY c.pct, c.total DESC""")
    teams = db.query("SELECT name, have, total, pct FROM coverage_counts WHERE kind = 'team' ORDER BY pct, total DESC")
    notes = [
        "Coverage % for Tests and ODIs = matches held / (held + known missing) since Cricsheet began checking. "
        "Cricsheet lists missing matches only for Tests, ODIs and the competitions it covers.",
        "Matches before 'Cricsheet checks from' aren't counted as missing -- the data simply starts there, so "
        "careers that began earlier are incomplete.",
    ]
    w = withheld()
    if w.get("matches"):
        notes.append(f"Cricsheet also withholds {w['matches']:,} matches: {w['reason']}")
    return result("Data coverage by format", cols, rows, None, notes, labels=labels,
                  competitions=result("Coverage by competition (Cricsheet)", ["competition", "gender", "have",
                                      "total", "pct", "data_from"],
                                      [[r["name"], r["gender"], r["have"], r["total"], r["pct"], r["data_from"]]
                                       for r in comps]),
                  teams=result("Coverage by team (Cricsheet)", ["team", "have", "total", "pct"],
                               [[r["name"], r["have"], r["total"], r["pct"]] for r in teams]),
                  withheld=w or None,
                  sources={"coverage": "https://cricsheet.org/coverage/", "missing": "https://cricsheet.org/missing/"})


def _competition_names(q: str) -> list[str]:
    names = {q}
    try:
        comp = catalog.resolve_competition(q)
        names.add(comp.display)
        names.update(comp.events)
    except ResolutionError:
        pass
    return sorted(names)


@_tool
def missing_matches(format: str | None = None, competition: str | None = None, gender: str | None = None,  # noqa: A002
                    team: str | None = None, from_year: int | None = None, to_year: int | None = None,
                    limit: int = 500) -> dict:
    """Matches Cricsheet knows it is missing (no ball-by-ball data), newest
    first. Only Tests, ODIs and the competitions Cricsheet covers are listed."""
    _require()
    where = []
    if format:
        name = FORMAT_WORDS.get(str(format).strip().lower())
        if not name:
            raise EngineError("format must be Test or ODI -- Cricsheet lists missing matches only for Tests, ODIs "
                              "and the competitions it covers (use competition= for those).")
        where.append(f"kind = 'match type' AND name = {lit(name)}")
    if competition:
        names = _competition_names(competition)
        where.append("kind = 'competition' AND (" + " OR ".join(
            [f"lower(name) = lower({lit(n)})" for n in names] + [f"name ILIKE {lit('%' + competition + '%')}"]) + ")")
    g = catalog.resolve_gender(gender) if gender else None
    if g:
        where.append(f"gender = {lit(g)}")
    if team:
        where.append(f"(team1 ILIKE {lit('%' + team + '%')} OR team2 ILIKE {lit('%' + team + '%')})")
    if from_year:
        where.append(f"CAST(substr(date, 1, 4) AS INTEGER) >= {int(from_year)}")
    if to_year:
        where.append(f"CAST(substr(date, 1, 4) AS INTEGER) <= {int(to_year)}")
    limit = max(1, min(int(limit or 500), 5000))
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.query(f"SELECT COUNT(*) AS n FROM missing_matches {clause}")[0]["n"]
    rows = db.query(f"SELECT date, team1, team2, name, gender FROM missing_matches {clause} "
                    f"ORDER BY date DESC LIMIT {limit}")
    cols = ["date", "team1", "team2", "competition_or_format", "gender"]
    notes = [f"{total:,} known-missing matches match these filters" + (f"; showing the latest {limit}." if total > limit
                                                                        else ".")]
    return result("Matches missing from the data", cols, [[r[c if c != "competition_or_format" else "name"]
                                                           for c in cols] for r in rows],
                  None, notes, total=total, source="https://cricsheet.org/missing/")


_SINGULAR = {"Test Matches": ("Test", "Tests"), "One-day Internationals": ("ODI", "ODIs")}


def _count(n: int, name: str) -> str:
    """'1 Test', '16 Tests', '3 Syed Mushtaq Ali Trophy matches'."""
    if name in _SINGULAR:
        return f"{n} {_SINGULAR[name][n != 1]}"
    return f"{n} {name} match{'es' if n != 1 else ''}"


def player_notes(player: catalog.Player) -> list[str]:
    """Caveats for one player's record: matches their teams played during
    their career that Cricsheet has no data for, and withheld matches."""
    if not available():
        return []
    notes = []
    if "Afghanistan" in (player.teams or []):
        w = withheld()
        notes.append(f"Cricsheet withholds matches involving Afghanistan ({w.get('matches') or 'several hundred'} "
                     "in all), so this player's matches for or against Afghanistan aren't in the data.")
    if not player.teams or not player.first or not player.last:
        return notes
    rows = db.query(f"""
        SELECT name, COUNT(*) AS n FROM missing_matches
        WHERE (team1 IN {lit_list(player.teams)} OR team2 IN {lit_list(player.teams)})
          AND gender = {lit(player.gender or 'male')} AND date BETWEEN {lit(player.first)} AND {lit(player.last)}
        GROUP BY name ORDER BY n DESC""")
    if rows:
        total = sum(r["n"] for r in rows)
        parts = ", ".join(_count(r["n"], r["name"]) for r in rows[:4])
        notes.append(f"Cricsheet has no ball-by-ball data for {total} match{'es' if total != 1 else ''} this "
                     f"player's teams played during their career ({parts}), so totals may fall short of official "
                     "records if they played in them.")
    return notes
