"""
The user's workspace: chats, projects (with notes), boards, saved views and
the watchlist. SQLite next to the cricket database by default (the cricket DB
itself stays read-only at runtime); Postgres when DATABASE_URL is set.

Every function takes the owning `user` first and filters on it, so one user can
never read or change another's rows. Locally the only user is `local`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from analytics import db
from . import migrations

LOCAL_USER = "local"
MAX_NOTE_CHARS = 20_000
_engines: dict[str, Engine] = {}
_lock = threading.Lock()


def _url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        # Hosts hand out postgres:// or postgresql://; SQLAlchemy wants a driver.
        return url.replace("postgres://", "postgresql+psycopg://", 1).replace("postgresql://", "postgresql+psycopg://", 1)
    return f"sqlite:///{Path(db.DB_PATH).with_name('workspace.db')}"


def engine() -> Engine:
    url = _url()
    with _lock:
        if url not in _engines:
            eng = create_engine(url, pool_pre_ping=True)
            migrations.apply(eng)
            _engines[url] = eng
    return _engines[url]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _rows(res) -> list[dict]:
    return [dict(r._mapping) for r in res]


def _one(res) -> dict | None:
    r = res.first()
    return dict(r._mapping) if r else None


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# -- projects and notes -------------------------------------------------------

def list_projects(user: str) -> list[dict]:
    with engine().connect() as con:
        return _rows(con.execute(text("SELECT * FROM projects WHERE user_id = :u ORDER BY updated DESC"), {"u": user}))


def get_project(user: str, project_id: str) -> dict | None:
    with engine().connect() as con:
        return _one(con.execute(text("SELECT * FROM projects WHERE id = :i AND user_id = :u"), {"i": project_id, "u": user}))


def create_project(user: str, name: str, instructions: str = "") -> dict:
    now = _now()
    row = {"id": _id(), "user_id": user, "name": name, "instructions": instructions, "created": now, "updated": now}
    with engine().begin() as con:
        con.execute(text("INSERT INTO projects VALUES (:id, :user_id, :name, :instructions, :created, :updated)"), row)
    return row


def update_project(user: str, project_id: str, **fields) -> dict | None:
    fields = {k: v for k, v in fields.items() if k in ("name", "instructions") and v is not None}
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        with engine().begin() as con:
            con.execute(text(f"UPDATE projects SET {sets}, updated = :now WHERE id = :i AND user_id = :u"),
                        {**fields, "now": _now(), "i": project_id, "u": user})
    return get_project(user, project_id)


def delete_project(user: str, project_id: str) -> bool:
    """Deletes the project's notes and boards; its chats are kept, unassigned."""
    if not get_project(user, project_id):
        return False
    p = {"i": project_id, "u": user}
    with engine().begin() as con:
        con.execute(text("DELETE FROM notes WHERE project_id = :i AND user_id = :u"), p)
        con.execute(text("DELETE FROM boards WHERE project_id = :i AND user_id = :u"), p)
        con.execute(text("UPDATE chats SET project_id = NULL WHERE project_id = :i AND user_id = :u"), p)
        con.execute(text("UPDATE views SET project_id = NULL WHERE project_id = :i AND user_id = :u"), p)
        con.execute(text("DELETE FROM projects WHERE id = :i AND user_id = :u"), p)
    return True


def project_context(user: str, project_id: str | None) -> dict | None:
    """What the AI reads from a project: its brief and enabled notes."""
    project = get_project(user, project_id) if project_id else None
    if not project:
        return None
    return {"name": project["name"], "instructions": project["instructions"],
            "notes": [{"title": n["title"], "body": n["body"]} for n in list_notes(user, project_id, enabled_only=True)]}


def _note(r: dict) -> dict:
    return {**r, "enabled": bool(r["enabled"])}


def list_notes(user: str, project_id: str, enabled_only: bool = False) -> list[dict]:
    with engine().connect() as con:
        rows = _rows(con.execute(text("SELECT * FROM notes WHERE project_id = :p AND user_id = :u ORDER BY created"),
                                 {"p": project_id, "u": user}))
    return [n for n in map(_note, rows) if n["enabled"] or not enabled_only]


def get_note(user: str, note_id: str) -> dict | None:
    with engine().connect() as con:
        r = _one(con.execute(text("SELECT * FROM notes WHERE id = :i AND user_id = :u"), {"i": note_id, "u": user}))
    return _note(r) if r else None


def create_note(user: str, project_id: str, title: str, body: str, source: str = "typed") -> dict | None:
    if not get_project(user, project_id):
        return None
    now = _now()
    row = {"id": _id(), "user_id": user, "project_id": project_id, "title": title, "body": body[:MAX_NOTE_CHARS],
           "source": source, "enabled": True, "created": now, "updated": now}
    with engine().begin() as con:
        con.execute(text("INSERT INTO notes VALUES (:id, :user_id, :project_id, :title, :body, :source, :enabled, :created, :updated)"), row)
        con.execute(text("UPDATE projects SET updated = :n WHERE id = :p"), {"n": now, "p": project_id})
    return row


