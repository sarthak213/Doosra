"""
Sign-in (Google or GitHub, through OAuth), sign-out, "who am I", and the
invite list. Only meaningful when AUTH_MODE=oauth; locally /api/me just says so.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from . import auth, workspace

router = APIRouter()

_PROVIDERS = {
    "google": {"label": "Google", "env": "GOOGLE"},
    "github": {"label": "GitHub", "env": "GITHUB"},
}
_oauth = None


def public_url() -> str:
    return os.environ.get("PUBLIC_URL", "").rstrip("/")


def app_url() -> str:
    return os.environ.get("APP_URL", "").rstrip("/") or public_url() or "/"


def configured() -> list[str]:
    return [p for p, c in _PROVIDERS.items() if os.environ.get(f"{c['env']}_CLIENT_ID") and os.environ.get(f"{c['env']}_CLIENT_SECRET")]


def _client(provider: str):
    """The Authlib client for a provider, registered on first use."""
    global _oauth
    if provider not in configured():
        raise HTTPException(status_code=404, detail="Unknown or unconfigured sign-in provider")
    from authlib.integrations.starlette_client import OAuth
    if _oauth is None:
        _oauth = OAuth()
    if provider not in _oauth._registry:
        env = _PROVIDERS[provider]["env"]
        common = {"client_id": os.environ[f"{env}_CLIENT_ID"], "client_secret": os.environ[f"{env}_CLIENT_SECRET"]}
        if provider == "google":
            _oauth.register("google", server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                            client_kwargs={"scope": "openid email profile"}, **common)
        else:
            _oauth.register("github", access_token_url="https://github.com/login/oauth/access_token",
                            authorize_url="https://github.com/login/oauth/authorize", api_base_url="https://api.github.com/",
                            client_kwargs={"scope": "read:user user:email"}, **common)
    return _oauth.create_client(provider)


async def _identity(provider: str, client, token: dict) -> tuple[str | None, str | None]:
    """(verified email, display name) from the provider. An unverified email never counts."""
    if provider == "google":
        info = token.get("userinfo") or {}
        return (info.get("email") if info.get("email_verified") else None), info.get("name")
    profile = (await client.get("user", token=token)).json()
    emails = (await client.get("user/emails", token=token)).json()
    primary = next((e["email"] for e in emails if e.get("primary") and e.get("verified")), None)
    return primary, profile.get("name") or profile.get("login")


@router.get("/auth/providers")
def providers():
    return {"mode": auth.auth_mode(), "providers": [{"id": p, "label": _PROVIDERS[p]["label"]} for p in configured()]}


@router.get("/auth/login/{provider}")
async def login(request: Request, provider: str):
    if not auth.hosted():
        raise HTTPException(status_code=404, detail="Sign-in is off (AUTH_MODE=none)")
    client = _client(provider)
    base = public_url() or str(request.base_url).rstrip("/")
    return await client.authorize_redirect(request, f"{base}/auth/callback/{provider}")


@router.get("/auth/callback/{provider}")
async def callback(request: Request, provider: str):
    if not auth.hosted():
        raise HTTPException(status_code=404, detail="Sign-in is off (AUTH_MODE=none)")
    client = _client(provider)
    try:
        token = await client.authorize_access_token(request)
        email, name = await _identity(provider, client, token)
    except Exception:  # noqa: BLE001 - a failed or cancelled sign-in goes back to the login page
        return RedirectResponse(f"{app_url()}/login?error=failed", status_code=303)
    user = auth.admit(email or "", name)
    if not user:
        request.session.clear()
        return RedirectResponse(f"{app_url()}/login?error=not_invited", status_code=303)
    request.session.clear()
    request.session["uid"] = user["id"]
    return RedirectResponse(f"{app_url()}/", status_code=303)


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "signed out"}


@router.get("/api/me")
def me(request: Request):
    if not auth.hosted():
        return {"mode": "none", "user": {"id": "local", "role": "admin"}, "quota": auth.quota("local")}
    user = auth._session_user(request)
    if not user:
        return {"mode": auth.auth_mode(), "user": None}
    return {"mode": auth.auth_mode(), "user": {k: user[k] for k in ("id", "email", "name", "role")}, "quota": auth.quota(user["id"])}


class InviteBody(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.get("/api/admin/invites")
def list_invites(_admin: dict = Depends(auth.current_admin)):
    return {"invites": workspace.list_invites(), "admins": sorted(auth.admin_emails())}


@router.post("/api/admin/invites")
def add_invite(body: InviteBody, admin: dict = Depends(auth.current_admin)):
    return workspace.add_invite(body.email, admin.get("email"))


@router.delete("/api/admin/invites/{email}")
def remove_invite(email: str, _admin: dict = Depends(auth.current_admin)):
    if not workspace.remove_invite(email):
        raise HTTPException(status_code=404, detail="No such invite")
    return {"status": "removed"}
