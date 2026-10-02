"""
The evaluation sections of the fine-tuned models' cards, from ToolEval's saved runs (each run's JSON has every
question's result): ml/hf/toolcall/evaluation-<run>.md, picked up by ml/build_hf.py.

    python ml/toolcall/eval_report.py [path to ToolEval's workspace/runs]

End to end is the same 40 questions for every model, so all models share one table. The held-out tests come from the
dataset version a model was trained on (v1 and v2 have different test sets), so each model is compared only with
runs on its own test set.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "ToolEval" / "workspace" / "runs"
OUT = ROOT / "ml" / "hf" / "toolcall"

NAMES = {   # ToolEval run label (first word) -> (row name, card it belongs to, test set)
    "base-4b": ("Qwen3.5 4B (long prompt)", None, 1),
    "base-9b": ("Qwen3.5 9B (long prompt)", None, 1),
    "ft-a": ("v1, run A", "a", 1),
    "ft-b-350": ("v1, run B at step 350", "b-step350", 1),
    "ft-b": ("v1, run B", "b", 1),
    "ft-v2-epoch1": ("v2 at epoch 1", "v2-epoch1", 2),
    "ft-v2": ("**v2**", "v2", 2),
}
ORDER = list(NAMES)


def pct(x) -> str:
    return "–" if x is None else f"{100 * x:.0f}%"


def load() -> dict:
    """The latest run per label; a base model's run on the v2 test set is kept apart ('base-4b@2')."""
    out = {}
    for p in sorted(RUNS.glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        key = r["label"].split(" ")[0]
        if key in NAMES:
            if "v2 test" in r["label"]:
                key += "@2"
            out[key] = r
    return out


def e2e_table(runs: dict) -> str:
    lines = ["| Model | Correct of 40 | Auto-graded (35) | Reviewed (5) | Calls per question | Median s | 90th pct s |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for k in ORDER:
        r = runs.get(k)
        if not r or "end_to_end" not in r:
            continue
        e = r["end_to_end"]
        rows = e["rows"]
        auto = sum(1 for x in rows if x.get("grade") != "manual" and x.get("correct"))
        manual = sum(1 for x in rows if x.get("grade") == "manual" and x.get("correct"))
        lines.append(f"| {NAMES[k][0]} | **{e['correct']}** | {auto} | {manual} | {e['mean_calls']} | "
                     f"{e['median_seconds']} | {e['p90_seconds']} |")
    return "\n".join(lines)


def heldout_table(runs: dict, test: int) -> str:
    keys = [k for k in ORDER if NAMES[k][2] == test and k in runs and "tool_choice" in runs[k]]
    if test == 2 and "base-4b@2" in runs:
        keys = ["base-4b@2"] + keys
    lines = ["| Model | Right tool | Exact arguments | Conversations all right | Answers when it should | Answers fully grounded |",
             "|---|---:|---:|---:|---:|---:|"]
    for k in keys:
        r = runs[k]
        c, t = r["tool_choice"], r.get("trajectory", {})
        name = NAMES[k.split("@")[0]][0]
        lines.append(f"| {name} | {pct(c['tool_accuracy'])} | {pct(c['args_exact'])} | {pct(t.get('conversations_match'))} | "
                     f"{pct(t.get('answered'))} | {pct(t.get('fully_grounded'))} |")
    return "\n".join(lines)


HOW = """Measured with ToolEval, a local evaluation app, each model served by llama.cpp as Doosra runs it (Q4_K_M, thinking
off): the base models with the app's full prompt and tool list, the fine-tuned ones with the short prompt they were
trained on.

**End to end:** 40 open questions (Doosra's own evaluation set, written separately from the training templates and
kept out of training), each answered through a real tool loop against Doosra's database. 35 are graded automatically
on the facts the answer must contain (accepting name variants such as "V Kohli" for "Virat Kohli"); 5 are open-ended
(a qualification or definition to choose) and were checked by hand against the database."""


def section(card: str, runs: dict) -> str:
    test = 2 if card.startswith("v2") else 1
    parts = [HOW, "### End to end (the same 40 questions for every model)", e2e_table(runs)]
    if card == "v2":
        parts.append("""It answers more open questions right than both base models while making less than half the base 4B's calls and
answering in under half the time. The four auto-graded questions it misses: a comparison that listed only the leader's
average (q24), a `COUNT(*)` where `COUNT(DISTINCT player)` was needed (q34), and two questions where the definition
differs from the reference (Kohli's matches: batting innings 808 against appearances 834; most wins: men's cricket
by default against both genders).""")
    if test == 1:
        parts.append("""v1 was near-perfect on its templated tests but lost end to end: it couldn't write SQL (the compact tool list had
dropped the schema), answered with a template's headline figure instead of the one asked (sixes, balls faced), made
one call and stopped where a second was needed, and had learned answers with no figures from a template bug. Dataset
v2 fixed each of these.""")
    parts += [f"### Held-out test set (dataset v{test}: unseen players, teams and venues)",
              "First call of 300 conversations, and every step of 100 with the final answer checked against the tool results.",
              heldout_table(runs, test)]
    if test == 2 and "base-4b@2" not in runs:
        parts.append("_The base 4B on this test set is still being measured._")
    return "\n\n".join(parts)


def main() -> None:
    runs = load()
    print("runs:", sorted(runs))
    for k, (_, card, _) in NAMES.items():
        if card and k in runs:
            (OUT / f"evaluation-{card}.md").write_text(section(card, runs) + "\n", encoding="utf-8")
            print("wrote", OUT / f"evaluation-{card}.md")


if __name__ == "__main__":
    main()
