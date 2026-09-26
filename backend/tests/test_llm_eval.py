"""LLM-in-the-loop eval: runs the eval corpus through the FULL agent loop
against the real database and checks each final answer mentions the expected
value or player.

Costs LLM API credits and needs the provider env vars set (see
backend/.env.example), so it is skipped unless explicitly requested:

    pytest -m llm tests/test_llm_eval.py

Questions marked requires_new_schema are skipped while the database lacks
the deliveries_wickets table (they need a post-reingest database).
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from agent import stats as stats_mod
from agent import tools as tools_mod
from agent.graph import run_agent

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not os.environ.get("GROQ_API_KEY") and not os.environ.get("LLM_BASE_URL"),
                       reason="no LLM provider configured (GROQ_API_KEY / LLM_BASE_URL)"),
]

BACKEND_DIR = Path(__file__).resolve().parents[1]
REAL_DB = BACKEND_DIR / "data" / "cricket.duckdb"
EVAL_FILE = Path(__file__).parent / "eval_fixtures" / "eval_questions.json"

pytestmark.append(pytest.mark.skipif(not REAL_DB.exists(), reason="data/cricket.duckdb not present"))

QUESTIONS = json.loads(EVAL_FILE.read_text(encoding="utf-8"))["questions"]


@pytest.fixture(autouse=True)
def use_real_db(monkeypatch):
    """Overrides conftest's autouse synthetic-DB fixture: the eval runs
    against the real database the questions' expected answers assume."""
    monkeypatch.setattr(tools_mod, "DB_PATH", REAL_DB)
    monkeypatch.setattr(tools_mod, "_player_names_cache", None)
    monkeypatch.setattr(tools_mod, "_tournament_names_cache", None)
    monkeypatch.setattr(stats_mod, "_delivery_columns_cache", None)
    yield


def _has_wickets_table():
    con = tools_mod._get_connection()
    try:
        return bool(con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'deliveries_wickets'"
        ).fetchone()[0])
    finally:
        con.close()


def _collect(question):
    """Runs the full agent loop and returns its events (list of dicts)."""
    async def run():
        events = []
        async for event in run_agent(question):
            events.append(event)
        return events
    return asyncio.run(run())


@pytest.mark.parametrize("q", QUESTIONS, ids=lambda q: q["id"])
def test_llm_answer(q):
    if q.get("requires_new_schema") and not _has_wickets_table():
        pytest.skip("database predates the deliveries_wickets schema")

    events = _collect(q["question"])

    final = [e for e in events if e["type"] == "final_answer"]
    assert final, f"no final answer; events: {[e['type'] for e in events]}"
    answer = final[-1]["content"]

    # The final answer should reflect the recorded expected answer: check
    # every number and capitalized name in it appears (or the answer
    # transparently notes what it found instead).
    expected = q["expected_answer"]
    for token in _expected_tokens(expected):
        assert token in answer, f"expected {token!r} in answer, got: {answer}"
    # Hard-value questions must also be internally consistent: rerun the
    # deterministic check for the same question if one exists.
    if "check" in q:
        check = q["check"]
        fn = getattr(stats_mod, check["tool"], None) or getattr(tools_mod, check["tool"])
        result = fn(**check["args"])
        assert "error" not in result


def _expected_tokens(expected):
    """Numbers and Proper-cased words worth asserting verbatim; skips
    filler words like 'A'/'The' and 'About'."""
    skip = {"a", "the", "about", "ideally", "with", "by", "in", "for", "or", "and", "roughly", "between", "higher", "applied", "named", "count", "at", "least", "best"}
    tokens = []
    for word in expected.replace(",", " ").split():
        if word.lower().strip("()") in skip:
            continue
        if any(ch.isdigit() for ch in word):
            tokens.append(word.rstrip("."))
        elif word[0].isupper():
            tokens.append(word.rstrip("."))
    return tokens
