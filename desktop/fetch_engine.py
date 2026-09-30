"""
Fetch the llama.cpp server that ships inside the desktop app.

    python desktop/fetch_engine.py            # into desktop/build/engine/{vulkan,cpu}

The build is pinned: the release and each file's sha256 are fixed here, and a
download that doesn't match is refused. Bump them together (from the release's
asset list) to update the engine. llama.cpp is MIT-licensed; its licence goes
into THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

RELEASE = "b11249"
BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{RELEASE}"
BUILDS = {
    # GPU through Vulkan: Intel Arc, AMD and NVIDIA on Windows.
    "vulkan": ("llama-b11249-bin-win-vulkan-x64.zip",
               "32cf3b0e3477263c959ae17a08882f9b1e091c4d6d93ccc0631efd2185f5f87d"),
    # The fallback when no usable GPU driver is present.
    "cpu": ("llama-b11249-bin-win-cpu-x64.zip",
            "ba0beb2076250970c113b90c0e60611909bc1e7e973ee92a416756a64dd0763c"),
}
OUT = Path(__file__).resolve().parent / "build" / "engine"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(kind: str) -> Path:
    name, digest = BUILDS[kind]
    target = OUT / kind
    if (target / "llama-server.exe").exists() and (target / ".release").read_text().strip() == RELEASE:
        print(f"{kind}: {RELEASE} already here")
        return target
    zpath = OUT / name
    OUT.mkdir(parents=True, exist_ok=True)
    if not zpath.exists() or sha256(zpath) != digest:
        print(f"{kind}: downloading {name}")
        with urllib.request.urlopen(f"{BASE}/{name}", timeout=120) as r, open(zpath, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
    got = sha256(zpath)
    if got != digest:
        zpath.unlink(missing_ok=True)
        raise SystemExit(f"{name}: sha256 {got} doesn't match the pinned {digest}; refusing it.")
    shutil.rmtree(target, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(target)
    # Some releases nest the files in a folder; flatten so llama-server.exe is at the top.
    if not (target / "llama-server.exe").exists():
        found = next(target.rglob("llama-server.exe"), None)
        if not found:
            raise SystemExit(f"{name}: no llama-server.exe inside")
        for item in found.parent.iterdir():
            shutil.move(str(item), target / item.name)
    (target / ".release").write_text(RELEASE)
    zpath.unlink()
    print(f"{kind}: {RELEASE} ready in {target}")
    return target


if __name__ == "__main__":
    for kind in (sys.argv[1:] or BUILDS):
        fetch(kind)
