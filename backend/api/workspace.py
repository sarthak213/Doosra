"""
The user's workspace -- saved views and a player watchlist -- in a small
SQLite file next to the cricket database (the cricket DB itself stays
read-only at runtime).
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import uuid
from pathlib import Path

from analytics import db


def _path() -> Path:
    return Path(db.DB_PATH).with_name("workspace.db")


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(_path())
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS views (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, "
                "state TEXT NOT NULL, created TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return con


def list_views(kind: str | None = None) -> list[dict]:
    with _connect() as con:
        sql = "SELECT * FROM views" + (" WHERE kind = ?" if kind else "") + " ORDER BY created DESC"
        rows = con.execute(sql, [kind] if kind else []).fetchall()
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "state": json.loads(r["state"]),
             "created": r["created"]} for r in rows]


def save_view(name: str, kind: str, state: dict) -> dict:
    view = {"id": uuid.uuid4().hex[:12], "name": name, "kind": kind, "state": state,
            "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    with _connect() as con:
        con.execute("INSERT INTO views VALUES (?, ?, ?, ?, ?)",
                    [view["id"], name, kind, json.dumps(state), view["created"]])
    return view


def delete_view(view_id: str) -> None:
    with _connect() as con:
        con.execute("DELETE FROM views WHERE id = ?", [view_id])


def get_watchlist() -> list[str]:
    with _connect() as con:
        row = con.execute("SELECT value FROM kv WHERE key = 'watchlist'").fetchone()
    return json.loads(row["value"]) if row else []


def set_watchlist(players: list[str]) -> list[str]:
    players = list(dict.fromkeys(p.strip() for p in players if p and p.strip()))[:200]
    with _connect() as con:
        con.execute("INSERT INTO kv VALUES ('watchlist', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    [json.dumps(players)])
    return players
