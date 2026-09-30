"""
Export Doosra's tool-calling evaluation as a dataset folder for ToolEval (or any evaluator that reads the same
layout): ml/out/toolcall/eval-pack/

    test.jsonl                   the held-out test conversations (category = intent)
    profiles/compact/            the short prompt and compact tools the fine-tuned models were trained with
    profiles/full/               the app's full prompt and full tool list, as base models get them
    questions.jsonl              the 40 end-to-end questions, graded by the numbers and names they must include
    dataset.json                 final_answer as the answer tool; eval_mcp.py as the MCP server for end to end

    python ml/toolcall/export_eval.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from agent import graph  # noqa: E402
from agent.prompts import build_system_prompt  # noqa: E402
from analytics import catalog  # noqa: E402
from mcp_server.server import mcp  # noqa: E402
from test_llm_eval import _expected_tokens  # noqa: E402

SRC = ROOT / "ml" / "out" / "toolcall"
OUT = SRC / "eval-pack"


async def full_tools() -> list:
    async with create_connected_server_and_client_session(mcp) as s:
        return graph.mcp_tools_to_openai((await s.list_tools()).tools) + graph.LOCAL_TOOLS + [graph.FINAL_ANSWER]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for p in ("compact", "full"):
        (OUT / "profiles" / p).mkdir(parents=True)
    rows = []
    for line in (SRC / "test.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        r["category"] = r.get("intent")
        r.pop("paraphrased", None)
        rows.append(json.dumps(r, ensure_ascii=False))
    (OUT / "test.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    shutil.copy(SRC / "system_prompt.txt", OUT / "profiles" / "compact" / "system_prompt.txt")
    shutil.copy(SRC / "tools.json", OUT / "profiles" / "compact" / "tools.json")
    cat = catalog.get_catalog()
    (OUT / "profiles" / "full" / "system_prompt.txt").write_text(
        build_system_prompt(date_min=cat.date_min, date_max=cat.date_max, today=dt.date.today().isoformat()),
        encoding="utf-8")
    (OUT / "profiles" / "full" / "tools.json").write_text(json.dumps(asyncio.run(full_tools()), indent=1), encoding="utf-8")
    questions = json.loads((ROOT / "backend" / "tests" / "eval_fixtures" / "eval_questions.json").read_text(encoding="utf-8"))["questions"]
    (OUT / "questions.jsonl").write_text("".join(json.dumps(
        {"id": q["id"], "question": q["question"], "expected": q["expected_answer"],
         "must_include": _expected_tokens(q["expected_answer"])}, ensure_ascii=False) + "\n" for q in questions),
        encoding="utf-8")
    (OUT / "dataset.json").write_text(json.dumps({
        "name": "Doosra tool calls",
        "answer_tool": {"name": "final_answer", "arg": "answer"},
        "default_profile": "compact",
        "mcp": {"command": sys.executable, "args": [str(ROOT / "ml" / "toolcall" / "eval_mcp.py")], "cwd": str(ROOT)},
    }, indent=1), encoding="utf-8")
    print(f"wrote {OUT}: {len(rows)} test conversations, {len(questions)} end-to-end questions")


if __name__ == "__main__":
    main()
