"""
The copilot agent: a LangGraph state machine over Doosra's MCP tools.

    START -> agent --(tool calls)--> tools --> agent ... --> END
               |                       \\
               |                        `-(step limit)--> wrap_up --> END
               `--(plain text quoting numbers, no lookup yet)--> agent (once)

* Tools come from the MCP server (mcp_server/server.py) through an
  in-memory MCP client session -- the same tool definitions Claude Desktop
  or any other MCP client sees. Three app-only tools are added here:
  plot_chart (draw from a result table), open_in_app (drive the UI) and
  final_answer.
* The model is any OpenAI-compatible endpoint (LM Studio, Ollama, Groq).
* Grounding guards: tables go to the UI straight from tool output; charts
  are built from those tables by id, never from numbers the model typed;
  an answer quoting numbers with no data lookup is sent back once; a
  final_answer issued before the results it depends on is ignored; the step
  limit ends with a best-effort answer instead of an error.
* Copilot context: the UI can send what the user is looking at (view,
  filters, visible data); the agent answers questions about it from that.

Configuration (see .env.example): LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL,
LLM_TIMEOUT_SECONDS, LLM_THINKING, GROQ_API_KEY.

Event types streamed by run_agent():
    thought, tool_call, tool_result, self_correction, table, chart,
    ui_action, final_answer, error
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import operator
import os
import re
from typing import Annotated, AsyncGenerator, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from mcp.shared.memory import create_connected_server_and_client_session
from openai import AsyncOpenAI

from analytics import catalog
from analytics.results import is_table, to_records
from analytics.scope import FILTER_ALIASES, FILTER_ARGS

from . import cancellation
from .prompts import INSIGHT_RECIPE, build_system_prompt

TOOL_TIMEOUT_SECONDS = 30.0
MAX_TOOL_ROUNDS = 8
MAX_ROWS_TO_MODEL = 25
MAX_HISTORY_MESSAGES = 8
MAX_CONTEXT_CHARS = 6000
MAX_PROJECT_CHARS = 8000
MAX_BOARD_CONTEXT_CHARS = 9000

_PROVIDER_PRESETS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile",
             "api_key_env": "GROQ_API_KEY", "timeout": 60.0},
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "qwen3:14b", "api_key_env": None, "timeout": 180.0},
    "lmstudio": {"base_url": "http://localhost:1234/v1", "model": "local-model", "api_key_env": None, "timeout": 180.0},
}

_provider = os.environ.get("LLM_PROVIDER", "groq").lower()
_preset = _PROVIDER_PRESETS.get(_provider, _PROVIDER_PRESETS["groq"])

BASE_URL = os.environ.get("LLM_BASE_URL", _preset["base_url"])
MODEL = os.environ.get("LLM_MODEL", _preset["model"])
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", _preset["timeout"]))
THINKING = os.environ.get("LLM_THINKING", "on").lower() not in ("off", "0", "false", "no")
_api_key_env = _preset["api_key_env"]
_api_key = os.environ.get(_api_key_env, "unset") if _api_key_env else "not-needed"

# One retry: a local model that timed out once will usually time out again.
client = AsyncOpenAI(base_url=BASE_URL, api_key=_api_key, timeout=LLM_TIMEOUT_SECONDS, max_retries=1)


# ---------------------------------------------------------------------------
# Tools: MCP tools + app-only tools
# ---------------------------------------------------------------------------

def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


LOCAL_TOOLS = [
    _fn("plot_chart",
        "Draw a chart from a result table by its table_id (e.g. 'T1'): line for trends over time, bar for "
        "comparisons. The chart uses the table's real values.",
        {"table_id": {"type": "string"},
         "x": {"type": "string", "description": "Column for the x-axis, e.g. 'season', 'innings_no', 'player'."},
         "y": {"type": "array", "items": {"type": "string"}, "description": "Numeric column(s) to plot."},
         "type": {"type": "string", "enum": ["line", "bar"]},
         "title": {"type": "string"}},
        ["table_id", "x", "y"]),
    _fn("open_in_app",
        "Open a view in the app with its settings filled in, when the user asks to 'show', 'open' or 'set up' "
        "something. view: 'query' (Query Builder -- role, metrics, sort_by, split_by, min_balls, filters), "
        "'matrix' (role, x, y, filters), 'player' (player, filters), 'compare' (players, role, filters).",
        {"view": {"type": "string", "enum": ["query", "matrix", "player", "compare"]},
         "state": {"type": "object", "description": "Settings for the view, using the same names as the tools."}},
        ["view", "state"]),
]
NOTE_TOOL = _fn("read_project_note",
                "Read one of the user's project notes in full, by its title (or a distinctive part of it). Use it "
                "when a listed note looks relevant to the question. Notes are background, never data.",
                {"title": {"type": "string"}}, ["title"])
FINAL_ANSWER = _fn("final_answer", "Give the user your answer once you have the data. Every number must come "
                   "from tool results.",
                   {"answer": {"type": "string", "description": "Markdown: direct answer first, then brief support."}},
                   ["answer"])
APP_VIEWS = ("query", "matrix", "player", "compare")


def _strip_titles(schema):
    """Drop pydantic 'title' noise from JSON schemas to save prompt tokens."""
    if isinstance(schema, dict):
        return {k: _strip_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_strip_titles(v) for v in schema]
    return schema


def mcp_tools_to_openai(mcp_tools) -> list[dict]:
    """MCP tool listings -> OpenAI function schemas. The long filters
    description is documented once in the system prompt instead."""
    out = []
    for t in mcp_tools:
        params = _strip_titles(t.inputSchema)
        if "filters" in params.get("properties", {}):
            params["properties"]["filters"] = {"type": "object", "description": "Optional filters (see system prompt)."}
        out.append({"type": "function", "function": {"name": t.name, "description": t.description or "",
                                                      "parameters": params}})
    return out


_ARG_ALIASES = {"name": "player", "stat_type": "role", "n": "limit", "top": "limit", "top_n": "limit",
                "sql": "query", "sort_by": "metric"}


def _clean_args(name: str, args: dict, schema: dict | None) -> dict:
    """Make a model's arguments fit the tool: drop empties, map common alias
    names, and move filter keys given at the top level into `filters`."""
    props = (schema or {}).get("properties", {})
    filters = dict(args["filters"]) if isinstance(args.get("filters"), dict) else {}
    out = {}
    for k, v in (args or {}).items():
        if k == "filters" or v is None or v == "" or (isinstance(v, str) and v.lower() in ("null", "none")):
            continue
        key = k if k in props else _ARG_ALIASES.get(k, k)
        if key in props:
            out.setdefault(key, v)
        elif "filters" in props and (k in FILTER_ARGS or k in FILTER_ALIASES):
            filters.setdefault(k, v)
    if filters and "filters" in props:
        out["filters"] = filters
    return out


# ---------------------------------------------------------------------------
# Result handling
# ---------------------------------------------------------------------------

def _nested_tables(output) -> list[dict]:
    """Tables inside a result: the result itself, or player_profile's
    per-discipline summary/by-format tables."""
    if is_table(output):
        return [output] if output["rows"] else []
    found = []
    if isinstance(output, dict):
        for role in ("batting", "bowling"):
            part = output.get(role)
            if isinstance(part, dict):
                found += [t for t in (part.get("summary"), part.get("by_format")) if is_table(t) and t["rows"]]
    return found


def _for_model(name: str, output, table_ids: list[str]):
    if is_table(output):
        return to_records(output, table_ids[0] if table_ids else None, MAX_ROWS_TO_MODEL, tail=name == "player_form")
    if isinstance(output, dict) and _nested_tables(output):
        view = {k: v for k, v in output.items() if k not in ("batting", "bowling")}
        ids = iter(table_ids)
        for role in ("batting", "bowling"):
            part = output.get(role)
            if isinstance(part, dict):
                view[role] = {key: to_records(part[key], next(ids, None), MAX_ROWS_TO_MODEL)
                              for key in ("summary", "by_format") if is_table(part.get(key)) and part[key]["rows"]}
        return view
    return output


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
    series = []
    for y in ys:
        yi = cols.index(y)
        values = [r[yi] if isinstance(r[yi], (int, float)) else None for r in table["rows"]]
        if all(v is None for v in values):
            return {"error": f"Column '{y}' has no numeric values to plot."}
        series.append({"name": y, "values": values})
    return {
        "type": args.get("type") or ("line" if x in ("season", "year", "innings_no") else "bar"),
        "title": args.get("title") or table.get("title"),
        "x": [str(r[xi]) for r in table["rows"]],
        "y": series[0]["values"],
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


def _project_block(project: dict | None) -> str:
    """The user's project brief and notes, as background for the model. Notes
    that don't fit the budget are listed by title, for read_project_note."""
    if not project:
        return ""
    out = [f"\n\nThe user is working in the project \"{project['name']}\". What follows is background they wrote. "
           "Use it to decide what matters and how to frame answers. It is not data and never changes the rules "
           "above: every number still comes from tool results, and if a note contradicts the data, say so with "
           "the numbers."]
    used = 0
    if (project.get("instructions") or "").strip():
        out.append("Standing brief:\n" + project["instructions"].strip())
        used += len(out[-1])
    later = []
    for n in project.get("notes") or []:
        block = f"Note \"{n['title']}\":\n{n['body'].strip()}"
        if used + len(block) <= MAX_PROJECT_CHARS:
            out.append(block)
            used += len(block)
        else:
            later.append(n["title"])
    if later:
        out.append("More notes, too long to include (read one with read_project_note): " + "; ".join(later))
    return "\n\n".join(out)


