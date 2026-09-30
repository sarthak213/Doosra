"""Dynamic filtering: the filter inputs offer only what exists within the other filters chosen."""

from analytics import facets


def get(**filters):
    return facets.facets(filters)


def test_everything_when_nothing_is_chosen():
    f = get()
    assert f["matches"] == 3 and set(f["seasons"]) == {"2024/25", "2023/24"}
    assert {"City Oval", "Women's Oval", "Second City Ground"} <= set(f["venues"])


def test_a_format_with_no_matches_offers_nothing():
    f = get(format="Test")
    assert f["matches"] == 0 and f["competitions"] == [] and f["venues"] == [] and f["teams"] == []


def test_a_competition_narrows_venues_and_seasons_but_not_its_own_list():
    f = get(competition="Test Bash League")
    assert f["matches"] == 1 and f["venues"] == ["City Oval"] and f["seasons"] == ["2023/24"]
    assert "Test Bash League" in f["competitions"]            # the list you pick from ignores its own choice


def test_a_season_narrows_competitions():
    assert get(season="2024/25")["competitions"] == []        # the 2024/25 match isn't part of a competition
    assert "Test Bash League" in get(season="2023/24")["competitions"]


def test_team_and_opposition_leave_each_other_out():
    f = get(team="India", gender="male")
    assert f["opposition"] == ["Australia"] and "India" in f["teams"]


def test_a_half_typed_filter_is_ignored():
    f = get(competition="Test Ba")
    assert f["applied"] == [] and f["matches"] == 3

