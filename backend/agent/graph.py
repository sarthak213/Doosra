"""
The agent's reasoning loop.

Deliberately hand-rolled (rather than a framework's prebuilt ReAct agent) so
every step can be emitted as an event for the frontend's live reasoning
trace, and so the loop itself is easy to explain in an interview.

Uses the OpenAI SDK against any OpenAI-compatible chat completions endpoint,
so the same code runs against Groq (hosted, fast, needs an API key), Ollama
(local, free, needs a machine that can run the model), or LM Studio (local,
free, GUI-based) -- just change environment variables, no code changes.

Configure via environment variables (see .env.example):
    LLM_PROVIDER    -- "groq" | "ollama" | "lmstudio" (default: "groq")
    LLM_BASE_URL    -- override the endpoint directly instead of using a preset
    LLM_MODEL       -- override the model name directly instead of using a preset
    GROQ_API_KEY    -- required only when LLM_PROVIDER=groq

Event types yielded by run_agent():
    {"type": "thought", "content": str}
    {"type": "tool_call", "tool": str, "input": dict}
    {"type": "tool_result", "tool": str, "output": Any}
    {"type": "self_correction", "content": str}
    {"type": "chart", "chart_data": dict}
    {"type": "final_answer", "content": str, "chart_data": dict | None, "table_data": dict | None}
    {"type": "error", "content": str}
"""

import asyncio
import json
import os
from typing import AsyncGenerator

from openai import AsyncOpenAI

from . import cancellation
from . import tools
from . import stats
from .prompts import SYSTEM_PROMPT

# Wall-clock cap on a single tool call (run in a worker thread via
# asyncio.to_thread). DuckDB has no first-class query-cancellation hook and
# Python threads can't be killed outright, so this doesn't stop the
# underlying query -- it stops the agent loop from hanging forever and lets
# the model retry with a narrower query instead.
TOOL_TIMEOUT_SECONDS = 15.0
LLM_TIMEOUT_SECONDS = 30.0

# Presets for each provider. LLM_BASE_URL / LLM_MODEL env vars override these
# directly if you want a model/endpoint not listed here.
_PROVIDER_PRESETS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.1:8b",
        "api_key_env": None,  # Ollama doesn't check the key, any string works
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "model": "local-model",  # LM Studio ignores this if only one model is loaded
        "api_key_env": None,
    },
}

_provider = os.environ.get("LLM_PROVIDER", "groq").lower()
_preset = _PROVIDER_PRESETS.get(_provider, _PROVIDER_PRESETS["groq"])

BASE_URL = os.environ.get("LLM_BASE_URL", _preset["base_url"])
MODEL = os.environ.get("LLM_MODEL", _preset["model"])
MAX_TOOL_ROUNDS = 10

# api_key falls back to a placeholder so the module can be imported (and the
# rest of the API can boot) even before a real key is set; the real error
# surfaces as a clean {"type": "error", ...} event on first actual query.
# Local providers (Ollama/LM Studio) don't check the key at all.
_api_key_env = _preset["api_key_env"]
_api_key = os.environ.get(_api_key_env, "unset") if _api_key_env else "not-needed"

