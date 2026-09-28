"""
Builds the cricket database from Cricsheet data.

    python ingest/build_db.py --zip data/raw/all_json.zip --register-dir data/raw --out data/cricket.duckdb
    python ingest/build_db.py --raw-dir path/to/json/folder --out data/cricket.duckdb
    python ingest/build_db.py --zip recently_added_7_json.zip --incremental --out data/cricket.duckdb

Match JSON is read straight from the Cricsheet zip (streamed, never
extracted) or from a folder of files. Matches are parsed and written in
batches so the whole archive never sits in memory. --incremental upserts the
given matches into an existing database instead of rebuilding.

After building, run `python -m analytics.build` for the derived tables.

Tables
------
matches            one row per match. The FORMAT is match_type (Test, ODI, T20,
                   IT20, ODM, MDM); the COMPETITION is event_name. winner is NULL
                   for ties/draws/no results -- `result` says which, and
                   `eliminator` names the super-over/bowl-out winner of a tie.
deliveries         one row per ball. extra_* hold each extra type's runs (a ball
                   can carry several); wicket_kind/player_dismissed describe the
                   FIRST wicket only -- see deliveries_wickets. is_super_over marks
                   super-over innings; non_boundary marks a "4" that was run.
deliveries_wickets one row per wicket (a ball can have two), with fielders.
players_matches    the playing XI of each match, with Cricsheet person ids.
people             the Cricsheet register (one row per person, ids for other sites).
people_names       every name the register knows a person by.
coverage_*,        Cricsheet's coverage periods and figures, and its list of
missing_matches    known-missing matches (ingest/coverage.py), when the pages
                   are in the register folder.
build_info         when and from what the database was built.

Data: Cricsheet (https://cricsheet.org), Open Data Commons Attribution
License 1.0. Public use must credit Cricsheet.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import duckdb
import pandas as pd
from tqdm import tqdm

try:
    from ingest import coverage
except ImportError:  # run as a script: python ingest/build_db.py
    import coverage

# Matches are parsed and flushed to disk in batches so the whole Cricsheet
# archive (tens of thousands of matches) doesn't have to sit in memory.
BATCH_SIZE = 500
SCHEMA_VERSION = 3

SCHEMA = {
    "matches": """
        match_id VARCHAR, match_type VARCHAR, event_name VARCHAR, match_number VARCHAR,
        gender VARCHAR, team_type VARCHAR, overs_per_innings INTEGER, balls_per_over INTEGER,
        date VARCHAR, venue VARCHAR, city VARCHAR, season VARCHAR, team1 VARCHAR, team2 VARCHAR,
        toss_winner VARCHAR, toss_decision VARCHAR, winner VARCHAR, win_by_runs INTEGER,
        win_by_wickets INTEGER, result VARCHAR, eliminator VARCHAR, method VARCHAR,
        target_runs INTEGER, target_overs DOUBLE, player_of_match VARCHAR,
        data_version VARCHAR, revision INTEGER""",
    "deliveries": """
        match_id VARCHAR, innings_num INTEGER, batting_team VARCHAR, is_super_over BOOLEAN,
        over_num INTEGER, ball_in_over INTEGER, batter VARCHAR, bowler VARCHAR, non_striker VARCHAR,
        runs_batter INTEGER, runs_extras INTEGER, runs_total INTEGER, non_boundary BOOLEAN,
        extra_type VARCHAR, extra_wides INTEGER, extra_noballs INTEGER, extra_byes INTEGER,
        extra_legbyes INTEGER, extra_penalty INTEGER,
        is_wicket BOOLEAN, wicket_kind VARCHAR, player_dismissed VARCHAR""",
    "deliveries_wickets": """
        match_id VARCHAR, innings_num INTEGER, over_num INTEGER, ball_in_over INTEGER,
        wicket_seq INTEGER, kind VARCHAR, player_out VARCHAR, fielders VARCHAR, fielder_ids VARCHAR""",
    "players_matches": """
        match_id VARCHAR, team VARCHAR, player VARCHAR, player_id VARCHAR""",
}
MATCH_TABLES = tuple(SCHEMA)


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_match(match_id: str, data: dict):
    info = data.get("info", {})
    meta = data.get("meta", {}) or {}
    dates = info.get("dates", [])
    outcome = info.get("outcome", {}) or {}
    teams = info.get("teams", [])
    event = info.get("event", {}) or {}
    registry = (info.get("registry") or {}).get("people") or {}
    innings_list = data.get("innings", []) or []

    target = {}
    for inn in innings_list:
        if inn.get("target"):
            target = inn["target"]
            break

    match_row = {
        "match_id": match_id,
        "match_type": info.get("match_type"),
        "event_name": event.get("name"),
        "match_number": str(event.get("match_number")) if event.get("match_number") is not None else event.get("stage"),
        "gender": info.get("gender"),
        "team_type": info.get("team_type"),
        "overs_per_innings": _int(info.get("overs")),
        "balls_per_over": _int(info.get("balls_per_over")),
        "date": dates[0] if dates else None,
        "venue": info.get("venue"),
        "city": info.get("city"),
        "season": str(info.get("season")) if info.get("season") is not None else None,
        "team1": teams[0] if len(teams) > 0 else None,
        "team2": teams[1] if len(teams) > 1 else None,
        "toss_winner": (info.get("toss") or {}).get("winner"),
        "toss_decision": (info.get("toss") or {}).get("decision"),
        "winner": outcome.get("winner"),
        "win_by_runs": _int((outcome.get("by") or {}).get("runs")),
        "win_by_wickets": _int((outcome.get("by") or {}).get("wickets")),
        "result": outcome.get("result"),
        "eliminator": outcome.get("eliminator") or outcome.get("bowl_out"),
        "method": outcome.get("method"),
        "target_runs": _int(target.get("runs")),
        "target_overs": target.get("overs"),
        "player_of_match": (info.get("player_of_match") or [None])[0],
        "data_version": meta.get("data_version"),
        "revision": _int(meta.get("revision")),
    }

    delivery_rows, wicket_rows = [], []
    for innings_num, innings in enumerate(innings_list, start=1):
        batting_team = innings.get("team")
        super_over = bool(innings.get("super_over", False))
        for over in innings.get("overs", []):
            over_num = over.get("over")
            for ball_idx, delivery in enumerate(over.get("deliveries", []), start=1):
                runs = delivery.get("runs", {})
                extras = delivery.get("extras", {}) or {}
                wickets = delivery.get("wickets") or []
                delivery_rows.append({
                    "match_id": match_id,
                    "innings_num": innings_num,
                    "batting_team": batting_team,
                    "is_super_over": super_over,
                    "over_num": over_num,
                    "ball_in_over": ball_idx,
                    "batter": delivery.get("batter"),
                    "bowler": delivery.get("bowler"),
                    "non_striker": delivery.get("non_striker"),
                    "runs_batter": runs.get("batter", 0),
                    "runs_extras": runs.get("extras", 0),
                    "runs_total": runs.get("total", 0),
                    "non_boundary": bool(runs.get("non_boundary", False)),
                    # Joined keys kept for simple queries; the per-type columns are exact.
                    "extra_type": "+".join(sorted(extras.keys())) if extras else None,
                    "extra_wides": extras.get("wides"),
                    "extra_noballs": extras.get("noballs"),
                    "extra_byes": extras.get("byes"),
                    "extra_legbyes": extras.get("legbyes"),
                    "extra_penalty": extras.get("penalty"),
                    "is_wicket": bool(wickets),
                    "wicket_kind": wickets[0].get("kind") if wickets else None,
                    "player_dismissed": wickets[0].get("player_out") if wickets else None,
                })
                for seq, w in enumerate(wickets, start=1):
                    names = [f.get("name") for f in (w.get("fielders") or []) if f.get("name")]
                    wicket_rows.append({
                        "match_id": match_id,
                        "innings_num": innings_num,
                        "over_num": over_num,
                        "ball_in_over": ball_idx,
                        "wicket_seq": seq,
                        "kind": w.get("kind"),
                        "player_out": w.get("player_out"),
                        "fielders": json.dumps(names),
                        "fielder_ids": json.dumps([registry.get(n) for n in names]),
                    })

    player_rows = [
        {"match_id": match_id, "team": team, "player": p, "player_id": registry.get(p)}
        for team, players in (info.get("players") or {}).items()
        for p in players
    ]
    return match_row, delivery_rows, wicket_rows, player_rows


def iter_match_files(source: Path):
    """Yield (match_id, parsed json) from a Cricsheet zip or a folder of .json files."""
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as zf:
            names = sorted(n for n in zf.namelist() if n.lower().endswith(".json"))
            for name in tqdm(names, desc=f"Parsing {source.name}"):
                try:
                    with zf.open(name) as fh:
                        yield Path(name).stem, json.load(fh)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
    else:
        for path in tqdm(sorted(source.rglob("*.json")), desc=f"Parsing {source}"):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    yield path.stem, json.load(fh)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_register(con, register_dir: Path) -> dict:
    """people.csv and names.csv from https://cricsheet.org/register/ -> tables."""
    people, names = register_dir / "people.csv", register_dir / "names.csv"
    counts = {}
    if people.exists():
        con.execute(f"CREATE OR REPLACE TABLE people AS SELECT * FROM read_csv('{people.as_posix()}', header=true, all_varchar=true)")
        counts["people"] = con.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    if names.exists():
        con.execute(f"""CREATE OR REPLACE TABLE people_names AS
                        SELECT identifier, name FROM read_csv('{names.as_posix()}', header=true, all_varchar=true)""")
        # Every person is also known by their register name.
        if people.exists():
            con.execute("""INSERT INTO people_names SELECT identifier, name FROM people
                           WHERE (identifier, name) NOT IN (SELECT identifier, name FROM people_names)""")
        counts["people_names"] = con.execute("SELECT COUNT(*) FROM people_names").fetchone()[0]
    return counts


