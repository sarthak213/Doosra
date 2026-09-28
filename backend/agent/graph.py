"""
The agent's reasoning loop.

Deliberately hand-rolled (rather than a framework's prebuilt ReAct agent) so
every step can be emitted as an event for the frontend's live reasoning
trace, and so the loop itself is easy to follow.

Uses the OpenAI SDK against any OpenAI-compatible chat completions endpoint,
so the same code runs against Groq (hosted), Ollama or LM Studio (local) --
just change environment variables.

Configure via environment variables (see .env.example):
    LLM_PROVIDER        -- "groq" | "ollama" | "lmstudio" (default: "groq")
    LLM_BASE_URL        -- override the endpoint directly instead of using a preset
    LLM_MODEL           -- override the model name directly instead of using a preset
    LLM_TIMEOUT_SECONDS -- per-request timeout (default 60 hosted, 180 local)
    LLM_THINKING        -- "off" to disable Qwen3-style thinking (faster), default "on"
    GROQ_API_KEY        -- required only when LLM_PROVIDER=groq

Design choices that keep answers grounded:
  * Tools take plain names and resolve them deterministically (catalog.py),
    so the model never chains exact-string lookups -- the main source of
    wrong answers before.
  * Tabular tool results get an id ("T1") and are sent to the frontend as
    tables immediately; charts are drawn from those tables by id, so numbers
    shown to the user never pass through the model's transcription.
  * Answers containing numbers with no data lookup behind them are pushed
    back once; a final_answer issued before seeing results is ignored; the
    step limit ends with a best-effort answer rather than an error.

Event types yielded by run_agent():
    {"type": "thought", "content": str}
    {"type": "tool_call", "tool": str, "input": dict}
    {"type": "tool_result", "tool": str, "output": Any}
    {"type": "self_correction", "content": str}
    {"type": "table", "table_id": str, "table_data": dict}
    {"type": "chart", "chart_data": dict}
    {"type": "final_answer", "content": str, "chart_data": None, "table_data": None}
    {"type": "error", "content": str}
"""

import asyncio
import datetime as dt
import json
import os
import re
from typing import AsyncGenerator

from openai import AsyncOpenAI

from . import cancellation, catalog, stats, tools
from .prompts import build_system_prompt

TOOL_TIMEOUT_SECONDS = 30.0
MAX_TOOL_ROUNDS = 8
MAX_ROWS_TO_MODEL = 25
MAX_HISTORY_MESSAGES = 8

_PROVIDER_PRESETS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
        "timeout": 60.0,
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3:14b",
        "api_key_env": None,  # Ollama doesn't check the key
        "timeout": 180.0,
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "model": "local-model",  # LM Studio ignores this if only one model is loaded
        "api_key_env": None,
        "timeout": 180.0,
    },
}

_provider = os.environ.get("LLM_PROVIDER", "groq").lower()
_preset = _PROVIDER_PRESETS.get(_provider, _PROVIDER_PRESETS["groq"])

BASE_URL = os.environ.get("LLM_BASE_URL", _preset["base_url"])
MODEL = os.environ.get("LLM_MODEL", _preset["model"])
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", _preset["timeout"]))
THINKING = os.environ.get("LLM_THINKING", "on").lower() not in ("off", "0", "false", "no")

# api_key falls back to a placeholder so the module can be imported before a
# real key is set; the real error surfaces as an {"type": "error"} event.
_api_key_env = _preset["api_key_env"]
_api_key = os.environ.get(_api_key_env, "unset") if _api_key_env else "not-needed"