client = AsyncOpenAI(base_url=BASE_URL, api_key=_api_key, timeout=LLM_TIMEOUT_SECONDS)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": "Get the database schema (tables and columns).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_player",
            "description": "Fuzzy-match a player name to the exact spelling used in the database. Call this before filtering SQL on a player name.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_tournament",
            "description": "Resolve a tournament/competition name (including abbreviations like 'IPL' or 'BBL') to the exact event_name string used in the database. ALWAYS call this before filtering matches.event_name -- guessing the string wrong silently returns zero rows rather than an error.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Execute a read-only SELECT query against the cricket database. Prefer get_batting_stats/get_bowling_stats/compare_players for standard player statistics -- only use run_sql for questions those tools don't cover (team records, venue records, custom aggregations, etc.).",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_batting_stats",
            "description": "Correct batting figures for one player: runs, balls faced, dismissals, average, strike rate, fours, sixes, matches. Uses the correct dismissal-counting formula (via player_dismissed, not batter+is_wicket) which naive SQL gets wrong for run-outs. Player name must already be resolved via search_player; tournament must already be resolved via search_tournament if given.",
            "parameters": {
                "type": "object",
                "properties": {
                    "player": {"type": "string", "description": "Exact player name as it appears in the database."},
                    "tournament": {"type": "string", "description": "Optional. Exact event_name, resolved via search_tournament first."},
                    "match_type": {"type": "string", "description": "Optional. Exact format: Test, ODI, T20, IT20, etc."},
                    "gender": {"type": "string", "description": "Optional. 'male' or 'female'. The database mixes men's and women's cricket -- set this unless the question is explicitly about both."},
                    "min_over": {"type": "integer", "description": "Optional. Filter to deliveries at or after this over (0-indexed) -- e.g. use for powerplay/death-overs analysis."},
                    "max_over": {"type": "integer", "description": "Optional. Filter to deliveries at or before this over (0-indexed)."},
                },
                "required": ["player"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bowling_stats",
            "description": "Correct bowling figures for one player: wickets, runs conceded, balls bowled, economy, average, strike rate, matches. Correctly excludes byes/leg-byes from runs conceded and run-outs from wickets credited. Player name must already be resolved via search_player; tournament must already be resolved via search_tournament if given.",
            "parameters": {
                "type": "object",
                "properties": {
                    "player": {"type": "string", "description": "Exact player name as it appears in the database."},
                    "tournament": {"type": "string", "description": "Optional. Exact event_name, resolved via search_tournament first."},
                    "match_type": {"type": "string", "description": "Optional. Exact format: Test, ODI, T20, IT20, etc."},
                    "gender": {"type": "string", "description": "Optional. 'male' or 'female'. The database mixes men's and women's cricket -- set this unless the question is explicitly about both."},
                    "min_over": {"type": "integer", "description": "Optional. Filter to deliveries at or after this over (0-indexed)."},
                    "max_over": {"type": "integer", "description": "Optional. Filter to deliveries at or before this over (0-indexed)."},
                },
                "required": ["player"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_players",
            "description": "Computes one metric across multiple players at once, ready to hand to plot_chart. Use this whenever the question asks to compare two or more players -- don't call get_batting_stats/get_bowling_stats separately and manually assemble the comparison yourself.",
            "parameters": {
                "type": "object",
                "properties": {
                    "players": {"type": "array", "items": {"type": "string"}, "description": "Exact player names, already resolved via search_player."},
                    "metric": {"type": "string", "description": "One of: runs, average, strike_rate, fours, sixes, matches, balls_faced, dismissals (batting) OR wickets, economy, runs_conceded, balls_bowled, overs, average, strike_rate (bowling)."},
                    "stat_type": {"type": "string", "description": "'batting' or 'bowling' -- required since 'average'/'strike_rate' mean different things for each."},
                    "tournament": {"type": "string", "description": "Optional. Exact event_name, resolved via search_tournament first."},
                    "match_type": {"type": "string", "description": "Optional. Exact format: Test, ODI, T20, IT20, etc."},
                    "gender": {"type": "string", "description": "Optional. 'male' or 'female'. The database mixes men's and women's cricket -- set this unless the question is explicitly about both."},
                },
                "required": ["players", "metric", "stat_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fielding_stats",
            "description": "Fielding figures for one player: catches, stumpings, run-outs effected, matches. Player name must already be resolved via search_player.",
            "parameters": {
                "type": "object",
                "properties": {
                    "player": {"type": "string", "description": "Exact player name as it appears in the database."},
                    "tournament": {"type": "string", "description": "Optional. Exact event_name, resolved via search_tournament first."},
                    "match_type": {"type": "string", "description": "Optional. Exact format: Test, ODI, T20, IT20, etc."},
                    "gender": {"type": "string", "description": "Optional. 'male' or 'female'."},
                },
                "required": ["player"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_head_to_head",
            "description": "Head-to-head record between two teams: matches played, each team's wins, draws/no-results, last meeting. Use exact team names as stored in the database (e.g. 'India', 'Australia') -- resolve them with run_sql on DISTINCT team if unsure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "team1": {"type": "string", "description": "Exact team name as in the database."},
                    "team2": {"type": "string", "description": "Exact team name as in the database."},
                    "tournament": {"type": "string", "description": "Optional. Exact event_name, resolved via search_tournament first."},
                    "match_type": {"type": "string", "description": "Optional. Exact format: Test, ODI, T20, IT20, etc."},
                    "gender": {"type": "string", "description": "Optional. 'male' or 'female'."},
                },
                "required": ["team1", "team2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_venue_stats",
            "description": "Venue record: matches hosted, highest innings total with team, average first-innings total. Use the exact venue string as stored in the database -- resolve it first with run_sql on DISTINCT venue if unsure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "venue": {"type": "string", "description": "Exact venue name as in the database."},
                    "tournament": {"type": "string", "description": "Optional. Exact event_name, resolved via search_tournament first."},
                    "match_type": {"type": "string", "description": "Optional. Exact format: Test, ODI, T20, IT20, etc."},
                    "gender": {"type": "string", "description": "Optional. 'male' or 'female'."},
                },
                "required": ["venue"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_season_trend",
            "description": "Per-season rows for one player (batting or bowling), ready to hand to plot_chart as a line chart. Returns season + the same correct figures as get_batting_stats/get_bowling_stats, grouped by season.",
            "parameters": {
                "type": "object",
                "properties": {
                    "player": {"type": "string", "description": "Exact player name as it appears in the database."},
                    "stat_type": {"type": "string", "description": "'batting' or 'bowling'."},
                    "tournament": {"type": "string", "description": "Optional. Exact event_name, resolved via search_tournament first."},
                    "match_type": {"type": "string", "description": "Optional. Exact format: Test, ODI, T20, IT20, etc."},
                    "gender": {"type": "string", "description": "Optional. 'male' or 'female'."},
                },
                "required": ["player", "stat_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_matchup",
            "description": "Batter-vs-bowler matchup: every delivery where this exact pair faced each other -- runs off the bat, balls faced, strike rate, dismissals, wickets, dot balls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "batter": {"type": "string", "description": "Exact player name as it appears in the database."},
                    "bowler": {"type": "string", "description": "Exact player name as it appears in the database."},
                    "tournament": {"type": "string", "description": "Optional. Exact event_name, resolved via search_tournament first."},
                    "match_type": {"type": "string", "description": "Optional. Exact format: Test, ODI, T20, IT20, etc."},
                    "gender": {"type": "string", "description": "Optional. 'male' or 'female'."},
                },
                "required": ["batter", "bowler"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plot_chart",
            "description": "Render a chart inline in the conversation. Call this whenever the question involves a comparison or a trend -- you can call it multiple times for multiple charts, and it renders immediately rather than waiting for your final answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "'bar' for comparisons across categories, 'line' for a trend over time/overs."},
                    "x": {"type": "array", "items": {"type": "string"}, "description": "Category/x-axis labels."},
                    "y": {"type": "array", "items": {"type": "number"}, "description": "Values, same length and order as x."},
                    "x_label": {"type": "string"},
                    "y_label": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["type", "x", "y"],
            },
        },
    },
]

TOOL_IMPLS = {
    "get_schema": lambda **kwargs: tools.get_schema(),
    "search_player": lambda **kwargs: tools.search_player(kwargs["name"]),
    "search_tournament": lambda **kwargs: tools.search_tournament(kwargs["name"]),
    "run_sql": lambda **kwargs: tools.run_sql(kwargs["query"]),
    "get_batting_stats": lambda **kwargs: stats.get_batting_stats(**kwargs),
    "get_bowling_stats": lambda **kwargs: stats.get_bowling_stats(**kwargs),
    "compare_players": lambda **kwargs: stats.compare_players(**kwargs),
    "get_fielding_stats": lambda **kwargs: stats.get_fielding_stats(**kwargs),
    "get_head_to_head": lambda **kwargs: stats.get_head_to_head(**kwargs),
    "get_venue_stats": lambda **kwargs: stats.get_venue_stats(**kwargs),
    "get_season_trend": lambda **kwargs: stats.get_season_trend(**kwargs),
    "get_matchup": lambda **kwargs: stats.get_matchup(**kwargs),
    # plot_chart's "implementation" is just echoing the spec back as
    # confirmation -- its real effect is the separate `chart` event emitted
    # in run_agent() below, which is what the frontend actually renders.
    "plot_chart": lambda **kwargs: {"status": "chart rendered"},
}

# The final_answer function isn't a "real" tool — it's how we ask the model
# to hand back a structured payload instead of free text, so the frontend
# gets clean chart/table data rather than having to parse it out of prose.
FINAL_ANSWER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "final_answer",
        "description": "Call this when you have enough information to answer the user's question.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Natural language answer to the user's question."},
                "chart_data": {
                    "type": "object",
                    "description": "Optional. Data for a chart if the result is naturally visual, e.g. {\"type\": \"bar\", \"x\": [...], \"y\": [...], \"x_label\": \"...\", \"y_label\": \"...\"}",
                },
                "table_data": {
                    "type": "object",
                    "description": "Optional. {\"columns\": [...], \"rows\": [[...], ...]} for tabular results worth showing raw.",
                },
            },
            "required": ["summary"],
        },
    },
}


async def run_agent(
    question: str,
    history: list[dict] | None = None,
    request_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        # Only plain user/assistant turns are accepted here (see main.py) --
        # never replay raw tool-call messages from a prior turn, which keeps
        # the payload small and avoids re-triggering tool-call parsing edge
        # cases across turns. Cap to the most recent turns to bound context.
        messages.extend(history[-20:])
    messages.append({"role": "user", "content": question})

    for round_num in range(MAX_TOOL_ROUNDS):
        if cancellation.is_cancelled(request_id):
            yield {"type": "error", "content": "Cancelled by user."}
            return

        try:
            response = await client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS + [FINAL_ANSWER_SCHEMA],
                tool_choice="auto",
                temperature=0,
            )
        except Exception as e:  # noqa: BLE001
            yield {"type": "error", "content": f"LLM request failed: {e}"}
            return

        message = response.choices[0].message

        if message.content:
            yield {"type": "thought", "content": message.content}

        if not message.tool_calls:
            # Model returned plain text with no tool call at all — treat it
            # as the final answer so the loop doesn't stall.
            yield {"type": "final_answer", "content": message.content or "", "chart_data": None, "table_data": None}
            return

        messages.append(message.model_dump(exclude_unset=True))

        for tool_call in message.tool_calls:
            if cancellation.is_cancelled(request_id):
                yield {"type": "error", "content": "Cancelled by user."}
                return

            name = tool_call.function.name
            try:
                args = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "final_answer":
                yield {
                    "type": "final_answer",
                    "content": args.get("summary", ""),
                    "chart_data": args.get("chart_data"),
                    "table_data": args.get("table_data"),
                }
                return

            yield {"type": "tool_call", "tool": name, "input": args}

            impl = TOOL_IMPLS.get(name)
            if impl is None:
                output = {"error": f"Unknown tool '{name}'"}
            else:
                try:
                    output = await asyncio.wait_for(
                        asyncio.to_thread(lambda: impl(**args)),
                        timeout=TOOL_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    output = {"error": f"Tool '{name}' timed out after {TOOL_TIMEOUT_SECONDS}s."}
                except TypeError as e:
                    output = {"error": f"Invalid arguments for '{name}': {e}"}
                except Exception as e:  # noqa: BLE001 - surface to the model so it can retry
                    output = {"error": f"Tool '{name}' raised an exception: {e}"}

            # Surface the "agent catching its own mistake" moment distinctly
            # so the UI can style it differently from a normal tool result.
            if isinstance(output, dict) and output.get("error"):
                yield {"type": "self_correction", "content": f"{name} failed: {output['error']}. Retrying with a corrected approach."}
            else:
                yield {"type": "tool_result", "tool": name, "output": output}

            if name == "plot_chart" and not (isinstance(output, dict) and output.get("error")):
                yield {"type": "chart", "chart_data": args}

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(output, default=str),
            })

    yield {"type": "error", "content": "Agent did not reach a final answer within the step limit."}
