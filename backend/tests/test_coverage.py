"""Cricsheet's coverage and missing-match pages (ingest/coverage.py) and the
coverage report built on them (analytics/coverage.py). The HTML fixtures
mirror the structure of cricsheet.org/coverage/ and /missing/."""

import json
import zipfile

import pytest

from analytics import build as analytics_build
from analytics import catalog, db, engine
from analytics import coverage as cov
from ingest import build_db, coverage

COVERAGE_HTML = """<html><body>
<h3>What do we mean by coverage?</h3><p>...</p>
<h3>Coverage periods</h3>
<h4>Women’s Matches</h4>
<table><tr><th>Match Type</th><th>Earliest checked</th><th>Earliest provided</th></tr>
<tr><td>Test Matches</td><td>Jan 2002</td><td>Feb 2003</td></tr></table>
<table><tr><th>Competition</th><th>Earliest checked</th><th>Earliest provided</th></tr>
<tr><td>Super Smash</td><td>Dec 2009</td><td>Jan 2020</td></tr></table>
<h4>Men’s Matches</h4>
<table><tr><th>Match Type</th><th>Earliest checked</th><th>Earliest provided</th></tr>
<tr><td>Test Matches</td><td>Dec 2001</td><td>Dec 2001</td></tr>
<tr><td>One-day Internationals</td><td>Dec 2001</td><td>Jun 2002</td></tr></table>
<table><tr><th>Competition</th><th>Earliest checked</th><th>Earliest provided</th></tr>
<tr><td>One-Day Cup (Australia)</td><td>Feb 2021</td><td>Feb 2021</td></tr></table>
<h3>The numbers</h3>
<h4>By Competition</h4>
<table><tr><th>Competition</th><th>Coverage</th><th>%</th></tr>
<tr><td>One-Day Cup (Australia)</td><td>112 of 115</td><td>97.39</td></tr>
<tr><td>Super Smash</td><td>40 of 50</td><td>80.00</td></tr></table>
<h6>Women’s matches</h6>
<table><tr><th>Competition</th><th>Coverage</th><th>%</th></tr>
<tr><td>Super Smash</td><td>40 of 50</td><td>80.00</td></tr></table>
<h6>Men’s matches</h6>
<table><tr><th>Competition</th><th>Coverage</th><th>%</th></tr>
<tr><td>One-Day Cup (Australia)</td><td>112 of 115</td><td>97.39</td></tr></table>
<h4>By Team</h4>
<table><tr><th>Team</th><th>Coverage</th><th>%</th></tr>
<tr><td>India</td><td>1,200 of 1,260</td><td>95.24</td></tr></table>
</body></html>"""

MISSING_HTML = """<html><body>
<h3>What do we mean by missing?</h3>
<h4>The missing entries</h4>
<h4>By match type</h4>
<h5>Test Matches</h5>
<h6>Male matches</h6>
<dl>
  <dt>2006-03-18</dt>
  <dd>England vs India</dd>
  <dt>2006-05-01</dt>
  <dd>Australia vs West Indies</dd>
  <dd>South Africa vs India</dd>
</dl>
<h5>Odi Matches</h5>
<h6>Female matches</h6>
<dl><dt>2002-01-06</dt><dd>England vs India</dd></dl>
<h4>By competition</h4>
<h5>One day cup ( australia) Matches</h5>
<h6>Male matches</h6>
<dl><dt>2021-02-10</dt><dd>Victoria vs Queensland</dd></dl>
</body></html>"""

MATCHES_HTML = """<p>One thing that should be explained, is that 377 matches are currently being withheld from the data
provided on the site. These matches either involve Afghanistan, or took place in the Afghanistan Premier League.
A more complete explanation ...</p>"""


@pytest.fixture(autouse=True)
def schema():
    """Override conftest's schema-parametrized fixture: these tests build
    their own database."""
    yield "coverage"


def test_parse_coverage_periods_and_counts():
    periods, counts = coverage.parse_coverage(COVERAGE_HTML)
    got = {(r.gender, r.kind, r.name): (r.earliest_checked, r.earliest_provided) for r in periods.itertuples()}
    assert got[("female", "match type", "Test Matches")] == ("2002-01", "2003-02")
    assert got[("male", "match type", "One-day Internationals")] == ("2001-12", "2002-06")
    assert got[("male", "competition", "One-Day Cup (Australia)")] == ("2021-02", "2021-02")
    # The combined competition table is skipped: one row per competition per gender.
    comp = counts[counts.kind == "competition"]
    assert sorted(zip(comp.gender, comp.name)) == [("female", "Super Smash"), ("male", "One-Day Cup (Australia)")]
    team = counts[counts.kind == "team"].iloc[0]
    assert (team["name"], team["have"], team["total"], team["pct"]) == ("India", 1200, 1260, 95.24)
    assert team["gender"] is None or team["gender"] != team["gender"]  # None / NaN -> NULL in the table


