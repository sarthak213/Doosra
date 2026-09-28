"""Deterministic function-level eval: runs every eval question that has a
'check' block against the REAL database (not the synthetic fixture DB) and
asserts the recorded expected value. No LLM involved -- this catches
regressions in the stats functions and the SQL guard quickly and cheaply.

Skipped entirely when data/cricket.duckdb is absent (e.g. CI without data).
"""

import json
import os
from pathlib import Path

import pytest

from agent import stats as stats_mod
from agent import tools as tools_mod
from analytics import db

BACKEND_DIR = Path(__file__).resolve().parents[1]
REAL_DB = BACKEND_DIR / "data" / os.environ.get("DOOSRA_EVAL_DB", "cricket.duckdb")
EVAL_FILE = Path(__file__).parent / "eval_fixtures" / "eval_questions.json"

pytestmark = pytest.mark.skipif(not REAL_DB.exists(), reason="data/cricket.duckdb not present")

QUESTIONS = json.loads(EVAL_FILE.read_text(encoding="utf-8"))["questions"]
CHECKED = [q for q in QUESTIONS if "check" in q]


@pytest.fixture(autouse=True)
def schema(monkeypatch):
    """Overrides conftest's autouse (schema-parametrized) synthetic-DB
    fixture: the expected values in eval_questions.json were captured
    against the real database."""
    monkeypatch.setattr(db, "DB_PATH", REAL_DB)
    yield "real"


def _resolve_tool(name):
    fn = getattr(stats_mod, name, None) or getattr(tools_mod, name)
    assert fn is not None, f"unknown tool in eval file: {name}"
    return fn


def _get_path(result, path):
    """A dotted path into the result dict -- or, for a table result, a column
    name, read from the first row."""
    cols = result.get("columns")
    if isinstance(cols, list) and path in cols and path not in result:
        assert result["rows"], f"empty table: {result}"
        return result["rows"][0][cols.index(path)]
    current = result
    for part in path.split("."):
        current = current[part]
    return current


@pytest.mark.parametrize("q", CHECKED, ids=lambda q: q["id"])
def test_eval_question(q):
    check = q["check"]
    result = _resolve_tool(check["tool"])(**check["args"])
    assert "error" not in result, f"{q['id']} ({q['question']}): {result}"
    if "field" in check:
        assert _get_path(result, check["field"]) == check["equals"], (
            f"{q['id']} ({q['question']}): {check['field']} mismatch"
        )
    for path, expected in check.get("fields", {}).items():
        assert _get_path(result, path) == expected, (
            f"{q['id']} ({q['question']}): {path} mismatch"
        )


def test_eval_corpus_shape():
    """Meta-check: the corpus is healthy (30+ questions, unique ids, most
    with deterministic checks) so a bad edit to the JSON can't silently gut
    the eval."""
    assert len(QUESTIONS) >= 30
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids))
    assert len(CHECKED) >= 15
    for q in QUESTIONS:
        assert q["question"].endswith(("?", ".")), f"{q['id']} should be a full-sentence question"
        assert q["expected_answer"], f"{q['id']} needs an expected answer for the LLM eval"