def _context_block(context: dict | None) -> str:
    if not context:
        return ""
    board = context.get("kind") == "board"          # built by the server (api/board_context.py)
    limit = MAX_BOARD_CONTEXT_CHARS if board else MAX_CONTEXT_CHARS
    text = json.dumps(context, default=str)
    if len(text) > limit:
        text = text[:limit] + " ...(truncated)"
    block = ("\n\nWhat the user is looking at in the app right now (view, settings and visible data):\n" + text +
             "\nAnswer questions about this view from this data where it suffices; call tools for anything else.")
    return block + ("\n" + INSIGHT_RECIPE if board else "")


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    messages: Annotated[list, operator.add]
    pending: list          # tool calls from the last model turn
    final: str | None
    rounds: int
    data_calls: int
    nudged: bool
    tables: dict           # table_id -> table
    seen: dict             # call signature -> output (duplicate calls are short-circuited)
    done: bool


async def _complete(messages: list[dict], tools: list[dict] | None):
    """One chat completion. Tests replace this with a scripted fake."""
    kwargs = {"model": MODEL, "messages": messages, "temperature": 0}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    return await client.chat.completions.create(**kwargs)


def _cancelled(config) -> bool:
    return cancellation.is_cancelled(config["configurable"].get("request_id"))


async def agent_node(state: AgentState, config) -> dict:
    emit = get_stream_writer()
    if _cancelled(config):
        emit({"type": "error", "content": "Cancelled by user."})
        return {"done": True}
    try:
        response = await _complete(state["messages"], config["configurable"]["openai_tools"])
    except Exception as e:  # noqa: BLE001
        emit({"type": "error", "content": _llm_error(e)})
        return {"done": True}

    message = response.choices[0].message
    content = _clean_text(message.content)
    calls = message.tool_calls or []
    rounds = state.get("rounds", 0) + 1

    if not calls:
        # Plain text is the answer -- unless it quotes numbers with no lookup
        # behind it. On-screen view data counts as a source (Explain mode).
        has_source = state.get("data_calls", 0) > 0 or config["configurable"].get("has_context")
        if not has_source and not state.get("nudged") and _DIGIT_RE.search(content):
            emit({"type": "self_correction", "content": "Answer wasn't based on the data -- querying the database instead."})
            return {"messages": [{"role": "assistant", "content": content},
                                 {"role": "user", "content": "Don't answer from memory. Use the tools to get these "
                                  "numbers from the database, then call final_answer."}],
                    "nudged": True, "rounds": rounds, "pending": []}
        return {"final": content, "rounds": rounds, "pending": []}

    if content:
        emit({"type": "thought", "content": content})
    assistant_msg = {"role": "assistant", "content": content or None, "tool_calls": [
        {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
        for c in calls]}
    return {"messages": [assistant_msg], "rounds": rounds, "pending": [
        {"id": c.id, "name": c.function.name, "arguments": c.function.arguments or "{}"} for c in calls]}


async def _call_mcp(session, name: str, args: dict):
    res = await asyncio.wait_for(session.call_tool(name, args), timeout=TOOL_TIMEOUT_SECONDS)
    text = res.content[0].text if res.content else "{}"
    try:
        output = json.loads(text)
    except json.JSONDecodeError:
        return {"error": text} if res.isError else {"text": text}
    if res.isError and not (isinstance(output, dict) and "error" in output):
        return {"error": text}
    return output


def view_source(name: str, args: dict) -> dict | None:
    """The saved-view spec that re-runs a tool call, for tools that map 1:1 onto
    the Query Builder, Matrix or Compare (so "add to board" makes a live card)."""
    f = args.get("filters") or {}
    role = args.get("role") or "batting"
    if role not in ("batting", "bowling"):
        return None                      # fielding has no Query Builder view
    if name == "leaderboard" and args.get("metric"):
        # The same columns the leaderboard tool adds, so a replay matches what was shown.
        extras = args.get("extra_metrics") or ["matches", "innings" if role == "batting" else "wickets"]
        metrics = [args["metric"], *[m for m in extras if m != args["metric"]]]
        return {"kind": "query", "state": {"role": role, "metrics": metrics, "sort_by": args["metric"],
                "ascending": args.get("ascending"), "min_balls": args.get("min_balls"),
                "limit": args.get("limit") or 10, "filters": f}}
    if name == "player_stats" and args.get("player"):
        return {"kind": "query", "state": {"role": role, "metrics": args.get("metrics"), "players": [args["player"]],
                "split_by": args.get("split_by"), "limit": 50, "filters": f}}
    if name == "compare_players" and args.get("players"):
        return {"kind": "compare", "state": {"players": args["players"], "role": role,
                "metrics": args.get("metrics"), "filters": f}}
    if name == "player_matrix" and args.get("x") and args.get("y"):
        return {"kind": "matrix", "state": {"role": role, "x": args["x"], "y": args["y"],
                "min_balls": args.get("min_balls"), "highlight": args.get("highlight"), "filters": f}}
    return None


def _read_note(title, notes: dict) -> dict:
    wanted = str(title or "").strip().lower()
    hits = [t for t in notes if wanted and (wanted == t.lower() or wanted in t.lower())]
    if len(hits) == 1 or (hits and wanted == hits[0].lower()):
        return {"title": hits[0], "body": notes[hits[0]][:MAX_PROJECT_CHARS]}
    return {"error": "No single note matches that title. Notes: " + "; ".join(notes) if notes else "No notes."}


def _llm_error(e: Exception) -> str:
    """The model call failed. A too-small context window is the usual cause with local models
    (a board explanation plus project notes needs ~10-12k tokens), so say how to fix that one."""
    text = str(e)
    if "context size" in text or "context length" in text or "maximum context" in text:
        return ("The model's context window is too small for this question (the prompt, project notes and data "
                "don't fit). In LM Studio, reload the model with a larger Context Length -- 16384 or more -- "
                f"and ask again. Details: {text[:300]}")
    return f"LLM request failed: {text}"


async def tools_node(state: AgentState, config) -> dict:
    emit = get_stream_writer()
    cfg = config["configurable"]
    session, schemas = cfg["session"], cfg["schemas"]
    tables = dict(state.get("tables") or {})
    seen = dict(state.get("seen") or {})
    data_calls = state.get("data_calls", 0)
    pending = state.get("pending") or []
    answer_too_early = any(c["name"] != "final_answer" for c in pending)
    new_messages, final = [], None

    for call in pending:
        if _cancelled(config):
            emit({"type": "error", "content": "Cancelled by user."})
            return {"done": True}
        name = call["name"]
        try:
            raw = json.loads(call["arguments"] or "{}")
            raw = raw if isinstance(raw, dict) else {}
        except json.JSONDecodeError:
            raw = {}

        if name == "final_answer":
            if answer_too_early:
                reply = {"error": "Ignored: call final_answer only after you've seen the tool results."}
            else:
                final = _clean_text(raw.get("answer") or raw.get("summary") or "")
                reply = {}
            new_messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(reply)})
            continue

        args = _clean_args(name, raw, schemas.get(name))
        emit({"type": "tool_call", "tool": name, "input": args})
        key = name + json.dumps(args, sort_keys=True, default=str)
        table_ids: list[str] = []

        if name == "plot_chart":
            output = _build_chart(args, tables)
            model_view = output if "error" in output else {"status": "chart shown to the user"}
        elif name == "open_in_app":
            ok = args.get("view") in APP_VIEWS
            output = {"status": f"opened the {args.get('view')} view"} if ok else \
                {"error": f"view must be one of: {', '.join(APP_VIEWS)}"}
            model_view = output
        elif name == "read_project_note":
            output = _read_note(args.get("title"), cfg.get("notes") or {})
            model_view = output
        elif key in seen:
            output = seen[key]
            model_view = {"note": "You already made this exact call; the result is unchanged. Use it.",
                          "result": _for_model(name, output, [])}
        elif name in schemas:
            try:
                output = await _call_mcp(session, name, args)
            except asyncio.TimeoutError:
                output = {"error": f"'{name}' timed out after {TOOL_TIMEOUT_SECONDS:.0f}s -- narrow the query."}
            except Exception as e:  # noqa: BLE001
                output = {"error": f"'{name}' failed: {e}"}
            seen[key] = output
            if name not in ("lookup_entity", "search_metrics"):
                data_calls += 1
            if not (isinstance(output, dict) and output.get("error")):
                for t in _nested_tables(output):
                    tid = f"T{len(tables) + 1}"
                    tables[tid] = t
                    table_ids.append(tid)
            model_view = _for_model(name, output, table_ids)
        else:
            output = model_view = {"error": f"Unknown tool '{name}'."}

        if isinstance(output, dict) and output.get("error"):
            emit({"type": "self_correction", "content": f"{name}: {output['error']}"})
        else:
            emit({"type": "tool_result", "tool": name, "output": model_view})
        source = view_source(name, args) if table_ids else None
        for tid in table_ids:
            emit({"type": "table", "table_id": tid, "table_data": tables[tid],
                  **({"source": source} if source and tid == table_ids[0] else {})})
        if name == "plot_chart" and "error" not in output:
            emit({"type": "chart", "chart_data": output})
        if name == "open_in_app" and "error" not in output:
            emit({"type": "ui_action", "action": "open", "view": args["view"], "state": args.get("state") or {}})
        new_messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(model_view, default=str)})

    update = {"messages": new_messages, "tables": tables, "seen": seen, "data_calls": data_calls, "pending": []}
    if final:
        update["final"] = final
    return update


