"""
Evaluate models on Doosra's tool calling, each served by Doosra's own llama.cpp engine the way the app would
run it (the base models with the full prompt and tool list; the fine-tuned ones with the compact prompt they
were trained on).

    python ml/toolcall/evaluate.py --model base-4b=path/Qwen3.5-4B-Q4_K_M.gguf:full \\
                                   --model ft-a=path/doosra-a.Q4_K_M.gguf:compact --n 300
    python ml/toolcall/evaluate.py --report          # ml/out/toolcall/eval/*.json -> report.md + plots

Two measures per model:
  tool choice  the held-out test set (players, teams and venues the fine-tuned models never saw): is the first
               call the right tool with the right arguments? Also prompt tokens and seconds per question.
  end to end   the 40 questions of tests/eval_fixtures/eval_questions.json through the real agent loop, graded
               like tests/test_llm_eval.py (every number and name of the expected answer must appear).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

import local_llm  # noqa: E402
from agent import graph  # noqa: E402

OUT = ROOT / "ml" / "out" / "toolcall"
EVAL = OUT / "eval"
QUESTIONS = json.loads((ROOT / "backend" / "tests" / "eval_fixtures" / "eval_questions.json").read_text(encoding="utf-8"))["questions"]


def expected_tokens(expected: str) -> list[str]:
    from test_llm_eval import _expected_tokens
    return _expected_tokens(expected)


def normalise(args: dict, schema: dict | None, name: str) -> dict:
    return json.loads(json.dumps(graph._clean_args(name, args, schema), sort_keys=True).lower())


async def tool_lists():
    from mcp_server.server import mcp
    async with create_connected_server_and_client_session(mcp) as s:
        full = graph.mcp_tools_to_openai((await s.list_tools()).tools) + graph.LOCAL_TOOLS + [graph.FINAL_ANSWER]
    return full, graph.compact_tools(full)


async def tool_choice(n: int, compact: bool) -> dict:
    full, small = await tool_lists()
    tools = small if compact else full
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in full}
    system = graph._system_prompt()
    rows = [json.loads(l) for l in (OUT / "test.jsonl").read_text(encoding="utf-8").splitlines()][:n]
    results = []
    for r in rows:
        gold = r["messages"][2]["tool_calls"][0]["function"]
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": r["question"]}]
        t = time.time()
        try:
            resp = await graph.client.chat.completions.create(
                model=graph.MODEL, messages=msgs, tools=tools, tool_choice="auto", temperature=0, max_tokens=400,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none"})
        except Exception as e:  # noqa: BLE001
            results.append({"id": r["id"], "intent": r["intent"], "error": str(e)[:200]})
            continue
        msg = resp.choices[0].message
        call = (msg.tool_calls or [None])[0]
        got_name = call.function.name if call else None
        try:
            got_args = json.loads(call.function.arguments) if call else {}
            valid = True
        except (json.JSONDecodeError, TypeError):
            got_args, valid = {}, False
        gold_args = json.loads(gold["arguments"])
        g, a = normalise(gold_args, schemas.get(gold["name"]), gold["name"]), normalise(got_args, schemas.get(got_name), got_name or "")
        required = schemas.get(gold["name"], {}).get("required", [])
        results.append({
            "id": r["id"], "intent": r["intent"], "gold": gold["name"], "got": got_name, "valid_json": valid,
            "tool_ok": got_name == gold["name"], "args_exact": got_name == gold["name"] and g == a,
            "key_args_ok": got_name == gold["name"] and all(g.get(k) == a.get(k) for k in required)
                           and g.get("filters", {}) == a.get("filters", {}),
            "prompt_tokens": getattr(resp.usage, "prompt_tokens", None), "seconds": round(time.time() - t, 2)})
    ok = [x for x in results if "error" not in x]

    def share(key):
        return round(sum(x[key] for x in ok) / max(len(ok), 1), 4)
    by_tool: dict[str, list] = {}
    for x in ok:
        by_tool.setdefault(x["gold"], []).append(x["tool_ok"])
    return {"n": len(results), "errors": len(results) - len(ok), "tool_accuracy": share("tool_ok"),
            "args_exact": share("args_exact"), "key_args": share("key_args_ok"), "valid_json": share("valid_json"),
            "median_prompt_tokens": statistics.median([x["prompt_tokens"] for x in ok if x["prompt_tokens"]] or [0]),
            "median_seconds": statistics.median([x["seconds"] for x in ok] or [0]),
            "by_tool": {k: round(sum(v) / len(v), 3) for k, v in sorted(by_tool.items())}, "rows": results}


async def end_to_end(deep: bool) -> dict:
    results = []
    for q in QUESTIONS:
        t = time.time()
        events = [e async for e in graph.run_agent(q["question"], deep=deep)]
        final = [e["content"] for e in events if e["type"] == "final_answer"]
        answer = final[-1] if final else ""
        missing = [tok for tok in expected_tokens(q["expected_answer"]) if tok not in answer]
        results.append({"id": q["id"], "correct": bool(final) and not missing, "answered": bool(final),
                        "missing": missing, "tool_calls": sum(e["type"] == "tool_call" for e in events),
                        "seconds": round(time.time() - t, 1), "answer": answer[:600]})
    return {"n": len(results), "correct": sum(r["correct"] for r in results), "answered": sum(r["answered"] for r in results),
            "median_seconds": statistics.median(r["seconds"] for r in results),
            "mean_tool_calls": round(statistics.mean(r["tool_calls"] for r in results), 2), "rows": results}


async def evaluate(name: str, gguf: Path, compact: bool, n: int, deep: bool) -> dict:
    engine = local_llm.Engine()
    mode = engine.start(gguf)
    graph.configure("llamacpp", base_url=engine.base_url, compact=compact)
    print(f"{name}: engine up ({mode}), {'compact' if compact else 'full'} prompt", flush=True)
    try:
        t = time.time()
        choice = await tool_choice(n, compact)
        print(f"  tool choice: {choice['tool_accuracy']:.1%} right tool, {choice['args_exact']:.1%} exact args "
              f"({time.time() - t:.0f}s)", flush=True)
        t = time.time()
        e2e = await end_to_end(deep)
        print(f"  end to end: {e2e['correct']}/{e2e['n']} correct ({time.time() - t:.0f}s)", flush=True)
    finally:
        engine.stop()
    out = {"name": name, "gguf": str(gguf), "compact": compact, "deep": deep, "engine_mode": mode,
           "tool_choice": choice, "end_to_end": e2e}
    EVAL.mkdir(parents=True, exist_ok=True)
    (EVAL / f"{name}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def report() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EVAL.glob("*.json"))]
    lines = ["# Doosra tool-calling evaluation", "",
             "| Model | Prompt | Right tool | Exact args | Key args | Valid calls | Prompt tokens | s / question | "
             "End to end (of 40) | Median s (e2e) |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in runs:
        c, e = r["tool_choice"], r["end_to_end"]
        lines.append(f"| {r['name']} | {'compact' if r['compact'] else 'full'} | {c['tool_accuracy']:.1%} | {c['args_exact']:.1%} | "
                     f"{c['key_args']:.1%} | {c['valid_json']:.1%} | {c['median_prompt_tokens']:,.0f} | {c['median_seconds']:.1f} | "
                     f"{e['correct']} | {e['median_seconds']:.0f} |")
    tools = sorted({t for r in runs for t in r["tool_choice"]["by_tool"]})
    lines += ["", "## Right tool, by tool", "", "| Tool | " + " | ".join(r["name"] for r in runs) + " |",
              "|---|" + "---:|" * len(runs)]
    for t in tools:
        lines.append(f"| {t} | " + " | ".join(f"{r['tool_choice']['by_tool'].get(t, float('nan')):.0%}" for r in runs) + " |")
    (EVAL / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    fig, ax = plt.subplots(figsize=(7, 4))
    names = [r["name"] for r in runs]
    x = range(len(runs))
    ax.bar([i - 0.2 for i in x], [r["tool_choice"]["tool_accuracy"] * 100 for r in runs], 0.4, label="right tool (held-out)",
           color="#b08a3a")
    ax.bar([i + 0.2 for i in x], [r["end_to_end"]["correct"] / r["end_to_end"]["n"] * 100 for r in runs], 0.4,
           label="end-to-end correct", color="#3987e5")
    ax.set_xticks(list(x), names)
    ax.set(ylabel="%", ylim=(0, 100), title="Doosra tool calling")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(EVAL / "comparison.png", dpi=150)
    print((EVAL / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", default=[], help="name=path.gguf:full|compact")
    ap.add_argument("--n", type=int, default=300, help="held-out questions for the tool-choice measure")
    ap.add_argument("--deep", action="store_true", help="end to end with reasoning on (as saved chats do)")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    for spec in a.model:
        name, rest = spec.split("=", 1)
        path, style = rest.rsplit(":", 1) if rest.rsplit(":", 1)[-1] in ("full", "compact") else (rest, "full")
        asyncio.run(evaluate(name, Path(path), style == "compact", a.n, a.deep))
    if a.report:
        report()
