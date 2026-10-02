"""
The desktop app's own state: which AI engine and model to use, what this PC can run, and what's
installed. Used by the first-run setup screen and the Settings page (api/setup_routes.py).

Only active when DOOSRA_DESKTOP=1 (set by the launcher): a hosted or Docker instance never
exposes any of it.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

import doosra_home
import local_llm

REGISTRY = json.loads((Path(__file__).resolve().parent / "models.json").read_text(encoding="utf-8"))["models"]
DEFAULTS = {"engine": "builtin", "model": None, "model_path": None, "engine_mode": None,
            "ctx_size": local_llm.CTX_SIZE, "lmstudio_model": None}
LMSTUDIO_URL = "http://localhost:1234/v1"
GB = 1024 ** 3


def enabled() -> bool:
    return os.environ.get("DOOSRA_DESKTOP") == "1"


# -- settings -----------------------------------------------------------------

def load_settings() -> dict:
    try:
        saved = json.loads(doosra_home.settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save_settings(**changes) -> dict:
    settings = {**load_settings(), **{k: v for k, v in changes.items() if k in DEFAULTS}}
    path = doosra_home.settings_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    tmp.replace(path)
    return settings


# -- models ---------------------------------------------------------------------

def model_info(model_id: str) -> dict:
    for m in REGISTRY:
        if m["id"] == model_id:
            return m
    raise KeyError(f"Unknown model '{model_id}'")


def compact(model_id: str | None) -> bool:
    """Whether a model runs with the short prompt and tool list it was fine-tuned on (agent.graph COMPACT)."""
    try:
        return bool(model_id) and bool(model_info(model_id).get("compact"))
    except KeyError:
        return False


def model_path(model_id: str, settings: dict | None = None) -> Path:
    """Where the model file is: a file the user pointed us at (e.g. LM Studio's copy), or our folder."""
    settings = settings or load_settings()
    if settings.get("model") == model_id and settings.get("model_path") and Path(settings["model_path"]).exists():
        return Path(settings["model_path"])
    return doosra_home.models_dir() / model_info(model_id)["file"]


def installed_models(settings: dict | None = None) -> list[str]:
    settings = settings or load_settings()
    return [m["id"] for m in REGISTRY if model_path(m["id"], settings).exists()]


def lmstudio_copies() -> dict[str, Path]:
    """Our models already downloaded by LM Studio, which can be used in place instead of a second
    multi-GB download. Setup checks each one's sha256 before using it."""
    root = Path.home() / ".lmstudio" / "models"
    if not root.exists():
        return {}
    return {m["id"]: hit for m in REGISTRY if (hit := next(root.rglob(m["file"]), None))}


# -- this PC ---------------------------------------------------------------------

def total_ram_gb() -> float:
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return round(stat.ullTotalPhys / GB, 1)
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GB, 1)
    except (AttributeError, ValueError, OSError):
        return 0.0


_DEVICE = re.compile(r"^\s*(\w+\d*): (.+?) \((\d+) MiB, (\d+) MiB free\)", re.M)


def gpus() -> list[dict]:
    """GPUs the bundled engine can use (from `llama-server --list-devices`)."""
    exe = local_llm.engine_root() / "vulkan" / "llama-server.exe"
    if not exe.exists():
        return []
    try:
        out = subprocess.run([str(exe), "--list-devices"], capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [{"id": m[0], "name": m[1].strip(), "memory_gb": round(int(m[2]) / 1024, 1),
             "free_gb": round(int(m[3]) / 1024, 1)} for m in _DEVICE.findall(out)]


def hardware() -> dict:
    home = doosra_home.data_dir()
    gpu = gpus()
    ram = total_ram_gb()
    return {"ram_gb": ram, "gpus": gpu, "free_disk_gb": round(shutil.disk_usage(home).free / GB, 1),
            "recommended": recommend(ram, gpu)}


def recommend(ram_gb: float, gpu_list: list[dict]) -> str:
    """Doosra's fine-tuned 4B on every PC: in the v3 evaluation it answered more questions right than the 9B
    (33 of 40 against 30), at half the size and about 2.5x the speed (ml/toolcall/, model card). The 9B is still
    offered, for questions far outside Doosra's tools."""
    return "qwen3.5-4b-doosra"


# -- LM Studio, if the user already has it ---------------------------------------------

def lmstudio() -> dict:
    try:
        with urllib.request.urlopen(f"{LMSTUDIO_URL}/models", timeout=2) as r:
            models = [m["id"] for m in json.loads(r.read()).get("data", []) if "embed" not in m["id"]]
        return {"running": True, "models": models}
    except Exception:  # noqa: BLE001 - not installed or not started
        return {"running": False, "models": []}
