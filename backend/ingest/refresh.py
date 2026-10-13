"""
Keeps the app on the latest published database.

Every DATA_REFRESH_HOURS a background thread runs `ingest.pull`, which checks
the `data-latest` release and, when it is newer, downloads it, verifies its
hashes and swaps it in atomically. The API opens the database per request, so
requests already running finish on the old file and the next one sees the new
one; the name catalog is cleared so it reloads from the new data.

A failed refresh (network, GitHub rate limit, a bad download) is logged and
retried at the next interval; the database in use is never touched until a
verified replacement is ready.

The desktop app is opened and closed, so it can't wait a day for the first check:
it checks a minute after it starts (`start(first_after=60)`), and Settings can
install a newer build at once (`install_now`). STATUS follows the download.
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("doosra.refresh")
_thread: threading.Thread | None = None
_stop = threading.Event()
_lock = threading.Lock()                     # one refresh at a time: the daily check or Settings' "Install now"
STATUS: dict = {"state": "idle"}             # idle | downloading | unpacking | verifying | installing | installed | error


def interval_hours() -> float:
    try:
        return float(os.environ.get("DATA_REFRESH_HOURS") or 0)
    except ValueError:
        return 0.0


def run_once() -> bool:
    """One check-and-install. True when a newer database was installed. Without a database
    there's nothing to refresh (the desktop app's setup installs the first one)."""
    from agent import tools
    from ingest import pull

    if not _lock.acquire(blocking=False):
        return False                         # already refreshing
    try:
        before = (pull.local_info() or {}).get("built_at")
        if not before:
            STATUS.clear()
            STATUS.update(state="idle")
            return False

        def progress(done: int, total: int | None) -> None:
            STATUS.update(done=done, total=total)

        def stage(name: str) -> None:
            STATUS.update(state=name)

        try:
            pull.pull(progress=progress, stage=stage)
        except Exception as e:
            STATUS.clear()
            STATUS.update(state="error", error=str(e))
            raise
        after = (pull.local_info() or {}).get("built_at")
        if after and after != before:
            tools.invalidate_caches()
            tools.warm_caches()
            STATUS.clear()
            STATUS.update(state="installed", built_at=after)
            log.info("database refreshed: built %s", after)
            return True
        STATUS.clear()
        STATUS.update(state="idle")
        return False
    finally:
        _lock.release()


def busy() -> bool:
    return _lock.locked()


def install_now() -> bool:
    """Settings' "Install now": refresh in the background. False if a refresh is already running."""
    if busy():
        return False
    STATUS.clear()
    STATUS.update(state="downloading", done=0, total=None)
    threading.Thread(target=_safe_run, name="data-install", daemon=True).start()
    return True


def _safe_run() -> None:
    try:
        run_once()
    except Exception:  # noqa: BLE001 - kept in STATUS for Settings; the database in use is untouched
        log.exception("data refresh failed")


def _loop(hours: float, first_after: float) -> None:
    wait = first_after
    while not _stop.wait(wait):
        try:
            run_once()
        except Exception:  # noqa: BLE001 - keep serving on the current database, try again next time
            log.exception("data refresh failed; will retry in %s h", hours)
        wait = hours * 3600


def start(first_after: float | None = None) -> bool:
    """Start the refresher if DATA_REFRESH_HOURS > 0, checking first after `first_after`
    seconds (default: one interval). Safe to call twice."""
    global _thread
    hours = interval_hours()
    if hours <= 0 or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    first = hours * 3600 if first_after is None else first_after
    _thread = threading.Thread(target=_loop, args=(hours, first), name="data-refresh", daemon=True)
    _thread.start()
    return True


def stop() -> None:
    _stop.set()