# One retry: a local model that timed out once will usually time out again,
# and three silent attempts made the UI look hung for minutes.
client = AsyncOpenAI(base_url=BASE_URL, api_key=_api_key, timeout=LLM_TIMEOUT_SECONDS, max_retries=1)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_FILTER_PROPS = {
    "competition": {"type": "string", "description": "Tournament/league in plain words: 'IPL', 'T20 World Cup', 'World Cup', 'Ashes', 'BBL', 'WPL'. A year inside it ('IPL 2016') sets the season. Omit for all cricket."},
    "format": {"type": "string", "description": "'Test', 'ODI', 'T20I' (official internationals), 'T20' (all T20 incl. leagues), 'first-class', 'List A' or 'international'."},
    "gender": {"type": "string", "description": "'male', 'female' or 'all'. Set 'female' for women's cricket; otherwise leave unset."},
    "team": {"type": "string", "description": "Team the player played for / the team in question, in plain words ('India', 'RCB', 'Mumbai Indians')."},
    "opposition": {"type": "string", "description": "Opponent team in plain words."},
    "venue": {"type": "string", "description": "Ground (or city) in plain words: 'Eden Gardens', 'MCG', 'Chinnaswamy', 'Lord's'."},
    "season": {"type": "string", "description": "One season: '2024' or '2023/24'."},
    "from_year": {"type": "integer", "description": "First calendar year to include."},
    "to_year": {"type": "integer", "description": "Last calendar year to include."},
    "phase": {"type": "string", "description": "'powerplay', 'middle' or 'death' (limited-overs only)."},
    "innings": {"type": "integer", "description": "1 = batting first, 2 = chasing (Tests: 1-4)."},
}


def _filters(*exclude: str) -> dict:
    return {k: v for k, v in _FILTER_PROPS.items() if k not in exclude}


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


_ROLE = {"type": "string", "enum": ["batting", "bowling", "fielding"]}

TOOL_SCHEMAS = [
    _fn("player_stats",
        "One player's batting, bowling or fielding figures in any scope (matches, runs, average, strike rate, "
        "hundreds, economy, best figures...). Use split_by for breakdowns: season-by-season trends, by format, "
        "by opposition, by phase, etc. Pass the name as the user wrote it.",
        {"player": {"type": "string"}, "role": _ROLE,
         "split_by": {"type": "string", "enum": list(stats.SPLITS)}, **_filters()},
        ["player", "role"]),
    _fn("compare_players",
        "Full figures for two or more players side by side in the same scope. Use for any comparison of players.",
        {"players": {"type": "array", "items": {"type": "string"}}, "role": _ROLE, **_filters()},
        ["players", "role"]),
    _fn("leaderboard",
        "Rank players or teams by a metric: 'most runs', 'best economy', 'most sixes', 'most wins at a venue'... "
        "Rate metrics (average, strike_rate, economy...) apply a sensible minimum-balls qualification automatically. "
        "Batting metrics: runs, average, strike_rate, sixes, fours, hundreds, fifties, ducks, boundary_pct, dot_pct, "
        "balls_per_six. Bowling: wickets, economy, average, strike_rate, dot_pct, maidens, five_wkt_hauls, "
        "four_wkt_hauls. Fielding: catches, stumpings, run_outs, dismissals. Team: wins, win_pct, matches, losses.",
        {"role": {"type": "string", "enum": ["batting", "bowling", "fielding", "team"]},
         "metric": {"type": "string"},
         "limit": {"type": "integer", "description": "How many to list (default 10)."},
         "min_balls": {"type": "integer", "description": "Override the qualification (balls faced/bowled)."},
         "min_matches": {"type": "integer", "description": "Team win_pct qualification (default 10)."},
         **_filters()},
        ["role", "metric"]),
    _fn("top_performances",
        "Record lists: highest individual scores (kind='batting_innings'), best bowling figures in an innings "
        "('bowling_figures'), or highest/lowest team totals ('team_total'). Optionally for one player.",
        {"kind": {"type": "string", "enum": ["batting_innings", "bowling_figures", "team_total"]},
         "order": {"type": "string", "enum": ["highest", "lowest"]},
         "player": {"type": "string"},
         "limit": {"type": "integer"}, **_filters()},
        ["kind"]),
    _fn("team_stats",
        "A team's results: matches, won, lost, win %, wins batting first vs chasing, tosses, recent results. Give "
        "an opposition for a head-to-head record. split_by for season-by-season or by format/opposition/venue.",
        {"team": {"type": "string"}, "opposition": {"type": "string"},
         "split_by": {"type": "string", "enum": ["season", "year", "format", "competition", "opposition", "venue"]},
         **_filters("team", "opposition")},
        ["team"]),
    _fn("venue_stats",
        "How a ground plays, per format: matches, average 1st and 2nd innings scores, wins batting first vs "
        "chasing, toss decisions, highest total.",
        {"venue": {"type": "string"}, **_filters("venue")},
        ["venue"]),
    _fn("matchup",
        "Batter vs bowler: balls, runs, dismissals, strike rate. Give both names for one matchup; give only a "
        "batter to list the bowlers who dismissed them most, or only a bowler for the batters they dismissed most.",
        {"batter": {"type": "string"}, "bowler": {"type": "string"},
         "limit": {"type": "integer"}, "min_balls": {"type": "integer"}, **_filters()},
        []),
    _fn("lookup",
        "Show how a name resolves in the database, with candidates. Only needed when a stats tool reported an "
        "ambiguous or unknown name.",
        {"kind": {"type": "string", "enum": ["player", "team", "venue", "competition"]}, "name": {"type": "string"}},
        ["kind", "name"]),
    _fn("run_sql",
        "Read-only SQL (SELECT/WITH) for questions the tools above don't cover -- dismissal types, player-of-the-"
        "match counts, extras, toss trends, etc. " + tools.SQL_GUIDE,
        {"query": {"type": "string"}},
        ["query"]),
    _fn("plot_chart",
        "Draw a chart from a table you already have, by its table_id (e.g. 'T1'). Use for trends (line) and "
        "comparisons (bar). The chart is built from the table's real values.",
        {"table_id": {"type": "string"},
         "x": {"type": "string", "description": "Column name for the x-axis/categories, e.g. 'season' or 'player'."},
         "y": {"type": "array", "items": {"type": "string"}, "description": "One or more numeric column names."},
         "type": {"type": "string", "enum": ["line", "bar"]},
         "title": {"type": "string"}},
        ["table_id", "x", "y"]),
]

