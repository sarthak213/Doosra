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


MANUAL = {"q22", "q23", "q26", "q35", "q36"}     # open-ended (a qualification or definition to choose): reviewed by hand
VARIANTS = {"IPL": ["IPL", "Indian Premier League"], "T20Is": "T20I", "ODIs": "ODI", "17-20": ["17-20", "death"]}


def tokens(expected: str) -> list:
    """What an answer must contain, from an expected answer verified against the database: its numbers and names,
    accepting the usual variants. A given name or initials before a surname is dropped ('Virat Kohli' and 'V Kohli'
    both need only 'Kohli'); 'IPL' also matches 'Indian Premier League'; plurals of formats match the singular."""
    raw = _expected_tokens(expected)
    word = lambda t: t[:1].isupper() and not any(ch.isdigit() for ch in t)  # noqa: E731
    out = []
    for i, t in enumerate(raw):
        if word(t) and i + 1 < len(raw) and word(raw[i + 1]) and not raw[i + 1].isupper():     # 'Kohli IPL' keeps Kohli
            continue
        t = t.strip("().:;")
        if t and t not in ("Both", "Played/won", "About"):
            out.append(VARIANTS.get(t, t))
    return out


async def computed_facts() -> dict[str, list]:
    """The facts behind the descriptive expected answers ('A specific player named with a count'), from the same
    tools and tables the agent uses, so they stay right as the data is updated."""
    from analytics import db
    async with create_connected_server_and_client_session(mcp) as s:
        async def tool(name, args):
            return json.loads((await s.call_tool(name, args)).content[0].text)
        ipl = {"competition": "Indian Premier League"}
        top = (await tool("leaderboard", {"metric": "runs", "filters": ipl}))["rows"][0]
        avgs = (await tool("compare_players", {"players": ["Virat Kohli", "Rohit Sharma"], "metrics": ["average"],
                                               "filters": ipl}))["rows"]
        seasons = (await tool("player_stats", {"player": "Virat Kohli", "split_by": "season", "metrics": ["runs"],
                                               "filters": ipl}))["rows"]
        h2h = (await tool("team_record", {"team": "India", "opposition": "Pakistan",
                                          "filters": {"format": "ODI", "gender": "male"}}))["rows"][0]
        mu = (await tool("matchup", {"batter": "Virat Kohli", "bowler": "Rashid Khan"}))["rows"][0]
        total = (await tool("records", {"kind": "team_total", "filters": ipl}))["rows"][0]
    surname = lambda x: str(x).split()[-1]  # noqa: E731
    n = lambda x: f"{x:,}" if isinstance(x, int) and x >= 1000 else str(x)  # noqa: E731
    best = max(seasons, key=lambda r: r[1])
    venue = db.query("SELECT venue, COUNT(*) n FROM matches GROUP BY 1 ORDER BY 2 DESC LIMIT 1")[0]
    wins = db.query("SELECT winner, COUNT(*) n FROM matches WHERE winner IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 1")[0]
    pom = db.query("SELECT player_of_match p, COUNT(*) n FROM matches WHERE player_of_match IS NOT NULL "
                   "GROUP BY 1 ORDER BY 2 DESC LIMIT 1")[0]
    players = db.query("SELECT COUNT(DISTINCT player) n FROM players_matches")[0]["n"]
    run_outs = [db.query(f"SELECT COUNT(*) n FROM deliveries WHERE wicket_kind = 'run out'{w}")[0]["n"]
                for w in ("", " AND innings_num <= 2")]     # with super overs (the fixture) or without (run_sql's rule)
    return {
        "q04": [[n(x) for x in run_outs]],
        "q21": [surname(top[1])],
        "q24": [str(avgs[0][1]), str(avgs[1][1])],
        "q25": [str(best[0]), n(best[1])],
        "q27": [str(h2h[0]), str(h2h[1]), str(h2h[2])],
        "q28": [venue["venue"].split(",")[0].split()[0], n(venue["n"])],
        "q29": [str(mu[1]), str(mu[0])],
        "q30": ["caught"],
        "q31": [wins["winner"], n(wins["n"])],
        "q32": [surname(pom["p"]), n(pom["n"])],
        "q33": [total[1].split("/")[0], surname(total[0])],
        "q34": [n(players)],
    }


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
    if (SRC / "v1" / "tools.json").exists():     # what the first fine-tunes were trained with, to test them fairly
        (OUT / "profiles" / "compact-v1").mkdir(parents=True)
        for f in ("system_prompt.txt", "tools.json"):
            shutil.copy(SRC / "v1" / f, OUT / "profiles" / "compact-v1" / f)
    cat = catalog.get_catalog()
    (OUT / "profiles" / "full" / "system_prompt.txt").write_text(
        build_system_prompt(date_min=cat.date_min, date_max=cat.date_max, today=dt.date.today().isoformat()),
        encoding="utf-8")
    (OUT / "profiles" / "full" / "tools.json").write_text(json.dumps(asyncio.run(full_tools()), indent=1), encoding="utf-8")
    questions = json.loads((ROOT / "backend" / "tests" / "eval_fixtures" / "eval_questions.json").read_text(encoding="utf-8"))["questions"]
    facts = asyncio.run(computed_facts())
    graded = []
    for q in questions:
        if q["id"] in MANUAL:
            grade, must = "manual", []
        elif q["id"] in facts:
            grade, must = "auto", facts[q["id"]]
        else:
            grade, must = "auto", tokens(q["expected_answer"])
        graded.append({"id": q["id"], "question": q["question"], "expected": q["expected_answer"], "grade": grade,
                     "must_include": must})
    (OUT / "questions.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in graded), encoding="utf-8")
    (OUT / "dataset.json").write_text(json.dumps({
        "name": "Doosra tool calls",
        "answer_tool": {"name": "final_answer", "arg": "answer"},
        "default_profile": "compact",
        "mcp": {"command": sys.executable, "args": [str(ROOT / "ml" / "toolcall" / "eval_mcp.py")], "cwd": str(ROOT)},
    }, indent=1), encoding="utf-8")
    print(f"wrote {OUT}: {len(rows)} test conversations, {len(questions)} end-to-end questions")


if __name__ == "__main__":
    main()
