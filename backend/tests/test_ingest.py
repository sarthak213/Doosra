"""Tests of ingest/build_db.py's newer fields: reading from a zip, the
super-over flag, tie/eliminator and DLS outcomes, non-boundary fours,
registry person ids, the register tables, incremental upserts and
build_info. Uses its own two-match database (not conftest's fixture), so
the hand-computed values elsewhere stay untouched."""

import json
import zipfile
from pathlib import Path

import duckdb
import pytest

from ingest import build_db


def _d(batter, bowler, rb=0, rx=0, extras=None, wickets=None, ns="", non_boundary=False):
    runs = {"batter": rb, "extras": rx, "total": rb + rx}
    if non_boundary:
        runs["non_boundary"] = True
    d = {"batter": batter, "bowler": bowler, "non_striker": ns or "X", "runs": runs}
    if extras:
        d["extras"] = extras
    if wickets:
        d["wickets"] = wickets
    return d


TIE = {
    "meta": {"data_version": "1.1.0", "revision": 2},
    "info": {
        "match_type": "T20", "gender": "male", "team_type": "club", "overs": 20, "balls_per_over": 6,
        "event": {"name": "Ingest League"}, "dates": ["2025-01-01"], "venue": "Ground A", "season": "2024/25",
        "teams": ["Strikers", "Heat"], "toss": {"winner": "Heat", "decision": "field"},
        "outcome": {"result": "tie", "eliminator": "Heat"},
        "players": {"Strikers": ["A Khan", "B Lee"], "Heat": ["C Dey", "D Roy"]},
        "registry": {"people": {"A Khan": "kh01", "B Lee": "le01", "C Dey": "de01", "D Roy": "ro01"}},
    },
    "innings": [
        {"team": "Strikers", "overs": [{"over": 0, "deliveries": [
            _d("A Khan", "C Dey", rb=4, non_boundary=True, ns="B Lee"),   # a "4" that was run
            _d("A Khan", "C Dey", rb=4, ns="B Lee"),                      # a real four
            _d("A Khan", "C Dey", wickets=[{"kind": "caught", "player_out": "A Khan",
                                            "fielders": [{"name": "D Roy"}]}], ns="B Lee"),
        ]}]},
        {"team": "Heat", "target": {"runs": 9, "overs": 20}, "overs": [{"over": 0, "deliveries": [
            _d("C Dey", "B Lee", rb=6, ns="D Roy"), _d("C Dey", "B Lee", rb=2, ns="D Roy"),
        ]}]},
        {"team": "Heat", "super_over": True, "overs": [{"over": 0, "deliveries": [_d("D Roy", "A Khan", rb=6, ns="C Dey")]}]},
        {"team": "Strikers", "super_over": True, "overs": [{"over": 0, "deliveries": [_d("B Lee", "C Dey", rb=1, ns="A Khan")]}]},
    ],
}

DLS = {
    "meta": {"data_version": "1.1.0", "revision": 1},
    "info": {
        "match_type": "ODI", "gender": "male", "team_type": "international", "overs": 50,
        "dates": ["2025-02-01"], "venue": "Ground B", "season": "2025", "teams": ["Heat", "Strikers"],
        "outcome": {"winner": "Heat", "by": {"runs": 5}, "method": "D/L"},
        # A different person with the same name as kh01.
        "players": {"Heat": ["A Khan"], "Strikers": ["B Lee"]},
        "registry": {"people": {"A Khan": "kh02", "B Lee": "le01"}},
    },
    "innings": [{"team": "Heat", "overs": [{"over": 0, "deliveries": [_d("A Khan", "B Lee", rb=1, ns="Y")]}]}],
}

PEOPLE = """identifier,name,unique_name,key_cricinfo
kh01,A Khan,A Khan,111
kh02,A Khan,A Khan (2),222
le01,B Lee,B Lee,333
"""
NAMES = """identifier,name
kh01,Asif Khan
le01,Brett Lee
"""


@pytest.fixture(autouse=True)
def schema():
    """Override conftest's schema-parametrized fixture: these tests build
    their own database."""
    yield "ingest"


@pytest.fixture
def built(tmp_path):
    zpath = tmp_path / "matches.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("m_tie.json", json.dumps(TIE))
        zf.writestr("m_dls.json", json.dumps(DLS))
        zf.writestr("README.txt", "not a match")
    (tmp_path / "people.csv").write_text(PEOPLE, encoding="utf-8")
    (tmp_path / "names.csv").write_text(NAMES, encoding="utf-8")
    out = tmp_path / "db.duckdb"
    info = build_db.build([zpath], out, register_dir=tmp_path, progress=lambda *_: None)
    return out, info


def q(path, sql):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_reads_matches_from_zip(built):
    out, info = built
    assert info["rows"]["matches"] == 2
    assert q(out, "SELECT match_id FROM matches ORDER BY 1") == [("m_dls",), ("m_tie",)]


