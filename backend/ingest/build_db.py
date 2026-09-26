"""
Parses raw Cricsheet JSON match files into a normalized DuckDB database with
four tables: matches, deliveries, deliveries_wickets, players_matches.

Schema
------
matches(
    match_id TEXT PRIMARY KEY,
    match_type TEXT,        -- Test, ODI, T20, IT20, MDM, etc. (the FORMAT, not the tournament)
    event_name TEXT,        -- the TOURNAMENT/competition, e.g. "Indian Premier League",
                             -- "Big Bash League", "ICC Men's T20 World Cup" (null for bilateral series)
    match_number TEXT,      -- e.g. "Final", "Qualifier 1", or a numeric match number within the event
    gender TEXT,             -- male / female
    team_type TEXT,          -- international / club
    overs_per_innings INTEGER,  -- null for Test matches
    date TEXT,               -- first date of match
    venue TEXT,
    city TEXT,
    season TEXT,
    team1 TEXT,
    team2 TEXT,
    toss_winner TEXT,
    toss_decision TEXT,
    winner TEXT,
    win_by_runs INTEGER,
    win_by_wickets INTEGER,
    player_of_match TEXT
)

deliveries(
    match_id TEXT,
    innings_num INTEGER,       -- 1, 2, (3/4 for tests)
    batting_team TEXT,
    over_num INTEGER,          -- 0-indexed, matches Cricsheet convention
    ball_in_over INTEGER,      -- 1-indexed within the over (legal + illegal deliveries counted in source order)
    batter TEXT,
    bowler TEXT,
    non_striker TEXT,
    runs_batter INTEGER,
    runs_extras INTEGER,
    runs_total INTEGER,
    extra_type TEXT,           -- "+"-joined extra type keys present on this ball, e.g.
                                -- "noballs+byes" if more than one applies (nullable)
    extra_wides INTEGER,       -- per-type extra amounts, nullable when that type doesn't apply
    extra_noballs INTEGER,     -- (a ball can legitimately have more than one extra type at once,
    extra_byes INTEGER,        -- e.g. a no-ball that also runs byes -- these columns, unlike the
    extra_legbyes INTEGER,     -- single extra_type string above, don't lose that information)
    extra_penalty INTEGER,
    is_wicket BOOLEAN,
    wicket_kind TEXT,          -- kind of the FIRST wicket on this ball, kept for backward
                                -- compatibility with simple queries (caught, bowled, lbw, run out,
                                -- etc.; nullable) -- see deliveries_wickets for ALL wickets on a ball
    player_dismissed TEXT      -- player dismissed by the FIRST wicket on this ball (nullable)
)

deliveries_wickets(
    match_id TEXT,
    innings_num INTEGER,
    over_num INTEGER,
    ball_in_over INTEGER,
    wicket_seq INTEGER,       -- 1-indexed order of this wicket among (rare) multiple wickets on one ball
    kind TEXT,                -- caught, bowled, lbw, run out, etc.
    player_out TEXT,
    fielders TEXT             -- JSON-encoded list of fielder names involved, e.g. '["A de Villiers"]'
)

players_matches(
    match_id TEXT,
    team TEXT,
    player TEXT
)

Usage:
    python build_db.py --raw-dir ../data/raw --out ../data/cricket.duckdb

NOTE: this schema (extra_wides/noballs/byes/legbyes/penalty columns and the
new deliveries_wickets table) changed from the single-extra-type /
first-wicket-only version. Any existing cricket.duckdb built with the old
schema must be rebuilt by re-running this script against the raw JSON.
"""

import argparse
import json
from pathlib import Path

import duckdb
import pandas as pd
from tqdm import tqdm

# Matches are parsed and flushed to disk in batches so the whole Cricsheet
# archive (tens of thousands of matches) doesn't have to sit in memory as
# Python lists/DataFrames at once.
BATCH_SIZE = 500


