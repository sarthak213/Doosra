"""
Check the generated tool-calling dataset before it's published or trained on:

    python ml/toolcall/validate.py

- every conversation is system, user, then assistant tool call / tool result pairs, ending in final_answer;
- every tool name exists and every call's arguments are valid JSON;
- held-out players, teams and venues never appear in train or validation (so the test set is honest);
- the length distribution (characters / 4 as a token estimate), which sets the training sequence length.
Exits non-zero on any problem.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import entities  # noqa: E402

OUT = HERE.parents[1] / "ml" / "out" / "toolcall"


def check(rows: list[dict], tools: set[str]) -> list[str]:
    problems = []
    for r in rows:
        m = r["messages"]
        if m[0]["role"] != "system" or m[1]["role"] != "user":
            problems.append(f"{r['id']}: doesn't start with system + user")
            continue
        rest = m[2:]
        for i in range(0, len(rest) - 1, 2):
            a, t = rest[i], rest[i + 1]
            if a["role"] != "assistant" or t["role"] != "tool" or a["tool_calls"][0]["id"] != t["tool_call_id"]:
                problems.append(f"{r['id']}: call/result pairs out of order")
                break
        last = rest[-1]
        if last["role"] != "assistant" or last["tool_calls"][0]["function"]["name"] != "final_answer":
            problems.append(f"{r['id']}: doesn't end in final_answer")
        for a in (x for x in rest if x["role"] == "assistant"):
            fn = a["tool_calls"][0]["function"]
            if fn["name"] not in tools:
                problems.append(f"{r['id']}: unknown tool {fn['name']}")
            try:
                json.loads(fn["arguments"])
            except json.JSONDecodeError:
                problems.append(f"{r['id']}: arguments aren't JSON")
    return problems


def _values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _values(v)
    elif isinstance(obj, str):
        yield obj


def leaks(rows: list[dict], held: set[str]) -> list[str]:
    """Held-out entities in what an example is about: an argument equal to one, or the name in the question
    as a whole name ('Nepal', not 'Nepal Premier League'). Answers can name anyone in the results."""
    import re
    out = []
    for r in rows:
        args = set()
        for a in r["messages"]:
            if a.get("tool_calls") and a["tool_calls"][0]["function"]["name"] != "final_answer":
                args |= set(_values(json.loads(a["tool_calls"][0]["function"]["arguments"])))
        hit = [h for h in held if h in args or re.search(rf"{re.escape(h)}(?!\s+[A-Z])", r["question"])]
        if hit:
            out.append(f"{r['id']}: held-out {hit[:2]}")
    return out


def main() -> int:
    tools = {t["function"]["name"] for t in json.loads((OUT / "tools.json").read_text(encoding="utf-8"))}
    pools = entities.load()
    held = ({p.name for p in pools.players if p.test and len(p.name) > 6} | {t["name"] for t in pools.teams if entities.held_out(t["name"])}
            | {v["name"] for v in pools.venues if entities.held_out(v["name"])})
    problems = []
    for split in ("train", "validation", "test"):
        rows = [json.loads(l) for l in (OUT / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()]
        p = check(rows, tools)
        if split != "test":
            p += leaks(rows, held)
        lengths = [sum(len(json.dumps(m)) for m in r["messages"]) // 4 for r in rows]
        print(f"{split}: {len(rows):,} conversations, {len(p)} problems; est. tokens without tool schemas: "
              f"median {statistics.median(lengths):,.0f}, p95 {sorted(lengths)[int(0.95 * len(lengths))]:,}, "
              f"max {max(lengths):,}; paraphrased {sum(1 for r in rows if r.get('original_question')):,}")
        problems += p
    for x in problems[:20]:
        print("  ", x)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