FINAL_ANSWER_SCHEMA = _fn(
    "final_answer",
    "Give the user your answer once you have the data. Every number must come from tool results.",
    {"answer": {"type": "string", "description": "Markdown answer: direct answer first, then brief support and caveats."}},
    ["answer"],
)

ALL_TOOLS = TOOL_SCHEMAS + [FINAL_ANSWER_SCHEMA]

TOOL_IMPLS = {
    "player_stats": stats.player_stats,
    "compare_players": stats.compare_players,
    "leaderboard": stats.leaderboard,
    "top_performances": stats.top_performances,
    "team_stats": stats.team_stats,
    "venue_stats": stats.venue_stats,
    "matchup": stats.matchup,
    "lookup": stats.lookup,
    "run_sql": lambda query: tools.run_sql(query),
    # plot_chart is handled inside the loop (it needs the run's tables).
    "plot_chart": None,
}

# Argument names small models commonly reach for, mapped onto ours.
_ARG_ALIASES = {
    "tournament": "competition", "event": "competition", "event_name": "competition", "league": "competition",
    "match_type": "format", "stat_type": "role", "type_of_stats": "role", "name": "player",
    "year": "season", "ground": "venue", "stadium": "venue", "against": "opposition", "vs": "opposition",
    "team1": "team", "team2": "opposition", "n": "limit", "top": "limit", "top_n": "limit", "sql": "query",
}


def _clean_args(name: str, args: dict) -> dict:
    """Map alias argument names, drop empty values and anything the tool
    doesn't accept (so a stray argument can't crash the call)."""
    schema = next((t for t in ALL_TOOLS if t["function"]["name"] == name), None)
    if schema is None:
        return args
    allowed = schema["function"]["parameters"]["properties"]
    out = {}
    for k, v in (args or {}).items():
        key = k if k in allowed else _ARG_ALIASES.get(k, k)
        if key not in allowed or v is None or v == "" or (isinstance(v, str) and v.lower() in ("null", "none")):
            continue
        if isinstance(v, str) and v.strip().lower() in ("all", "both", "any", "n/a", "overall"):
            # "all" means "no filter" -- except gender, where it's meaningful.
            if key == "gender":
                out[key] = "all"
            continue
        out.setdefault(key, v)
    return out


# ---------------------------------------------------------------------------
# Result handling
# ---------------------------------------------------------------------------

def _is_table(output) -> bool:
    return isinstance(output, dict) and isinstance(output.get("columns"), list) and isinstance(output.get("rows"), list)