def update_note(user: str, note_id: str, **fields) -> dict | None:
    fields = {k: v for k, v in fields.items() if k in ("title", "body", "enabled") and v is not None}
    if "body" in fields:
        fields["body"] = fields["body"][:MAX_NOTE_CHARS]
    if fields:
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        with engine().begin() as con:
            con.execute(text(f"UPDATE notes SET {sets}, updated = :now WHERE id = :i AND user_id = :u"),
                        {**fields, "now": _now(), "i": note_id, "u": user})
    return get_note(user, note_id)


def delete_note(user: str, note_id: str) -> bool:
    with engine().begin() as con:
        return con.execute(text("DELETE FROM notes WHERE id = :i AND user_id = :u"), {"i": note_id, "u": user}).rowcount > 0


# -- chats and messages -------------------------------------------------------

def list_chats(user: str, project_id: str | None = None, q: str | None = None, unassigned: bool = False) -> list[dict]:
    sql, p = "SELECT id, project_id, board_id, title, created, updated FROM chats WHERE user_id = :u", {"u": user}
    if project_id:
        sql, p["p"] = sql + " AND project_id = :p", project_id
    elif unassigned:
        sql += " AND project_id IS NULL"
    if q and q.strip():
        sql += " AND (title LIKE :q ESCAPE '\\' OR id IN (SELECT chat_id FROM messages WHERE content LIKE :q ESCAPE '\\'))"
        p["q"] = _like(q.strip())
    with engine().connect() as con:
        return _rows(con.execute(text(sql + " ORDER BY updated DESC LIMIT 200"), p))


def create_chat(user: str, title: str = "New chat", project_id: str | None = None, board_id: str | None = None) -> dict | None:
    if project_id and not get_project(user, project_id):
        return None
    if board_id and not get_board(user, board_id):
        return None
    now = _now()
    row = {"id": _id(), "user_id": user, "project_id": project_id, "board_id": board_id, "title": title[:120],
           "created": now, "updated": now}
    with engine().begin() as con:
        con.execute(text("INSERT INTO chats VALUES (:id, :user_id, :project_id, :board_id, :title, :created, :updated)"), row)
    return row


def get_chat(user: str, chat_id: str, messages: bool = True) -> dict | None:
    with engine().connect() as con:
        chat = _one(con.execute(text("SELECT * FROM chats WHERE id = :i AND user_id = :u"), {"i": chat_id, "u": user}))
        if chat and messages:
            rows = _rows(con.execute(text("SELECT seq, role, content, events, created FROM messages WHERE chat_id = :i ORDER BY seq"),
                                     {"i": chat_id}))
            chat["messages"] = [{**r, "events": json.loads(r["events"]) if r["events"] else None} for r in rows]
    return chat


def update_chat(user: str, chat_id: str, title: str | None = None, project_id: str | None = None, move: bool = False) -> dict | None:
    """Rename and/or move (`move=True` sets project_id, which may be None = unassigned)."""
    if move and project_id and not get_project(user, project_id):
        return None
    sets, p = [], {"i": chat_id, "u": user}
    if title:
        sets.append("title = :t")
        p["t"] = title[:120]
    if move:
        sets.append("project_id = :p")
        p["p"] = project_id
    if sets:
        with engine().begin() as con:
            con.execute(text(f"UPDATE chats SET {', '.join(sets)} WHERE id = :i AND user_id = :u"), p)
    return get_chat(user, chat_id, messages=False)


def delete_chat(user: str, chat_id: str) -> bool:
    if not get_chat(user, chat_id, messages=False):
        return False
    with engine().begin() as con:
        con.execute(text("DELETE FROM messages WHERE chat_id = :i"), {"i": chat_id})
        con.execute(text("DELETE FROM chats WHERE id = :i AND user_id = :u"), {"i": chat_id, "u": user})
    return True


def add_message(user: str, chat_id: str, role: str, content: str, events: dict | None = None) -> dict | None:
    """Appends a message; the first user message also titles an untitled chat."""
    chat = get_chat(user, chat_id, messages=False)
    if not chat:
        return None
    now = _now()
    with engine().begin() as con:
        seq = (con.execute(text("SELECT COALESCE(MAX(seq), 0) FROM messages WHERE chat_id = :i"), {"i": chat_id}).scalar() or 0) + 1
        con.execute(text("INSERT INTO messages VALUES (:id, :c, :s, :r, :t, :e, :n)"),
                    {"id": _id(), "c": chat_id, "s": seq, "r": role, "t": content,
                     "e": json.dumps(events, default=str) if events else None, "n": now})
        title = chat["title"]
        if seq == 1 and role == "user" and title == "New chat":
            title = " ".join(content.split())[:60] or title
        con.execute(text("UPDATE chats SET updated = :n, title = :t WHERE id = :i"), {"n": now, "t": title, "i": chat_id})
    return {"seq": seq, "role": role}