def test_parse_missing_restores_proper_names():
    missing = coverage.parse_missing(MISSING_HTML, ["One-Day Cup (Australia)", "Super Smash"])
    rows = [tuple(r) for r in missing[["kind", "name", "gender", "date", "team1", "team2"]].itertuples(index=False)]
    assert rows == [
        ("match type", "Test Matches", "male", "2006-03-18", "England", "India"),
        ("match type", "Test Matches", "male", "2006-05-01", "Australia", "West Indies"),
        ("match type", "Test Matches", "male", "2006-05-01", "South Africa", "India"),   # two matches, one date
        ("match type", "One-day Internationals", "female", "2002-01-06", "England", "India"),
        ("competition", "One-Day Cup (Australia)", "male", "2021-02-10", "Victoria", "Queensland"),
    ]


def test_parse_withheld():
    w = coverage.parse_withheld(MATCHES_HTML)
    assert w["withheld_matches"] == 377
    assert "Afghanistan" in w["withheld_reason"]


# --- end to end: the pages go in with the register, the report reads them ---

def _test_match(date, india_batter="I Batter"):
    return {
        "meta": {"data_version": "1.1.0", "revision": 1},
        "info": {
            "match_type": "Test", "gender": "male", "team_type": "international", "dates": [date],
            "venue": "Ground", "season": date[:4], "teams": ["India", "England"],
            "outcome": {"result": "draw"},
            "players": {"India": [india_batter, "I Bowler"], "England": ["E Batter", "E Bowler"]},
        },
        "innings": [{"team": "India", "overs": [{"over": 0, "deliveries": [
            {"batter": india_batter, "bowler": "E Bowler", "non_striker": "I Bowler",
             "runs": {"batter": 1, "extras": 0, "total": 1}}]}]}],
    }


@pytest.fixture
def covered(tmp_path, monkeypatch):
    zpath = tmp_path / "m.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("t1.json", json.dumps(_test_match("2006-03-01")))
        zf.writestr("t2.json", json.dumps(_test_match("2006-04-01")))
    for name, html in (("cricsheet_coverage.html", COVERAGE_HTML), ("cricsheet_missing.html", MISSING_HTML),
                       ("cricsheet_matches.html", MATCHES_HTML)):
        (tmp_path / name).write_text(html, encoding="utf-8")
    out = tmp_path / "c.duckdb"
    info = build_db.build([zpath], out, register_dir=tmp_path, progress=lambda *_: None)
    analytics_build.build(out, progress=lambda *_: None)
    monkeypatch.setattr(db, "DB_PATH", out)
    catalog.invalidate()
    yield info
    catalog.invalidate()


def test_build_loads_coverage_tables(covered):
    assert covered["coverage"] == {"coverage_periods": 5, "coverage_counts": 3, "missing_matches": 5}


def test_data_coverage_report(covered):
    r = cov.data_coverage(gender="male")
    tests = dict(zip(r["columns"], next(row for row in r["rows"] if row[0] == "Tests")))
    assert (tests["matches"], tests["known_missing"], tests["checked_from"]) == (2, 3, "2001-12")
    assert tests["coverage_pct"] == 40.0          # 2 held, 3 known missing
    assert r["withheld"]["matches"] == 377
    assert any("withholds 377" in n for n in r["notes"])
    assert r["competitions"]["rows"][0][:4] == ["One-Day Cup (Australia)", "male", 112, 115]


def test_missing_matches_filters(covered):
    r = cov.missing_matches(format="Test", team="India")
    assert r["total"] == 2 and [row[0] for row in r["rows"]] == ["2006-05-01", "2006-03-18"]
    assert cov.missing_matches(competition="One-Day Cup (Australia)")["total"] == 1
    assert "error" in cov.missing_matches(format="T20")


def test_player_notes_count_gaps_in_the_career(covered):
    # I Batter played 2006-03-01 to 2006-04-01; India's Test on 2006-03-18 is missing (05-01 is after).
    notes = engine.player_profile("I Batter")["coverage_notes"]
    assert len(notes) == 1 and "1 match" in notes[0] and "(1 Test)" in notes[0]


def test_coverage_reports_cleanly_without_the_tables(tmp_path, monkeypatch):
    zpath = tmp_path / "m.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("t1.json", json.dumps(_test_match("2006-03-01")))
    out = tmp_path / "n.duckdb"
    build_db.build([zpath], out, progress=lambda *_: None)
    monkeypatch.setattr(db, "DB_PATH", out)
    assert "error" in cov.data_coverage()
    assert cov.player_notes(catalog.Player("x", 1, "male", ["India"], "2006-01-01", "2007-01-01", True)) == []


def test_release_gate_fails_when_the_pages_parse_to_nothing(tmp_path):
    """Pages downloaded but a layout change leaves nothing to parse: the
    weekly release must stop rather than ship an empty Data page."""
    import duckdb

    from ingest import validate

    zpath = tmp_path / "m.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("t1.json", json.dumps(_test_match("2006-03-01")))
    for name, html in (("cricsheet_coverage.html", COVERAGE_HTML), ("cricsheet_missing.html", "<p>redesigned</p>"),
                       ("cricsheet_matches.html", MATCHES_HTML)):
        (tmp_path / name).write_text(html, encoding="utf-8")
    out = tmp_path / "v.duckdb"
    build_db.build([zpath], out, register_dir=tmp_path, progress=lambda *_: None)
    con = duckdb.connect(str(out), read_only=True)
    try:
        rep = validate.Report()
        validate.check_coverage(con, rep)
    finally:
        con.close()
    assert any("missing_matches" in f for f in rep.failures)