def build(sources: list[Path], out: Path, register_dir: Path | None = None, incremental: bool = False,
          progress=print) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(out))
    try:
        if not incremental:
            for t in (*MATCH_TABLES, "people", "people_names", *coverage.TABLES, "build_info"):
                con.execute(f"DROP TABLE IF EXISTS {t}")
        for t, cols in SCHEMA.items():
            con.execute(f"CREATE TABLE IF NOT EXISTS {t} ({cols})")

        buffers = {t: [] for t in MATCH_TABLES}
        batch_ids: list[str] = []
        totals = {t: 0 for t in MATCH_TABLES}

        def flush():
            if not batch_ids:
                return
            if incremental:
                ids = pd.DataFrame({"match_id": batch_ids})
                con.register("ids_df", ids)
                for t in MATCH_TABLES:
                    con.execute(f"DELETE FROM {t} WHERE match_id IN (SELECT match_id FROM ids_df)")
                con.unregister("ids_df")
            for t in MATCH_TABLES:
                if not buffers[t]:
                    continue
                df = pd.DataFrame(buffers[t])
                cols = [c.split()[0] for c in SCHEMA[t].replace("\n", " ").split(",")]
                con.register("batch_df", df)
                try:
                    con.execute(f"INSERT INTO {t} ({', '.join(cols)}) SELECT {', '.join(cols)} FROM batch_df")
                finally:
                    con.unregister("batch_df")
                totals[t] += len(df)
                buffers[t].clear()
            batch_ids.clear()

        for source in sources:
            for match_id, data in iter_match_files(source):
                m, d, w, p = parse_match(match_id, data)
                buffers["matches"].append(m)
                buffers["deliveries"].extend(d)
                buffers["deliveries_wickets"].extend(w)
                buffers["players_matches"].extend(p)
                batch_ids.append(match_id)
                if len(batch_ids) >= BATCH_SIZE:
                    flush()
        flush()

        register_counts = load_register(con, register_dir) if register_dir else {}
        coverage_counts = coverage.load(con, register_dir) if register_dir else {}

        for sql in (
            "CREATE INDEX IF NOT EXISTS idx_deliveries_match ON deliveries(match_id)",
            "CREATE INDEX IF NOT EXISTS idx_deliveries_batter ON deliveries(batter)",
            "CREATE INDEX IF NOT EXISTS idx_deliveries_bowler ON deliveries(bowler)",
            "CREATE INDEX IF NOT EXISTS idx_wickets_match ON deliveries_wickets(match_id)",
            "CREATE INDEX IF NOT EXISTS idx_matches_type ON matches(match_type)",
            "CREATE INDEX IF NOT EXISTS idx_matches_event ON matches(event_name)",
            "CREATE INDEX IF NOT EXISTS idx_players_matches_player ON players_matches(player)",
        ):
            con.execute(sql)

        info = {
            "schema_version": SCHEMA_VERSION,
            "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "incremental": incremental,
            "sources": {s.name: sha256(s) for s in sources if s.is_file()},
            "rows": {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in MATCH_TABLES},
            "register": register_counts,
            "coverage": coverage_counts,
            "latest_match_date": con.execute("SELECT MAX(date) FROM matches").fetchone()[0],
        }
        con.execute("CREATE OR REPLACE TABLE build_info (key VARCHAR, value VARCHAR)")
        con.executemany("INSERT INTO build_info VALUES (?, ?)", [[k, json.dumps(v)] for k, v in info.items()])
    finally:
        con.close()

    progress(f"Parsed this run: " + ", ".join(f"{t}={n:,}" for t, n in totals.items()))
    progress(f"Database now: " + ", ".join(f"{t}={n:,}" for t, n in info["rows"].items())
             + (f"; register: {register_counts}" if register_counts else ""))
    progress(f"Wrote {out}")
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the cricket database from Cricsheet data.")
    parser.add_argument("--zip", action="append", default=[], help="Cricsheet JSON zip (repeatable)")
    parser.add_argument("--raw-dir", help="folder of Cricsheet .json files (searched recursively)")
    parser.add_argument("--register-dir", help="folder containing people.csv and names.csv")
    parser.add_argument("--incremental", action="store_true", help="upsert into an existing database")
    parser.add_argument("--out", default="data/cricket.duckdb")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    sources = [Path(z) for z in args.zip] + ([Path(args.raw_dir)] if args.raw_dir else [])
    if not sources:
        parser.error("give --zip and/or --raw-dir")
    for s in sources:
        if not s.exists():
            parser.error(f"not found: {s}")
    build(sources, Path(args.out), Path(args.register_dir) if args.register_dir else None, args.incremental)


if __name__ == "__main__":
    main()