def history(user: str, chat_id: str) -> list[dict]:
    chat = get_chat(user, chat_id)
    return [{"role": m["role"], "content": m["content"]} for m in (chat or {}).get("messages", [])]


# -- boards -------------------------------------------------------------------

def _board(r: dict) -> dict:
    return {**r, "cards": json.loads(r["cards"])}


def list_boards(user: str, project_id: str | None = None) -> list[dict]:
    sql, p = "SELECT * FROM boards WHERE user_id = :u", {"u": user}
    if project_id:
        sql, p["p"] = sql + " AND project_id = :p", project_id
    with engine().connect() as con:
        return [_board(r) for r in _rows(con.execute(text(sql + " ORDER BY updated DESC"), p))]


def get_board(user: str, board_id: str) -> dict | None:
    with engine().connect() as con:
        r = _one(con.execute(text("SELECT * FROM boards WHERE id = :i AND user_id = :u"), {"i": board_id, "u": user}))
    return _board(r) if r else None


def create_board(user: str, name: str, project_id: str | None = None, description: str = "", cards: list | None = None) -> dict | None:
    if project_id and not get_project(user, project_id):
        return None
    now = _now()
    row = {"id": _id(), "user_id": user, "project_id": project_id, "name": name, "description": description,
           "cards": json.dumps(cards or []), "created": now, "updated": now}
    with engine().begin() as con:
        con.execute(text("INSERT INTO boards VALUES (:id, :user_id, :project_id, :name, :description, :cards, :created, :updated)"), row)
    return _board(row)


def update_board(user: str, board_id: str, name: str | None = None, description: str | None = None,
                 cards: list | None = None, project_id: str | None = None, move: bool = False) -> dict | None:
    if move and project_id and not get_project(user, project_id):
        return None
    sets, p = [], {"i": board_id, "u": user, "now": _now()}
    for col, val in (("name", name), ("description", description)):
        if val is not None:
            sets.append(f"{col} = :{col}")
            p[col] = val
    if cards is not None:
        sets.append("cards = :cards")
        p["cards"] = json.dumps(cards, default=str)
    if move:
        sets.append("project_id = :project_id")
        p["project_id"] = project_id
    if sets:
        with engine().begin() as con:
            con.execute(text(f"UPDATE boards SET {', '.join(sets)}, updated = :now WHERE id = :i AND user_id = :u"), p)
    return get_board(user, board_id)


def delete_board(user: str, board_id: str) -> bool:
    with engine().begin() as con:
        con.execute(text("UPDATE chats SET board_id = NULL WHERE board_id = :i AND user_id = :u"), {"i": board_id, "u": user})
        return con.execute(text("DELETE FROM boards WHERE id = :i AND user_id = :u"), {"i": board_id, "u": user}).rowcount > 0


# -- saved views and the watchlist --------------------------------------------

def list_views(user: str, kind: str | None = None) -> list[dict]:
    sql = "SELECT * FROM views WHERE user_id = :u" + (" AND kind = :k" if kind else "") + " ORDER BY created DESC"
    with engine().connect() as con:
        rows = _rows(con.execute(text(sql), {"u": user, "k": kind} if kind else {"u": user}))
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "state": json.loads(r["state"]), "created": r["created"]} for r in rows]


def save_view(user: str, name: str, kind: str, state: dict) -> dict:
    view = {"id": _id(), "name": name, "kind": kind, "state": state, "created": _now()}
    with engine().begin() as con:
        con.execute(text("INSERT INTO views (id, name, kind, state, created, user_id) VALUES (:id, :n, :k, :s, :c, :u)"),
                    {"id": view["id"], "n": name, "k": kind, "s": json.dumps(state), "c": view["created"], "u": user})
    return view


def delete_view(user: str, view_id: str) -> None:
    with engine().begin() as con:
        con.execute(text("DELETE FROM views WHERE id = :i AND user_id = :u"), {"i": view_id, "u": user})


def get_watchlist(user: str) -> list[str]:
    with engine().connect() as con:
        row = con.execute(text("SELECT value FROM kv WHERE key = :k"), {"k": f"watchlist:{user}"}).first()
    return json.loads(row[0]) if row else []


def set_watchlist(user: str, players: list[str]) -> list[str]:
    players = list(dict.fromkeys(p.strip() for p in players if p and p.strip()))[:200]
    key = f"watchlist:{user}"
    with engine().begin() as con:
        con.execute(text("DELETE FROM kv WHERE key = :k"), {"k": key})
        con.execute(text("INSERT INTO kv (key, value) VALUES (:k, :v)"), {"k": key, "v": json.dumps(players)})
    return players


