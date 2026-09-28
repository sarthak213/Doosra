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

from . import db, fibs
from .components import BAT_COMPONENTS, BOWL_COMPONENTS, INPLAY_SQL, bat_cols, bowl_cols
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


# Per-ball wicket flags (SQL over the wickets table `wk`).
WICKET_FLAGS = {
    "bowled": "kind = 'bowled'",
    "lbw": "kind = 'lbw'",
    "ct_keeper": "kind = 'caught' AND keeper_catch",
    "ct_field": "kind = 'caught' AND NOT keeper_catch",
    "ct_bowler": "kind = 'caught and bowled'",
    "stumped": "kind = 'stumped'",
    "hit_wicket": "kind = 'hit wicket'",
    "run_out": "kind = 'run out'",
}
# The FIBS study's K for these metrics is stored on every innings row.
K_COLUMNS = {
    "bowling": ("runs", "wickets", "dot", "boundary", "ct_field", "inplay_runs"),
    "batting": ("runs", "outs", "dot", "boundary", "ct_field", "inplay_runs"),
}
EXPECTATION_SUMS = (["n_faced", "bat_runs", "bat_outs", "n_deliveries", "bowl_runs", "bowl_wkts",
                     "n_inplay", "s_inplay_runs"]
                    + [f"sb_{c}" for c in BOWL_COMPONENTS] + [f"st_{c}" for c in BAT_COMPONENTS])


