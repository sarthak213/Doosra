"""The fine-tuned model's plumbing (v3.0): the compact prompt and tools it was trained on, and the pieces of
the training-data pipeline in ml/toolcall/ that decide what goes into the dataset."""

import importlib.util
import json
import random
import re
import sys
from pathlib import Path

import pytest

from agent import graph

ROOT = Path(__file__).resolve().parents[2]
TOOLCALL = ROOT / "ml" / "toolcall"


def load(name):
    sys.path.insert(0, str(TOOLCALL))
    spec = importlib.util.spec_from_file_location(name, TOOLCALL / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FULL = [{"type": "function", "function": {
    "name": "player_stats",
    "description": "One player's figures in any scope (e.g. by season). Split results include `highlights`.",
    "parameters": {"type": "object", "required": ["player"], "properties": {
        "player": {"type": "string", "title": "Player"},
        "split_by": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "description": "Break down by..."},
        "filters": {"type": "object", "description": "Optional filters (see system prompt)."}}}}}]


class TestCompact:
    def test_tools_keep_names_and_parameters_without_the_noise(self):
        t = graph.compact_tools(FULL)[0]["function"]
        assert t["name"] == "player_stats" and t["parameters"]["required"] == ["player"]
        assert t["description"] == "One player's figures in any scope (e.g. by season)."    # "e.g." doesn't end it
        props = t["parameters"]["properties"]
        assert props["split_by"] == {"type": "string"} and props["filters"] == {"type": "object"}
        assert "title" not in json.dumps(t)

    def test_compact_mode_uses_the_short_prompt_and_never_reasons(self):
        before = (graph.PROVIDER, graph.BASE_URL, graph.MODEL)
        try:
            graph.configure("llamacpp", base_url="http://127.0.0.1:1/v1", compact=True)
            assert graph.thinking_for(True) is False
            assert len(graph._system_prompt()) < 2500 and "final_answer" in graph._system_prompt()
        finally:
            graph.configure(*before)
        assert graph.COMPACT is False


class TestPipeline:
    def test_a_paraphrase_must_keep_names_and_numbers(self):
        p = load("paraphrase")
        q = "Who has the most sixes in the IPL 2024?"
        assert p.ok(q, "Top six-hitter in IPL 2024?")
        assert not p.ok(q, "Top six-hitter in the IPL?")            # dropped the season
        assert not p.ok(q, "Who hit the most sixes in 2024?")       # dropped the competition
        assert not p.ok(q, q)                                       # not a rewrite

    def test_answers_quote_only_the_results(self):
        a = load("answers")
        view = {"table_id": "T1", "title": "Top bowling by economy — Indian Premier League, male",
                "notes": ["'Jasprit Bumrah' is stored as 'JJ Bumrah' (India).",
                          "Qualification: at least 56 balls bowled -- set min_balls to change it."],
                "rows": [{"rank": 1, "player": "JJ Bumrah", "team": "Mumbai Indians", "economy": 7.28, "matches": 44},
                         {"rank": 2, "player": "SP Narine", "team": "KKR", "economy": 7.4, "matches": 40}]}
        text = a.write(random.Random(0), "leaderboard", [view], {"metric": "economy", "label": "economy"})
        assert "**JJ Bumrah** (Jasprit Bumrah)" in text and "**7.28**" in text and "Indian Premier League" in text
        numbers = set(re.findall(r"\d+(?:\.\d+)?", text))
        in_result = set(re.findall(r"\d+(?:\.\d+)?", json.dumps(view)))
        assert numbers <= in_result                                 # nothing the result doesn't say

    def test_no_answer_without_data(self):
        a = load("answers")
        assert a.write(random.Random(0), "leaderboard", [{"rows": []}], {"metric": "runs"}) is None


@pytest.mark.skipif(not (ROOT / "ml" / "out" / "toolcall" / "train.jsonl").exists(), reason="dataset not generated")
def test_the_generated_dataset_is_well_formed():
    v = load("validate")
    tools = {t["function"]["name"] for t in json.loads((ROOT / "ml" / "out" / "toolcall" / "tools.json").read_text())}
    rows = [json.loads(line) for line in (ROOT / "ml" / "out" / "toolcall" / "train.jsonl").read_text(
        encoding="utf-8").splitlines()[:200]]
    assert v.check(rows, tools) == []


class TestDatasetV2:
    """What the first fine-tunes taught us (they aced the templates and lost on open questions)."""

    def test_compact_tools_keep_the_sql_schema(self):
        sql = [{"type": "function", "function": {"name": "run_sql", "description": "Read-only SQL. Tables: matches(...).",
                                                 "parameters": {"type": "object", "properties": {}}}}]
        assert "Tables: matches" in graph.compact_tools(sql)[0]["function"]["description"]

    def test_a_team_record_answer_has_its_numbers(self):
        answers = load("answers")
        view = {"title": "Results — India vs Australia", "filters": {"gender": "male", "team": "India", "opposition": "Australia"},
                "notes": ["No gender specified -- defaulted to male cricket."],
                "rows": [{"matches": 171, "won": 75, "lost": 73, "no_result": 23, "win_pct": 50.68,
                          "won_batting_first": 31, "batted_first": 72, "won_chasing": 44, "chased": 99, "tosses_won": 68}]}
        a = answers.write(random.Random(0), "team", [view], {"team": "India", "opposition": "Australia"})
        assert "**171** matches" in a and "**75** won" in a and "No gender" not in a

    def test_a_recovery_answers_from_the_corrected_call(self):
        answers = load("answers")
        err = {"error": "Binder Error: column competition not found"}
        ok = {"rows": [{"matches": 1243}]}
        a = answers.write(random.Random(0), "sql", [err, ok], {"what": "IPL matches", "use_last": True})
        assert a == "**1,243** IPL matches."

    def test_split_labels_read_as_positions_and_innings(self):
        answers = load("answers")
        view = {"title": "Batting — X by batting position — T20I", "rows": [{"position_no": "7", "runs": 39}],
                "highlights": {"best_runs": "39 (7)"}}
        a = answers.write(random.Random(0), "split", [view], {"player": "X", "split": "position"})
        assert "39 (No. 7)" in a

    def test_quality_check_catches_answers_without_figures(self):
        validate = load("validate")
        bad = {"id": "x", "intent": "team_record", "question": "How have Oman done?", "messages": [
            {"role": "tool", "content": '{"rows": [{"matches": 106}]}'},
            {"role": "assistant", "tool_calls": [{"function": {"name": "final_answer",
                                                               "arguments": json.dumps({"answer": "**Oman**'s results:"})}}]}]}
        assert any("no figures" in p for p in validate.quality([bad]))

    def test_training_keeps_clear_of_the_evaluation(self):
        gen = load("generate")
        intents = load("intents")
        assert gen.eval_overlap(intents.Example("x", "How many sixes has Virat Kohli hit?", [("player_stats", {})]))
        assert gen.eval_overlap(intents.Example("x", "Most IPL runs?", [("leaderboard", {
            "metric": "runs", "filters": {"competition": "Indian Premier League"}})]))
        assert not gen.eval_overlap(intents.Example("x", "Most IPL runs in 2016?", [("leaderboard", {
            "metric": "runs", "filters": {"competition": "Indian Premier League", "season": "2016"}})]))

    def test_grading_accepts_name_and_competition_variants(self):
        export = load("export_eval")
        assert export.tokens("Virat Kohli leads IPL run-scoring.") == ["Kohli", ["IPL", "Indian Premier League"]]
        assert export.tokens("A strike rate of 132.92 for RG Sharma.") == ["132.92", "Sharma"]