def test_outcomes(built):
    out, _ = built
    tie = q(out, "SELECT winner, result, eliminator, target_runs, balls_per_over, data_version, revision "
                 "FROM matches WHERE match_id = 'm_tie'")[0]
    assert tie == (None, "tie", "Heat", 9, 6, "1.1.0", 2)
    dls = q(out, "SELECT winner, win_by_runs, method FROM matches WHERE match_id = 'm_dls'")[0]
    assert dls == ("Heat", 5, "D/L")


def test_super_over_flag(built):
    out, _ = built
    assert q(out, "SELECT innings_num, is_super_over FROM deliveries WHERE match_id = 'm_tie' "
                  "GROUP BY ALL ORDER BY 1") == [(1, False), (2, False), (3, True), (4, True)]


def test_non_boundary_four(built):
    out, _ = built
    assert q(out, "SELECT runs_batter, non_boundary FROM deliveries WHERE match_id = 'm_tie' AND innings_num = 1 "
                  "AND runs_batter = 4 ORDER BY ball_in_over") == [(4, True), (4, False)]


def test_registry_ids_separate_namesakes(built):
    out, _ = built
    assert q(out, "SELECT match_id, player_id FROM players_matches WHERE player = 'A Khan' ORDER BY 1") == [
        ("m_dls", "kh02"), ("m_tie", "kh01")]
    assert q(out, "SELECT fielders, fielder_ids FROM deliveries_wickets") == [('["D Roy"]', '["ro01"]')]


def test_register_tables(built):
    out, _ = built
    assert q(out, "SELECT unique_name FROM people WHERE identifier = 'kh02'") == [("A Khan (2)",)]
    names = {r[0] for r in q(out, "SELECT name FROM people_names WHERE identifier = 'kh01'")}
    assert names == {"Asif Khan", "A Khan"}  # alternate name + register name


def test_build_info(built):
    out, info = built
    assert info["schema_version"] == build_db.SCHEMA_VERSION
    assert info["latest_match_date"] == "2025-02-01"
    assert "matches.zip" in info["sources"]
    stored = dict(q(out, "SELECT key, value FROM build_info"))
    assert json.loads(stored["rows"])["matches"] == 2


def test_incremental_upsert_replaces_matches(built, tmp_path):
    out, _ = built
    revised = json.loads(json.dumps(DLS))
    revised["meta"]["revision"] = 2
    revised["info"]["outcome"] = {"winner": "Strikers", "by": {"wickets": 3}}
    folder = tmp_path / "update"
    folder.mkdir()
    (folder / "m_dls.json").write_text(json.dumps(revised), encoding="utf-8")
    build_db.build([folder], out, incremental=True, progress=lambda *_: None)
    assert q(out, "SELECT COUNT(*) FROM matches") == [(2,)]
    assert q(out, "SELECT winner, revision FROM matches WHERE match_id = 'm_dls'") == [("Strikers", 2)]
    assert q(out, "SELECT COUNT(*) FROM deliveries WHERE match_id = 'm_dls'") == [(1,)]  # not duplicated
    # The register survives an incremental run.
    assert q(out, "SELECT COUNT(*) FROM people") == [(3,)]


@pytest.fixture
def analysed(built, monkeypatch):
    """The ingest database with derived tables, as the app would see it."""
    from analytics import build as analytics_build
    from analytics import catalog, db

    out, _ = built
    analytics_build.build(out, progress=lambda *_: None)
    monkeypatch.setattr(db, "DB_PATH", out)
    catalog.invalidate()
    yield out
    catalog.invalidate()


def test_namesakes_are_different_players(analysed):
    from analytics import catalog, engine

    cat = catalog.get_catalog()
    assert {"A Khan", "A Khan (2)"} <= set(cat.players)
    assert cat.players["A Khan"].player_id == "kh01"
    assert cat.players["A Khan (2)"].raw_name == "A Khan"
    # Each person's runs stay their own: kh01 made 8 (4 run + 4), kh02 made 1.
    got = engine.query_stats(role="batting", metrics=["runs"], players=["A Khan (2)"])
    assert got["rows"] == [["A Khan (2)", 1]]
    # kh01 by his register alternate name (plain "A Khan" fits both people;
    # prominence would pick the international one).
    got = engine.query_stats(role="batting", metrics=["runs", "fours"], players=["Asif Khan"])
    assert got["rows"] == [["A Khan", 8, 1]]  # the run "4" isn't a boundary


def test_register_alternate_name_resolves(analysed):
    from analytics import catalog

    assert catalog.resolve_player("Asif Khan").name == "A Khan"
    assert catalog.resolve_player("Brett Lee").name == "B Lee"
    assert catalog.get_catalog().players["A Khan"].describe()["cricinfo_url"].endswith("-111")


def test_super_overs_left_out_of_derived_tables(analysed):
    from analytics import db

    # D Roy's 6 and B Lee's 1 came in the super over only.
    assert db.query("SELECT COUNT(*) AS n FROM batting_innings WHERE player = 'D Roy' AND balls > 0")[0]["n"] == 0
    assert db.query("SELECT SUM(runs) AS r FROM bowling_innings WHERE player = 'A Khan'")[0]["r"] is None


