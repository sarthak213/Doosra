"""
Saving a chat turn as it streams, and rebuilding a chat's history for the
model. The stream's events (steps, tables, charts, UI actions) are stored with
the assistant message so a reopened chat shows exactly what the user saw.
"""

from __future__ import annotations

import json

from . import workspace

# Saved chats whose answer is still being written (chat id -> the agent's task). The agent runs as
# its own task, so leaving the page (which closes the stream) doesn't throw away an answer a slow
# local model is minutes into: it finishes and is saved, and a reopened chat waits for it.
ANSWERING: dict = {}

MAX_STRING = 2000           # per string field in a step event (tables and charts are kept whole)
TABLE_HINT_ROWS = 4
TABLE_HINT_CHARS = 700
_KEEP_WHOLE = {"table", "chart"}


def answering(chat_id: str) -> bool:
    task = ANSWERING.get(chat_id)
    return bool(task and not task.done())


def slim(event: dict) -> dict:
    """An event as stored: long strings in steps (tool results, SQL) are cut."""
    if event.get("type") in _KEEP_WHOLE:
        return event
    return {k: (v[:MAX_STRING] + "…" if isinstance(v, str) and len(v) > MAX_STRING else v) for k, v in event.items()}


def table_hint(events: dict | None) -> str:
    """A compact note of the tables an earlier answer showed, so a follow-up
    like "now split that by phase" has the numbers and not only the prose."""
    parts = []
    for e in (events or {}).get("events", []):
        if e.get("type") != "table":
            continue
        t = e.get("table_data") or {}
        rows = json.dumps((t.get("rows") or [])[:TABLE_HINT_ROWS], default=str)
        parts.append(f"{e.get('table_id', 'table')} {t.get('title', '')} columns={t.get('columns')} first rows={rows}")
    text = "\n[Data shown with this answer: " + "; ".join(parts) + "]" if parts else ""
    return text[:TABLE_HINT_CHARS]


def history_for_model(user: str, chat_id: str) -> list[dict]:
    chat = workspace.get_chat(user, chat_id)
    out = []
    for m in (chat or {}).get("messages", []):
        if not m["content"].strip():
            continue                                       # a stopped or failed turn
        hint = table_hint(m["events"]) if m["role"] == "assistant" else ""
        out.append({"role": m["role"], "content": m["content"] + hint})
    return out


class Recorder:
    """Collects one turn's events and writes the assistant message once, on
    completion, error or cancellation (whichever comes first)."""

    def __init__(self, user: str, chat_id: str):
        self.user, self.chat_id = user, chat_id
        self.events: list[dict] = []
        self.answer = ""
        self.final: dict = {}
        self.saved = False

    def see(self, event: dict) -> None:
        if event.get("type") == "final_answer":
            self.answer = event.get("content") or ""
            self.final = {k: event.get(k) for k in ("chart_data", "table_data") if event.get(k)}
        else:
            self.events.append(slim(event))

    def save(self) -> None:
        if self.saved or not (self.answer or self.events):
            return
        self.saved = True
        workspace.add_message(self.user, self.chat_id, "assistant", self.answer,
                              {"events": self.events, **({"final": self.final} if self.final else {})})
