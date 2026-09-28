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
