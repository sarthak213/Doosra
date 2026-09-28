"""
The metric registry: every statistic the app can compute, declared once.

A metric is an aggregate SQL expression over one row per innings (the
derived tables from build.py, aliased `m`), plus the metadata the engine,
the UI and the AI need: label, family, which direction is better, whether
it's a rate that needs a minimum sample, whether it can be computed within
a single phase, and a plain-English definition.

Adding a metric = adding one entry here. The engine, the Query Builder's
metric picker, the Player Matrix axes, the glossary and the MCP
`search_metrics` tool all read from this list.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

BAT, BOWL = "batting", "bowling"


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    role: str
    family: str
    sql: str
    definition: str
    higher_is_better: bool = True
    rate: bool = False           # needs a minimum-balls qualification to rank fairly
    phase_ok: bool = True        # computable from the per-phase tables
    kind: str = "number"         # number | int | pct | text

    def public(self) -> dict:
        d = asdict(self)
        d.pop("sql")
        return d


def _avg(num: str, den: str) -> str:
    return f"CAST({num} AS DOUBLE) / NULLIF({den}, 0)"


_BAT_AVG = _avg("SUM(m.runs)", "SUM(m.out)")
_BAT_SR = f"100.0 * {_avg('SUM(m.runs)', 'SUM(m.balls)')}"
_BAT_XSR = f"100.0 * {_avg('SUM(m.exp_runs)', 'SUM(m.balls)')}"
_BAT_XAVG = _avg("SUM(m.exp_runs)", "SUM(m.exp_outs)")
_BOWL_ECON = f"6.0 * {_avg('SUM(m.runs)', 'SUM(m.balls)')}"
_BOWL_XECON = f"6.0 * {_avg('SUM(m.exp_runs)', 'SUM(m.balls)')}"
_BOWL_AVG = _avg("SUM(m.runs)", "SUM(m.wickets)")


def _k(col: str, den: str) -> str:
    """A reliability constant (K, from the FIBS study) for the rows in scope:
    K varies by format, so mixed formats get the sample-weighted mean."""
    return f"(SUM(m.{den} * m.{col}) / NULLIF(SUM(m.{den}), 0))"


def _regressed(num: str, exp: str, den: str, k: str) -> str:
    """Observed rate shrunk toward the situation-expected rate by K balls:
    (sum(x) + K * expected rate) / (sum(n) + K)."""
    return (f"(SUM(m.{num}) + {_k(k, den)} * {_avg(f'SUM(m.{exp})', f'SUM(m.{den})')}) "
            f"/ NULLIF(SUM(m.{den}) + {_k(k, den)}, 0)")


def _skill_count(num: str, exp: str, den: str, k: str) -> str:
    """How many of an outcome the player's skill accounts for: their rate
    regressed toward expected by K, times their sample."""
    return f"(SUM(m.{den}) * {_regressed(num, exp, den, k)})"


# Fielding-Independent figures keep the outcomes that are the player's own
# and replace the two that fielding and luck affect most -- catches in the
# field, and runs off scoring shots in play -- with the player's skill-level
# estimate of them (their own rate, regressed by the K the FIBS study
# measured). What's left over is luck.
_SKILL_CT_BOWL = _skill_count("n_ct_field", "x_ct_field", "deliveries", "k_ct_field")
_SKILL_CT_BAT = _skill_count("n_ct_field", "x_ct_field", "balls", "k_ct_field")
_SKILL_INPLAY = _skill_count("inplay_runs", "x_inplay_runs", "n_inplay", "k_inplay_runs")
_FIB_WKTS = f"(SUM(m.wickets) - SUM(m.n_ct_field) + {_SKILL_CT_BOWL})"
_FIB_OUTS = f"(SUM(m.out) - SUM(m.n_ct_field) + {_SKILL_CT_BAT})"
_FIB_RUNS = f"(SUM(m.runs) - SUM(m.inplay_runs) + {_SKILL_INPLAY})"
_REG_BOWL_RUNS = _regressed("runs", "exp_runs", "balls", "k_runs")
_REG_BOWL_WKTS = _regressed("wickets", "exp_wkts", "balls", "k_wickets")
_REG_BAT_RUNS = _regressed("runs", "exp_runs", "balls", "k_runs")
_REG_BAT_OUTS = _regressed("out", "exp_outs", "balls", "k_outs")
_FIBS = " See the FIBS methodology page."

METRICS: tuple[Metric, ...] = (
    # ---- batting: traditional ------------------------------------------------
    Metric("matches", "Matches", BAT, "traditional", "COUNT(DISTINCT m.match_id)",
           "Matches in which the player batted (or was at the crease).", kind="int"),
    Metric("innings", "Innings", BAT, "traditional", "COUNT(*)", "Innings batted.", kind="int"),
    Metric("not_outs", "Not outs", BAT, "traditional", "COUNT(*) - SUM(m.out)", "Innings finished not out.", kind="int"),
    Metric("dismissals", "Dismissals", BAT, "traditional", "SUM(m.out)",
           "Times out (retired hurt / retired not out don't count).", kind="int"),
    Metric("runs", "Runs", BAT, "traditional", "SUM(m.runs)", "Runs off the bat.", kind="int"),
    Metric("balls", "Balls", BAT, "traditional", "SUM(m.balls)", "Balls faced (wides excluded, no-balls included).", kind="int"),
    Metric("average", "Average", BAT, "traditional", _BAT_AVG, "Runs per dismissal.", rate=True),
    Metric("strike_rate", "Strike rate", BAT, "traditional", _BAT_SR, "Runs per 100 balls.", rate=True),
    Metric("highest", "Highest", BAT, "traditional",
           "arg_max(CAST(m.runs AS VARCHAR) || CASE WHEN m.out = 0 THEN '*' ELSE '' END, m.runs * 2 + CAST(m.out = 0 AS INTEGER))",
           "Highest score (* = not out).", phase_ok=False, kind="text"),
    Metric("hundreds", "100s", BAT, "traditional", "SUM(CAST(m.runs >= 100 AS INTEGER))", "Scores of 100+.",
           phase_ok=False, kind="int"),
    Metric("fifties", "50s", BAT, "traditional", "SUM(CAST(m.runs >= 50 AND m.runs < 100 AS INTEGER))",
           "Scores of 50-99.", phase_ok=False, kind="int"),
    Metric("ducks", "Ducks", BAT, "traditional", "SUM(CAST(m.runs = 0 AND m.out = 1 AS INTEGER))",
           "Dismissed for 0.", higher_is_better=False, phase_ok=False, kind="int"),
    Metric("fours", "4s", BAT, "traditional", "SUM(m.fours)", "Fours hit.", kind="int"),
    Metric("sixes", "6s", BAT, "traditional", "SUM(m.sixes)", "Sixes hit.", kind="int"),
    # ---- batting: scoring profile --------------------------------------------
    Metric("dot_pct", "Dot %", BAT, "scoring profile", f"100.0 * {_avg('SUM(m.dots)', 'SUM(m.balls)')}",
           "Share of balls faced that weren't scored off.", higher_is_better=False, rate=True, kind="pct"),
    Metric("boundary_pct", "Boundary %", BAT, "scoring profile",
           f"100.0 * {_avg('SUM(m.fours) + SUM(m.sixes)', 'SUM(m.balls)')}",
           "Share of balls faced hit for four or six.", rate=True, kind="pct"),
    Metric("boundary_runs_pct", "Runs in boundaries %", BAT, "scoring profile",
           f"100.0 * {_avg('4 * SUM(m.fours) + 6 * SUM(m.sixes)', 'SUM(m.runs)')}",
           "Share of runs that came from fours and sixes.", rate=True, kind="pct"),
    Metric("balls_per_boundary", "Balls per boundary", BAT, "scoring profile",
           _avg("SUM(m.balls)", "SUM(m.fours) + SUM(m.sixes)"), "Balls faced per four or six.",
           higher_is_better=False, rate=True),
    Metric("balls_per_six", "Balls per six", BAT, "scoring profile", _avg("SUM(m.balls)", "SUM(m.sixes)"),
           "Balls faced per six.", higher_is_better=False, rate=True),
    Metric("balls_per_dismissal", "Balls per dismissal", BAT, "scoring profile", _avg("SUM(m.balls)", "SUM(m.out)"),
           "How long they last: balls faced per dismissal.", rate=True),
    Metric("runs_per_innings", "Runs per innings", BAT, "scoring profile", _avg("SUM(m.runs)", "COUNT(*)"),
           "Average runs per innings, counting not-outs as completed (unlike average).", rate=True),
    # ---- batting: reliability ------------------------------------------------
    Metric("conversion_pct", "50→100 conversion %", BAT, "reliability",
           "100.0 * " + _avg("SUM(CAST(m.runs >= 100 AS INTEGER))", "SUM(CAST(m.runs >= 50 AS INTEGER))"),
           "Share of 50+ scores turned into hundreds.", phase_ok=False, kind="pct"),
    Metric("thirty_plus_pct", "30+ scores %", BAT, "reliability", "100.0 * AVG(CAST(m.runs >= 30 AS INTEGER))",
           "Share of innings with 30 or more.", phase_ok=False, rate=True, kind="pct"),
    Metric("median_score", "Median score", BAT, "reliability", "median(m.runs)",
           "The middle innings score -- less swayed by one big knock than the average.", phase_ok=False),
    Metric("consistency_cv", "Score variability (CV)", BAT, "reliability",
           _avg("stddev_samp(m.runs)", "AVG(m.runs)"),
           "Standard deviation of scores / mean score. Lower = more consistent.",
           higher_is_better=False, phase_ok=False, rate=True),
    # ---- batting: context-adjusted (Kimber-style) ----------------------------
    Metric("expected_sr", "Expected SR", BAT, "context", _BAT_XSR,
           "Strike rate an average batter would have had facing the same balls (same format, year, gender, "
           "innings, over and wickets down).", rate=True),
    Metric("true_sr", "True strike rate", BAT, "context", f"({_BAT_SR}) - ({_BAT_XSR})",
           "Strike rate minus Expected SR: runs per 100 balls above an average batter in the same situations. "
           "0 = average, +10 = ten runs per 100 balls better.", rate=True),
    Metric("expected_average", "Expected average", BAT, "context", _BAT_XAVG,
           "Average an average batter would have had in the same situations.", rate=True),
    Metric("true_average", "True average", BAT, "context", f"({_BAT_AVG}) - ({_BAT_XAVG})",
           "Average minus Expected average: runs per dismissal above an average batter in the same situations.",
           rate=True),
    Metric("runs_above_expected", "Runs above expected", BAT, "context", "SUM(m.runs) - SUM(m.exp_runs)",
           "Total runs scored beyond what an average batter would have scored off the same balls."),
    Metric("rae_per_innings", "Runs above expected / inns", BAT, "context",
           _avg("SUM(m.runs) - SUM(m.exp_runs)", "COUNT(*)"), "Runs above expected per innings.", rate=True),
    Metric("match_factor", "Match factor", BAT, "context",
           f"({_BAT_AVG}) / NULLIF({_avg('SUM(m.mc_runs)', 'SUM(m.mc_outs)')}, 0)",
           "Batting average divided by the average of every other top-7 batter in the same matches. "
           "1.0 = par for the conditions they played in; 1.5 = 50% better.", phase_ok=False, rate=True),
    Metric("era_factor", "Era factor", BAT, "context",
           f"({_BAT_AVG}) / NULLIF({_avg('SUM(m.era_runs)', 'SUM(m.era_outs)')}, 0)",
           "Batting average divided by the average of batters in the same position, format and era "
           "(+/- 2 years). 1.0 = par for their slot and era.", phase_ok=False, rate=True),
    Metric("first5_sr", "First-5-ball SR", BAT, "context",
           f"100.0 * {_avg('SUM(m.runs_first5)', 'SUM(m.balls_first5)')}",
           "Strike rate over the first five balls of each innings -- how fast they start.", phase_ok=False, rate=True),
    Metric("true_first5_sr", "True first-5-ball SR", BAT, "context",
           f"100.0 * {_avg('SUM(m.runs_first5) - SUM(m.exp_runs_first5)', 'SUM(m.balls_first5)')}",
           "First-five-ball strike rate above an average batter facing those same balls.", phase_ok=False, rate=True),
    # ---- batting: FIBS -------------------------------------------------
    Metric("fib_average", "FIB average", BAT, "fielding-independent",
           _avg(_FIB_RUNS, _FIB_OUTS),
           "Batting average with the two outcomes fielding and luck affect most -- runs off scoring shots in play "
           "(not boundaries) and catches in the field -- replaced by the batter's skill-level rates for them "
           "(their own rates regressed to the situation average by how reliable each is)." + _FIBS,
           phase_ok=False, rate=True),
    Metric("fib_sr", "FIB strike rate", BAT, "fielding-independent", f"100.0 * {_avg(_FIB_RUNS, 'SUM(m.balls)')}",
           "Strike rate with runs off scoring shots in play replaced by the batter's skill-level rate for "
           "them." + _FIBS, phase_ok=False, rate=True),
    Metric("runs_luck", "Runs luck", BAT, "fielding-independent", f"SUM(m.runs) - {_FIB_RUNS}",
           "Runs off scoring shots in play beyond what the batter's skill accounts for. Positive = the gaps and "
           "the fielders were kind." + _FIBS, phase_ok=False),
    Metric("dismissal_luck", "Dismissal luck", BAT, "fielding-independent", f"{_FIB_OUTS} - SUM(m.out)",
           "Catches in the field the batter's skill accounts for minus the catches actually taken off them. "
           "Positive = caught less often than their game deserved." + _FIBS, phase_ok=False),
    Metric("regressed_sr", "Regressed strike rate", BAT, "reliability-adjusted", f"100.0 * {_REG_BAT_RUNS}",
           "Strike rate shrunk toward the situation-expected strike rate by K balls, K being how many balls it "
           "takes for scoring rate to be half skill, half noise -- a fair estimate from small samples." + _FIBS,
           phase_ok=False, rate=True),
    Metric("regressed_average", "Regressed average", BAT, "reliability-adjusted",
           f"({_REG_BAT_RUNS}) / NULLIF({_REG_BAT_OUTS}, 0)",
           "Average from scoring rate and dismissal rate each shrunk toward the situation-expected rates by "
           "their own K. Short careers are pulled toward average; long ones barely move." + _FIBS,
           phase_ok=False, rate=True),
    Metric("regressed_dot_pct", "Regressed dot %", BAT, "reliability-adjusted",
           f"100.0 * {_regressed('n_dot', 'x_dot', 'balls', 'k_dot')}",
           "Dot-ball percentage shrunk toward the situation-expected rate by K balls." + _FIBS,
           higher_is_better=False, phase_ok=False, rate=True, kind="pct"),
    Metric("regressed_boundary_pct", "Regressed boundary %", BAT, "reliability-adjusted",
           f"100.0 * (SUM(m.n_four) + SUM(m.n_six) + {_k('k_boundary', 'balls')} * "
           f"{_avg('SUM(m.x_four) + SUM(m.x_six)', 'SUM(m.balls)')}) / NULLIF(SUM(m.balls) + {_k('k_boundary', 'balls')}, 0)",
           "Boundary percentage shrunk toward the situation-expected rate by K balls." + _FIBS,
           phase_ok=False, rate=True, kind="pct"),
    # ---- batting: role & situation -------------------------------------------
    Metric("avg_position", "Avg batting position", BAT, "role", "AVG(m.position)",
           "Average position in the order (1 = opener).", higher_is_better=False, phase_ok=False),
    Metric("avg_entry_over", "Avg entry over", BAT, "role", "AVG(m.entry_over) + 1",
           "Average over in which they came in.", higher_is_better=False, phase_ok=False),
    Metric("avg_entry_wickets", "Avg wickets down at entry", BAT, "role", "AVG(m.entry_wkts)",
           "Average wickets already down when they came in.", higher_is_better=False, phase_ok=False),
    Metric("team_win_pct", "Team win %", BAT, "role",
           "100.0 * " + _avg("SUM(CAST(m.result = 'won' AS INTEGER))", "SUM(CAST(m.result <> 'no result' AS INTEGER))"),
           "Share of decided games their team won when they batted.", phase_ok=False, kind="pct"),

    # ---- bowling: traditional --------------------------------------------------
    Metric("matches", "Matches", BOWL, "traditional", "COUNT(DISTINCT m.match_id)", "Matches in which they bowled.",
           kind="int"),
    Metric("innings", "Innings", BOWL, "traditional", "COUNT(*)", "Innings bowled in.", kind="int"),
    Metric("balls", "Balls", BOWL, "traditional", "SUM(m.balls)", "Legal balls bowled.", kind="int"),
    Metric("runs", "Runs conceded", BOWL, "traditional", "SUM(m.runs)",
           "Runs charged to the bowler (byes, leg-byes and penalties excluded).", higher_is_better=False, kind="int"),
    Metric("wickets", "Wickets", BOWL, "traditional", "SUM(m.wickets)",
           "Wickets credited to the bowler (run-outs excluded).", kind="int"),
    Metric("average", "Average", BOWL, "traditional", _BOWL_AVG, "Runs conceded per wicket.",
           higher_is_better=False, rate=True),
    Metric("economy", "Economy", BOWL, "traditional", _BOWL_ECON, "Runs conceded per over.",
           higher_is_better=False, rate=True),
    Metric("strike_rate", "Strike rate", BOWL, "traditional", _avg("SUM(m.balls)", "SUM(m.wickets)"),
           "Balls per wicket.", higher_is_better=False, rate=True),
    Metric("best", "Best figures", BOWL, "traditional",
           "arg_max(CAST(m.wickets AS VARCHAR) || '/' || CAST(m.runs AS VARCHAR), m.wickets * 100000 - m.runs)",
           "Best figures in an innings.", phase_ok=False, kind="text"),
    Metric("four_wkt_hauls", "4w", BOWL, "traditional", "SUM(CAST(m.wickets >= 4 AS INTEGER))",
           "Innings with 4+ wickets.", phase_ok=False, kind="int"),
    Metric("five_wkt_hauls", "5w", BOWL, "traditional", "SUM(CAST(m.wickets >= 5 AS INTEGER))",
           "Innings with 5+ wickets.", phase_ok=False, kind="int"),
    Metric("maidens", "Maidens", BOWL, "traditional", "SUM(m.maidens)", "Overs with no runs conceded.",
           phase_ok=False, kind="int"),
    # ---- bowling: profile ------------------------------------------------------
    Metric("dot_pct", "Dot %", BOWL, "scoring profile", f"100.0 * {_avg('SUM(m.dots)', 'SUM(m.balls)')}",
           "Share of legal balls with nothing conceded.", rate=True, kind="pct"),
    Metric("boundary_pct", "Boundary % conceded", BOWL, "scoring profile",
           f"100.0 * {_avg('SUM(m.fours) + SUM(m.sixes)', 'SUM(m.balls)')}",
           "Share of legal balls hit for four or six.", higher_is_better=False, rate=True, kind="pct"),
    Metric("balls_per_boundary", "Balls per boundary conceded", BOWL, "scoring profile",
           _avg("SUM(m.balls)", "SUM(m.fours) + SUM(m.sixes)"), "Legal balls per four or six conceded.", rate=True),
    Metric("wickets_per_innings", "Wickets per innings", BOWL, "scoring profile", _avg("SUM(m.wickets)", "COUNT(*)"),
           "Average wickets per innings bowled.", rate=True),
    # ---- bowling: context-adjusted ---------------------------------------------
    Metric("expected_economy", "Expected economy", BOWL, "context", _BOWL_XECON,
           "Economy an average bowler would have had bowling the same situations (format, year, gender, "
           "innings, over, wickets down).", higher_is_better=False, rate=True),
    Metric("true_economy", "True economy", BOWL, "context", f"({_BOWL_XECON}) - ({_BOWL_ECON})",
           "Expected economy minus actual: runs per over saved versus an average bowler in the same "
           "situations. Positive = better than average.", rate=True),
    Metric("expected_wickets", "Expected wickets", BOWL, "context", "SUM(m.exp_wkts)",
           "Wickets an average bowler would have taken bowling the same situations."),
    Metric("true_wickets", "True wickets", BOWL, "context", "SUM(m.wickets) - SUM(m.exp_wkts)",
           "Wickets above what an average bowler would have taken in the same situations."),
    Metric("runs_saved", "Runs saved", BOWL, "context", "SUM(m.exp_runs) - SUM(m.runs)",
           "Runs conceded below what an average bowler would have conceded in the same situations."),
    Metric("match_factor", "Bowling match factor", BOWL, "context",
           f"({_avg('SUM(m.mc_runs)', 'SUM(m.mc_wkts)')}) / NULLIF({_BOWL_AVG}, 0)",
           "Average of every other bowler in the same matches divided by this bowler's average. "
           "1.0 = par for the conditions; above 1 = better.", phase_ok=False, rate=True),
    # ---- bowling: FIBS -------------------------------------------------
    Metric("fib_economy", "FIB economy", BOWL, "fielding-independent", f"6.0 * {_avg(_FIB_RUNS, 'SUM(m.balls)')}",
           "Fielding-Independent Bowling: economy with runs off scoring shots in play (not boundaries) replaced by "
           "the bowler's skill-level rate for them -- their own rate regressed to the situation average by how "
           "reliable it is. Dots, boundaries and extras stay the bowler's own." + _FIBS,
           higher_is_better=False, phase_ok=False, rate=True),
    Metric("fib_wickets", "FIB wickets", BOWL, "fielding-independent", _FIB_WKTS,
           "Wickets with catches in the field replaced by the bowler's skill-level number of them; bowled, lbw, "
           "caught behind, caught and bowled and stumped stay the bowler's own." + _FIBS, phase_ok=False),
    Metric("fib_average", "FIB average", BOWL, "fielding-independent", _avg(_FIB_RUNS, _FIB_WKTS),
           "Bowling average from FIB runs and FIB wickets." + _FIBS, higher_is_better=False, phase_ok=False,
           rate=True),
    Metric("fib_strike_rate", "FIB strike rate", BOWL, "fielding-independent", _avg("SUM(m.balls)", _FIB_WKTS),
           "Balls per FIB wicket." + _FIBS, higher_is_better=False, phase_ok=False, rate=True),
    Metric("wicket_luck", "Wicket luck", BOWL, "fielding-independent", f"SUM(m.n_ct_field) - {_SKILL_CT_BOWL}",
           "Wickets minus FIB wickets: catches in the field beyond what the bowler's skill accounts for. "
           "Positive = more catches than their bowling deserved (edges went to hand, catches stuck)." + _FIBS,
           phase_ok=False),
    Metric("runs_luck", "Runs luck", BOWL, "fielding-independent", f"{_FIB_RUNS} - SUM(m.runs)",
           "FIB runs minus runs conceded: runs saved off scoring shots in play beyond what the bowler's skill "
           "accounts for. Positive = the gaps and the fielders helped." + _FIBS, phase_ok=False),
    Metric("inplay_runs_per_shot", "Runs per scoring shot in play", BOWL, "fielding-independent",
           _avg("SUM(m.inplay_runs)", "SUM(m.n_inplay)"),
           "Average runs off each non-boundary scoring shot -- cricket's version of baseball's batting average "
           "on balls in play." + _FIBS, higher_is_better=False, rate=True),
    Metric("bowled_lbw_pct", "Bowled + LBW %", BOWL, "dismissal profile",
           f"100.0 * {_avg('SUM(m.n_bowled) + SUM(m.n_lbw)', 'SUM(m.deliveries)')}",
           "Bowled and lbw dismissals per 100 deliveries: wickets the bowler takes without a fielder.",
           rate=True, kind="pct"),
    Metric("caught_behind_pct", "Caught behind %", BOWL, "dismissal profile",
           f"100.0 * {_avg('SUM(m.n_ct_keeper)', 'SUM(m.deliveries)')}",
           "Catches by the wicketkeeper per 100 deliveries (the keeper is inferred from stumpings)." + _FIBS,
           rate=True, kind="pct"),
    Metric("caught_field_pct", "Caught in the field %", BOWL, "dismissal profile",
           f"100.0 * {_avg('SUM(m.n_ct_field)', 'SUM(m.deliveries)')}",
           "Catches by fielders other than the keeper and the bowler, per 100 deliveries.", rate=True, kind="pct"),
    Metric("regressed_economy", "Regressed economy", BOWL, "reliability-adjusted", f"6.0 * {_REG_BOWL_RUNS}",
           "Economy shrunk toward the situation-expected economy by K balls, K being how many balls it takes for "
           "economy to be half skill, half noise -- a fair estimate from small samples." + _FIBS,
           higher_is_better=False, phase_ok=False, rate=True),
    Metric("regressed_strike_rate", "Regressed strike rate", BOWL, "reliability-adjusted",
           f"1.0 / NULLIF({_REG_BOWL_WKTS}, 0)",
           "Balls per wicket from a wicket rate shrunk toward the expected rate by K balls. Wicket rates need "
           "thousands of balls to mean much, so this moves a lot." + _FIBS,
           higher_is_better=False, phase_ok=False, rate=True),
    Metric("regressed_average", "Regressed average", BOWL, "reliability-adjusted",
           f"({_REG_BOWL_RUNS}) / NULLIF({_REG_BOWL_WKTS}, 0)",
           "Bowling average from economy and wicket rate each shrunk toward the expected rates by their own K."
           + _FIBS, higher_is_better=False, phase_ok=False, rate=True),
    Metric("regressed_dot_pct", "Regressed dot %", BOWL, "reliability-adjusted",
           f"100.0 * {_regressed('n_dot', 'x_dot', 'deliveries', 'k_dot')}",
           "Dot-ball percentage (of deliveries) shrunk toward the expected rate by K balls." + _FIBS,
           phase_ok=False, rate=True, kind="pct"),
    Metric("regressed_boundary_pct", "Regressed boundary %", BOWL, "reliability-adjusted",
           f"100.0 * (SUM(m.n_four) + SUM(m.n_six) + {_k('k_boundary', 'deliveries')} * "
           f"{_avg('SUM(m.x_four) + SUM(m.x_six)', 'SUM(m.deliveries)')}) / NULLIF(SUM(m.deliveries) + {_k('k_boundary', 'deliveries')}, 0)",
           "Boundary percentage (of deliveries) shrunk toward the expected rate by K balls." + _FIBS,
           higher_is_better=False, phase_ok=False, rate=True, kind="pct"),
    Metric("team_win_pct", "Team win %", BOWL, "role",
           "100.0 * " + _avg("SUM(CAST(m.result = 'won' AS INTEGER))", "SUM(CAST(m.result <> 'no result' AS INTEGER))"),
           "Share of decided games their team won when they bowled.", phase_ok=False, kind="pct"),
)

_BY_KEY = {(m.role, m.id): m for m in METRICS}


def get(role: str, metric_id: str) -> Metric:
    m = _BY_KEY.get((role, metric_id))
    if m is None:
        valid = ", ".join(x.id for x in METRICS if x.role == role)
        raise ValueError(f"Unknown {role} metric '{metric_id}'. Valid: {valid}")
    return m


def for_role(role: str) -> list[Metric]:
    return [m for m in METRICS if m.role == role]


def search(query: str = "", role: str | None = None) -> list[dict]:
    """Metrics whose id, label, family or definition mention every word of
    the query (for discovery by the UI and the AI)."""
    words = query.lower().split()
    out = []
    for m in METRICS:
        if role and m.role != role:
            continue
        hay = f"{m.id} {m.label} {m.family} {m.definition}".lower()
        if all(w in hay for w in words):
            out.append(m.public())
    return out


# Dimensions a result can be split by. `roles` limits batting-only ones.
DIMENSIONS = {
    "season": {"label": "Season", "roles": (BAT, BOWL)},
    "year": {"label": "Year", "roles": (BAT, BOWL)},
    "format": {"label": "Format", "roles": (BAT, BOWL)},
    "competition": {"label": "Competition", "roles": (BAT, BOWL)},
    "team": {"label": "Team", "roles": (BAT, BOWL)},
    "opposition": {"label": "Opposition", "roles": (BAT, BOWL)},
    "venue": {"label": "Venue", "roles": (BAT, BOWL)},
    "innings": {"label": "Innings", "roles": (BAT, BOWL)},
    "chase": {"label": "Setting / chasing", "roles": (BAT, BOWL)},
    "result": {"label": "Result", "roles": (BAT, BOWL)},
    "phase": {"label": "Phase", "roles": (BAT, BOWL)},
    "position": {"label": "Batting position", "roles": (BAT,)},
    "entry_wickets": {"label": "Wickets down at entry", "roles": (BAT,)},
    "entry_phase": {"label": "Entry phase", "roles": (BAT,)},
    "dismissal": {"label": "Dismissal type", "roles": (BAT,)},
}
