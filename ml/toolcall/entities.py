"""
Entity pools for the tool-calling training data: real players, teams, venues, competitions and seasons
from Doosra's database, with what each one actually played (so generated questions make sense: a bowler
is asked about wickets, an IPL player about the IPL).

About 15% of players, teams and venues are held out (by a stable hash of the name): they appear only in
the test split, so the evaluation measures generalisation to names the model never saw in training.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from analytics import catalog, db  # noqa: E402

HOLDOUT_SHARE = 0.15
FORMAT_LABEL = {"T20I": "T20Is", "ODI": "ODIs", "Test": "Tests", "T20": "T20s"}


def held_out(name: str) -> bool:
    return int(hashlib.sha1(name.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < HOLDOUT_SHARE


@dataclass
class Player:
    name: str            # how people write it ("Virat Kohli"); the tools resolve it
    stored: str          # Doosra's unique name ("V Kohli")
    gender: str
    batter: bool
    bowler: bool
    formats: list[str]   # formats with enough innings, most played first ("T20I", "ODI", "Test", "T20")
    competitions: list[str] = field(default_factory=list)   # leagues/tournaments with enough innings
    teams: list[str] = field(default_factory=list)
    weight: float = 1.0  # how often it's picked: square root of matches, so stars are common without drowning the rest

    @property
    def test(self) -> bool:
        return held_out(self.name)


@dataclass
class Pools:
    players: list[Player]
    teams: list[dict]          # {name, gender, international}
    venues: list[dict]         # {name, weight}
    competitions: list[dict]   # {name, format, gender, seasons, weight}

    def split(self, test: bool) -> "Pools":
        keep = (lambda n: held_out(n)) if test else (lambda n: not held_out(n))
        return Pools([p for p in self.players if keep(p.name)], [t for t in self.teams if keep(t["name"])],
                     [v for v in self.venues if keep(v["name"])], self.competitions)


def _full_names() -> dict[str, str]:
    """Doosra's unique name -> the name people write (the register's longest alternate name that isn't
    initials, e.g. 'V Kohli' -> 'Virat Kohli')."""
    rows = db.query("""
        SELECT pe.unique_name AS stored, pn.name
        FROM people pe JOIN people_names pn USING (identifier)""")
    best: dict[str, str] = {}
    for r in rows:
        first = r["name"].split()[0] if r["name"].split() else ""
        if len(first) <= 2 or "." in first or len(r["name"].split()) < 2:
            continue                                            # "V Kohli", "J B": initials, not a name
        if len(r["name"]) > len(best.get(r["stored"], "")):
            best[r["stored"]] = r["name"]
    return best


@lru_cache(maxsize=1)
def load() -> Pools:
    names = _full_names()
    bat = {(r["player"], r["gender"]): r for r in db.query("""
        SELECT player, gender, COUNT(*) AS inns, SUM(balls) AS balls FROM batting_innings GROUP BY 1, 2""")}
    bowl = {(r["player"], r["gender"]): r for r in db.query("""
        SELECT player, gender, COUNT(*) AS inns, SUM(balls) AS balls FROM bowling_innings GROUP BY 1, 2""")}
    fmts: dict[tuple, list] = {}
    for r in db.query("""
        SELECT player, gender, fmt, COUNT(*) AS n FROM batting_innings GROUP BY 1, 2, 3 HAVING COUNT(*) >= 8
        UNION ALL
        SELECT player, gender, fmt, COUNT(*) AS n FROM bowling_innings GROUP BY 1, 2, 3 HAVING COUNT(*) >= 8
        ORDER BY n DESC"""):
        fmts.setdefault((r["player"], r["gender"]), []).append(r["fmt"])
    comps: dict[tuple, list] = {}
    for r in db.query("""
        SELECT player, gender, event_name, COUNT(*) AS n FROM batting_innings
        WHERE event_name IS NOT NULL AND team_type = 'club' GROUP BY 1, 2, 3 HAVING COUNT(*) >= 10 ORDER BY n DESC"""):
        comps.setdefault((r["player"], r["gender"]), []).append(r["event_name"])
    cat = catalog.get_catalog()
    players = []
    for p in cat.players.values():
        key = (p.name, p.gender)
        b, w = bat.get(key), bowl.get(key)
        if p.matches < 40 or not (b or w):
            continue
        formats = [f for f in dict.fromkeys(fmts.get(key, [])) if f in FORMAT_LABEL]
        if not formats:
            continue
        players.append(Player(
            name=names.get(p.name, p.raw_name or p.name), stored=p.name, gender=p.gender or "male",
            batter=bool(b and (b["balls"] or 0) >= 1000), bowler=bool(w and (w["balls"] or 0) >= 1500),
            formats=formats, competitions=comps.get(key, [])[:3], teams=p.teams[:3], weight=p.matches ** 0.5))
    teams = [{"name": t, "gender": max(g, key=g.get), "international": t in _internationals(),
              "weight": sum(g.values()) ** 0.5} for t, g in cat.teams.items() if sum(g.values()) >= 60]
    grounds: dict[str, int] = {}
    for v, info in cat.venues.items():
        grounds[v.split(",")[0]] = grounds.get(v.split(",")[0], 0) + info["matches"]
    venues = [{"name": v, "weight": n ** 0.5} for v, n in sorted(grounds.items()) if n >= 40]
    competitions = [{"name": r["event_name"], "format": r["fmt"], "gender": r["gender"],
                     "seasons": sorted(r["seasons"]), "weight": r["matches"] ** 0.5}
                    for r in db.query("""
                        SELECT event_name, any_value(fmt) AS fmt, any_value(gender) AS gender,
                               list(DISTINCT season) AS seasons, COUNT(DISTINCT match_id) AS matches
                        FROM batting_innings WHERE event_name IS NOT NULL
                        GROUP BY 1 HAVING COUNT(DISTINCT match_id) >= 60""")]
    return Pools(players, teams, venues, competitions)


@lru_cache(maxsize=1)
def _internationals() -> frozenset:
    return frozenset(r["t"] for r in db.query("""
        SELECT team1 AS t FROM matches WHERE team_type = 'international'
        UNION SELECT team2 FROM matches WHERE team_type = 'international'"""))


if __name__ == "__main__":
    p = load()
    test = p.split(True)
    print(f"{len(p.players):,} players ({sum(x.batter for x in p.players):,} batters, "
          f"{sum(x.bowler for x in p.players):,} bowlers; {len(test.players):,} held out), "
          f"{len(p.teams)} teams, {len(p.venues)} venues, {len(p.competitions)} competitions")
    for x in p.players[:3] + [y for y in p.players if y.stored in ("V Kohli", "JJ Bumrah", "S Mandhana")]:
        print(x)
    print(p.competitions[:3])
