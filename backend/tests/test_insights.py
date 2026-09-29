"""Deterministic facts about chart data (analytics/insights.py): checked on
synthetic numbers whose answers are known, plus the fixture database for the
regressed-versus-raw comparison."""

import json

from analytics import insights

LABELS = list("ABCDEFGH")


def table(columns, rows, **extra):
    return {"columns": columns, "rows": rows, **extra}


class TestColumnFacts:
    VALUES = [10, 11, 9, 10, 12, 10, 11, 60]

    def test_leader_laggard_and_the_outlier(self):
        f = insights.column_facts(LABELS, self.VALUES)
        assert f["best"] == {"label": "H", "value": 60} and f["worst"] == {"label": "C", "value": 9}
        assert f["median"] == 10.5 and f["spread"] == 51
        assert [o["label"] for o in f["outliers"]] == ["H"] and f["outliers"][0]["z"] > 2

    def test_lower_is_better_reverses_best_and_worst(self):
        f = insights.column_facts(LABELS, self.VALUES, higher_better=False)
        assert f["best"]["label"] == "C" and f["worst"]["label"] == "H"

    def test_share_of_total_only_for_counts(self):
        assert insights.column_facts(LABELS, self.VALUES, count=True)["leader_share_of_listed_total_pct"] == round(100 * 60 / sum(self.VALUES), 1)
        assert "leader_share_of_listed_total_pct" not in insights.column_facts(LABELS, self.VALUES)

    def test_gaps_and_empty(self):
        f = insights.column_facts(["A", "B", "C"], [3, None, 1])
        assert f["n"] == 2 and f["best"]["label"] == "A"
        assert insights.column_facts(["A"], [None]) is None

    def test_detail_level_shortens_the_ranking(self):
        assert len(insights.column_facts(LABELS, self.VALUES, detail=2)["ranked"]) == 5
        assert len(insights.column_facts(LABELS, self.VALUES, detail=0)["ranked"]) == 1


class TestSeries:
    def test_rising_flat_and_falling(self):
        up = insights.series_facts(["2019", "2020", "2021", "2022", "2023"], [1, 2, 3, 4, 5])
        assert up["trend"] == "rising" and up["change_over_period"] == 4.0
        assert up["peak"]["label"] == "2023" and up["trough"]["label"] == "2019"
        assert up["latest_vs_mean_pct"] == round(100 * (5 - 3) / 3, 1)
        assert insights.series_facts(list("abcd"), [5, 5.1, 4.9, 5])["trend"] == "flat"
        assert insights.series_facts(list("abc"), [9, 6, 3])["trend"] == "falling"

    def test_too_short(self):
        assert insights.series_facts(["a"], [1]) is None


class TestScatter:
    RECS = [{"player": p, "x": x, "y": y} for p, x, y in
            [("A", 1, 2), ("B", 2, 4), ("C", 3, 6), ("D", 4, 8), ("E", 5, 10)]]

    def test_perfect_correlation_and_quadrants(self):
        f = insights.scatter_facts(self.RECS, "x", "y", None, None)
        assert f["correlation"] == 1.0 and f["strength"] == "strong"
        assert f["medians"] == {"x": 3, "y": 6} and f["best_on_both"][0] == "E"

    def test_direction_of_better_comes_from_the_registry(self):
        # bowling economy: lower is better, so the best-on-both player has the lowest economy and most wickets
        recs = [{"player": "A", "economy": 6.0, "wickets": 30}, {"player": "B", "economy": 9.0, "wickets": 10},
                {"player": "C", "economy": 7.5, "wickets": 20}]
        f = insights.scatter_facts(recs, "economy", "wickets", "bowling", None)
        assert f["x_better"] == "lower" and f["best_on_both"][0] == "A"

    def test_needs_three_points(self):
        assert insights.scatter_facts(self.RECS[:2], "x", "y", None, None) is None


