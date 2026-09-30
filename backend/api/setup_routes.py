"""
The desktop app's setup and settings endpoints: /api/desktop/*.

They exist only in the desktop app (DOOSRA_DESKTOP=1) and only answer this PC (127.0.0.1): they
download files, start processes and open folders, which no hosted instance should ever expose.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import desktop_app
import doosra_home
import local_llm
import setup_job
import updates
from version import VERSION
from analytics import db
from ingest import pull

router = APIRouter(prefix="/api/desktop")
_hardware_cache: dict = {}


def desktop_only(request: Request) -> None:
    if not desktop_app.enabled():
        raise HTTPException(status_code=404, detail="Not found")
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="The desktop settings only answer this computer.")


Local = Depends(desktop_only)


def _hardware() -> dict:
    # `llama-server --list-devices` takes about a second; the answer doesn't change while we run.
    if not _hardware_cache or time.time() - _hardware_cache["at"] > 300:
        _hardware_cache.update(at=time.time(), value=desktop_app.hardware())
    return _hardware_cache["value"]


@router.get("/status")
def status():
    """The one endpoint that answers outside the desktop app too, so the page can tell it apart."""
    if not desktop_app.enabled():
        return {"desktop": False}
    settings = desktop_app.load_settings()
    info = pull.local_info(db.DB_PATH) or {}
    return {
        "desktop": True, "ready": setup_job.ready(), "settings": settings,
        "models": desktop_app.REGISTRY, "installed": desktop_app.installed_models(settings),
        "lmstudio_copies": sorted(desktop_app.lmstudio_copies()), "lmstudio": desktop_app.lmstudio(),
        "hardware": _hardware(),
        "data": {"built_at": info.get("built_at"), "latest_match": info.get("latest_match_date")} if info else None,
        "engine": local_llm.ENGINE.status(), "setup": setup_job.JOB.snapshot(),
        "home": str(doosra_home.home()), "version": VERSION,
    }


class SetupBody(BaseModel):
    engine: str = Field(default="builtin", pattern="^(builtin|lmstudio)$")
    model: str | None = None
    use_lmstudio_copy: bool = True
    lmstudio_model: str | None = None


@router.post("/setup", dependencies=[Local])
def start_setup(body: SetupBody):
    if body.model:
        try:
            desktop_app.model_info(body.model)
        except KeyError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    return setup_job.start(body.engine, body.model, body.use_lmstudio_copy, body.lmstudio_model).snapshot()


@router.post("/setup/cancel", dependencies=[Local])
def cancel_setup():
    setup_job.cancel()
    return setup_job.JOB.snapshot()


@router.get("/setup/progress", dependencies=[Local])
async def progress():
    """The setup job's state twice a second, until it finishes (server-sent events)."""
    async def events():
        while True:
            snap = setup_job.JOB.snapshot()
            yield f"data: {json.dumps(snap, default=str)}\n\n"
            if snap["state"] != "running":
                break
            await asyncio.sleep(0.5)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class SettingsBody(BaseModel):
    ctx_size: int | None = Field(default=None, ge=4096, le=65536)
    engine_mode: str | None = Field(default=None, pattern="^(vulkan|vulkan-nocoopmat|cpu)$")


@router.put("/settings", dependencies=[Local])
def update_settings(body: SettingsBody):
    """Engine options; they take effect the next time the engine starts (Settings restarts it)."""
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    return desktop_app.save_settings(**changes)


@router.post("/engine/restart", dependencies=[Local])
def restart_engine():
    settings = desktop_app.load_settings()
    if settings["engine"] != "builtin" or not settings.get("model"):
        raise HTTPException(status_code=400, detail="The built-in engine isn't in use.")
    local_llm.ENGINE.stop()
    setup_job.apply_on_startup()
    return {"status": "restarting"}


@router.delete("/models/{model_id}", dependencies=[Local])
def delete_model(model_id: str):
    """Delete a downloaded model to free space. A file LM Studio owns is never deleted."""
    try:
        info = desktop_app.model_info(model_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    settings = desktop_app.load_settings()
    if settings.get("model") == model_id and settings["engine"] == "builtin" and local_llm.ENGINE.running():
        raise HTTPException(status_code=409, detail="That model is in use. Switch to another model first.")
    ours = doosra_home.models_dir() / info["file"]
    for leftover in (ours, ours.with_name(ours.name + ".part")):
        leftover.unlink(missing_ok=True)
    if settings.get("model") == model_id:
        desktop_app.save_settings(model=None, model_path=None)
    return {"status": "deleted", "installed": desktop_app.installed_models()}


@router.get("/data/check", dependencies=[Local])
def check_data():
    try:
        manifest = pull.published()
    except Exception as e:  # noqa: BLE001 - offline, GitHub rate limit...
        raise HTTPException(status_code=503, detail=f"Couldn't reach the data releases: {e}") from e
    have = pull.local_info(db.DB_PATH) or {}
    return {"installed": have.get("built_at"), "published": manifest["built_at"],
            "latest_match": manifest["latest_match"], "newer": have.get("built_at") != manifest["built_at"],
            "download_mb": round(manifest["asset"]["bytes"] / 1e6)}


@router.post("/open-folder", dependencies=[Local])
def open_folder():
    if os.name != "nt":
        raise HTTPException(status_code=400, detail="Only on Windows.")
    os.startfile(doosra_home.home())                        # noqa: S606 - our own data folder
    return {"status": "opened"}


@router.get("/update", dependencies=[Local])
async def update_status(force: bool = False):
    """Is there a newer Doosra, and how far its download has got."""
    info = await asyncio.to_thread(updates.check, force)
    return {**info, "download": updates.DOWNLOAD.snapshot()}


@router.post("/update/download", dependencies=[Local])
def update_download():
    try:
        return updates.start_download().snapshot()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/update/install", dependencies=[Local])
def update_install():
    """Close the app and run the downloaded installer; the new version opens when it's done."""
    try:
        updates.install_on_quit()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"status": "closing"}