def _for_model(output, table_id: str | None):
    """Compact, model-friendly view of a tool result: rows as records (far
    less error-prone for a small model to read than parallel arrays)."""
    if not _is_table(output):
        return output
    cols = output["columns"]
    rows = output["rows"]
    view = {"table_id": table_id}
    for k in ("title", "filters", "highlights", "notes", "error"):
        if output.get(k):
            view[k] = output[k]
    view["rows"] = [dict(zip(cols, r)) for r in rows[:MAX_ROWS_TO_MODEL]]
    if len(rows) > MAX_ROWS_TO_MODEL:
        view["rows_not_shown"] = len(rows) - MAX_ROWS_TO_MODEL
    if not rows:
        view["rows"] = []
        view["empty"] = True
    for k in ("recent_results", "player"):
        if output.get(k):
            view[k] = output[k]
    return view


def _build_chart(args: dict, tables: dict) -> dict:
    table_id = str(args.get("table_id", "")).strip().upper()
    table = tables.get(table_id)
    if table is None:
        return {"error": f"Unknown table_id '{args.get('table_id')}'. Available: {', '.join(tables) or 'none yet'}."}
    cols = table["columns"]
    x = args.get("x")
    ys = args.get("y") or []
    if isinstance(ys, str):
        ys = [ys]
    if x not in cols:
        return {"error": f"Column '{x}' not in {table_id}. Columns: {cols}"}
    missing = [y for y in ys if y not in cols]
    if missing or not ys:
        return {"error": f"y column(s) {missing or ys} not in {table_id}. Columns: {cols}"}
    xi = cols.index(x)
    labels = [str(r[xi]) for r in table["rows"]]
    series = []
    for y in ys:
        yi = cols.index(y)
        values = [r[yi] if isinstance(r[yi], (int, float)) else None for r in table["rows"]]
        if all(v is None for v in values):
            return {"error": f"Column '{y}' has no numeric values to plot."}
        series.append({"name": y, "values": values})
    chart_type = args.get("type") or ("line" if x in ("season", "year") else "bar")
    return {
        "type": chart_type,
        "title": args.get("title") or table.get("title"),
        "x": labels,
        "y": series[0]["values"],  # single-series shape older frontends understand
        "series": series,
        "x_label": x,
        "y_label": ys[0] if len(ys) == 1 else None,
    }


_THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL)
_DIGIT_RE = re.compile(r"\d")


def _clean_text(text: str | None) -> str:
    return _THINK_RE.sub("", text or "").strip()


