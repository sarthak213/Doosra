"""
Doosra.exe: the desktop app.

Starts the API (uvicorn, in this process) on 127.0.0.1 and opens it in a native
window (pywebview, on Windows' built-in WebView2). The API brings up the AI
engine chosen in setup; closing the window stops both.

    Doosra.exe                 the app
    Doosra.exe --self-test     start the API headless, check it and the engine, exit 0 or 1 (CI)
    python desktop/launcher.py the same from a checkout (uses backend/data unless DOOSRA_HOME is set)
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

TITLE = "Doosra"
PREFERRED_PORT = 47800        # a fixed port keeps the page's origin (and its saved state) the same between runs
FROZEN = getattr(sys, "frozen", False)
ROOT = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if not FROZEN:
    sys.path.insert(0, str(BACKEND))

log = logging.getLogger("doosra.desktop")


def configure_env() -> None:
    """The settings the backend reads at import: local mode, the app served from this process."""
    os.environ["DOOSRA_DESKTOP"] = "1"
    os.environ["AUTH_MODE"] = "none"
    os.environ["SERVE_FRONTEND"] = "1"
    os.environ.setdefault("FRONTEND_DIST", str(ROOT / "frontend" / "dist"))
    os.environ.setdefault("DATA_REFRESH_HOURS", "24")     # stay on the weekly database without asking
    os.environ.setdefault("LLM_THINKING", "auto")


def setup_logging() -> Path:
    import doosra_home

    logs = doosra_home.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "doosra.log"
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    if FROZEN and sys.stdout is None:          # a windowed exe has no console: print() and tracebacks go to the log
        stream = open(path.with_name("console.log"), "a", encoding="utf-8", buffering=1)   # noqa: SIM115
        sys.stdout = sys.stderr = stream
    return path


def pick_port() -> int:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", PREFERRED_PORT))
            return PREFERRED_PORT
        except OSError:
            pass
    import local_llm
    return local_llm.free_port()


# ---- one window at a time ----------------------------------------------------

_mutex = None


def already_running() -> bool:
    """Own a named mutex for the app's lifetime. If another copy owns it, bring its window forward;
    if that copy is closing instead (it takes a few seconds to unload), wait for it and carry on."""
    global _mutex
    if sys.platform != "win32":
        return False
    k32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _mutex = k32.CreateMutexW(None, True, r"Local\DoosraDesktopApp")
    if k32.GetLastError() != 183:              # ERROR_ALREADY_EXISTS; otherwise it's ours
        return False
    end = time.time() + 60
    while time.time() < end:
        if k32.WaitForSingleObject(_mutex, 250) in (0, 0x80):   # released or abandoned: the other copy is gone
            return False
        if hwnd := user32.FindWindowW(None, TITLE):
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)     # SW_RESTORE, only if minimized (it would un-maximize it)
            user32.SetForegroundWindow(hwnd)
            return True
    return False


def message_box(text: str, flags: int = 0x10) -> int:
    if sys.platform != "win32":
        print(text, file=sys.stderr)
        return 0
    return ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags)


# ---- the API ------------------------------------------------------------------

class Api:
    """uvicorn on a background thread, stopped when the window closes."""

    def __init__(self, port: int):
        import uvicorn
        from main import app

        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None,
                                                    access_log=False, timeout_graceful_shutdown=5))
        self.thread = threading.Thread(target=self.server.run, name="api", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 60) -> None:
        self.thread.start()
        end = time.time() + timeout
        while not self.server.started:
            if not self.thread.is_alive() or time.time() > end:
                raise RuntimeError("The Doosra server didn't start. See the log for details.")
            time.sleep(0.05)
        log.info("API on %s", self.url)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)
        import local_llm
        local_llm.ENGINE.stop()                # the API's shutdown does this too; this covers a stuck shutdown


def self_test() -> int:
    """Headless check for CI: the API answers, the app page is served, the engine binaries run."""
    import local_llm

    api = Api(local_llm.free_port())
    ok = True
    try:
        api.start()
        for path in ("/health", "/api/desktop/status", "/"):
            with urllib.request.urlopen(api.url + path, timeout=30) as r:
                body = r.read(400)
                print(f"{path}: {r.status} {body[:80]!r}")
                ok &= r.status == 200
        for build in ("vulkan", "cpu"):
            exe = local_llm.engine_root() / build / "llama-server.exe"
            out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            line = (out.stdout + out.stderr).strip().splitlines()
            print(f"engine {build}: exit {out.returncode} {line[-1] if line else ''}")
            ok &= out.returncode == 0
    except Exception:  # noqa: BLE001 - reported, and the test fails
        traceback.print_exc()
        ok = False
    finally:
        api.stop()
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def run_window(api: Api) -> None:
    import doosra_home
    import updates
    import webview

    window = webview.create_window(TITLE, api.url, width=1280, height=800, min_size=(960, 640), maximized=True,
                                   background_color="#0f1e16")
    updates.quit_app = window.destroy          # "Restart to update" in Settings closes the app this way
    # A saved profile (not private mode), kept with the app's data, so the page's own storage survives restarts.
    webview.start(private_mode=False, storage_path=str(doosra_home.home() / "webview"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Doosra")
    parser.add_argument("--self-test", action="store_true", help="check the app headless and exit")
    parser.add_argument("--port", type=int, help="serve on this port instead of the usual one")
    args = parser.parse_args(argv)

    configure_env()
    if not args.self_test and already_running():
        return 0
    log_path = setup_logging()
    log.info("Doosra starting (frozen=%s, home=%s)", FROZEN, os.environ.get("DOOSRA_HOME") or "default")
    if args.self_test:
        return self_test()
    try:
        api = Api(args.port or pick_port())
        api.start()
    except Exception as e:  # noqa: BLE001 - shown to the person, with where to look
        log.exception("start-up failed")
        if message_box(f"Doosra couldn't start:\n\n{e}\n\nOpen the log folder?", 0x10 | 0x4) == 6:   # Yes
            os.startfile(log_path.parent)      # noqa: S606 - our own log folder
        return 1
    try:
        run_window(api)
    except Exception as e:  # noqa: BLE001
        log.exception("window failed")
        message_box(f"Doosra's window couldn't open:\n\n{e}\n\n"
                    "Doosra needs Microsoft Edge WebView2, which comes with Windows 11. "
                    f"The log is in {log_path.parent}.")
        return 1
    finally:
        log.info("closing")
        api.stop()
        import updates
        if updates.run_pending_installer():
            log.info("started the update installer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