async def wrap_up_node(state: AgentState, config) -> dict:
    """Out of steps: answer from what we have rather than failing."""
    emit = get_stream_writer()
    messages = state["messages"] + [{"role": "user", "content": (
        "Stop calling tools. Answer the question now using only the tool results above; say plainly if "
        "they aren't enough.")}]
    try:
        response = await _complete(messages, None)
        answer = _clean_text(response.choices[0].message.content)
    except Exception as e:  # noqa: BLE001
        emit({"type": "error", "content": _llm_error(e)})
        return {"done": True}
    if not answer:
        emit({"type": "error", "content": "The agent couldn't reach an answer within the step limit."})
        return {"done": True}
    return {"final": answer}


def _after_agent(state: AgentState) -> str:
    if state.get("done") or state.get("final") is not None:
        return END
    return "tools" if state.get("pending") else "agent"


def _after_tools(state: AgentState) -> str:
    if state.get("done") or state.get("final"):
        return END
    return "wrap_up" if state.get("rounds", 0) >= MAX_TOOL_ROUNDS else "agent"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_node("wrap_up", wrap_up_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", _after_agent, ["tools", "agent", END])
    g.add_conditional_edges("tools", _after_tools, ["agent", "wrap_up", END])
    g.add_edge("wrap_up", END)
    return g.compile()


GRAPH = build_graph()


def _system_prompt(context: dict | None, project: dict | None = None) -> str:
    cat = catalog.get_catalog()
    prompt = build_system_prompt(date_min=cat.date_min, date_max=cat.date_max, today=dt.date.today().isoformat())
    prompt += _project_block(project)
    prompt += _context_block(context)
    if not THINKING:
        prompt += "\n/no_think"
    return prompt


async def run_agent(question: str, history: list[dict] | None = None, request_id: str | None = None,
                    context: dict | None = None, project: dict | None = None) -> AsyncGenerator[dict, None]:
    from mcp_server.server import mcp  # late import: the server imports agent.stats

    try:
        system = await asyncio.to_thread(_system_prompt, context, project)
    except Exception as e:  # noqa: BLE001 - e.g. database missing
        yield {"type": "error", "content": f"Couldn't open the cricket database: {e}"}
        return

    async with create_connected_server_and_client_session(mcp) as session:
        listed = await session.list_tools()
        notes = {n['title']: n['body'] for n in (project or {}).get('notes') or []}
        openai_tools = (mcp_tools_to_openai(listed.tools) + LOCAL_TOOLS + ([NOTE_TOOL] if notes else []) + [FINAL_ANSWER])
        schemas = {t["function"]["name"]: t["function"]["parameters"] for t in openai_tools}
        state: AgentState = {
            "messages": [{"role": "system", "content": system}, *_sanitize_history(history),
                         {"role": "user", "content": question}],
            "rounds": 0, "data_calls": 0, "nudged": False, "tables": {}, "seen": {}, "pending": [],
            "final": None, "done": False,
        }
        config = {"configurable": {"session": session, "schemas": schemas, "openai_tools": openai_tools,
                                   "request_id": request_id, "has_context": bool(context), "notes": notes},
                  "recursion_limit": 4 * MAX_TOOL_ROUNDS + 10}
        final = None
        async for mode, chunk in GRAPH.astream(state, config, stream_mode=["custom", "values"]):
            if mode == "custom":
                yield chunk
            else:
                final = chunk.get("final")
        if final:
            yield {"type": "final_answer", "content": final, "chart_data": None, "table_data": None}
