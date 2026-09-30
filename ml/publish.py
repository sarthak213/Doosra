"""
Publish Doosra's dataset, models and demo Spaces to Hugging Face.

Sign in first, yourself (the token never passes through this script):

    hf auth login

then, from the repository root:

    python ml/publish.py dataset                 # ml/out/dataset -> <you>/doosra-cricket
    python ml/publish.py dataset --dry-run       # show what would be uploaded, and where
    python ml/publish.py winprob                 # ml/out/hf/winprob-model -> <you>/doosra-win-probability
    python ml/publish.py winprob-space           # ml/out/hf/winprob-space -> spaces/<you>/doosra-win-probability

Everything published is public. Repos are created if they don't exist; uploads replace the
files they name and leave the rest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"


def prefer_ipv4() -> None:
    """Try IPv4 addresses first. Some networks have a broken IPv6 route to Hugging Face's CDN: the connection
    is reset, and Python (unlike browsers) doesn't fall back to IPv4 on its own."""
    import socket

    original = socket.getaddrinfo

    def ordered(*args, **kwargs):
        return sorted(original(*args, **kwargs), key=lambda a: a[0] != socket.AF_INET)

    socket.getaddrinfo = ordered


prefer_ipv4()

TARGETS = {
    # name: (repo suffix, repo type, local folder)
    "dataset": ("doosra-cricket", "dataset", OUT / "dataset"),
    "winprob": ("doosra-win-probability", "model", OUT / "hf" / "winprob-model"),
    "winprob-space": ("doosra-win-probability", "space", OUT / "hf" / "winprob-space"),
    "ball-outcome": ("doosra-ball-outcome", "model", OUT / "hf" / "ball-outcome-model"),
    "toolcall-data": ("doosra-toolcalls", "dataset", OUT / "hf" / "toolcall-data"),
    # the fine-tuned models' cards and loss plots (the notebook already pushed the model files; these are kept)
    "toolcall-a": ("doosra-qwen3.5-4b-toolcalls-a", "model", OUT / "hf" / "toolcall-model-a"),
    "toolcall-b": ("doosra-qwen3.5-4b-toolcalls-b", "model", OUT / "hf" / "toolcall-model-b"),
    "toolcall-b-step350": ("doosra-qwen3.5-4b-toolcalls-b-step350", "model", OUT / "hf" / "toolcall-model-b-step350"),
}


def whoami() -> str:
    from huggingface_hub import HfApi
    from huggingface_hub.errors import LocalTokenNotFoundError

    try:
        return HfApi().whoami()["name"]
    except LocalTokenNotFoundError:
        sys.exit("Not signed in to Hugging Face: run `hf auth login` first.")
    except OSError as e:
        sys.exit(f"Couldn't reach Hugging Face ({e}); try again.")


def publish(target: str, dry_run: bool = False) -> str:
    suffix, repo_type, folder = TARGETS[target]
    if not (folder / "README.md").exists():
        sys.exit(f"{folder} isn't built yet (see the script for {target}).")
    user = "<you>" if dry_run else whoami()
    repo_id = f"{user}/{suffix}"
    readme = folder / "README.md"
    text = readme.read_text(encoding="utf-8")
    if ("{repo_id}" in text or "{user}" in text) and not dry_run:     # cards name their own and sibling repos
        readme.write_text(text.replace("{repo_id}", repo_id).replace("{user}", user), encoding="utf-8")
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    print(f"{repo_type} {repo_id}: {len(files)} files, {sum(p.stat().st_size for p in files) / 1e6:.0f} MB")
    for p in files:
        print("  ", p.relative_to(folder).as_posix())
    if dry_run:
        return repo_id
    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(repo_id, repo_type=repo_type, exist_ok=True,
                    **({"space_sdk": "static"} if repo_type == "space" else {}))
    api.upload_folder(repo_id=repo_id, repo_type=repo_type, folder_path=str(folder),
                      commit_message=f"Publish {target} from Doosra")
    kind = {"dataset": "datasets/", "space": "spaces/"}.get(repo_type, "")
    url = f"https://huggingface.co/{kind}{repo_id}"
    print("published:", url)
    return repo_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=sorted(TARGETS))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    publish(args.target, args.dry_run)


if __name__ == "__main__":
    main()
