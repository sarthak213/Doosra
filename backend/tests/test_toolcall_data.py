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
