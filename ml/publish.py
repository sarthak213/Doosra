"""
Publish Doosra's dataset, models and demo Spaces to Hugging Face.

Sign in first, yourself (the token never passes through this script):

    hf auth login

then, from the repository root:

    python ml/publish.py dataset                 # ml/out/dataset -> <you>/doosra-cricket
    python ml/publish.py dataset --dry-run       # show what would be uploaded, and where

Everything published is public. Repos are created if they don't exist; uploads replace the
files they name and leave the rest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"

TARGETS = {
    # name: (repo suffix, repo type, local folder)
    "dataset": ("doosra-cricket", "dataset", OUT / "dataset"),
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
    if "{repo_id}" in text and not dry_run:      # the card names its own repo in its code snippets
        readme.write_text(text.replace("{repo_id}", repo_id), encoding="utf-8")
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    print(f"{repo_type} {repo_id}: {len(files)} files, {sum(p.stat().st_size for p in files) / 1e6:.0f} MB")
    for p in files:
        print("  ", p.relative_to(folder).as_posix())
    if dry_run:
        return repo_id
    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(repo_id, repo_type=repo_type, exist_ok=True)
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
