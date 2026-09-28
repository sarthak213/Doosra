"""
Builds the derived tables every analytics view reads from. Run after ingest:

    python -m analytics.build                      # data/cricket.duckdb
    python -m analytics.build --db path/to/x.duckdb

Everything context-dependent is computed here, once, over the 11M+ ball
table, so the metrics themselves are cheap sums over one row per innings:

  ball_expectation  expected batter runs / dismissal chance and expected
                    bowler runs / wicket chance for every ball state
                    (format group x gender x year x innings x over x
                    wickets down). The baseline behind True SR/Average/
                    Economy/Wickets and Runs Above Expected.
  batting_innings   one row per batter per innings, including innings where
                    they never faced a ball: runs, balls, boundaries, dots,
                    dismissal, batting position (order of arrival), entry
                    point (over, score, wickets down), first-five-ball
                    figures, expected runs/outs for the balls faced, and the
                    match and era context used by Match Factor / Era Factor.
  batting_phase     the same per phase (powerplay / middle / death) for
                    limited-overs innings.
  bowling_innings   one row per bowler per innings: legal balls, runs
                    charged, credited wickets, dots, boundaries, maidens,
                    expected runs/wickets, and match context.
  bowling_phase     the same per phase.

Super-over innings are excluded throughout, and "retired hurt"/"retired not
out" are not dismissals. Works on both database schemas (see
scope.ball_exprs).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from . import db
from .scope import CREDITED_SQL, FORMAT_LABEL_SQL, NOT_DISMISSALS_SQL, PHASE_SQL, ball_exprs

DERIVED_TABLES = ("ball_expectation", "batting_innings", "batting_phase", "bowling_innings", "bowling_phase")

# A ball state with fewer samples than this in one calendar year falls back
# to the all-years estimate for that state.
MIN_STATE_SAMPLES = 300
# Era Factor compares against the same batting position within +/- this
# many years.
ERA_WINDOW_YEARS = 2

FGROUP_SQL = ("CASE WHEN m.match_type IN ('T20', 'IT20') THEN 'T20' "
              "WHEN m.match_type IN ('ODI', 'ODM') THEN 'ODI' ELSE 'MULTI' END")
# Limited overs: every over is its own state. Multi-day: 10-over buckets.
STATE_OVER_SQL = "CASE WHEN fgroup = 'MULTI' THEN LEAST(over_num // 10, 15) ELSE LEAST(over_num, 49) END"

MATCH_COLS = ("date", "season", "yr", "match_type", "team_type", "fmt", "fgroup", "event_name", "gender",
              "venue", "city", "team1", "team2", "winner")


def _has(con, table: str) -> bool:
    return bool(con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table]).fetchone()[0])


def build(path: str | Path | None = None, progress=print) -> dict:
    """(Re)build every derived table. Returns row counts."""
    started = time.time()
    con = db.connect_writable(path)
    try:
        cols = {r[0] for r in con.execute("DESCRIBE deliveries").fetchall()}
        e = ball_exprs(cols)
        new_wickets = _has(con, "deliveries_wickets")
        pm_cols = {r[0] for r in con.execute("DESCRIBE players_matches").fetchall()}
        has_register = _has(con, "people") and "player_id" in pm_cols

        def step(msg, sql):
            t = time.time()
            con.execute(sql)
            progress(f"  {msg} ({time.time() - t:.1f}s)")

        progress("Building derived tables")

        step("match metadata", f"""
            CREATE OR REPLACE TEMP TABLE mm AS
            SELECT m.match_id, m.date, m.season, CAST(substr(m.date, 1, 4) AS INTEGER) AS yr,
                   m.match_type, m.team_type, {FORMAT_LABEL_SQL} AS fmt, {FGROUP_SQL} AS fgroup,
                   m.event_name, m.gender, m.venue, m.city, m.team1, m.team2, m.winner
            FROM matches m
        """)

        # Person identity. Names in the ball-by-ball data aren't unique across
        # people (three different "Rashid Khan"s), but within one match each
        # name maps to one register id. With the register, every player is
        # identified by the register's unique_name ("Rashid Khan (2)").
        uname = "COALESCE(pe.unique_name, pm.player)" if has_register else "pm.player"
        pjoin = "LEFT JOIN people pe ON pe.identifier = pm.player_id" if has_register else ""
        step("player identities", f"""
            CREATE OR REPLACE TEMP TABLE idmap AS
            SELECT pm.match_id, pm.player AS name, any_value({uname}) AS uname
            FROM players_matches pm {pjoin}
            GROUP BY pm.match_id, pm.player
        """)

        # Every delivery (super overs excluded) with scoring-rule flags.
        step("deliveries", f"""
            CREATE OR REPLACE TEMP TABLE b0 AS
            SELECT d.match_id, d.innings_num, d.batting_team, d.over_num, d.ball_in_over,
                   d.over_num * 1000 + d.ball_in_over AS seq,
                   COALESCE(ib.uname, d.batter) AS batter, COALESCE(iw.uname, d.bowler) AS bowler,
                   COALESCE(ins.uname, d.non_striker) AS non_striker, d.runs_batter, d.runs_total,
                   d.is_wicket, d.wicket_kind, d.player_dismissed,
                   {e['faced']} AS faced, {e['legal']} AS legal, {e['bowler_runs']} AS bowler_runs,
                   {e['four']} AS is_four, {e['six']} AS is_six,
                   ({PHASE_SQL}) AS phase,
                   {FGROUP_SQL} AS fgroup, m.gender, CAST(substr(m.date, 1, 4) AS INTEGER) AS yr
            FROM deliveries d JOIN matches m ON d.match_id = m.match_id
            LEFT JOIN idmap ib ON ib.match_id = d.match_id AND ib.name = d.batter
            LEFT JOIN idmap iw ON iw.match_id = d.match_id AND iw.name = d.bowler
            LEFT JOIN idmap ins ON ins.match_id = d.match_id AND ins.name = d.non_striker
            WHERE {e['regular']}
        """)

        # One row per wicket (every wicket on a ball on the current schema).
        if new_wickets:
            wsrc = """SELECT dw.match_id, dw.innings_num, dw.over_num * 1000 + dw.ball_in_over AS seq,
                             dw.kind, dw.player_out
                      FROM deliveries_wickets dw"""
        else:
            wsrc = """SELECT match_id, innings_num, over_num * 1000 + ball_in_over AS seq,
                             wicket_kind AS kind, player_dismissed AS player_out
                      FROM deliveries WHERE is_wicket"""
        step("wickets", f"""
            CREATE OR REPLACE TEMP TABLE wk AS
            SELECT w.match_id, w.innings_num, w.seq, w.kind, COALESCE(i.uname, w.player_out) AS player_out
            FROM ({wsrc}) w LEFT JOIN idmap i ON i.match_id = w.match_id AND i.name = w.player_out
        """)

        step("ball state", f"""
            CREATE OR REPLACE TEMP TABLE b AS
            WITH per_ball AS (
                SELECT match_id, innings_num, seq,
                       SUM(CAST(kind NOT IN {NOT_DISMISSALS_SQL} AS INTEGER)) AS wkts_on_ball,
                       SUM(CAST(kind IN {CREDITED_SQL} AS INTEGER)) AS credited,
                       list(player_out) FILTER (WHERE kind NOT IN {NOT_DISMISSALS_SQL}) AS outs
                FROM wk GROUP BY match_id, innings_num, seq
            )
            SELECT b0.*, {STATE_OVER_SQL} AS state_over,
                   COALESCE(p.wkts_on_ball, 0) AS wkts_on_ball,
                   COALESCE(p.credited, 0) AS credited,
                   COALESCE(list_contains(p.outs, b0.batter), FALSE) AS striker_out,
                   COALESCE(SUM(COALESCE(p.wkts_on_ball, 0)) OVER w, 0) AS wkts_before,
                   COALESCE(SUM(b0.runs_total) OVER w, 0) AS score_before,
                   SUM(CAST(b0.faced AS INTEGER)) OVER (
                       PARTITION BY b0.match_id, b0.innings_num, b0.batter ORDER BY b0.seq
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS bat_ball_no
            FROM b0 LEFT JOIN per_ball p
              ON p.match_id = b0.match_id AND p.innings_num = b0.innings_num AND p.seq = b0.seq
            WINDOW w AS (PARTITION BY b0.match_id, b0.innings_num ORDER BY b0.seq
                         ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
        """)

        # Expected outcome per ball state; thin year-level cells fall back to
        # the all-years estimate for the same state.
        step("ball expectation", f"""
            CREATE OR REPLACE TABLE ball_expectation AS
            WITH k AS (
                SELECT fgroup, gender, yr, innings_num, state_over, LEAST(wkts_before, 9) AS wkts,
                       faced, striker_out, runs_batter, bowler_runs, credited
                FROM b
            ),
            yearly AS (
                SELECT fgroup, gender, yr, innings_num, state_over, wkts,
                       SUM(CAST(faced AS INTEGER)) AS n_faced,
                       SUM(CASE WHEN faced THEN runs_batter ELSE 0 END) AS bat_runs,
                       SUM(CAST(faced AND striker_out AS INTEGER)) AS bat_outs,
                       COUNT(*) AS n_deliveries, SUM(bowler_runs) AS bowl_runs, SUM(credited) AS bowl_wkts
                FROM k GROUP BY ALL
            ),
            overall AS (
                SELECT fgroup, gender, innings_num, state_over, wkts,
                       SUM(n_faced) AS n_faced, SUM(bat_runs) AS bat_runs, SUM(bat_outs) AS bat_outs,
                       SUM(n_deliveries) AS n_deliveries, SUM(bowl_runs) AS bowl_runs, SUM(bowl_wkts) AS bowl_wkts
                FROM yearly GROUP BY ALL
            )
            SELECT y.fgroup, y.gender, y.yr, y.innings_num, y.state_over, y.wkts,
                   y.n_faced, y.n_deliveries,
                   CASE WHEN y.n_faced >= {MIN_STATE_SAMPLES} THEN y.bat_runs / y.n_faced
                        ELSE o.bat_runs / NULLIF(o.n_faced, 0) END AS exp_bat_runs,
                   CASE WHEN y.n_faced >= {MIN_STATE_SAMPLES} THEN y.bat_outs / y.n_faced
                        ELSE o.bat_outs / NULLIF(o.n_faced, 0) END AS exp_bat_out,
                   CASE WHEN y.n_deliveries >= {MIN_STATE_SAMPLES} THEN y.bowl_runs / y.n_deliveries
                        ELSE o.bowl_runs / NULLIF(o.n_deliveries, 0) END AS exp_bowl_runs,
                   CASE WHEN y.n_deliveries >= {MIN_STATE_SAMPLES} THEN y.bowl_wkts / y.n_deliveries
                        ELSE o.bowl_wkts / NULLIF(o.n_deliveries, 0) END AS exp_bowl_wkt
            FROM yearly y JOIN overall o USING (fgroup, gender, innings_num, state_over, wkts)
        """)

        step("expected values per ball", """
            CREATE OR REPLACE TEMP TABLE bx AS
            SELECT b.*, COALESCE(x.exp_bat_runs, 0) AS exp_bat_runs, COALESCE(x.exp_bat_out, 0) AS exp_bat_out,
                   COALESCE(x.exp_bowl_runs, 0) AS exp_bowl_runs, COALESCE(x.exp_bowl_wkt, 0) AS exp_bowl_wkt
            FROM b LEFT JOIN ball_expectation x
              ON x.fgroup = b.fgroup AND x.gender IS NOT DISTINCT FROM b.gender AND x.yr = b.yr
             AND x.innings_num = b.innings_num AND x.state_over = b.state_over AND x.wkts = LEAST(b.wkts_before, 9)
        """)

        # --- batting -------------------------------------------------------
        step("batting appearances", """
            CREATE OR REPLACE TEMP TABLE app AS
            WITH a AS (
                SELECT match_id, innings_num, batting_team, batter AS player, seq * 2 AS k FROM bx
                UNION ALL
                SELECT match_id, innings_num, batting_team, non_striker, seq * 2 + 1 FROM bx
                WHERE non_striker IS NOT NULL
            ),
            first AS (
                SELECT match_id, innings_num, player, any_value(batting_team) AS team, MIN(k) AS first_k
                FROM a GROUP BY match_id, innings_num, player
            )
            SELECT f.*, ROW_NUMBER() OVER (PARTITION BY match_id, innings_num ORDER BY first_k) AS position,
                   COUNT(*) OVER (PARTITION BY match_id, player) AS player_innings_in_match
            FROM first f
        """)

        step("batting innings", f"""
            CREATE OR REPLACE TABLE batting_innings AS
            WITH agg AS (
                SELECT match_id, innings_num, batter AS player,
                       SUM(runs_batter) AS runs, SUM(CAST(faced AS INTEGER)) AS balls,
                       SUM(CAST(is_four AS INTEGER)) AS fours, SUM(CAST(is_six AS INTEGER)) AS sixes,
                       SUM(CAST(faced AND runs_batter = 0 AS INTEGER)) AS dots,
                       SUM(CASE WHEN faced THEN exp_bat_runs ELSE 0 END) AS exp_runs,
                       SUM(CASE WHEN faced THEN exp_bat_out ELSE 0 END) AS exp_outs,
                       SUM(CASE WHEN faced AND bat_ball_no <= 5 THEN runs_batter ELSE 0 END) AS runs_first5,
                       SUM(CAST(faced AND bat_ball_no <= 5 AS INTEGER)) AS balls_first5,
                       SUM(CASE WHEN faced AND bat_ball_no <= 5 THEN exp_bat_runs ELSE 0 END) AS exp_runs_first5
                FROM bx GROUP BY match_id, innings_num, batter
            ),
            outs AS (
                SELECT match_id, innings_num, player_out AS player, COUNT(*) AS outs, any_value(kind) AS kind
                FROM wk WHERE kind NOT IN {NOT_DISMISSALS_SQL} AND player_out IS NOT NULL
                GROUP BY match_id, innings_num, player_out
            ),
            entry AS (
                SELECT a.match_id, a.innings_num, a.player, bx.over_num AS entry_over,
                       bx.score_before AS entry_score, bx.wkts_before AS entry_wkts
                FROM app a JOIN bx ON bx.match_id = a.match_id AND bx.innings_num = a.innings_num
                                  AND bx.seq = a.first_k // 2 AND bx.batter IS NOT NULL
                QUALIFY ROW_NUMBER() OVER (PARTITION BY a.match_id, a.innings_num, a.player) = 1
            )
            SELECT a.match_id, a.innings_num, a.player, a.team,
                   CASE WHEN a.team = mm.team1 THEN mm.team2 ELSE mm.team1 END AS opposition,
                   a.position, e.entry_over, e.entry_score, e.entry_wkts,
                   COALESCE(g.runs, 0) AS runs, COALESCE(g.balls, 0) AS balls,
                   COALESCE(g.fours, 0) AS fours, COALESCE(g.sixes, 0) AS sixes, COALESCE(g.dots, 0) AS dots,
                   LEAST(COALESCE(o.outs, 0), 1) AS out, o.kind AS dismissal,
                   COALESCE(g.exp_runs, 0) AS exp_runs, COALESCE(g.exp_outs, 0) AS exp_outs,
                   COALESCE(g.runs_first5, 0) AS runs_first5, COALESCE(g.balls_first5, 0) AS balls_first5,
                   COALESCE(g.exp_runs_first5, 0) AS exp_runs_first5,
                   a.player_innings_in_match,
                   CASE WHEN mm.winner IS NULL THEN 'no result' WHEN mm.winner = a.team THEN 'won' ELSE 'lost' END AS result,
                   {", ".join(f"mm.{c}" for c in MATCH_COLS)}
            FROM app a
            JOIN mm ON mm.match_id = a.match_id
            LEFT JOIN agg g ON g.match_id = a.match_id AND g.innings_num = a.innings_num AND g.player = a.player
            LEFT JOIN outs o ON o.match_id = a.match_id AND o.innings_num = a.innings_num AND o.player = a.player
            LEFT JOIN entry e ON e.match_id = a.match_id AND e.innings_num = a.innings_num AND e.player = a.player
        """)

        # Match Factor context: runs and dismissals of every OTHER top-7
        # batter in the same match, split evenly over this player's innings
        # in that match so sums over innings count each match once.
        step("match context (batting)", """
            CREATE OR REPLACE TEMP TABLE mc AS
            WITH m AS (
                SELECT match_id, SUM(runs) AS r, SUM(out) AS o FROM batting_innings
                WHERE position <= 7 GROUP BY match_id
            ),
            own AS (
                SELECT match_id, player, SUM(CASE WHEN position <= 7 THEN runs ELSE 0 END) AS r,
                       SUM(CASE WHEN position <= 7 THEN out ELSE 0 END) AS o
                FROM batting_innings GROUP BY match_id, player
            )
            SELECT own.match_id, own.player, m.r - own.r AS mc_runs, m.o - own.o AS mc_outs
            FROM own JOIN m USING (match_id)
        """)

        # Era Factor context: runs and dismissals per innings for the same
        # batting position, format group and gender, within +/- N years.
        step("era baseline", f"""
            CREATE OR REPLACE TEMP TABLE era AS
            WITH y AS (
                SELECT fgroup, gender, LEAST(position, 11) AS pos, yr,
                       COUNT(*) AS n, SUM(runs) AS r, SUM(out) AS o
                FROM batting_innings GROUP BY ALL
            )
            SELECT a.fgroup, a.gender, a.pos, a.yr,
                   SUM(b.r) / SUM(b.n) AS era_runs_pi, SUM(b.o) / SUM(b.n) AS era_outs_pi
            FROM (SELECT DISTINCT fgroup, gender, pos, yr FROM y) a
            JOIN y b ON b.fgroup = a.fgroup AND b.gender IS NOT DISTINCT FROM a.gender AND b.pos = a.pos
                    AND b.yr BETWEEN a.yr - {ERA_WINDOW_YEARS} AND a.yr + {ERA_WINDOW_YEARS}
            GROUP BY ALL
        """)

        step("batting context columns", """
            CREATE OR REPLACE TABLE batting_innings AS
            SELECT bi.*,
                   mc.mc_runs / bi.player_innings_in_match AS mc_runs,
                   mc.mc_outs / bi.player_innings_in_match AS mc_outs,
                   era.era_runs_pi AS era_runs, era.era_outs_pi AS era_outs
            FROM batting_innings bi
            LEFT JOIN mc ON mc.match_id = bi.match_id AND mc.player = bi.player
            LEFT JOIN era ON era.fgroup = bi.fgroup AND era.gender IS NOT DISTINCT FROM bi.gender
                         AND era.pos = LEAST(bi.position, 11) AND era.yr = bi.yr
        """)

        step("batting by phase", f"""
            CREATE OR REPLACE TABLE batting_phase AS
            WITH agg AS (
                SELECT match_id, innings_num, batter AS player, phase,
                       SUM(runs_batter) AS runs, SUM(CAST(faced AS INTEGER)) AS balls,
                       SUM(CAST(is_four AS INTEGER)) AS fours, SUM(CAST(is_six AS INTEGER)) AS sixes,
                       SUM(CAST(faced AND runs_batter = 0 AS INTEGER)) AS dots,
                       SUM(CASE WHEN faced THEN exp_bat_runs ELSE 0 END) AS exp_runs,
                       SUM(CASE WHEN faced THEN exp_bat_out ELSE 0 END) AS exp_outs
                FROM bx WHERE phase IS NOT NULL GROUP BY ALL
            ),
            outs AS (
                SELECT w.match_id, w.innings_num, w.player_out AS player, b.phase, COUNT(*) AS outs
                FROM wk w JOIN bx b ON b.match_id = w.match_id AND b.innings_num = w.innings_num AND b.seq = w.seq
                WHERE w.kind NOT IN {NOT_DISMISSALS_SQL} AND b.phase IS NOT NULL
                GROUP BY ALL
            )
            SELECT bi.match_id, bi.innings_num, bi.player, bi.team, bi.opposition, bi.position,
                   ph.phase, COALESCE(g.runs, 0) AS runs, COALESCE(g.balls, 0) AS balls,
                   COALESCE(g.fours, 0) AS fours, COALESCE(g.sixes, 0) AS sixes, COALESCE(g.dots, 0) AS dots,
                   COALESCE(o.outs, 0) AS out, COALESCE(g.exp_runs, 0) AS exp_runs, COALESCE(g.exp_outs, 0) AS exp_outs,
                   bi.result, {", ".join(f"bi.{c}" for c in MATCH_COLS)}
            FROM batting_innings bi
            CROSS JOIN (VALUES ('powerplay'), ('middle'), ('death')) ph(phase)
            LEFT JOIN agg g ON g.match_id = bi.match_id AND g.innings_num = bi.innings_num
                           AND g.player = bi.player AND g.phase = ph.phase
            LEFT JOIN outs o ON o.match_id = bi.match_id AND o.innings_num = bi.innings_num
                            AND o.player = bi.player AND o.phase = ph.phase
            WHERE bi.fgroup <> 'MULTI' AND (g.balls > 0 OR o.outs > 0)
        """)

        # --- bowling -------------------------------------------------------
        bowl_aggs = """
                       SUM(CAST(legal AS INTEGER)) AS balls, COUNT(*) AS deliveries, SUM(bowler_runs) AS runs,
                       SUM(credited) AS wickets, SUM(CAST(legal AND bowler_runs = 0 AS INTEGER)) AS dots,
                       SUM(CAST(is_four AS INTEGER)) AS fours, SUM(CAST(is_six AS INTEGER)) AS sixes,
                       SUM(exp_bowl_runs) AS exp_runs, SUM(exp_bowl_wkt) AS exp_wkts"""
        step("bowling innings", f"""
            CREATE OR REPLACE TABLE bowling_innings AS
            WITH agg AS (
                SELECT match_id, innings_num, bowler AS player, any_value(batting_team) AS opposition, {bowl_aggs}
                FROM bx GROUP BY match_id, innings_num, bowler
            ),
            ovr AS (
                SELECT match_id, innings_num, bowler AS player, over_num,
                       SUM(CAST(legal AS INTEGER)) AS lb, SUM(bowler_runs) AS r
                FROM bx GROUP BY ALL
            ),
            mdn AS (
                SELECT match_id, innings_num, player, COUNT(*) AS maidens FROM ovr WHERE lb >= 6 AND r = 0 GROUP BY ALL
            ),
            m AS (SELECT match_id, SUM(runs) AS r, SUM(wickets) AS w FROM agg GROUP BY match_id),
            own AS (SELECT match_id, player, SUM(runs) AS r, SUM(wickets) AS w, COUNT(*) AS n FROM agg GROUP BY ALL)
            SELECT g.match_id, g.innings_num, g.player,
                   CASE WHEN g.opposition = mm.team1 THEN mm.team2 ELSE mm.team1 END AS team, g.opposition,
                   g.balls, g.deliveries, g.runs, g.wickets, g.dots, g.fours, g.sixes, g.exp_runs, g.exp_wkts,
                   COALESCE(mdn.maidens, 0) AS maidens,
                   (m.r - own.r) / own.n AS mc_runs, (m.w - own.w) / own.n AS mc_wkts,
                   CASE WHEN mm.winner IS NULL THEN 'no result'
                        WHEN mm.winner <> g.opposition THEN 'won' ELSE 'lost' END AS result,
                   {", ".join(f"mm.{c}" for c in MATCH_COLS)}
            FROM agg g
            JOIN mm ON mm.match_id = g.match_id
            JOIN m ON m.match_id = g.match_id
            JOIN own ON own.match_id = g.match_id AND own.player = g.player
            LEFT JOIN mdn ON mdn.match_id = g.match_id AND mdn.innings_num = g.innings_num AND mdn.player = g.player
        """)

        step("bowling by phase", f"""
            CREATE OR REPLACE TABLE bowling_phase AS
            WITH agg AS (
                SELECT match_id, innings_num, bowler AS player, phase, {bowl_aggs}
                FROM bx WHERE phase IS NOT NULL GROUP BY ALL
            )
            SELECT g.*, bi.team, bi.opposition, bi.result, {", ".join(f"bi.{c}" for c in MATCH_COLS)}
            FROM agg g JOIN bowling_innings bi
              ON bi.match_id = g.match_id AND bi.innings_num = g.innings_num AND bi.player = g.player
        """)

        for t, c in (("batting_innings", "player"), ("batting_phase", "player"),
                     ("bowling_innings", "player"), ("bowling_phase", "player")):
            con.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_{c} ON {t}({c})")

        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in DERIVED_TABLES}
    finally:
        con.close()

    progress(f"Done in {time.time() - started:.0f}s: " + ", ".join(f"{t}={n:,}" for t, n in counts.items()))
    return counts


def is_built() -> bool:
    con = db.connect()
    try:
        return all(_has(con, t) for t in DERIVED_TABLES)
    finally:
        con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", default=None, help="database path (default: data/cricket.duckdb)")
    build(parser.parse_args().db)
