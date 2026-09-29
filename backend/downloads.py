"""
Big downloads that survive bad connections: the cricket database and the AI model.

The file is written to <name>.part and continued with an HTTP Range request
after a dropped connection (Hugging Face resets connections often), or after
the app was closed mid-download. When it's complete its sha256 is checked, and
only then is it renamed into place, so a half-written or tampered file is never
used.
"""

from __future__ import annotations

import hashlib
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

CHUNK = 1 << 20
RETRIES = 12                  # rounds in a row with no progress; any progress resets the count
USER_AGENT = "Doosra/1 (+https://github.com/sarthak213/Doosra)"

Progress = Callable[[int, int | None], None]      # (bytes so far, total or None)


class DownloadError(RuntimeError):
    pass


class Cancelled(DownloadError):
    pass


def sha256_file(path: Path, progress: Progress | None = None) -> str:
    h, done, total = hashlib.sha256(), 0, path.stat().st_size
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
            done += len(block)
            if progress:
                progress(done, total)
    return h.hexdigest()


def download(url: str, dest: Path, sha256: str | None = None, progress: Progress | None = None,
             cancel: threading.Event | None = None, headers: dict | None = None, retries: int = RETRIES) -> Path:
    """Fetch `url` into `dest`, resuming a previous partial download. Raises Cancelled if `cancel`
    is set, DownloadError on a checksum mismatch or when the retries run out."""
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    total: int | None = None
    attempt = 0
    while True:
        have = part.stat().st_size if part.exists() else 0
        start = have
        if total is not None and have >= total:
            break
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:               # server ignored the range: start again
                    have = 0
                    part.unlink(missing_ok=True)
                length = r.headers.get("Content-Length")
                if length is not None:
                    total = have + int(length)
                with open(part, "ab" if have else "wb") as f:
                    for block in iter(lambda: r.read(CHUNK), b""):
                        if cancel is not None and cancel.is_set():
                            raise Cancelled("Download stopped; it will continue where it left off next time.")
                        f.write(block)
                        have += len(block)
                        if progress:
                            progress(have, total)
            if total is None or have >= total:
                break
            if have == start:
                attempt += 1                                 # the server sent nothing: count it as a failure
        except Cancelled:
            raise
        except urllib.error.HTTPError as e:
            if e.code == 416 and part.exists():              # asked past the end: already complete
                break
            if e.code in (401, 403, 404):
                raise DownloadError(f"{url} answered {e.code}; the file may have moved.") from e
            attempt += 1
        except (OSError, urllib.error.URLError, TimeoutError):
            attempt += 1                                     # dropped connection: resume from what we have
        if have > start:
            attempt = 0                                      # it's moving; only stalls count toward giving up
        if attempt > retries:
            raise DownloadError(f"Couldn't finish downloading {dest.name} after {retries} retries; "
                                "it will continue from where it stopped next time.")
        if attempt:
            time.sleep(min(2 ** attempt, 30))
    if sha256:
        got = sha256_file(part)
        if got.lower() != sha256.lower():
            part.unlink(missing_ok=True)
            raise DownloadError(f"{dest.name} didn't match its published checksum, so it was discarded. "
                                "Try again.")
    part.replace(dest)
    return dest
