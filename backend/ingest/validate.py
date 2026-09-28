"""
The data-release gate: checks a freshly built database before it's published.

    python -m ingest.validate --db data/cricket.duckdb
    python -m ingest.validate --db data/cricket.duckdb --previous old_manifest.json
    python -m ingest.validate --db data/cricket.duckdb --freeze     # rewrite frozen_careers.json

Exits non-zero if any check fails. The checks:

  schema      every expected table and column exists; build_info is current;
              the derived tables are non-empty.
  counts      no table shrank by more than SHRINK_TOLERANCE against the
              previous release's manifest (Cricsheet only ever adds matches).
  invariants  every run is in batting_innings exactly once; no super-over
              innings reach the derived tables; runs above expected sums to
              about zero; every match has a data_version.
  careers     retired players' career totals, computed with SQL straight over
              the raw ball-by-ball tables (independent of analytics/build.py),
              match the frozen values in frozen_careers.json -- and the
              derived tables agree with that raw SQL. A retired player's
              record can't legitimately change, so a difference means the
              pipeline broke. (Cricsheet does occasionally back-fill an old
              match; if that's the cause, review it and re-run --freeze.)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import duckdb

from analytics.build import DERIVED_TABLES
from analytics.scope import CREDITED_SQL, NOT_DISMISSALS_SQL
from ingest.build_db import SCHEMA, SCHEMA_VERSION

FROZEN_FILE = Path(__file__).with_name("frozen_careers.json")
SHRINK_TOLERANCE = 0.005
# Runs above expected is zero by construction per ball state, except where a
# thin state falls back to the all-years estimate.
RAE_TOLERANCE = 0.01

# Retired players whose records are frozen (register unique names). A name is
# only frozen if the player's last match is at least RETIRED_YEARS before the
# newest match in the database.
FROZEN_PLAYERS = [
    "SR Tendulkar", "R Dravid", "A Kumble", "JH Kallis", "KC Sangakkara", "DPMD Jayawardene",
    "M Muralitharan", "SL Malinga", "DW Steyn", "AB de Villiers", "BB McCullum", "Mohammad Hafeez",
    "M Raj", "CM Edwards",
]
RETIRED_YEARS = 2

FGROUP_SQL = ("CASE WHEN m.match_type IN ('T20', 'IT20') THEN 'T20' "
              "WHEN m.match_type IN ('ODI', 'ODM') THEN 'ODI' ELSE 'MULTI' END")
METRICS = ("bat_runs", "bat_balls", "bat_outs", "bowl_balls", "bowl_runs", "bowl_wkts")


class Report:
    def __init__(self):
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def check(self, ok: bool, msg: str):
        if not ok:
            self.failures.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}


def _columns(con, table: str) -> set[str]:
    return {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}


def _one(con, sql: str, params=None):
    return con.execute(sql, params or []).fetchone()[0]


# --- raw-table career totals ------------------------------------------------

def raw_career(con, player_id: str) -> dict:
    """Career totals per format group from the raw tables only. Within a match
    a name is one person, so the player's name is matched inside the matches
    their register id played in."""
    regular = "NOT COALESCE(d.is_super_over, FALSE)"
    sql = f"""
        WITH pm AS (
            SELECT DISTINCT match_id, player FROM players_matches WHERE player_id = $pid
        ),
        d AS (
            SELECT d.*, pm.player AS me, {FGROUP_SQL} AS fgroup
            FROM deliveries d JOIN pm ON pm.match_id = d.match_id JOIN matches m ON m.match_id = d.match_id
            WHERE {regular}
        ),
        balls AS (
            SELECT fgroup,
                   SUM(CASE WHEN batter = me THEN runs_batter ELSE 0 END) AS bat_runs,
                   SUM(CASE WHEN batter = me AND COALESCE(extra_wides, 0) = 0 THEN 1 ELSE 0 END) AS bat_balls,
                   SUM(CASE WHEN bowler = me AND COALESCE(extra_wides, 0) = 0 AND COALESCE(extra_noballs, 0) = 0
                            THEN 1 ELSE 0 END) AS bowl_balls,
                   SUM(CASE WHEN bowler = me
                            THEN runs_batter + COALESCE(extra_wides, 0) + COALESCE(extra_noballs, 0)
                            ELSE 0 END) AS bowl_runs
            FROM d GROUP BY fgroup
        ),
        w AS (
            SELECT d.fgroup,
                   SUM(CASE WHEN w.player_out = d.me AND w.kind NOT IN {NOT_DISMISSALS_SQL} THEN 1 ELSE 0 END) AS bat_outs,
                   SUM(CASE WHEN d.bowler = d.me AND w.kind IN {CREDITED_SQL} THEN 1 ELSE 0 END) AS bowl_wkts
            FROM deliveries_wickets w
            JOIN d ON d.match_id = w.match_id AND d.innings_num = w.innings_num
                  AND d.over_num = w.over_num AND d.ball_in_over = w.ball_in_over
            GROUP BY d.fgroup
        )
        SELECT balls.fgroup, bat_runs, bat_balls, COALESCE(bat_outs, 0), bowl_balls, bowl_runs, COALESCE(bowl_wkts, 0)
        FROM balls LEFT JOIN w USING (fgroup) ORDER BY 1
    """
    return {r[0]: dict(zip(METRICS, map(int, r[1:]))) for r in con.execute(sql, {"pid": player_id}).fetchall()}


def derived_career(con, unique_name: str) -> dict:
    """The same totals from the derived per-innings tables."""
    out: dict[str, dict] = {}
    for fg, runs, balls, outs in con.execute(
            "SELECT fgroup, SUM(runs), SUM(balls), SUM(out) FROM batting_innings WHERE player = ? GROUP BY 1",
            [unique_name]).fetchall():
        out.setdefault(fg, dict.fromkeys(METRICS, 0)).update(bat_runs=int(runs), bat_balls=int(balls), bat_outs=int(outs))
    for fg, balls, runs, wkts in con.execute(
            "SELECT fgroup, SUM(balls), SUM(runs), SUM(wickets) FROM bowling_innings WHERE player = ? GROUP BY 1",
            [unique_name]).fetchall():
        out.setdefault(fg, dict.fromkeys(METRICS, 0)).update(bowl_balls=int(balls), bowl_runs=int(runs), bowl_wkts=int(wkts))
    return out


def _nonzero(career: dict) -> dict:
    return {fg: m for fg, m in career.items() if any(m.values())}


def _person(con, unique_name: str):
    row = con.execute("SELECT identifier FROM people WHERE unique_name = ?", [unique_name]).fetchone()
    return row[0] if row else None


def freeze(con, path: Path = FROZEN_FILE) -> dict:
    """Record the current raw career totals of FROZEN_PLAYERS."""
    latest = _one(con, "SELECT MAX(date) FROM matches")
    cutoff = str(int(latest[:4]) - RETIRED_YEARS) + latest[4:]
    frozen = {}
    for name in FROZEN_PLAYERS:
        pid = _person(con, name)
        if not pid:
            print(f"  skip {name}: not in the register")
            continue
        last = _one(con, "SELECT MAX(m.date) FROM players_matches pm JOIN matches m USING (match_id) "
                         "WHERE pm.player_id = ?", [pid])
        if last is None or last > cutoff:
            print(f"  skip {name}: last match {last} is too recent to freeze")
            continue
        frozen[name] = {"player_id": pid, "last_match": last, "career": raw_career(con, pid)}
        print(f"  froze {name} ({pid}), last match {last}")
    doc = {"frozen_at": dt.date.today().isoformat(), "latest_match": latest, "players": frozen}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


# --- the checks -------------------------------------------------------------

def check_schema(con, rep: Report):
    tables = _tables(con)
    for t, ddl in SCHEMA.items():
        if t not in tables:
            rep.check(False, f"schema: table {t} missing")
            continue
        want = {c.split()[0] for c in ddl.replace("\n", " ").split(",")}
        missing = want - _columns(con, t)
        rep.check(not missing, f"schema: {t} missing columns {sorted(missing)}")
    for t in ("people", "people_names", "build_info", *DERIVED_TABLES):
        rep.check(t in tables, f"schema: table {t} missing")
    for t in DERIVED_TABLES:
        if t in tables:
            rep.check(_one(con, f"SELECT COUNT(*) FROM {t}") > 0, f"schema: derived table {t} is empty")
    if "build_info" in tables:
        info = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM build_info").fetchall()}
        rep.check(info.get("schema_version") == SCHEMA_VERSION,
                  f"schema: build_info schema_version {info.get('schema_version')} != {SCHEMA_VERSION}")
        rep.check(not info.get("incremental"), "schema: releases must be full builds, not incremental")


def row_counts(con) -> dict:
    tables = _tables(con)
    names = [*SCHEMA, "people", "people_names", *DERIVED_TABLES]
    return {t: _one(con, f"SELECT COUNT(*) FROM {t}") for t in names if t in tables}


def check_counts(con, rep: Report, previous: dict | None):
    if not previous:
        rep.warn("counts: no previous manifest; shrink check skipped")
        return
    now = row_counts(con)
    for t, before in (previous.get("rows") or {}).items():
        if t not in now:
            continue
        rep.check(now[t] >= before * (1 - SHRINK_TOLERANCE),
                  f"counts: {t} shrank from {before:,} to {now[t]:,}")


def check_invariants(con, rep: Report):
    raw_runs = _one(con, "SELECT SUM(runs_batter) FROM deliveries "
                         "WHERE NOT COALESCE(is_super_over, FALSE) AND batter IS NOT NULL")
    bi_runs = _one(con, "SELECT SUM(runs) FROM batting_innings")
    rep.check(raw_runs == bi_runs, f"invariant: batting_innings has {bi_runs:,} runs, deliveries {raw_runs:,}")

    leaked = _one(con, """
        SELECT COUNT(*) FROM batting_innings bi
        JOIN (SELECT DISTINCT match_id, innings_num FROM deliveries WHERE is_super_over) s
          USING (match_id, innings_num)""")
    rep.check(leaked == 0, f"invariant: {leaked} super-over innings in batting_innings")

    for fg, runs, rae in con.execute("""
            SELECT fgroup, SUM(runs), SUM(runs - exp_runs) FROM batting_innings
            GROUP BY fgroup ORDER BY 1""").fetchall():
        rep.check(abs(rae) <= RAE_TOLERANCE * runs,
                  f"invariant: runs above expected sums to {rae:,.0f} for {fg} ({runs:,} runs)")

    no_version = _one(con, "SELECT COUNT(*) FROM matches WHERE data_version IS NULL")
    rep.check(no_version == 0, f"invariant: {no_version} matches without a data_version")

    unknown = _one(con, """SELECT COUNT(DISTINCT player_id) FROM players_matches
                           WHERE player_id IS NOT NULL AND player_id NOT IN (SELECT identifier FROM people)""")
    if unknown:
        rep.warn(f"register: {unknown} player ids aren't in people.csv yet")


def check_careers(con, rep: Report, frozen_path: Path = FROZEN_FILE):
    if not frozen_path.exists():
        rep.warn(f"careers: {frozen_path.name} not found; run with --freeze")
        return
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))["players"]
    rep.check(bool(frozen), "careers: frozen_careers.json has no players")
    for name, f in frozen.items():
        raw = raw_career(con, f["player_id"])
        if raw != f["career"]:
            rep.check(False, f"careers: {name}'s raw totals changed: {f['career']} -> {raw}")
            continue
        derived = derived_career(con, name)
        rep.check(_nonzero(derived) == _nonzero(raw),
                  f"careers: {name}'s derived totals {derived} disagree with the raw tables {raw}")


def validate(db_path: Path, previous: dict | None = None, frozen_path: Path = FROZEN_FILE) -> Report:
    rep = Report()
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        check_schema(con, rep)
        if rep.failures:  # the remaining checks assume the schema
            return rep
        check_counts(con, rep, previous)
        check_invariants(con, rep)
        check_careers(con, rep, frozen_path)
    finally:
        con.close()
    return rep


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate a built cricket database before release.")
    parser.add_argument("--db", default="data/cricket.duckdb")
    parser.add_argument("--previous", help="manifest.json of the previous release (for the shrink check)")
    parser.add_argument("--freeze", action="store_true", help="rewrite frozen_careers.json from this database")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        parser.error(f"not found: {db_path}")
    if args.freeze:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            doc = freeze(con)
        finally:
            con.close()
        print(f"Froze {len(doc['players'])} careers into {FROZEN_FILE}")
        return 0

    previous = None
    if args.previous and Path(args.previous).exists():
        previous = json.loads(Path(args.previous).read_text(encoding="utf-8"))
    rep = validate(db_path, previous)
    for w in rep.warnings:
        print(f"WARN  {w}")
    for f in rep.failures:
        print(f"FAIL  {f}")
    print("Validation " + ("FAILED" if rep.failures else "passed") + f" ({len(rep.failures)} failures, "
          f"{len(rep.warnings)} warnings)")
    return 1 if rep.failures else 0


if __name__ == "__main__":
    sys.exit(main())
