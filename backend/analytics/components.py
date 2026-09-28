"""
The outcome taxonomy behind FIBS: every ball broken into the things
that can happen on it, for the bowler (per delivery bowled) and the batter
(per ball faced).

Each component is a SQL expression over one ball of the build's ball table
(`b`, see build.py) that counts how often the outcome happened on that ball.
build.py computes, for every component, the expected rate in each ball state
(format group x gender x year x innings x over x wickets down) and stores
observed and expected counts per innings (n_<id> and x_<id>), so any
component can be read "above expected" -- relative to an average player in
the same situations.

"Caught behind" needs to know who kept wicket, which Cricsheet doesn't
record: the keeper of a side in a match is the XI member who made a stumping
in it, otherwise the one with the most career stumpings (see build.py). A
side with no stumper on record has every catch counted as in the field.
"""

from __future__ import annotations

# Scoring shot in play: the batter scored off the bat without a boundary.
# The runs on these balls are what fielding and luck affect most -- the
# cricket version of baseball's "balls in play".
INPLAY_SQL = "(faced AND runs_batter > 0 AND NOT is_four AND NOT is_six)"

# Bowling components: counts per delivery bowled.
BOWL_COMPONENTS: dict[str, tuple[str, str]] = {
    "dot": ("Dot balls", "CAST(legal AND bowler_runs = 0 AS INTEGER)"),
    "four": ("Fours conceded", "CAST(is_four AS INTEGER)"),
    "six": ("Sixes conceded", "CAST(is_six AS INTEGER)"),
    "wide": ("Wides", "CAST(NOT faced AS INTEGER)"),
    "noball": ("No-balls", "CAST(faced AND NOT legal AS INTEGER)"),
    "inplay": ("Scoring shots in play", f"CAST({INPLAY_SQL} AS INTEGER)"),
    "bowled": ("Bowled", "w_bowled"),
    "lbw": ("LBW", "w_lbw"),
    "ct_keeper": ("Caught by the keeper", "w_ct_keeper"),
    "ct_field": ("Caught in the field", "w_ct_field"),
    "ct_bowler": ("Caught and bowled", "w_ct_bowler"),
    "stumped": ("Stumped", "w_stumped"),
    "hit_wicket": ("Hit wicket", "w_hit_wicket"),
    "run_out": ("Run outs", "w_run_out"),
}

# Batting components: counts per ball faced (only the striker's dismissals).
BAT_COMPONENTS: dict[str, tuple[str, str]] = {
    "dot": ("Dot balls", "CAST(runs_batter = 0 AS INTEGER)"),
    "four": ("Fours", "CAST(is_four AS INTEGER)"),
    "six": ("Sixes", "CAST(is_six AS INTEGER)"),
    "inplay": ("Scoring shots in play", f"CAST({INPLAY_SQL} AS INTEGER)"),
    "bowled": ("Out bowled", "CAST(striker_kind = 'bowled' AS INTEGER)"),
    "lbw": ("Out LBW", "CAST(striker_kind = 'lbw' AS INTEGER)"),
    "ct_keeper": ("Out caught by the keeper", "CAST(striker_kind = 'caught' AND striker_keeper AS INTEGER)"),
    "ct_field": ("Out caught in the field", "CAST(striker_kind = 'caught' AND NOT striker_keeper AS INTEGER)"),
    "ct_bowler": ("Out caught and bowled", "CAST(striker_kind = 'caught and bowled' AS INTEGER)"),
    "stumped": ("Out stumped", "CAST(striker_kind = 'stumped' AS INTEGER)"),
    "run_out": ("Run out (as striker)", "CAST(striker_kind = 'run out' AS INTEGER)"),
}


def bowl_cols() -> list[str]:
    """Per-innings columns build.py adds to bowling_innings / bowling_phase."""
    return [f"{p}_{c}" for c in BOWL_COMPONENTS for p in ("n", "x")] + \
        ["inplay_runs", "x_inplay_runs", "fib_runs", "fib_wkts"]


def bat_cols() -> list[str]:
    """Per-innings columns build.py adds to batting_innings."""
    return [f"{p}_{c}" for c in BAT_COMPONENTS for p in ("n", "x")] + \
        ["inplay_runs", "x_inplay_runs", "fib_runs", "fib_outs"]
