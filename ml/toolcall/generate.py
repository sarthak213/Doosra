"""
Build the tool-calling training data: sample questions (intents.py), run their gold tool calls against the
database exactly as the Doosra agent does, and write each as a chat conversation the fine-tuned model
learns from:

    system (the short prompt)  ->  user question  ->  assistant tool call(s)  ->  tool result(s)  ->  ...
    ->  assistant final_answer (written from the results by answers.py)

    python ml/toolcall/generate.py --n 6000              # ml/out/toolcall/{train,validation,test}.jsonl
    python ml/toolcall/generate.py --dump 1              # one example per intent, with the raw results

Calls that error or return nothing are dropped, so every example is grounded in real data. The test split
uses only held-out players, teams and venues (entities.py); train and validation never contain them.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "backend"))

from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

import answers  # noqa: E402
import entities  # noqa: E402
import intents  # noqa: E402
from agent import graph, prompts  # noqa: E402
from analytics import catalog  # noqa: E402

OUT = ROOT / "ml" / "out" / "toolcall"


def system_prompt() -> str:
    cat = catalog.get_catalog()
    return prompts.build_compact_prompt(cat.date_min, cat.date_max, dt.date.today().isoformat())


async def tool_list(session) -> list[dict]:
    listed = await session.list_tools()
    full = graph.mcp_tools_to_openai(listed.tools) + graph.LOCAL_TOOLS + [graph.FINAL_ANSWER]
    return graph.compact_tools(full)


def empty(output) -> bool:
    if not isinstance(output, dict):
        return not output
    if output.get("error") or output.get("empty"):
        return True
    return "rows" in output and not output["rows"]


async def run_calls(session, schemas: dict, calls: list[tuple[str, dict]]):
    """Execute gold calls the way agent.graph.tools_node does. Returns [(name, args, model_view)] or None
    if a call fails or returns nothing."""
    tables: dict = {}
    done = []
    for name, args in calls:
        args = graph._clean_args(name, args, schemas.get(name))
        if name == "plot_chart":
            output = graph._build_chart(args, tables)
            if "error" in output:
                return None
            view = {"status": "chart shown to the user"}
        elif name == "open_in_app":
            view = {"status": f"opened the {args['view']} view"}
        else:
            try:
                output = await asyncio.wait_for(graph._call_mcp(session, name, args), timeout=60)
            except Exception:  # noqa: BLE001 - a failing call just drops the example
                return None
            if empty(output):
                return None
            ids = []
            for t in graph._nested_tables(output):
                tid = f"T{len(tables) + 1}"
                tables[tid] = t
                ids.append(tid)
            view = graph._for_model(name, output, ids)
        done.append((name, args, view))
    return done


def conversation(system: str, question: str, results: list, answer: str) -> list[dict]:
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": question}]
    for i, (name, args, view) in enumerate(results):
        cid = f"call_{i + 1}"
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})
        msgs.append({"role": "tool", "tool_call_id": cid, "name": name, "content": json.dumps(view, default=str)})
    msgs.append({"role": "assistant", "content": None, "tool_calls": [
        {"id": f"call_{len(results) + 1}", "type": "function",
         "function": {"name": "final_answer", "arguments": json.dumps({"answer": answer})}}]})
    return msgs


async def dump(per_intent: int) -> None:
    from mcp_server.server import mcp
    pools = entities.load().split(False)
    rng = random.Random(1)
    async with create_connected_server_and_client_session(mcp) as session:
        tools = await tool_list(session)
        schemas = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
        for name, (fn, _) in intents.INTENTS.items():
            got = 0
            for _ in range(20):
                ex = fn(rng, pools)
                if not ex:
                    continue
                res = await run_calls(session, schemas, ex.calls)
                if not res:
                    continue
                print(f"\n===== {name}: {ex.question}")
                for call, args, view in res:
                    print(f"--> {call}({json.dumps(args)})\n{json.dumps(view, default=str)[:700]}")
                got += 1
                if got >= per_intent:
                    break


async def generate(n: int, seed: int = 7, test_share: float = 0.12, valid_share: float = 0.05) -> dict:
    """n examples in all: test ones from held-out entities, the rest split into train and validation."""
    from mcp_server.server import mcp
    all_pools = entities.load()
    pools = {"train": all_pools.split(False), "test": all_pools.split(True)}
    rng = random.Random(seed)
    system = system_prompt()
    OUT.mkdir(parents=True, exist_ok=True)
    files = {k: open(OUT / f"{k}.jsonl", "w", encoding="utf-8") for k in ("train", "validation", "test")}
    counts = {k: 0 for k in files}
    by_intent: dict[str, int] = {}
    seen: set[str] = set()
    tried = 0
    async with create_connected_server_and_client_session(mcp) as session:
        tools = await tool_list(session)
        schemas = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
        (OUT / "tools.json").write_text(json.dumps(tools, indent=1), encoding="utf-8")
        (OUT / "system_prompt.txt").write_text(system, encoding="utf-8")
        target_test = int(n * test_share)
        while sum(counts.values()) < n and tried < n * 6:
            tried += 1
            split = "test" if counts["test"] < target_test and rng.random() < test_share * 1.5 else "train"
            ex = intents.sample(rng, pools[split])
            if not ex or ex.question in seen:
                continue
            res = await run_calls(session, schemas, ex.calls)
            if not res:
                continue
            answer = answers.write(rng, ex.kind, [v for _, _, v in res], ex.meta)
            if not answer:
                continue
            seen.add(ex.question)
            if split == "train" and rng.random() < valid_share:
                split = "validation"
            record = {"id": f"{split[:2]}{counts[split]:05d}", "intent": ex.intent, "question": ex.question,
                      "messages": conversation(system, ex.question, res, answer)}
            files[split].write(json.dumps(record, ensure_ascii=False) + "\n")
            counts[split] += 1
            by_intent[ex.intent] = by_intent.get(ex.intent, 0) + 1
            if sum(counts.values()) % 250 == 0:
                print(f"  {sum(counts.values()):,} examples ({counts}) from {tried:,} tries", flush=True)
    for f in files.values():
        f.close()
    stats = {"counts": counts, "tried": tried, "by_intent": dict(sorted(by_intent.items(), key=lambda kv: -kv[1]))}
    (OUT / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", type=int, help="print N examples per intent with their results")
    parser.add_argument("--n", type=int, default=6000)
    a = parser.parse_args()
    if a.dump:
        asyncio.run(dump(a.dump))
    else:
        print(json.dumps(asyncio.run(generate(a.n)), indent=1))
