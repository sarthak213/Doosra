"""
Who is making the request, and whether they may.

Locally (AUTH_MODE=none, the default) there is one implicit user, `local`, and
nothing is gated. Hosted (AUTH_MODE=oauth) people sign in with Google or
GitHub and only invited emails are admitted; a signed, HttpOnly session cookie
carries the user id. Doosra never handles a password. AI questions are metered
per user and, optionally, across everyone, so a hosted instance's LLM spend has
a ceiling.
"""

from __future__ import annotations

import hashlib
import os

from fastapi import Depends, HTTPException, Request

from . import workspace
from .workspace import LOCAL_USER

DEFAULT_HOSTED_DAILY_QUESTIONS = 30


def auth_mode() -> str:
    return os.environ.get("AUTH_MODE", "none").strip().lower()


def hosted() -> bool:
    return auth_mode() != "none"


def session_secret() -> str:
    # Local mode never issues a session, so the fallback secret protects nothing there.
    return os.environ.get("SESSION_SECRET") or "doosra-local-mode-no-sessions"


def check_config() -> None:
    """Refuse to start a hosted instance that could be run insecurely."""
    if hosted() and len(os.environ.get("SESSION_SECRET", "")) < 32:
        raise RuntimeError("AUTH_MODE is on but SESSION_SECRET is missing or shorter than 32 characters.")


def admin_emails() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


def user_id_for(email: str) -> str:
    return "u" + hashlib.sha256(email.strip().lower().encode()).hexdigest()[:11]


def admit(email: str, name: str | None = None) -> dict | None:
    """Sign-in decision: invited (or an admin) becomes a user; anyone else gets None."""
    email = (email or "").strip().lower()
    is_admin = email in admin_emails()
    if not email or not (is_admin or workspace.is_invited(email)):
        return None
    return workspace.upsert_user(user_id_for(email), email, name, "admin" if is_admin else None)


def _session_user(request: Request) -> dict | None:
    uid = request.session.get("uid") if "session" in request.scope else None
    user = workspace.get_user(uid) if uid else None
    # Checked on every request, so revoking an invite ends that person's session at once.
    if user and (user["email"] in admin_emails() or workspace.is_invited(user["email"])):
        return user
    return None


def current_user(request: Request) -> str:
    """FastAPI dependency: the id every workspace query is scoped to."""
    if not hosted():
        return LOCAL_USER
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    return user["id"]


def current_admin(request: Request) -> dict:
    if not hosted():
        return {"id": LOCAL_USER, "email": None, "role": "admin"}
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    if user["role"] != "admin" and user["email"] not in admin_emails():
        raise HTTPException(status_code=403, detail="Admins only")
    return user


# -- AI quotas ----------------------------------------------------------------

def daily_limit() -> int:
    """Questions per user per day; 0 means unlimited (the local default)."""
    raw = os.environ.get("AI_DAILY_QUESTIONS")
    return int(raw) if raw not in (None, "") else (DEFAULT_HOSTED_DAILY_QUESTIONS if hosted() else 0)


def global_limit() -> int:
    return int(os.environ.get("AI_GLOBAL_DAILY_QUESTIONS") or 0)


def quota(user: str) -> dict:
    return {"used": workspace.questions_today(user), "limit": daily_limit()}


def charge_question(user: str = Depends(current_user)) -> str:
    """Dependency for the routes that call the LLM: counts one question, or
    answers 429 once the user's (or the whole instance's) daily allowance is spent."""
    limit, cap = daily_limit(), global_limit()
    if limit and workspace.questions_today(user) >= limit:
        raise HTTPException(status_code=429, detail=f"You've used today's {limit} AI questions. The allowance resets at 00:00 UTC.")
    if cap and workspace.questions_today(None) >= cap:
        raise HTTPException(status_code=429, detail="This instance has reached its AI budget for today. Try again tomorrow.")
    workspace.count_question(user)
    return user
