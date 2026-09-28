"""
FastAPI backend for Doosra.

Run with:
    uvicorn main:app --reload --port 8000

Endpoints:
    GET  /health
    GET  /stats                            -- dataset counts for the masthead
    GET  /players?search=<query>           -- autocomplete helper
    GET  /query/stream?q=<question>        -- SSE stream of the copilot (chat page)
    POST /api/chat/stream                  -- same, with the current view as context
    POST /query/cancel/<request_id>        -- cancel an in-flight question
    /api/...                               -- analytics for the UI views (api/routes.py)
    /mcp                                   -- the MCP server over streamable HTTP
"""

import json
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

load_dotenv()  # reads .env in the working directory, if present

from agent import cancellation, tools  # noqa: E402
from agent.graph import run_agent  # noqa: E402
from analytics.catalog import ResolutionError  # noqa: E402
from api.routes import router as api_router  # noqa: E402
from mcp_server.server import mcp  # noqa: E402

# The MCP app serves exactly /mcp (no redirect to /mcp/, which some clients
# won't follow on POST). It's mounted last, at the root, so every API route
# above takes precedence and anything else falls through to it (404).
mcp.settings.streamable_http_path = "/mcp"
_mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    tools.warm_caches()
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Doosra API", lifespan=lifespan)

_cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
    if o.strip()
]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_methods=["*"], allow_headers=["*"])
app.include_router(api_router)


@app.exception_handler(ResolutionError)
async def resolution_error(_request: Request, exc: ResolutionError):
    return JSONResponse(status_code=400, content=exc.to_dict())


# Simple in-memory sliding-window rate limiter for the expensive LLM routes.
# Fine for a single-process deployment; a public one needs a shared limiter.
_RATE_LIMIT_MAX = 20
_RATE_LIMIT_WINDOW_SECONDS = 300
_request_log: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(client_ip: str):
    now = time.monotonic()
    log = _request_log[client_ip]
    while log and log[0] < now - _RATE_LIMIT_WINDOW_SECONDS:
        log.pop(0)
    if len(log) >= _RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Too many requests, please slow down.")
    log.append(now)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats")
def stats():
    return tools.get_stats()


@app.get("/players")
def players(search: str = Query(..., min_length=1)):
    return {"matches": tools.search_player(search, limit=10)}


def _sse(question: str, history: list, request_id: str | None, context: dict | None = None) -> StreamingResponse:
    async def events():
        try:
            async for event in run_agent(question, history=history, request_id=request_id, context=context):
                yield f"data: {json.dumps(event, default=str)}\n\n"
            yield "event: done\ndata: {}\n\n"
        finally:
            if request_id:
                cancellation.clear(request_id)

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # disables nginx buffering if deployed behind it
    })


@app.get("/query/stream")
async def query_stream(request: Request, q: str = Query(..., min_length=1), history: str = Query("[]"),
                       request_id: str = Query("")):
    _check_rate_limit(request.client.host if request.client else "unknown")
    try:
        history_list = json.loads(history)
        if not isinstance(history_list, list):
            history_list = []
    except json.JSONDecodeError:
        history_list = []
    return _sse(q, history_list, request_id or None)


class ChatBody(BaseModel):
    question: str
    history: list = []
    request_id: str | None = None
    context: dict | None = None


@app.post("/api/chat/stream")
async def chat_stream(request: Request, body: ChatBody):
    _check_rate_limit(request.client.host if request.client else "unknown")
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="Empty question.")
    return _sse(body.question, body.history, body.request_id, body.context)


@app.post("/query/cancel/{request_id}")
def cancel_query(request_id: str):
    cancellation.mark_cancelled(request_id)
    return {"status": "cancellation requested"}


app.mount("/", _mcp_app)  # keep last: see the note by _mcp_app
