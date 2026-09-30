"""
App updates for the desktop app, from GitHub Releases.

A release tagged app-v<version> carries DoosraSetup-<version>.exe (built by
.github/workflows/desktop.yml). The app checks for a newer one, downloads the
installer (resumable, checked against the sha256 GitHub publishes for it), and
when the person says so, closes and runs it silently; the installer waits for
the app to exit and opens the new version when it's done.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import doosra_home
import downloads
from version import VERSION

REPO = os.environ.get("DOOSRA_REPO", "sarthak213/Doosra")
CHECK_EVERY = 6 * 3600
_cache: dict = {"at": 0.0, "release": None, "error": None}


def parse(version: str) -> tuple[int, ...]:
    """'2.4.0' -> (2, 4, 0); anything after the numbers (a -beta suffix) is ignored."""
    return tuple(int(n) for n in re.findall(r"\d+", version.split("-")[0])[:4])


def newest(releases: list[dict]) -> dict | None:
    """The highest app-v* release that has an installer (drafts and pre-releases left out)."""
    best = None
    for r in releases:
        tag = r.get("tag_name") or ""
        if not tag.startswith("app-v") or r.get("draft") or r.get("prerelease"):
            continue
        asset = next((a for a in r.get("assets") or []
                      if a.get("name", "").startswith("DoosraSetup-") and a["name"].endswith(".exe")), None)
        if not asset:
            continue
        version = tag[len("app-v"):]
        digest = asset.get("digest") or ""
        candidate = {"version": version, "tag": tag, "url": asset["browser_download_url"], "name": asset["name"],
                     "size": asset.get("size"), "sha256": digest[7:] if digest.startswith("sha256:") else None,
                     "notes": (r.get("body") or "")[:4000], "page": r.get("html_url")}
        if best is None or parse(version) > parse(best["version"]):
            best = candidate
    return best


def _fetch() -> list[dict]:
    # DOOSRA_RELEASES_URL points the check at another list of releases (testing an update end to end)
    url = os.environ.get("DOOSRA_RELEASES_URL") or f"https://api.github.com/repos/{REPO}/releases?per_page=30"
    req = urllib.request.Request(url,
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": f"Doosra/{VERSION}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def check(force: bool = False) -> dict:
    """What's installed, the newest release, and whether it's newer. Cached for a few hours."""
    if force or time.time() - _cache["at"] > CHECK_EVERY:
        try:
            _cache.update(release=newest(_fetch()), error=None)
        except Exception as e:  # noqa: BLE001 - offline, rate-limited: say so, try again next time
            _cache["error"] = f"Couldn't check for updates: {e}"
        _cache["at"] = time.time()
    release = _cache["release"]
    return {"current": VERSION, "latest": release, "error": _cache["error"],
            "available": bool(release and parse(release["version"]) > parse(VERSION))}


# ---- download, then install on quit ------------------------------------------

@dataclass
class Download:
    state: str = "idle"             # idle | downloading | ready | error
    done: int = 0
    total: int | None = None
    error: str | None = None
    version: str | None = None
    path: Path | None = None

    def snapshot(self) -> dict:
        return {"state": self.state, "done": self.done, "total": self.total, "error": self.error,
                "version": self.version}


DOWNLOAD = Download()
_lock = threading.Lock()


def start_download() -> Download:
    """Fetch the newest installer in the background (a no-op if it's already downloading or done)."""
    global DOWNLOAD
    with _lock:
        info = check()
        if not info["available"]:
            raise RuntimeError(info["error"] or "Doosra is up to date.")
        release = info["latest"]
        if DOWNLOAD.state in ("downloading", "ready") and DOWNLOAD.version == release["version"]:
            return DOWNLOAD
        DOWNLOAD = job = Download(state="downloading", total=release["size"], version=release["version"])

    def run():
        dest = doosra_home.home() / "updates" / release["name"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            def progress(done, total):
                job.done, job.total = done, total or job.total
            downloads.download(release["url"], dest, sha256=release["sha256"], progress=progress)
            job.path, job.state = dest, "ready"
        except Exception as e:  # noqa: BLE001 - shown in Settings, with a retry
            job.state, job.error = "error", str(e)
    threading.Thread(target=run, name="update-download", daemon=True).start()
    return job


# The launcher sets this: close the window (and so the app). Outside the desktop app it stays None.
quit_app = None
_pending: Path | None = None


def install_on_quit() -> None:
    """Remember the downloaded installer, then close the app; the launcher runs it on the way out."""
    global _pending
    if DOWNLOAD.state != "ready" or not DOWNLOAD.path:
        raise RuntimeError("The update hasn't finished downloading.")
    if quit_app is None:
        raise RuntimeError("Updates install from the desktop app.")
    _pending = DOWNLOAD.path
    quit_app()


def run_pending_installer() -> bool:
    """Called by the launcher as it exits: start the installer, detached, to update in place.
    /UPDATE=1 makes it wait for this app to finish closing and open the new version afterwards."""
    if not _pending or not _pending.exists():
        return False
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    args = [str(_pending), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/UPDATE=1"]
    try:
        subprocess.Popen(args, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), close_fds=True)
    except OSError:                  # a parent job that doesn't allow breaking away
        subprocess.Popen(args, creationflags=flags, close_fds=True)
    return True
