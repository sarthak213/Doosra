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

import asyncio
import json
import os
import time
from pathlib import Path
from collections import defaultdict
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

load_dotenv()  # reads .env in the working directory, if present

from agent import cancellation, tools  # noqa: E402
from agent.graph import run_agent  # noqa: E402
from analytics import db  # noqa: E402
from analytics.catalog import ResolutionError  # noqa: E402
from ingest import pull, refresh  # noqa: E402
from api import board_context, workspace  # noqa: E402
from api import auth  # noqa: E402
from api.auth import current_user  # noqa: E402
from api.auth_routes import router as auth_router  # noqa: E402
from api import chat_persist  # noqa: E402
from api.chat_persist import Recorder, history_for_model  # noqa: E402
from api.routes import router as api_router  # noqa: E402
from api.workspace_routes import router as workspace_router  # noqa: E402
from mcp_server.server import mcp  # noqa: E402

# The MCP app serves exactly /mcp (no redirect to /mcp/, which some clients
# won't follow on POST). It's mounted last, at the root, so every API route
# above takes precedence and anything else falls through to it (404).
mcp.settings.streamable_http_path = "/mcp"
_mcp_app = mcp.streamable_http_app()


def serves_ui() -> bool:
    """Serve the built React app from this process: always when hosted, and on request
    (SERVE_FRONTEND=1) for a local container run without sign-in."""
    return auth.hosted() or os.environ.get("SERVE_FRONTEND") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.check_config()
    tools.warm_caches()
    refresh.start()            # a hosted instance follows the published database (DATA_REFRESH_HOURS)
    if serves_ui():            # the MCP endpoint has no sign-in and shares the root path with the app, so it's off here
        yield
    else:
        async with mcp.session_manager.run():
            yield


app = FastAPI(title="Doosra API", lifespan=lifespan)

_cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
    if o.strip()
]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_methods=["*"], allow_headers=["*"],
                   allow_credentials=True)
# Signed, HttpOnly session cookie (only ever issued when hosted with sign-in).
app.add_middleware(SessionMiddleware, secret_key=auth.session_secret(), same_site="lax", max_age=14 * 24 * 3600,
                   https_only=os.environ.get("PUBLIC_URL", "").startswith("https://"))
app.include_router(auth_router)
app.include_router(api_router)
app.include_router(workspace_router)


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
    """Liveness plus which data build is being served."""
    info = None
    try:
        info = pull.local_info(Path(db.DB_PATH))
    except Exception:  # noqa: BLE001 - health must answer even if the database is mid-swap
        pass
    return {"status": "ok", "database": {"built_at": info.get("built_at"), "latest_match": info.get("latest_match_date")} if info else None}


@app.get("/stats")
def stats():
    return tools.get_stats()


@app.get("/players")
def players(search: str = Query(..., min_length=1)):
    return {"matches": tools.search_player(search, limit=10)}


_DONE = object()


def _sse(question: str, history: list, request_id: str | None, context: dict | None = None,
         recorder: Recorder | None = None, project: dict | None = None) -> StreamingResponse:
    queue: asyncio.Queue = asyncio.Queue()

    async def produce():
        try:
            async for event in run_agent(question, history=history, request_id=request_id, context=context,
                                       project=project, deep=recorder is not None):
                if recorder:
                    recorder.see(event)
                queue.put_nowait(event)
        except Exception as e:  # noqa: BLE001 - reported to the page, and saved with the turn
            event = {"type": "error", "content": f"The agent failed: {e}"}
            if recorder:
                recorder.see(event)
            queue.put_nowait(event)
        finally:
            if recorder:
                await asyncio.to_thread(recorder.save)   # also after a Stop, or when nobody is listening any more
                chat_persist.ANSWERING.pop(recorder.chat_id, None)
            if request_id:
                cancellation.clear(request_id)
            queue.put_nowait(_DONE)

    task = asyncio.create_task(produce())
    if recorder:
        chat_persist.ANSWERING[recorder.chat_id] = task

    async def events():
        try:
            while (event := await queue.get()) is not _DONE:
                yield f"data: {json.dumps(event, default=str)}\n\n"
            yield "event: done\ndata: {}\n\n"
        finally:
            if not recorder and not task.done():
                task.cancel()        # the copilot drawer's unsaved chat: nobody will read the answer

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # disables nginx buffering if deployed behind it
    })


@app.get("/query/stream")
async def query_stream(request: Request, q: str = Query(..., min_length=1), history: str = Query("[]"),
                       request_id: str = Query(""), user: str = Depends(auth.charge_question)):
    _check_rate_limit(request.client.host if request.client else "unknown")
    cancellation.set_owner(request_id or None, user)
    try:
        history_list = json.loads(history)
        if not isinstance(history_list, list):
            history_list = []
    except json.JSONDecodeError:
        history_list = []
    return _sse(q, history_list, request_id or None)


class ChatBody(BaseModel):
    question: str
    history: list = []          # used only when there is no chat_id (the copilot drawer)
    request_id: str | None = None
    context: dict | None = None
    chat_id: str | None = None  # a saved chat: history is loaded server-side and the turn is stored


@app.post("/api/chat/stream")
async def chat_stream(request: Request, body: ChatBody, user: str = Depends(auth.charge_question)):
    _check_rate_limit(request.client.host if request.client else "unknown")
    cancellation.set_owner(body.request_id, user)
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="Empty question.")
    if not body.chat_id:
        return _sse(body.question, body.history, body.request_id, body.context)
    chat = workspace.get_chat(user, body.chat_id, messages=False)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chat_persist.answering(body.chat_id):
        raise HTTPException(status_code=409, detail="This chat is still answering the last question.")
    context = body.context
    if chat["board_id"]:
        # An explain chat: the server rebuilds the board's digest from current data each turn. What the
        # client sent about the board is ignored; only which card it points at is taken from it.
        card_id = (body.context or {}).get("card_id") if isinstance(body.context, dict) else None
        built = await asyncio.to_thread(board_context.build, user, chat["board_id"], card_id)
        context = built or body.context
        focus = body.context.get("focus") if isinstance(body.context, dict) else None
        if built and focus:                      # an excerpt the user is asking about (a table or chart in an answer)
            context = {**built, "focus": focus}
    history = history_for_model(user, body.chat_id)
    project = workspace.project_context(user, chat["project_id"])      # its brief and notes, read fresh each turn
    workspace.add_message(user, body.chat_id, "user", body.question)
    return _sse(body.question, history, body.request_id, context, Recorder(user, body.chat_id), project)


@app.post("/query/cancel/{request_id}")
def cancel_query(request_id: str, user: str = Depends(current_user)):
    if not cancellation.may_cancel(request_id, user):
        raise HTTPException(status_code=403, detail="Not your request")
    cancellation.mark_cancelled(request_id)
    return {"status": "cancellation requested"}


class SPAStaticFiles(StaticFiles):
    """The built React app. Paths the app routes itself (/ask/abc, /boards/xyz) fall back to
    index.html; a missing API path stays a 404 rather than an HTML page."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or path.replace(os.sep, "/").split("/")[0] in ("api", "auth", "query", "mcp"):
                raise
            return await super().get_response("index.html", scope)


_dist = Path(os.environ.get("FRONTEND_DIST") or Path(__file__).resolve().parent.parent / "frontend" / "dist")
if not serves_ui():
    app.mount("/", _mcp_app)  # keep last: see the note by _mcp_app
elif _dist.is_dir():
    app.mount("/", SPAStaticFiles(directory=_dist, html=True), name="frontend")   # one origin: no CORS, first-party cookies
