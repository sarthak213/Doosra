"""
Schema of the user workspace (chats, projects, notes, boards, saved views) and
its forward-only migrations. Numbered steps run once each; `schema_version`
records how far a database has got. Works on SQLite (local, the default) and
Postgres (hosted, via DATABASE_URL).
"""

from __future__ import annotations

from sqlalchemy import (Boolean, Column, Index, Integer, MetaData, Table, Text, inspect, text)

metadata = MetaData()

Table("users", metadata,
      Column("id", Text, primary_key=True), Column("email", Text, nullable=False, unique=True),
      Column("name", Text), Column("role", Text, nullable=False, server_default="member"),
      Column("created", Text, nullable=False), Column("last_seen", Text))
Table("invites", metadata,
      Column("email", Text, primary_key=True), Column("invited_by", Text),
      Column("created", Text, nullable=False), Column("accepted", Boolean, nullable=False, server_default="0"))
Table("projects", metadata,
      Column("id", Text, primary_key=True), Column("user_id", Text, nullable=False),
      Column("name", Text, nullable=False), Column("instructions", Text, nullable=False, server_default=""),
      Column("created", Text, nullable=False), Column("updated", Text, nullable=False))
Table("notes", metadata,
      Column("id", Text, primary_key=True), Column("user_id", Text, nullable=False),
      Column("project_id", Text, nullable=False), Column("title", Text, nullable=False),
      Column("body", Text, nullable=False), Column("source", Text, nullable=False, server_default="typed"),
      Column("enabled", Boolean, nullable=False, server_default="1"),
      Column("created", Text, nullable=False), Column("updated", Text, nullable=False))
Table("chats", metadata,
      Column("id", Text, primary_key=True), Column("user_id", Text, nullable=False),
      Column("project_id", Text), Column("board_id", Text), Column("title", Text, nullable=False),
      Column("created", Text, nullable=False), Column("updated", Text, nullable=False))
Table("messages", metadata,
      Column("id", Text, primary_key=True), Column("chat_id", Text, nullable=False),
      Column("seq", Integer, nullable=False), Column("role", Text, nullable=False),
      Column("content", Text, nullable=False), Column("events", Text), Column("created", Text, nullable=False))
Table("boards", metadata,
      Column("id", Text, primary_key=True), Column("user_id", Text, nullable=False),
      Column("project_id", Text), Column("name", Text, nullable=False),
      Column("description", Text, nullable=False, server_default=""), Column("cards", Text, nullable=False),
      Column("created", Text, nullable=False), Column("updated", Text, nullable=False))
Table("usage", metadata,
      Column("user_id", Text, primary_key=True), Column("day", Text, primary_key=True),
      Column("questions", Integer, nullable=False, server_default="0"),
      Column("tokens", Integer, nullable=False, server_default="0"))
Table("views", metadata,
      Column("id", Text, primary_key=True), Column("name", Text, nullable=False),
      Column("kind", Text, nullable=False), Column("state", Text, nullable=False),
      Column("created", Text, nullable=False), Column("user_id", Text, nullable=False, server_default="local"),
      Column("project_id", Text))
Table("kv", metadata, Column("key", Text, primary_key=True), Column("value", Text, nullable=False))
Index("ix_chats_user", metadata.tables["chats"].c.user_id, metadata.tables["chats"].c.updated)
Index("ix_messages_chat", metadata.tables["messages"].c.chat_id, metadata.tables["messages"].c.seq)
Index("ix_notes_project", metadata.tables["notes"].c.project_id)
Index("ix_boards_user", metadata.tables["boards"].c.user_id)


def _v1(con) -> None:
    """The user workspace. Adopts a v0 database (the old views + kv only)."""
    insp = inspect(con)
    if insp.has_table("views") and "user_id" not in {c["name"] for c in insp.get_columns("views")}:
        con.execute(text("ALTER TABLE views ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'"))
        con.execute(text("ALTER TABLE views ADD COLUMN project_id TEXT"))
    con.execute(text("UPDATE kv SET key = 'watchlist:local' WHERE key = 'watchlist'")) if insp.has_table("kv") else None
    metadata.create_all(con)


MIGRATIONS = [_v1]


def apply(engine) -> None:
    with engine.begin() as con:
        con.execute(text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"))
        row = con.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
        current = row or 0
        for n, step in enumerate(MIGRATIONS, start=1):
            if n > current:
                step(con)
                con.execute(text("INSERT INTO schema_version VALUES (:v)"), {"v": n})
