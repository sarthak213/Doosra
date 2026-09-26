"""
Test fixtures.

A small DuckDB is built through the REAL ingest path (ingest.build_db.main)
from three synthetic Cricsheet-format matches deliberately covering the
scoring-rule edge cases the stats functions exist to get right:

- match_a: a no-ball that also runs byes (multi-extra ball), a wide, a
  penalty, a run-out of the NON-striker while facing a different batter,
  a retired hurt, a stumping, a caught-and-bowled, and a ball with TWO
  wickets on it (run out + caught).
- match_b: a women's match, for gender filtering.
- match_c: a second India-Australia match in a later season, for
  head-to-head and season-trend coverage.

Expected values in the tests are computed BY HAND from these matches, not
by running the code under test.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import stats, tools  # noqa: E402
from ingest import build_db  # noqa: E402


def _delivery(batter, bowler, runs_batter=0, runs_extras=0, extras=None, wickets=None, non_striker=""):
    d = {
        "batter": batter,
        "bowler": bowler,
        "runs": {"batter": runs_batter, "extras": runs_extras, "total": runs_batter + runs_extras},
    }
    if non_striker:
        d["non_striker"] = non_striker
    if extras:
        d["extras"] = extras
    if wickets:
        d["wickets"] = wickets
    return d


MATCH_A = {
    "info": {
        "match_type": "T20",
        "event": {"name": "Test Bash League", "match_number": 1},
        "gender": "male",
        "team_type": "international",
        "overs": 20,
        "dates": ["2024-01-01"],
        "venue": "City Oval",
        "city": "Cityville",
        "season": "2023/24",
        "teams": ["India", "Australia"],
        "toss": {"winner": "India", "decision": "bat"},
        "outcome": {"winner": "India", "by": {"runs": 5}},
        "player_of_match": ["S Sharma"],
        "players": {
            "India": ["S Sharma", "V Kohli", "R Pant", "H Pandya", "A Patel", "J Bumrah"],
            "Australia": ["D Warner", "M Wade", "S Smith", "G Maxwell", "P Starc", "A Zampa"],
        },
    },
    "innings": [
        {
            "team": "India",
            "overs": [
                {
                    "over": 0,
                    "deliveries": [
                        _delivery("S Sharma", "P Starc", runs_batter=1, non_striker="V Kohli"),
                        _delivery("S Sharma", "P Starc", runs_batter=4, non_striker="V Kohli"),
                        _delivery("S Sharma", "P Starc", runs_batter=6, non_striker="V Kohli"),
                        # Wide: batter does NOT face it, bowler IS charged.
                        _delivery("V Kohli", "P Starc", runs_extras=1, extras={"wides": 1}, non_striker="S Sharma"),
                        # No-ball + byes on one ball: batter DOES face it,
                        # bowler charged the no-ball (1) but NOT the byes (2).
                        _delivery(
                            "V Kohli", "P Starc", runs_extras=3,
                            extras={"noballs": 1, "byes": 2}, non_striker="S Sharma",
                        ),
                        # Run-out of the NON-striker (S Sharma) while Kohli
                        # faces: a dismissal for Sharma, not a wicket for Starc.
                        _delivery(
                            "V Kohli", "P Starc", runs_batter=1,
                            wickets=[{"kind": "run out", "player_out": "S Sharma", "fielders": [{"name": "G Maxwell"}]}],
                            non_striker="S Sharma",
                        ),
                        _delivery(
                            "V Kohli", "P Starc",
                            wickets=[{"kind": "bowled", "player_out": "V Kohli"}], non_striker="R Pant",
                        ),
                        _delivery(
                            "R Pant", "P Starc",
                            wickets=[{"kind": "caught", "player_out": "R Pant", "fielders": [{"name": "S Smith"}]}],
                            non_striker="H Pandya",
                        ),
                        _delivery(
                            "H Pandya", "P Starc",
                            wickets=[{"kind": "stumped", "player_out": "H Pandya", "fielders": [{"name": "M Wade"}]}],
                            non_striker="A Patel",
                        ),
                    ],
                },
                {
                    "over": 1,
                    "deliveries": [
                        # Leg-bye: batter faces it, bowler NOT charged.
                        _delivery("H Pandya", "A Zampa", runs_extras=1, extras={"legbyes": 1}, non_striker="A Patel"),
                        _delivery("H Pandya", "A Zampa", runs_batter=2, non_striker="A Patel"),
                        # Retired hurt: a dismissal, not a bowler wicket.
                        _delivery(
                            "H Pandya", "A Zampa",
                            wickets=[{"kind": "retired hurt", "player_out": "H Pandya"}], non_striker="A Patel",
                        ),
                        _delivery("A Patel", "A Zampa", runs_batter=1, non_striker="V Kohli"),
                        _delivery(
                            "A Patel", "A Zampa",
                            wickets=[{"kind": "caught and bowled", "player_out": "A Patel", "fielders": [{"name": "A Zampa"}]}],
                            non_striker="V Kohli",
                        ),
                    ],
                },
            ],
        },
        {
            "team": "Australia",
            "overs": [
                {
                    "over": 0,
                    "deliveries": [
                        _delivery("D Warner", "J Bumrah", runs_batter=6, non_striker="M Wade"),
                        # TWO wickets on one ball: run out + caught. Old
                        # schema recorded only the first; new schema gets both.
                        _delivery(
                            "D Warner", "J Bumrah",
                            wickets=[
                                {"kind": "run out", "player_out": "M Wade", "fielders": [{"name": "V Kohli"}]},
                                {"kind": "caught", "player_out": "D Warner", "fielders": [{"name": "V Kohli"}]},
                            ],
                            non_striker="M Wade",
                        ),
                        # Penalty: not charged to the bowler.
                        _delivery("G Maxwell", "J Bumrah", runs_extras=5, extras={"penalty": 5}, non_striker="S Smith"),
                    ],
                }
            ],
        },
    ],
}

MATCH_B = {
    "info": {
        "match_type": "T20",
        "gender": "female",
        "team_type": "international",
        "overs": 20,
        "dates": ["2024-02-01"],
        "venue": "Women's Oval",
        "city": "Cityville",
        "season": "2023/24",
        "teams": ["India Women", "Australia Women"],
        "toss": {"winner": "India Women", "decision": "field"},
        "outcome": {"winner": "Australia Women", "by": {"wickets": 8}},
        "players": {
            "India Women": ["S Mandhana", "H Kaur", "R Gayakwad"],
            "Australia Women": ["M Lanning", "E Perry", "J Jonassen"],
        },
    },
    "innings": [
        {
            "team": "India Women",
            "overs": [
                {
                    "over": 0,
                    "deliveries": [
                        _delivery("S Mandhana", "E Perry", runs_batter=3, non_striker="H Kaur"),
                        _delivery(
                            "S Mandhana", "E Perry",
                            wickets=[{"kind": "caught", "player_out": "S Mandhana", "fielders": [{"name": "M Lanning"}]}],
                            non_striker="H Kaur",
                        ),
                    ],
                }
            ],
        },
        {
            "team": "Australia Women",
            "overs": [
                {
                    "over": 0,
                    "deliveries": [
                        _delivery("M Lanning", "R Gayakwad", runs_batter=4, non_striker="E Perry"),
                        _delivery(
                            "M Lanning", "R Gayakwad",
                            wickets=[{"kind": "lbw", "player_out": "M Lanning"}], non_striker="E Perry",
                        ),
                    ],
                }
            ],
        },
    ],
}

MATCH_C = {
    "info": {
        "match_type": "T20",
        "gender": "male",
        "team_type": "international",
        "overs": 20,
        "dates": ["2024-06-01"],
        "venue": "Second City Ground",
        "city": "Townsburg",
        "season": "2024/25",
        "teams": ["India", "Australia"],
        "toss": {"winner": "Australia", "decision": "field"},
        "outcome": {"winner": "Australia", "by": {"wickets": 6}},
        "players": {
            "India": ["S Sharma", "V Kohli", "J Bumrah"],
            "Australia": ["D Warner", "G Maxwell", "P Starc", "S Smith"],
        },
    },
    "innings": [
        {
            "team": "India",
            "overs": [
                {
                    "over": 0,
                    "deliveries": [
                        _delivery("S Sharma", "P Starc", runs_batter=4, non_striker="V Kohli"),
                        _delivery(
                            "S Sharma", "P Starc",
                            wickets=[{"kind": "caught", "player_out": "S Sharma", "fielders": [{"name": "S Smith"}]}],
                            non_striker="V Kohli",
                        ),
                    ],
                }
            ],
        },
        {
            "team": "Australia",
            "overs": [
                {
                    "over": 0,
                    "deliveries": [
                        _delivery("D Warner", "J Bumrah", runs_batter=6, non_striker="G Maxwell"),
                        _delivery("G Maxwell", "J Bumrah", runs_batter=1, non_striker="D Warner"),
                    ],
                }
            ],
        },
    ],
}

MATCHES = {"match_a": MATCH_A, "match_b": MATCH_B, "match_c": MATCH_C}


@pytest.fixture(scope="session")
def fixture_db_path(tmp_path_factory):
    """Builds the synthetic-match database through the real ingest pipeline."""
    import json

    raw_dir = tmp_path_factory.mktemp("raw")
    for match_id, match in MATCHES.items():
        (raw_dir / f"{match_id}.json").write_text(json.dumps(match), encoding="utf-8")

    out_dir = tmp_path_factory.mktemp("out")
    db_path = out_dir / "fixture.duckdb"

    argv = sys.argv
    sys.argv = ["build_db.py", "--raw-dir", str(raw_dir), "--out", str(db_path)]
    try:
        build_db.main()
    finally:
        sys.argv = argv
    return db_path


@pytest.fixture(autouse=True)
def use_fixture_db(monkeypatch, fixture_db_path):
    """Points every tools/stats query at the fixture DB and resets the
    in-process caches so a test that ran earlier against another DB can't
    leak stale schema/name caches into this one."""
    monkeypatch.setattr(tools, "DB_PATH", fixture_db_path)
    monkeypatch.setattr(tools, "_player_names_cache", None)
    monkeypatch.setattr(tools, "_tournament_names_cache", None)
    monkeypatch.setattr(stats, "_delivery_columns_cache", None)
    yield