def parse_match(match_id: str, data: dict):
    info = data.get("info", {})
    dates = info.get("dates", [])
    outcome = info.get("outcome", {})
    teams = info.get("teams", [])
    event = info.get("event", {}) or {}

    match_row = {
        "match_id": match_id,
        "match_type": info.get("match_type"),
        "event_name": event.get("name"),
        "match_number": str(event.get("match_number")) if event.get("match_number") is not None else event.get("stage"),
        "gender": info.get("gender"),
        "team_type": info.get("team_type"),
        "overs_per_innings": info.get("overs"),
        "date": dates[0] if dates else None,
        "venue": info.get("venue"),
        "city": info.get("city"),
        "season": str(info.get("season")) if info.get("season") is not None else None,
        "team1": teams[0] if len(teams) > 0 else None,
        "team2": teams[1] if len(teams) > 1 else None,
        "toss_winner": info.get("toss", {}).get("winner"),
        "toss_decision": info.get("toss", {}).get("decision"),
        "winner": outcome.get("winner"),
        "win_by_runs": (outcome.get("by") or {}).get("runs"),
        "win_by_wickets": (outcome.get("by") or {}).get("wickets"),
        "player_of_match": (info.get("player_of_match") or [None])[0],
    }

    delivery_rows = []
    wicket_rows = []
    for innings_num, innings in enumerate(data.get("innings", []), start=1):
        batting_team = innings.get("team")
        for over in innings.get("overs", []):
            over_num = over.get("over")
            for ball_idx, delivery in enumerate(over.get("deliveries", []), start=1):
                runs = delivery.get("runs", {})
                extras = delivery.get("extras", {}) or {}
                # Keep the joined-keys string for backward compatibility, but
                # also capture every extra type's amount separately so a
                # ball with e.g. both "noballs" and "byes" doesn't silently
                # lose one of them.
                extra_type = "+".join(sorted(extras.keys())) if extras else None
                wickets = delivery.get("wickets") or []
                is_wicket = len(wickets) > 0
                wicket_kind = wickets[0].get("kind") if is_wicket else None
                player_dismissed = wickets[0].get("player_out") if is_wicket else None

                delivery_rows.append({
                    "match_id": match_id,
                    "innings_num": innings_num,
                    "batting_team": batting_team,
                    "over_num": over_num,
                    "ball_in_over": ball_idx,
                    "batter": delivery.get("batter"),
                    "bowler": delivery.get("bowler"),
                    "non_striker": delivery.get("non_striker"),
                    "runs_batter": runs.get("batter", 0),
                    "runs_extras": runs.get("extras", 0),
                    "runs_total": runs.get("total", 0),
                    "extra_type": extra_type,
                    "extra_wides": extras.get("wides"),
                    "extra_noballs": extras.get("noballs"),
                    "extra_byes": extras.get("byes"),
                    "extra_legbyes": extras.get("legbyes"),
                    "extra_penalty": extras.get("penalty"),
                    "is_wicket": is_wicket,
                    "wicket_kind": wicket_kind,
                    "player_dismissed": player_dismissed,
                })

                for seq, w in enumerate(wickets, start=1):
                    fielders = [f.get("name") for f in (w.get("fielders") or []) if f.get("name")]
                    wicket_rows.append({
                        "match_id": match_id,
                        "innings_num": innings_num,
                        "over_num": over_num,
                        "ball_in_over": ball_idx,
                        "wicket_seq": seq,
                        "kind": w.get("kind"),
                        "player_out": w.get("player_out"),
                        "fielders": json.dumps(fielders),
                    })

    player_rows = []
    for team, players in (info.get("players") or {}).items():
        for player in players:
            player_rows.append({"match_id": match_id, "team": team, "player": player})

    return match_row, delivery_rows, wicket_rows, player_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default="../data/raw")
    parser.add_argument("--out", default="../data/cricket.duckdb")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    json_files = sorted(raw_dir.rglob("*.json"))
    print(f"Found {len(json_files)} match files under {raw_dir}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(out_path))

    con.execute("DROP TABLE IF EXISTS matches")
    con.execute("DROP TABLE IF EXISTS deliveries")
    con.execute("DROP TABLE IF EXISTS deliveries_wickets")
    con.execute("DROP TABLE IF EXISTS players_matches")

    total_matches = total_deliveries = total_wickets = total_players = 0
    matches_buf, deliveries_buf, wickets_buf, players_buf = [], [], [], []
    first_batch = True

    def flush():
        # Rows are parsed in batches (BATCH_SIZE matches at a time) and
        # written to disk immediately, rather than accumulating the entire
        # Cricsheet archive as Python lists/DataFrames in memory at once.
        nonlocal first_batch, total_matches, total_deliveries, total_wickets, total_players
        matches_df = pd.DataFrame(matches_buf)
        deliveries_df = pd.DataFrame(deliveries_buf)
        wickets_df = pd.DataFrame(wickets_buf)
        players_df = pd.DataFrame(players_buf)

        con.register("matches_df", matches_df)
        con.register("deliveries_df", deliveries_df)
        con.register("wickets_df", wickets_df)
        con.register("players_df", players_df)
        try:
            if first_batch:
                con.execute("CREATE TABLE matches AS SELECT * FROM matches_df")
                con.execute("CREATE TABLE deliveries AS SELECT * FROM deliveries_df")
                con.execute("CREATE TABLE deliveries_wickets AS SELECT * FROM wickets_df")
                con.execute("CREATE TABLE players_matches AS SELECT * FROM players_df")
                first_batch = False
            else:
                con.execute("INSERT INTO matches SELECT * FROM matches_df")
                con.execute("INSERT INTO deliveries SELECT * FROM deliveries_df")
                con.execute("INSERT INTO deliveries_wickets SELECT * FROM wickets_df")
                con.execute("INSERT INTO players_matches SELECT * FROM players_df")
        finally:
            con.unregister("matches_df")
            con.unregister("deliveries_df")
            con.unregister("wickets_df")
            con.unregister("players_df")

        total_matches += len(matches_df)
        total_deliveries += len(deliveries_df)
        total_wickets += len(wickets_df)
        total_players += len(players_df)
        matches_buf.clear()
        deliveries_buf.clear()
        wickets_buf.clear()
        players_buf.clear()

    for path in tqdm(json_files, desc="Parsing"):
        match_id = path.stem
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        match_row, delivery_rows, wicket_rows, player_rows = parse_match(match_id, data)
        matches_buf.append(match_row)
        deliveries_buf.extend(delivery_rows)
        wickets_buf.extend(wicket_rows)
        players_buf.extend(player_rows)

        if len(matches_buf) >= BATCH_SIZE:
            flush()

    if matches_buf:
        flush()

    # Helpful indexes for the agent's typical query patterns
    con.execute("CREATE INDEX idx_deliveries_match ON deliveries(match_id)")
    con.execute("CREATE INDEX idx_deliveries_batter ON deliveries(batter)")
    con.execute("CREATE INDEX idx_deliveries_bowler ON deliveries(bowler)")
    con.execute("CREATE INDEX idx_wickets_match ON deliveries_wickets(match_id)")
    con.execute("CREATE INDEX idx_matches_type ON matches(match_type)")
    con.execute("CREATE INDEX idx_matches_event ON matches(event_name)")

    print(f"matches: {total_matches} rows")
    print(f"deliveries: {total_deliveries} rows")
    print(f"deliveries_wickets: {total_wickets} rows")
    print(f"players_matches: {total_players} rows")
    print(f"Wrote database to {out_path}")

    con.close()


if __name__ == "__main__":
    main()
