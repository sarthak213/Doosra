"""
FastAPI backend for the cricket agent.

Run with:
    uvicorn main:app --reload --port 8000

Endpoints:
    GET  /health
    GET  /stats                            -- dataset counts for the frontend masthead
    GET  /players?search=<query>          -- autocomplete helper for the frontend
    GET  /query/stream?q=<question>        -- SSE stream of agent reasoning + final answer
    POST /query/cancel/<request_id>        -- request cancellation of an in-flight query
"""

import json
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

load_dotenv()  # reads .env in the working directory, if present

from agent import cancellation, tools
from agent.graph import run_agent

@asynccontextmanager
async def lifespan(app: FastAPI):
    tools.warm_name_caches()
    yield


app = FastAPI(title="Cricket Agent API", lifespan=lifespan)

_cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple in-memory sliding-window rate limiter for the expensive /query/stream
# route. Fine for a single-process dev/demo deployment; a real public
# deployment should replace this with a reverse-proxy or slowapi-based
# limiter that works across multiple worker processes.
_RATE_LIMIT_MAX = 20
_RATE_LIMIT_WINDOW_SECONDS = 300
_request_log: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(client_ip: str):
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW_SECONDS
    log = _request_log[client_ip]
    while log and log[0] < window_start:
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


@app.get("/query/stream")
async def query_stream(request: Request, q: str = Query(..., min_length=1), history: str = Query("[]"), request_id: str = Query("")):
    _check_rate_limit(request.client.host if request.client else "unknown")

    try:
        history_list = json.loads(history)
        if not isinstance(history_list, list):
            history_list = []
    except json.JSONDecodeError:
        history_list = []

    async def event_generator():
        try:
            async for event in run_agent(q, history=history_list, request_id=request_id or None):
                yield f"data: {json.dumps(event, default=str)}\n\n"
            yield "event: done\ndata: {}\n\n"
        finally:
            if request_id:
                cancellation.clear(request_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disables nginx buffering if you deploy behind it
        },
    )


@app.post("/query/cancel/{request_id}")
def cancel_query(request_id: str):
    cancellation.mark_cancelled(request_id)
    return {"status": "cancellation requested"}