def _rate(total: str, n: str) -> str:
    """A ball-state rate, falling back to all years when the year is thin."""
    return (f"CASE WHEN y.{n} >= {MIN_STATE_SAMPLES} THEN y.{total} / y.{n} "
            f"ELSE o.{total} / NULLIF(o.{n}, 0) END")


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
                             dw.kind, dw.player_out, json_extract_string(dw.fielders, '$[0]') AS fielder_name
                      FROM deliveries_wickets dw"""
            keeper_sql = """
                WITH st AS (
                    SELECT dw.match_id, COALESCE(i.uname, json_extract_string(dw.fielders, '$[0]')) AS fielder
                    FROM deliveries_wickets dw
                    LEFT JOIN idmap i ON i.match_id = dw.match_id AND i.name = json_extract_string(dw.fielders, '$[0]')
                    WHERE dw.kind = 'stumped'
                ),
                career AS (SELECT fielder, COUNT(*) AS n FROM st GROUP BY fielder),
                inmatch AS (SELECT DISTINCT match_id, fielder FROM st),
                xi AS (
                    SELECT pm.match_id, pm.team, i.uname
                    FROM players_matches pm JOIN idmap i ON i.match_id = pm.match_id AND i.name = pm.player
                )
                SELECT xi.match_id, xi.team,
                       arg_max(xi.uname, CAST(im.fielder IS NOT NULL AS INTEGER) * 1000000 + c.n) AS keeper
                FROM xi JOIN career c ON c.fielder = xi.uname
                LEFT JOIN inmatch im ON im.match_id = xi.match_id AND im.fielder = xi.uname
                GROUP BY xi.match_id, xi.team"""
        else:
            wsrc = """SELECT match_id, innings_num, over_num * 1000 + ball_in_over AS seq,
                             wicket_kind AS kind, player_dismissed AS player_out, NULL::VARCHAR AS fielder_name
                      FROM deliveries WHERE is_wicket"""
            keeper_sql = "SELECT NULL::VARCHAR AS match_id, NULL::VARCHAR AS team, NULL::VARCHAR AS keeper WHERE FALSE"

        # Who kept wicket for each side in each match (Cricsheet doesn't say):
        # the XI member who made a stumping in the match, otherwise the one
        # with the most career stumpings. Tells caught-behind from caught in
        # the field.
        step("wicketkeepers", f"CREATE OR REPLACE TEMP TABLE keeper AS {keeper_sql}")

        step("wickets", f"""
            CREATE OR REPLACE TEMP TABLE wk AS
            WITH inn AS (SELECT match_id, innings_num, any_value(batting_team) AS batting_team FROM b0 GROUP BY ALL)
            SELECT w.match_id, w.innings_num, w.seq, w.kind, COALESCE(i.uname, w.player_out) AS player_out,
                   COALESCE(w.kind = 'caught' AND fi.uname = k.keeper, FALSE) AS keeper_catch
            FROM ({wsrc}) w
            LEFT JOIN idmap i ON i.match_id = w.match_id AND i.name = w.player_out
            LEFT JOIN idmap fi ON fi.match_id = w.match_id AND fi.name = w.fielder_name
            LEFT JOIN inn ON inn.match_id = w.match_id AND inn.innings_num = w.innings_num
            LEFT JOIN matches m ON m.match_id = w.match_id
            LEFT JOIN keeper k ON k.match_id = w.match_id
                              AND k.team = CASE WHEN inn.batting_team = m.team1 THEN m.team2 ELSE m.team1 END
        """)

        step("ball state", f"""
            CREATE OR REPLACE TEMP TABLE b AS
            WITH per_ball AS (
                SELECT match_id, innings_num, seq,
                       SUM(CAST(kind NOT IN {NOT_DISMISSALS_SQL} AS INTEGER)) AS wkts_on_ball,
                       SUM(CAST(kind IN {CREDITED_SQL} AS INTEGER)) AS credited,
                       list(player_out) FILTER (WHERE kind NOT IN {NOT_DISMISSALS_SQL}) AS outs,
                       {", ".join(f"SUM(CAST({cond} AS INTEGER)) AS w_{name}" for name, cond in WICKET_FLAGS.items())}
                FROM wk GROUP BY match_id, innings_num, seq
            ),
            striker AS (
                SELECT match_id, innings_num, seq, player_out, any_value(kind) AS kind,
                       bool_or(keeper_catch) AS keeper_catch
                FROM wk WHERE kind NOT IN {NOT_DISMISSALS_SQL} GROUP BY ALL
            )
            SELECT b0.*, {STATE_OVER_SQL} AS state_over,
                   COALESCE(p.wkts_on_ball, 0) AS wkts_on_ball,
                   COALESCE(p.credited, 0) AS credited,
                   {", ".join(f"COALESCE(p.w_{name}, 0) AS w_{name}" for name in WICKET_FLAGS)},
                   sk.kind AS striker_kind, COALESCE(sk.keeper_catch, FALSE) AS striker_keeper,
                   COALESCE(list_contains(p.outs, b0.batter), FALSE) AS striker_out,
                   COALESCE(SUM(COALESCE(p.wkts_on_ball, 0)) OVER w, 0) AS wkts_before,
                   COALESCE(SUM(b0.runs_total) OVER w, 0) AS score_before,
                   SUM(CAST(b0.faced AS INTEGER)) OVER (
                       PARTITION BY b0.match_id, b0.innings_num, b0.batter ORDER BY b0.seq
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS bat_ball_no
            FROM b0 LEFT JOIN per_ball p
              ON p.match_id = b0.match_id AND p.innings_num = b0.innings_num AND p.seq = b0.seq
            LEFT JOIN striker sk
              ON sk.match_id = b0.match_id AND sk.innings_num = b0.innings_num AND sk.seq = b0.seq
             AND sk.player_out = b0.batter
            WINDOW w AS (PARTITION BY b0.match_id, b0.innings_num ORDER BY b0.seq
                         ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
        """)

        # Expected outcome per ball state; thin year-level cells fall back to
        # the all-years estimate for the same state.
        step("ball expectation", f"""
            CREATE OR REPLACE TABLE ball_expectation AS
            WITH k AS (SELECT *, LEAST(wkts_before, 9) AS wkts FROM b),
            yearly AS (
                SELECT fgroup, gender, yr, innings_num, state_over, wkts,
                       SUM(CAST(faced AS INTEGER)) AS n_faced,
                       SUM(CASE WHEN faced THEN runs_batter ELSE 0 END) AS bat_runs,
                       SUM(CAST(faced AND striker_out AS INTEGER)) AS bat_outs,
                       COUNT(*) AS n_deliveries, SUM(bowler_runs) AS bowl_runs, SUM(credited) AS bowl_wkts,
                       {", ".join(f"SUM({expr}) AS sb_{c}" for c, (_, expr) in BOWL_COMPONENTS.items())},
                       {", ".join(f"SUM(CASE WHEN faced THEN {expr} ELSE 0 END) AS st_{c}"
                                  for c, (_, expr) in BAT_COMPONENTS.items())},
                       SUM(CAST({INPLAY_SQL} AS INTEGER)) AS n_inplay,
                       SUM(CASE WHEN {INPLAY_SQL} THEN runs_batter ELSE 0 END) AS s_inplay_runs
                FROM k GROUP BY fgroup, gender, yr, innings_num, state_over, wkts
            ),
            overall AS (
                SELECT fgroup, gender, innings_num, state_over, wkts,
                       {", ".join(f"SUM({c}) AS {c}" for c in EXPECTATION_SUMS)}
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
                        ELSE o.bowl_wkts / NULLIF(o.n_deliveries, 0) END AS exp_bowl_wkt,
                   {", ".join(_rate(f"sb_{c}", "n_deliveries") + f" AS exp_b_{c}" for c in BOWL_COMPONENTS)},
                   {", ".join(_rate(f"st_{c}", "n_faced") + f" AS exp_t_{c}" for c in BAT_COMPONENTS)},
                   {_rate("s_inplay_runs", "n_inplay")} AS exp_inplay_runs
            FROM yearly y JOIN overall o USING (fgroup, gender, innings_num, state_over, wkts)
        """)

        step("expected values per ball", f"""
            CREATE OR REPLACE TEMP TABLE bx AS
            SELECT b.*, COALESCE(x.exp_bat_runs, 0) AS exp_bat_runs, COALESCE(x.exp_bat_out, 0) AS exp_bat_out,
                   COALESCE(x.exp_bowl_runs, 0) AS exp_bowl_runs, COALESCE(x.exp_bowl_wkt, 0) AS exp_bowl_wkt,
                   {", ".join(f"{expr} AS bw_{c}, COALESCE(x.exp_b_{c}, 0) AS bwx_{c}"
                              for c, (_, expr) in BOWL_COMPONENTS.items())},
                   {", ".join(f"CASE WHEN faced THEN {expr} ELSE 0 END AS bt_{c}, "
                              f"CASE WHEN faced THEN COALESCE(x.exp_t_{c}, 0) ELSE 0 END AS btx_{c}"
                              for c, (_, expr) in BAT_COMPONENTS.items())},
                   CASE WHEN {INPLAY_SQL} THEN runs_batter ELSE 0 END AS inplay_runs,
                   CASE WHEN {INPLAY_SQL} THEN COALESCE(x.exp_inplay_runs, 0) ELSE 0 END AS x_inplay_runs
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
                       SUM(CASE WHEN faced AND bat_ball_no <= 5 THEN exp_bat_runs ELSE 0 END) AS exp_runs_first5,
                       {", ".join(f"SUM(bt_{c}) AS n_{c}, SUM(btx_{c}) AS x_{c}" for c in BAT_COMPONENTS)},
                       SUM(CASE WHEN faced THEN inplay_runs ELSE 0 END) AS inplay_runs,
                       SUM(CASE WHEN faced THEN x_inplay_runs ELSE 0 END) AS x_inplay_runs
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
                   {", ".join(f"COALESCE(g.{c}, 0) AS {c}" for c in bat_cols()[:-2])},
                   COALESCE(g.runs, 0) - COALESCE(g.inplay_runs, 0) + COALESCE(g.x_inplay_runs, 0) AS fib_runs,
                   LEAST(COALESCE(o.outs, 0), 1) - COALESCE(g.n_ct_field, 0) + COALESCE(g.x_ct_field, 0) AS fib_outs,
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
                       SUM(exp_bowl_runs) AS exp_runs, SUM(exp_bowl_wkt) AS exp_wkts,
                       """ + ", ".join(f"SUM(bw_{c}) AS n_{c}, SUM(bwx_{c}) AS x_{c}" for c in BOWL_COMPONENTS) + """,
                       SUM(inplay_runs) AS inplay_runs, SUM(x_inplay_runs) AS x_inplay_runs,
                       SUM(bowler_runs - inplay_runs + x_inplay_runs) AS fib_runs,
                       SUM(credited) - SUM(bw_ct_field) + SUM(bwx_ct_field) AS fib_wkts"""
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
                   {", ".join(f"g.{c}" for c in bowl_cols())},
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

        progress("FIBS study")
        fibs.run(con, progress)
        # Each innings carries the K of its format and gender (falling back to
        # the men's K for the format where a sample is too small to estimate
        # one), for the reliability-adjusted and FIB metrics.
        for table, role in (("bowling_innings", "bowling"), ("batting_innings", "batting")):
            ks = ", ".join(f"MAX(CASE WHEN metric = '{m}' THEN k_balls END) AS k_{m}" for m in K_COLUMNS[role])
            step(f"reliability constants ({role})", f"""
                CREATE OR REPLACE TABLE {table} AS
                WITH k AS (SELECT fgroup, gender, {ks} FROM fibs_stability WHERE role = '{role}' GROUP BY ALL)
                SELECT t.*, {", ".join(f"COALESCE(k.k_{m}, km.k_{m}) AS k_{m}" for m in K_COLUMNS[role])}
                FROM {table} t
                LEFT JOIN k ON k.fgroup = t.fgroup AND k.gender = COALESCE(t.gender, 'unknown')
                LEFT JOIN k km ON km.fgroup = t.fgroup AND km.gender = 'male'
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
