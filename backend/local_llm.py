"""
The built-in AI engine: llama.cpp's llama-server, run as a child process.

The desktop app ships llama.cpp (MIT) instead of asking people to install LM
Studio, whose licence doesn't allow bundling it. The same engine runs
underneath LM Studio; here Doosra starts, watches and stops it.

Start order, stopping at the first that answers /health:
    1. the Vulkan build (GPU: Intel Arc, AMD, NVIDIA),
    2. Vulkan with cooperative-matrix off (a known Intel Arc driver bug),
    3. the CPU build.
The mode that worked is remembered, so the next start goes straight to it.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import doosra_home

CTX_SIZE = 16384
START_TIMEOUT = 180          # loading a 5 GB model from a slow disk can take a while
MODES = ("vulkan", "vulkan-nocoopmat", "cpu")


def engine_root() -> Path:
    """Where the llama.cpp builds live: inside the packaged app, or desktop/build/engine in a checkout."""
    env = os.environ.get("DOOSRA_ENGINE_DIR")
    if env:
        return Path(env)
    if doosra_home.frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "engine"
    return Path(__file__).resolve().parent.parent / "desktop" / "build" / "engine"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def command(mode: str, model: Path, port: int, ctx: int = CTX_SIZE) -> tuple[list[str], dict]:
    """The llama-server command line and extra environment for a start mode."""
    build = "cpu" if mode == "cpu" else "vulkan"
    exe = engine_root() / build / "llama-server.exe"
    args = [str(exe), "-m", str(model), "--jinja", "--ctx-size", str(ctx),
            "--host", "127.0.0.1", "--port", str(port),
            # reasoning arrives separately (reasoning_content), so the app can show it live
            "--reasoning-format", "deepseek"]
    if build == "vulkan":
        args += ["-ngl", "99"]                 # every layer on the GPU
    env = {"GGML_VK_DISABLE_COOPMAT": "1"} if mode == "vulkan-nocoopmat" else {}
    return args, env


_JOB = None


def _tie_to_this_process(proc: subprocess.Popen) -> None:
    """Windows: put the engine in a job object that dies with this process, so a crash or a
    Task Manager kill of Doosra never leaves a 5 GB model loaded. (A no-op elsewhere.)"""
    global _JOB
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if _JOB is None:
        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                       "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # 9 = extended info
            return
        _JOB = job                                              # held open for the life of this process
    k32.AssignProcessToJobObject(wintypes.HANDLE(_JOB), wintypes.HANDLE(int(proc._handle)))


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return json.loads(r.read() or b"{}").get("status") == "ok"
    except Exception:  # noqa: BLE001 - not up yet
        return False


@dataclass
class Engine:
    model: Path | None = None
    port: int | None = None
    mode: str | None = None
    error: str | None = None
    proc: subprocess.Popen | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def base_url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}/v1" if self.port else None

    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None and self.port and _healthy(self.port))

    def start(self, model: Path, preferred: str | None = None, ctx: int = CTX_SIZE) -> str:
        """Start the engine on `model`, trying the preferred mode first. Returns the mode that worked;
        raises RuntimeError with the last error when none does."""
        with self._lock:
            self._stop()
            if not Path(model).exists():
                raise RuntimeError(f"The model file isn't there: {model}")
            order = ([preferred] if preferred in MODES else []) + [m for m in MODES if m != preferred]
            last = "no start mode was tried"
            for mode in order:
                args, extra = command(mode, Path(model), free_port(), ctx)
                if not Path(args[0]).exists():
                    last = f"{args[0]} is missing"
                    continue
                port = int(args[args.index("--port") + 1])
                log = open(doosra_home.logs_dir() / "engine.log", "ab")
                log.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} mode={mode}\n".encode())
                proc = subprocess.Popen(args, env={**os.environ, **extra}, stdout=log, stderr=subprocess.STDOUT,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    _tie_to_this_process(proc)
                except Exception:  # noqa: BLE001 - best effort; a clean shutdown still stops it
                    pass
                deadline = time.time() + START_TIMEOUT
                while time.time() < deadline and proc.poll() is None and not _healthy(port):
                    time.sleep(0.5)
                if proc.poll() is None and _healthy(port):
                    self.model, self.port, self.mode, self.proc, self.error = Path(model), port, mode, proc, None
                    return mode
                last = f"mode {mode} didn't start (exit code {proc.poll()}); see logs/engine.log"
                proc.kill()
            self.error = last
            raise RuntimeError(last)

    def _stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc, self.port = None, None

    def stop(self) -> None:
        with self._lock:
            self._stop()

    def status(self) -> dict:
        return {"running": self.running(), "mode": self.mode, "model": self.model.name if self.model else None,
                "base_url": self.base_url, "error": self.error}


ENGINE = Engine()


def start_in_background(model: Path, preferred: str | None = None, on_ready=None) -> threading.Thread:
    """Load the model without blocking start-up (a 5 GB model takes a few seconds to minutes).
    `on_ready(engine)` runs once it answers -- the app uses it to point the copilot at the engine."""
    def run():
        try:
            ENGINE.start(model, preferred)
            if on_ready:
                on_ready(ENGINE)
        except RuntimeError:
            pass                                   # ENGINE.error says why; Settings shows it
    thread = threading.Thread(target=run, name="llama-server", daemon=True)
    thread.start()
    return thread
