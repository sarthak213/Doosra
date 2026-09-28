"""Tests of name resolution (agent/catalog.py) and filter building
(agent/scope.py). Resolution is where most wrong answers came from: an
exact-spelled namesake beating the famous player stored under initials,
competitions split across naming eras, renamed franchises."""

import pytest

from agent import catalog
from agent.catalog import ResolutionError, _given_compat, _unitize
from agent.scope import build_scope


def units(name_tokens, initials):
    return _unitize(name_tokens, initials)


class TestGivenNameCompatibility:
    def test_full_name_vs_initials(self):
        # "Rohit" vs "RG": first-initial match, weaker than an exact name.
        assert _given_compat(units(["rohit"], [False]), units(["rg"], [True])) == 75.0

    def test_exact_initials(self):
        assert _given_compat(units(["ms"], [True]), units(["ms"], [True])) == 100.0

    def test_full_names_to_all_initials(self):
        assert _given_compat(units(["mahendra", "singh"], [False, False]), units(["ms"], [True])) == 85.0

    def test_used_name_later_in_initials(self):
        # "Lasith Malinga" is stored as "SL Malinga".
        assert _given_compat(units(["lasith"], [False]), units(["sl"], [True])) == 60.0

    def test_incompatible(self):
        assert _given_compat(units(["rohit"], [False]), units(["s"], [True])) is None

    def test_surname_only(self):
        assert _given_compat([], units(["v"], [True])) == 50.0


class TestPlayers:
    def test_first_name_expands_to_initial(self):
        assert catalog.resolve_player("Virat Kohli").name == "V Kohli"
        assert catalog.resolve_player("Steve Smith").name == "S Smith"

    def test_surname_only(self):
        assert catalog.resolve_player("Kohli").name == "V Kohli"

    def test_exact_name_has_no_note(self):
        assert catalog.resolve_player("V Kohli").note is None

    def test_resolution_is_reported(self):
        assert "V Kohli" in catalog.resolve_player("Virat Kohli").note

    def test_typo_in_surname(self):
        assert catalog.resolve_player("Virat Kohly").name == "V Kohli"

    def test_different_initial_is_a_different_person(self):
        # The only Sharma is "S Sharma" -- "Rohit Sharma" must not map onto him.
        with pytest.raises(ResolutionError):
            catalog.resolve_player("Rohit Sharma")

    def test_gender_restricts_candidates(self):
        with pytest.raises(ResolutionError):
            catalog.resolve_player("Lanning", gender="male")
        assert catalog.resolve_player("Lanning", gender="female").name == "M Lanning"


class TestCompetitionsTeamsVenues:
    def test_exact_event(self):
        c = catalog.resolve_competition("Test Bash League")
        assert c.events == ["Test Bash League"]

    def test_year_in_competition_becomes_season(self):
        c = catalog.resolve_competition("Test Bash League 2023/24")
        assert c.events == ["Test Bash League"] and c.season == "2023/24"

    def test_unknown_competition(self):
        with pytest.raises(ResolutionError):
            catalog.resolve_competition("Ranji Trophy")

    def test_team_alias(self):
        assert catalog.resolve_team("Aus").names == ["Australia"]

    def test_womens_team_name(self):
        assert catalog.resolve_team("India Women", gender="female").names == ["India Women"]

    def test_venue_substring(self):
        assert catalog.resolve_venue("City Oval").venues == ["City Oval"]

    def test_format(self):
        f = catalog.resolve_format("T20Is")
        assert f.match_types == ("T20",) and f.team_type == "international"


class TestScope:
    def test_defaults_are_recorded(self):
        s = build_scope(default_gender="male")
        assert s.gender == "male"
        assert any("defaulted to male" in n for n in s.notes)

    def test_all_gender_disables_default(self):
        assert build_scope(gender="all", default_gender="male").gender is None

    def test_season_year_matches_split_season(self):
        clause = " ".join(build_scope(season="2008").match_clauses())
        assert "2007/%" in clause  # IPL 2008 is stored as "2007/08"

    def test_bad_season(self):
        with pytest.raises(ResolutionError):
            build_scope(season="last year")

    def test_literals_are_escaped(self):
        from agent.scope import lit
        assert lit("Lord's") == "'Lord''s'"
