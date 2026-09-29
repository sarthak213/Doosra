"""
REST endpoints for the user's workspace: chats, projects and their notes,
boards, saved views and the watchlist. Everything is scoped to the current
user; a row that belongs to someone else is a 404, same as a missing one.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import routes as core
from . import chat_persist, workspace
from .auth import current_user

router = APIRouter(prefix="/api")

User = Depends(current_user)


def _found(value, what: str = "Not found"):
    if not value:
        raise HTTPException(status_code=404, detail=what)
    return value


# -- projects and notes -------------------------------------------------------

class ProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=4000)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    instructions: str | None = Field(default=None, max_length=4000)


@router.get("/projects")
def list_projects(user: str = User):
    return {"projects": workspace.list_projects(user)}


@router.post("/projects")
def create_project(body: ProjectBody, user: str = User):
    return workspace.create_project(user, body.name.strip(), body.instructions)


@router.get("/projects/{project_id}")
def get_project(project_id: str, user: str = User):
    project = _found(workspace.get_project(user, project_id))
    return {**project, "notes": workspace.list_notes(user, project_id),
            "chats": workspace.list_chats(user, project_id=project_id),
            "boards": [{k: b[k] for k in ("id", "name", "description", "updated")} | {"cards": len(b["cards"])}
                       for b in workspace.list_boards(user, project_id)]}


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectPatch, user: str = User):
    return _found(workspace.update_project(user, project_id, name=body.name, instructions=body.instructions))


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, user: str = User):
    _found(workspace.delete_project(user, project_id))
    return {"status": "deleted"}


@router.get("/projects/{project_id}/export")
def export_project(project_id: str, user: str = User):
    return _found(workspace.export_project(user, project_id))


@router.post("/projects/import")
def import_project(data: dict, user: str = User):
    if data.get("format") != "doosra-project" or not isinstance(data.get("project"), dict) or not data["project"].get("name"):
        raise HTTPException(status_code=400, detail="Not a Doosra project export")
    return workspace.import_project(user, data)


class NoteBody(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(max_length=workspace.MAX_NOTE_CHARS)
    source: str = Field(default="typed", max_length=200)


class NotePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    body: str | None = Field(default=None, max_length=workspace.MAX_NOTE_CHARS)
    enabled: bool | None = None


@router.post("/projects/{project_id}/notes")
def create_note(project_id: str, body: NoteBody, user: str = User):
    return _found(workspace.create_note(user, project_id, body.title.strip(), body.body, body.source), "Project not found")


@router.patch("/notes/{note_id}")
def update_note(note_id: str, body: NotePatch, user: str = User):
    return _found(workspace.update_note(user, note_id, title=body.title, body=body.body, enabled=body.enabled))


@router.delete("/notes/{note_id}")
def delete_note(note_id: str, user: str = User):
    _found(workspace.delete_note(user, note_id))
    return {"status": "deleted"}


# -- chats --------------------------------------------------------------------

class ChatBody(BaseModel):
    title: str = Field(default="New chat", max_length=120)
    project_id: str | None = None
    board_id: str | None = None


class ChatPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    project_id: str | None = None      # with `move`: the destination (null = no project)
    move: bool = False


@router.get("/chats")
def list_chats(project_id: str | None = None, q: str | None = None, unassigned: bool = False, user: str = User):
    return {"chats": workspace.list_chats(user, project_id=project_id, q=q, unassigned=unassigned)}


@router.post("/chats")
def create_chat(body: ChatBody, user: str = User):
    return _found(workspace.create_chat(user, body.title, body.project_id, body.board_id), "Project or board not found")


class LegacyTurns(BaseModel):
    title: str = Field(default="Imported chat", max_length=120)
    turns: list[dict] = Field(max_length=50)


@router.post("/chats/import")
def import_legacy_chat(body: LegacyTurns, user: str = User):
    """One-time move of the old browser-stored conversation (v2.2 and earlier)
    into a saved chat."""
    chat = workspace.create_chat(user, body.title)
    for t in body.turns:
        q = str(t.get("question") or "").strip()
        if not q:
            continue
        events = [e for e in (t.get("steps") or []) if isinstance(e, dict)]
        events += [{"type": "chart", "chart_data": c} for c in (t.get("charts") or [])]
        events += [{"type": "table", "table_id": x.get("id"), "table_data": {k: v for k, v in x.items() if k != "id"}}
                   for x in (t.get("tables") or []) if isinstance(x, dict)]
        if t.get("error"):
            events.append({"type": "error", "content": str(t["error"])})
        workspace.add_message(user, chat["id"], "user", q)
        workspace.add_message(user, chat["id"], "assistant", str(t.get("finalAnswer") or ""), {"events": events})
    return chat


@router.get("/chats/{chat_id}")
def get_chat(chat_id: str, user: str = User):
    chat = _found(workspace.get_chat(user, chat_id))
    return {**chat, "answering": chat_persist.answering(chat_id)}


@router.patch("/chats/{chat_id}")
def update_chat(chat_id: str, body: ChatPatch, user: str = User):
    _found(workspace.get_chat(user, chat_id, messages=False))
    return _found(workspace.update_chat(user, chat_id, body.title, body.project_id, body.move), "Project not found")


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: str, user: str = User):
    _found(workspace.delete_chat(user, chat_id))
    return {"status": "deleted"}


# -- boards -------------------------------------------------------------------

class Card(BaseModel):
    id: str = Field(max_length=40)
    title: str = Field(default="", max_length=160)
    note: str = Field(default="", max_length=2000)
    source: dict
    chart: dict = {}


class BoardBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    project_id: str | None = None
    cards: list[Card] = []


class BoardPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    cards: list[Card] | None = None
    project_id: str | None = None
    move: bool = False


def _cards(cards: list[Card] | None) -> list[dict] | None:
    if cards is None:
        return None
    if len(cards) > 24:
        raise HTTPException(status_code=400, detail="A board holds at most 24 cards")
    for c in cards:
        if c.source.get("kind") not in ("query", "matrix", "compare", "snapshot"):
            raise HTTPException(status_code=400, detail=f"Unknown card source kind: {c.source.get('kind')!r}")
    return [c.model_dump() for c in cards]


@router.get("/boards")
def list_boards(project_id: str | None = None, user: str = User):
    return {"boards": workspace.list_boards(user, project_id)}


@router.post("/boards")
def create_board(body: BoardBody, user: str = User):
    return _found(workspace.create_board(user, body.name.strip(), body.project_id, body.description, _cards(body.cards)),
                  "Project not found")


@router.get("/boards/{board_id}")
def get_board(board_id: str, user: str = User):
    return _found(workspace.get_board(user, board_id))


@router.patch("/boards/{board_id}")
def update_board(board_id: str, body: BoardPatch, user: str = User):
    _found(workspace.get_board(user, board_id))
    return _found(workspace.update_board(user, board_id, body.name, body.description, _cards(body.cards),
                                         body.project_id, body.move), "Project not found")


@router.delete("/boards/{board_id}")
def delete_board(board_id: str, user: str = User):
    _found(workspace.delete_board(user, board_id))
    return {"status": "deleted"}


class RenderBody(BaseModel):
    source: dict


@router.post("/boards/render-card")
def render_card(body: RenderBody, user: str = User):
    """Runs a card's source and returns its result table. Live sources go
    through the same handlers as /api/query, /api/matrix and /api/compare."""
    return render_source(body.source)


def render_source(source: dict) -> Any:
    kind, state = source.get("kind"), source.get("state") or {}
    try:
        if kind == "query":
            return core.query(core.QueryBody(**state))
        if kind == "matrix":
            return core.matrix(core.MatrixBody(**state))
        if kind == "compare":
            out = core.compare(core.CompareBody(**state))
            return out["table"] if isinstance(out, dict) and "table" in out else out
    except ValueError as e:      # a malformed state (pydantic ValidationError)
        raise HTTPException(status_code=400, detail=str(e)) from e
    if kind == "snapshot" and isinstance(source.get("table"), dict):
        return {**source["table"], "snapshot": True, "saved_at": source.get("saved_at")}
    raise HTTPException(status_code=400, detail=f"Unknown card source kind: {kind!r}")


# -- saved views and the watchlist -------------------------------------------

class ViewBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["query", "matrix", "compare", "player"]
    state: dict


@router.get("/views")
def list_views(kind: str | None = None, user: str = User):
    return {"views": workspace.list_views(user, kind)}


@router.post("/views")
def save_view(body: ViewBody, user: str = User):
    return workspace.save_view(user, body.name, body.kind, body.state)


@router.delete("/views/{view_id}")
def delete_view(view_id: str, user: str = User):
    workspace.delete_view(user, view_id)
    return {"status": "deleted"}


class WatchlistBody(BaseModel):
    players: list[str]


@router.get("/watchlist")
def get_watchlist(user: str = User):
    return {"players": workspace.get_watchlist(user)}


@router.put("/watchlist")
def put_watchlist(body: WatchlistBody, user: str = User):
    return {"players": workspace.set_watchlist(user, body.players)}
