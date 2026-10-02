"""
Doosra's tools over MCP (stdio) the way the app's agent shows them to a model, for outside evaluators such as
ToolEval: arguments cleaned like the agent does, tables as records with T1, T2... ids, plot_chart and open_in_app
answered as the app answers them. Doosra's own MCP server (python -m mcp_server) returns the raw results instead.

    python ml/toolcall/eval_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

_stdout, sys.stdout = sys.stdout, sys.stderr          # keep stdout for the protocol while Doosra loads
import mcp.types as types  # noqa: E402
from mcp.server.lowlevel import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from agent import graph  # noqa: E402
from mcp_server.server import mcp as doosra  # noqa: E402
sys.stdout = _stdout

server = Server("doosra-agent-view")
STATE: dict = {"inner": None, "tools": [], "schemas": {}, "tables": {}}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return STATE["tools"]


@server.call_tool(validate_input=False)          # the agent's own cleaning runs instead, as in the app
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    args = graph._clean_args(name, arguments or {}, STATE["schemas"].get(name))
    tables = STATE["tables"]
    if name == "plot_chart":
        out = graph._build_chart(args, tables)
        view = out if "error" in out else {"status": "chart shown to the user"}
    elif name == "open_in_app":
        view = {"status": f"opened the {args.get('view')} view"} if args.get("view") in graph.APP_VIEWS else \
            {"error": f"view must be one of: {', '.join(graph.APP_VIEWS)}"}
    else:
        try:
            output = await graph._call_mcp(STATE["inner"], name, args)
        except Exception as e:  # noqa: BLE001
            output = {"error": f"'{name}' failed: {e}"}
        ids = []
        if not (isinstance(output, dict) and output.get("error")):
            for t in graph._nested_tables(output):
                tid = f"T{len(tables) + 1}"
                tables[tid] = t
                ids.append(tid)
        view = graph._for_model(name, output, ids)
    return [types.TextContent(type="text", text=json.dumps(view, default=str))]


async def main() -> None:
    async with create_connected_server_and_client_session(doosra) as inner:
        STATE["inner"] = inner
        mcp_tools = (await inner.list_tools()).tools
        local = [types.Tool(name=t["function"]["name"], description=t["function"]["description"],
                            inputSchema=t["function"]["parameters"]) for t in graph.LOCAL_TOOLS]
        STATE["tools"] = list(mcp_tools) + local
        STATE["schemas"] = {t.name: t.inputSchema for t in STATE["tools"]}
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
