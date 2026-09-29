"""
What the AI is told when it explains a board. The server loads the board the
user owns, re-runs every card against the current data and computes the
digest itself, so a stale page or a doctored request can't put numbers in the
model's mouth.
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from analytics import insights

from . import workspace
from .workspace_routes import render_source

MAX_CARDS = 12


def _run(card: dict) -> tuple[dict | None, str | None]:
    try:
        out = render_source(card.get("source") or {})
    except HTTPException as e:
        return None, str(e.detail)
    except Exception as e:  # noqa: BLE001 - one broken card must not sink the explanation
        return None, f"could not run: {e}"
    if isinstance(out, JSONResponse):
        try:
            return None, json.loads(out.body).get("error") or "could not run"
        except ValueError:
            return None, "could not run"
    if not isinstance(out, dict) or "columns" not in out:
        return None, "unexpected result"
    return out, None


def build(user: str, board_id: str, card_id: str | None = None) -> dict | None:
    board = workspace.get_board(user, board_id)
    if not board:
        return None
    cards = board["cards"]
    focus = next((c for c in cards if c["id"] == card_id), None) if card_id else None
    chosen = [focus] if focus else cards[:MAX_CARDS]
    results = [(c, *_run(c)) for c in chosen]
    ctx = insights.board_digest(board, results)
    ctx["as_of"] = dt.date.today().isoformat()
    if focus:
        ctx["focus_card"] = focus.get("title") or "Untitled card"
    elif len(cards) > MAX_CARDS:
        ctx["omitted_cards"] = len(cards) - MAX_CARDS
    return ctx
