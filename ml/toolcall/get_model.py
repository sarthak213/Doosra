"""
Download one of Doosra's registered models (backend/models.json) into Doosra's models folder, checksum-
verified and resumable, the same file first-run setup would fetch:

    python ml/toolcall/get_model.py qwen3.5-4b

Prefers IPv4: on some networks the IPv6 route to Hugging Face resets connections.
"""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: sorted(_getaddrinfo(*a, **k), key=lambda x: x[0] != socket.AF_INET)

import desktop_app  # noqa: E402
import doosra_home  # noqa: E402
import downloads  # noqa: E402


def main(model_id: str) -> None:
    info = desktop_app.model_info(model_id)
    dest = doosra_home.models_dir() / info["file"]
    url = f"https://huggingface.co/{info['repo']}/resolve/main/{info['file']}"
    print(f"{model_id}: {url} -> {dest}", flush=True)
    last = [0.0]

    def progress(done, total):
        if time.time() - last[0] > 15:
            last[0] = time.time()
            print(f"  {done / 1e9:.2f} / {(total or 0) / 1e9:.2f} GB", flush=True)
    downloads.download(url, dest, sha256=info["sha256"], progress=progress)
    print("done, checksum verified:", dest, flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "qwen3.5-4b")