# -- backup -------------------------------------------------------------------

def export_project(user: str, project_id: str) -> dict | None:
    project = get_project(user, project_id)
    if not project:
        return None
    chats = [get_chat(user, c["id"]) for c in list_chats(user, project_id=project_id)]
    return {"format": "doosra-project", "version": 1,
            "project": {"name": project["name"], "instructions": project["instructions"]},
            "notes": [{k: n[k] for k in ("title", "body", "source", "enabled")} for n in list_notes(user, project_id)],
            "boards": [{k: b[k] for k in ("name", "description", "cards")} for b in list_boards(user, project_id)],
            "chats": [{"title": c["title"], "messages": [{k: m[k] for k in ("role", "content", "events")} for m in c["messages"]]}
                      for c in chats]}


def import_project(user: str, data: dict) -> dict:
    """Recreates an exported project (as a new project, under `user`)."""
    project = create_project(user, data["project"]["name"], data["project"].get("instructions", ""))
    for n in data.get("notes", []):
        note = create_note(user, project["id"], n["title"], n["body"], n.get("source", "typed"))
        if note and not n.get("enabled", True):
            update_note(user, note["id"], enabled=False)
    for b in data.get("boards", []):
        create_board(user, b["name"], project["id"], b.get("description", ""), b.get("cards", []))
    for c in data.get("chats", []):
        chat = create_chat(user, c["title"], project["id"])
        for m in c.get("messages", []):
            add_message(user, chat["id"], m["role"], m["content"], m.get("events"))
    return project


# -- users, invites and AI usage (hosted mode) --------------------------------

def get_user(user_id: str) -> dict | None:
    with engine().connect() as con:
        return _one(con.execute(text("SELECT * FROM users WHERE id = :i"), {"i": user_id}))


def upsert_user(user_id: str, email: str, name: str | None, role: str | None) -> dict:
    """Create the user on first sign-in; later sign-ins refresh name and last_seen
    (and promote to `role` when given, e.g. an admin listed in ADMIN_EMAILS)."""
    now = _now()
    with engine().begin() as con:
        if con.execute(text("SELECT 1 FROM users WHERE id = :i"), {"i": user_id}).first():
            con.execute(text("UPDATE users SET last_seen = :n, name = COALESCE(:nm, name), role = COALESCE(:r, role) WHERE id = :i"),
                        {"n": now, "nm": name, "r": role, "i": user_id})
        else:
            con.execute(text("INSERT INTO users (id, email, name, role, created, last_seen) VALUES (:i, :e, :nm, :r, :n, :n)"),
                        {"i": user_id, "e": email, "nm": name, "r": role or "member", "n": now})
        con.execute(text("UPDATE invites SET accepted = :t WHERE email = :e"), {"t": True, "e": email})
    return get_user(user_id)


def list_invites() -> list[dict]:
    with engine().connect() as con:
        return [{**r, "accepted": bool(r["accepted"])} for r in _rows(con.execute(text("SELECT * FROM invites ORDER BY created DESC")))]


def is_invited(email: str) -> bool:
    with engine().connect() as con:
        return con.execute(text("SELECT 1 FROM invites WHERE email = :e"), {"e": email.strip().lower()}).first() is not None


def add_invite(email: str, invited_by: str | None) -> dict:
    email = email.strip().lower()
    with engine().begin() as con:
        if not con.execute(text("SELECT 1 FROM invites WHERE email = :e"), {"e": email}).first():
            con.execute(text("INSERT INTO invites (email, invited_by, created, accepted) VALUES (:e, :b, :n, :a)"),
                        {"e": email, "b": invited_by, "n": _now(), "a": False})
    return next(i for i in list_invites() if i["email"] == email)


def remove_invite(email: str) -> bool:
    with engine().begin() as con:
        return con.execute(text("DELETE FROM invites WHERE email = :e"), {"e": email.strip().lower()}).rowcount > 0


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def questions_today(user: str | None) -> int:
    """AI questions asked today (UTC); everyone's when `user` is None."""
    sql, p = "SELECT COALESCE(SUM(questions), 0) FROM usage WHERE day = :d", {"d": _today()}
    if user:
        sql, p["u"] = sql + " AND user_id = :u", user
    with engine().connect() as con:
        return int(con.execute(text(sql), p).scalar() or 0)


def count_question(user: str) -> None:
    with engine().begin() as con:
        p = {"u": user, "d": _today()}
        if con.execute(text("UPDATE usage SET questions = questions + 1 WHERE user_id = :u AND day = :d"), p).rowcount == 0:
            con.execute(text("INSERT INTO usage (user_id, day, questions, tokens) VALUES (:u, :d, 1, 0)"), p)