def _sanitize_history(history: list | None) -> list[dict]:
    out = []
    for m in (history or [])[-MAX_HISTORY_MESSAGES:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            out.append({"role": m["role"], "content": m["content"][:4000]})
    return out


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

async def _complete(messages: list[dict], with_tools: bool = True):
    kwargs = {"model": MODEL, "messages": messages, "temperature": 0}
    if with_tools:
        kwargs["tools"] = ALL_TOOLS
        kwargs["tool_choice"] = "auto"
    return await client.chat.completions.create(**kwargs)


def _system_prompt() -> str:
    cat = catalog.get_catalog()
    prompt = build_system_prompt(date_min=cat.date_min, date_max=cat.date_max, today=dt.date.today().isoformat())
    if not THINKING:
        prompt += "\n/no_think"
    return prompt


async def run_agent(
    question: str,
    history: list[dict] | None = None,
    request_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    try:
        system = await asyncio.to_thread(_system_prompt)
    except Exception as e:  # noqa: BLE001 - e.g. database missing
        yield {"type": "error", "content": f"Couldn't open the cricket database: {e}"}
        return

    messages = [{"role": "system", "content": system}]
    messages.extend(_sanitize_history(history))
    messages.append({"role": "user", "content": question})

    tables: dict[str, dict] = {}
    seen_calls: dict[str, object] = {}
    data_calls = 0
    nudged = False

    for _round in range(MAX_TOOL_ROUNDS):
        if cancellation.is_cancelled(request_id):
            yield {"type": "error", "content": "Cancelled by user."}
            return

        try:
            response = await _complete(messages)
        except Exception as e:  # noqa: BLE001
            yield {"type": "error", "content": f"LLM request failed: {e}"}
            return

        message = response.choices[0].message
        content = _clean_text(message.content)
        tool_calls = message.tool_calls or []

        if not tool_calls:
            # Plain-text answer. If it quotes numbers without a single data
            # lookup behind it, it's from the model's memory -- push back once.
            if data_calls == 0 and not nudged and _DIGIT_RE.search(content):
                nudged = True
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": (
                    "Don't answer from memory. Use the stats tools to get these numbers from the database, "
                    "then call final_answer.")})
                yield {"type": "self_correction", "content": "Answer wasn't based on the data -- querying the database instead."}
                continue
            yield {"type": "final_answer", "content": content, "chart_data": None, "table_data": None}
            return

        if content:
            yield {"type": "thought", "content": content}

        assistant_msg = {"role": "assistant", "content": content or None, "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
            for tc in tool_calls
        ]}
        messages.append(assistant_msg)

        final_args = None
        other_calls = [tc for tc in tool_calls if tc.function.name != "final_answer"]

        for tc in tool_calls:
            if cancellation.is_cancelled(request_id):
                yield {"type": "error", "content": "Cancelled by user."}
                return

            name = tc.function.name
            try:
                raw_args = json.loads(tc.function.arguments or "{}")
                if not isinstance(raw_args, dict):
                    raw_args = {}
            except json.JSONDecodeError:
                raw_args = {}

            if name == "final_answer":
                if other_calls:
                    # Answer written before the results it depends on existed.
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(
                        {"error": "Ignored: call final_answer only after you've seen the tool results."})})
                else:
                    final_args = raw_args
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "{}"})
                continue

            args = _clean_args(name, raw_args)
            yield {"type": "tool_call", "tool": name, "input": args}

            call_key = name + json.dumps(args, sort_keys=True, default=str)
            table_id = None
            if call_key in seen_calls and name != "plot_chart":
                output = seen_calls[call_key]
                model_view = {"note": "You already made this exact call; the result is unchanged. "
                                      "Use it (or change the arguments).",
                              "result": output}
            elif name == "plot_chart":
                output = _build_chart(args, tables)
                model_view = output if "error" in output else {"status": "chart shown to the user"}
            elif name in TOOL_IMPLS:
                impl = TOOL_IMPLS[name]
                try:
                    output = await asyncio.wait_for(asyncio.to_thread(lambda: impl(**args)),
                                                    timeout=TOOL_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    output = {"error": f"'{name}' timed out after {TOOL_TIMEOUT_SECONDS:.0f}s -- narrow the query."}
                except TypeError as e:
                    output = {"error": f"Invalid arguments for '{name}': {e}"}
                except Exception as e:  # noqa: BLE001 - surface to the model so it can retry
                    output = {"error": f"'{name}' failed: {e}"}
                seen_calls[call_key] = output
                if name != "lookup":
                    data_calls += 1
                if _is_table(output) and "error" not in output and output["rows"]:
                    table_id = f"T{len(tables) + 1}"
                    if name == "run_sql":
                        output = {"title": "Query result", **output}
                    tables[table_id] = output
                model_view = _for_model(output, table_id)
            else:
                output = {"error": f"Unknown tool '{name}'."}
                model_view = output

            if isinstance(output, dict) and output.get("error"):
                yield {"type": "self_correction", "content": f"{name}: {output['error']}"}
            else:
                yield {"type": "tool_result", "tool": name, "output": model_view}
            if table_id:
                yield {"type": "table", "table_id": table_id, "table_data": tables[table_id]}
            if name == "plot_chart" and "error" not in output:
                yield {"type": "chart", "chart_data": output}

            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(model_view, default=str)})

        if final_args is not None:
            answer = _clean_text(final_args.get("answer") or final_args.get("summary") or "")
            if answer:
                yield {"type": "final_answer", "content": answer, "chart_data": None, "table_data": None}
                return

    # Out of steps: answer with what we have rather than failing outright.
    messages.append({"role": "user", "content": (
        "Stop calling tools. Answer the question now using only the tool results above; "
        "say plainly if they aren't enough.")})
    try:
        response = await _complete(messages, with_tools=False)
        answer = _clean_text(response.choices[0].message.content)
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "content": f"LLM request failed: {e}"}
        return
    if answer:
        yield {"type": "final_answer", "content": answer, "chart_data": None, "table_data": None}
    else:
        yield {"type": "error", "content": "The agent couldn't reach an answer within the step limit."}