def test_thin_samples_named():
    res = table(["player", "balls", "economy"], [["A", 30, 7.0], ["B", 600, 8.0], ["C", 90, 9.0]])
    t = insights.thin_samples(res, ["A", "B", "C"])
    assert t == {"measure": "balls", "under": 120, "count": 2, "examples": ["A", "C"]}
    assert insights.thin_samples(table(["player", "runs"], [["A", 1]]), ["A"]) is None


class TestDigest:
    def card(self, **kw):
        return {"title": "T", "note": "", "source": {"kind": "query", "state": {"role": "bowling", "metrics": ["wickets"], "sort_by": "wickets"}},
                "chart": {}, **kw}

    def test_bar_card_digest(self):
        res = table(["rank", "player", "wickets"], [[i + 1, p, w] for i, (p, w) in enumerate(zip(LABELS, [9, 8, 8, 7, 6, 6, 5, 40]))],
                    filters={"format": "T20"}, notes=["defaulted to men's"])
        d = insights.digest(self.card(), res)
        assert d["chart"] == "bar" and d["scope"] == {"format": "T20"} and d["data_notes"] == ["defaulted to men's"]
        assert d["metrics"]["wickets"]["best"]["label"] == "H" and "leader_share_of_listed_total_pct" in d["metrics"]["wickets"]

    def test_time_split_becomes_a_series(self):
        card = self.card(source={"kind": "query", "state": {"role": "batting", "metrics": ["runs"], "split_by": "season"}})
        d = insights.digest(card, table(["season", "runs"], [["2021", 100], ["2022", 150], ["2023", 200]]))
        assert d["chart"] == "line" and d["series"]["runs"]["trend"] == "rising"

    def test_snapshot_is_labelled_static_and_empty_is_flagged(self):
        snap = {"title": "S", "source": {"kind": "snapshot", "saved_at": "2026-03-01T10:00:00"}, "chart": {}}
        d = insights.digest(snap, table(["player", "runs"], [["A", 1], ["B", 2]]))
        assert "saved 2026-03-01" in d["static_snapshot"]
        assert insights.digest(self.card(), table(["player", "wickets"], []))["empty"] is True

    def test_user_note_is_carried(self):
        d = insights.digest(self.card(note="under 8.5 is our shortlist"), table(["player", "wickets"], [["A", 1], ["B", 2]]))
        assert d["user_note"] == "under 8.5 is our shortlist"


def test_board_digest_shrinks_to_fit_and_finds_recurring_players():
    big = table(["rank", "player", "runs", "balls"], [[i, f"P{i % 30}", i * 7, 500 + i] for i in range(60)])
    cards = [({"title": f"Card {i}", "chart": {}, "source": {"kind": "query", "state": {"role": "batting", "metrics": ["runs"]}}}, big, None)
             for i in range(12)]
    cards.append(({"title": "Broken", "source": {"kind": "query"}, "chart": {}}, None, "boom"))
    out = insights.board_digest({"name": "B", "description": "d"}, cards, budget=7000)
    assert len(json.dumps(out, default=str)) <= 7000 and out["cards"][-1] == {"title": "Broken", "error": "boom"}
    assert "P1" in out["players_in_several_cards"]


def test_regressed_check_compares_raw_with_regressed_on_the_fixture_db():
    card = {"source": {"kind": "query", "state": {"role": "batting", "metrics": ["runs", "strike_rate"], "filters": {}}}}
    from analytics import engine
    res = engine.query_stats(role="batting", metrics=["runs", "strike_rate"], limit=10)
    found = insights.regressed_check(card, res) or []
    for f in found:
        assert f["metric"] == "strike_rate" and abs(f["raw"] - f["regressed"]) / abs(f["regressed"]) > insights.REGRESSED_GAP
        assert f["gap_pct"] == round(100 * (f["raw"] - f["regressed"]) / abs(f["regressed"]), 1)
    assert insights.regressed_check({"source": {"kind": "matrix", "state": {}}}, res) is None
    assert insights.regressed_check(card, res, detail=0) is None