def test_ball_level_tools_respect_identity(analysed):
    from agent import stats

    # The ball-level (legacy) tools must also keep namesakes apart.
    r = stats.player_stats("A Khan (2)", role="batting")
    assert dict(zip(r["columns"], r["rows"][0]))["runs"] == 1
    m = stats.matchup(batter="Asif Khan", bowler="C Dey")
    assert dict(zip(m["columns"], m["rows"][0]))["runs"] == 8


# --- the release pipeline: validate, manifest, pull ---------------------------

@pytest.fixture
def frozen(analysed, tmp_path, monkeypatch):
    """A frozen-careers file for this database's two Strikers players."""
    from ingest import validate

    monkeypatch.setattr(validate, "FROZEN_PLAYERS", ["A Khan", "B Lee"])
    monkeypatch.setattr(validate, "RETIRED_YEARS", 0)
    path = tmp_path / "frozen.json"
    con = duckdb.connect(str(analysed), read_only=True)
    try:
        validate.freeze(con, path)
    finally:
        con.close()
    return path


def test_validate_passes_a_good_build(analysed, frozen):
    from ingest import validate

    rep = validate.validate(analysed, frozen_path=frozen)
    assert rep.failures == []
    doc = json.loads(frozen.read_text(encoding="utf-8"))
    # A Khan (kh01): 8 runs in the T20, the super over left out.
    assert doc["players"]["A Khan"]["career"]["T20"]["bat_runs"] == 8
    assert doc["players"]["B Lee"]["career"]["T20"]["bowl_runs"] == 8


def test_validate_catches_a_changed_career(analysed, frozen):
    from ingest import validate

    doc = json.loads(frozen.read_text(encoding="utf-8"))
    doc["players"]["A Khan"]["career"]["T20"]["bat_runs"] += 1
    frozen.write_text(json.dumps(doc), encoding="utf-8")
    rep = validate.validate(analysed, frozen_path=frozen)
    assert any("A Khan" in f for f in rep.failures)


def test_validate_catches_shrinking_tables(analysed, frozen):
    from ingest import validate

    rep = validate.validate(analysed, previous={"rows": {"matches": 100}}, frozen_path=frozen)
    assert any("matches shrank" in f for f in rep.failures)


def test_validate_requires_derived_tables(built, frozen):
    from ingest import validate

    out, _ = built
    con = duckdb.connect(str(out))
    con.execute("DROP TABLE bowling_phase")
    con.close()
    rep = validate.validate(out, frozen_path=frozen)
    assert any("bowling_phase" in f for f in rep.failures)


def test_manifest_and_pull_round_trip(analysed, tmp_path, monkeypatch):
    zstd = pytest.importorskip("compression.zstd")
    from ingest import manifest, pull

    asset = tmp_path / "cricket.duckdb.zst"
    asset.write_bytes(zstd.compress(analysed.read_bytes()))
    doc = manifest.make_manifest(analysed, asset, "data-2025-02-02")
    assert doc["rows"]["matches"] == 2 and doc["latest_match"] == "2025-02-01"
    (tmp_path / "manifest.json").write_text(json.dumps(doc), encoding="utf-8")

    # A fake release whose asset "urls" are local files.
    release = {"tag_name": "data-latest", "assets": [
        {"name": n, "browser_download_url": str(tmp_path / n)} for n in ("manifest.json", asset.name)]}
    monkeypatch.setattr(pull, "latest_release", lambda: release)
    monkeypatch.setattr(pull, "download", lambda url, dest: dest.write_bytes(Path(url).read_bytes()))

    target = tmp_path / "app" / "cricket.duckdb"
    target.parent.mkdir()
    target.write_bytes(b"old database")
    assert pull.pull(target=target) == 0
    assert target.read_bytes() == analysed.read_bytes()
    assert target.with_suffix(".duckdb.bak").read_bytes() == b"old database"
    assert pull.local_info(target)["latest_match_date"] == "2025-02-01"
    # Now up to date: nothing is replaced.
    assert pull.pull(target=target) == 0
    assert target.with_suffix(".duckdb.bak").read_bytes() == b"old database"


def test_pull_rejects_a_corrupt_download(analysed, tmp_path, monkeypatch):
    zstd = pytest.importorskip("compression.zstd")
    from ingest import manifest, pull

    asset = tmp_path / "cricket.duckdb.zst"
    asset.write_bytes(zstd.compress(analysed.read_bytes()))
    doc = manifest.make_manifest(analysed, asset)
    doc["asset"]["sha256"] = "0" * 64
    (tmp_path / "manifest.json").write_text(json.dumps(doc), encoding="utf-8")
    release = {"assets": [{"name": n, "browser_download_url": str(tmp_path / n)}
                          for n in ("manifest.json", asset.name)]}
    monkeypatch.setattr(pull, "latest_release", lambda: release)
    monkeypatch.setattr(pull, "download", lambda url, dest: dest.write_bytes(Path(url).read_bytes()))
    target = tmp_path / "app" / "cricket.duckdb"
    target.parent.mkdir()
    with pytest.raises(pull.PullError, match="sha256"):
        pull.pull(target=target)
    assert not target.exists()
