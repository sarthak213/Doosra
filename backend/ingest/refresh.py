"""
Keeps a long-running (hosted) instance on the latest published database.

Every DATA_REFRESH_HOURS a background thread runs `ingest.pull`, which checks
the `data-latest` release and, when it is newer, downloads it, verifies its
hashes and swaps it in atomically. The API opens the database per request, so
requests already running finish on the old file and the next one sees the new
one; the name catalog is cleared so it reloads from the new data.

A failed refresh (network, GitHub rate limit, a bad download) is logged and
retried at the next interval; the database in use is never touched until a
verified replacement is ready.
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("doosra.refresh")
_thread: threading.Thread | None = None
_stop = threading.Event()


def interval_hours() -> float:
    try:
        return float(os.environ.get("DATA_REFRESH_HOURS") or 0)
    except ValueError:
        return 0.0


def run_once() -> bool:
    """One check-and-install. True when a newer database was installed."""
    from agent import tools
    from ingest import pull

    before = (pull.local_info() or {}).get("built_at")
    pull.pull()
    after = (pull.local_info() or {}).get("built_at")
    if after and after != before:
        tools.invalidate_caches()
        tools.warm_caches()
        log.info("database refreshed: built %s", after)
        return True
    return False


def _loop(hours: float) -> None:
    while not _stop.wait(hours * 3600):
        try:
            run_once()
        except Exception:  # noqa: BLE001 - keep serving on the current database, try again next time
            log.exception("data refresh failed; will retry in %s h", hours)


def start() -> bool:
    """Start the refresher if DATA_REFRESH_HOURS > 0. Safe to call twice."""
    global _thread
    hours = interval_hours()
    if hours <= 0 or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(hours,), name="data-refresh", daemon=True)
    _thread.start()
    return True


def stop() -> None:
    _stop.set()
