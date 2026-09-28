"""
Downloads the latest published database from the project's GitHub Releases.

    python -m ingest.pull            # download, verify and install if newer
    python -m ingest.pull --check    # only say whether a newer build exists
    python -m ingest.pull --force    # reinstall even if up to date

The `data-latest` release carries cricket.duckdb.zst (the database, zstd
compressed), manifest.json (hashes, counts, build date) and DATA_NOTICE.md.
The download is checked against the manifest's sha256 before and after
decompression, then swapped in atomically; the previous database is kept as
cricket.duckdb.bak. Stop the API first: a database that's open can't be
replaced.

Uses the public GitHub API, no token needed (set GITHUB_TOKEN to lift the
anonymous rate limit). DOOSRA_DATA_REPO overrides the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

import duckdb
from tqdm import tqdm

from ingest.build_db import SCHEMA_VERSION, sha256

REPO = os.environ.get("DOOSRA_DATA_REPO", "sarthak213/Doosra")
TAG = "data-latest"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TARGET = DATA_DIR / "cricket.duckdb"


class PullError(RuntimeError):
    pass


def _request(url: str, accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "doosra-pull"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=60)


def latest_release(repo: str = REPO, tag: str = TAG) -> dict:
    try:
        with _request(f"https://api.github.com/repos/{repo}/releases/tags/{tag}") as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise PullError(f"no '{tag}' release found in {repo}") from e
        raise


def _asset_url(release: dict, name: str) -> str:
    for a in release.get("assets", []):
        if a["name"] == name:
            return a["browser_download_url"]
    raise PullError(f"release {release.get('tag_name')} has no {name}")


def download(url: str, dest: Path):
    with _request(url, accept="application/octet-stream") as r, open(dest, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0) or None
        with tqdm(total=total, unit="B", unit_scale=True, desc=dest.name) as bar:
            for chunk in iter(lambda: r.read(1 << 20), b""):
                fh.write(chunk)
                bar.update(len(chunk))


def _zstd_open(path: Path):
    try:
        from compression import zstd  # Python 3.14+
        return zstd.open(path, "rb")
    except ImportError:
        import zstandard  # older Pythons: pip install zstandard
        return zstandard.open(path, "rb")


def decompress(src: Path, dest: Path):
    with _zstd_open(src) as fin, open(dest, "wb") as fout:
        shutil.copyfileobj(fin, fout, 1 << 20)


def local_info(path: Path = TARGET) -> dict | None:
    """build_info of a database file, or None if there isn't one."""
    if not path.exists():
        return None
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error:
        return None
    try:
        return {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM build_info").fetchall()}
    except duckdb.Error:
        return {}
    finally:
        con.close()


def install(new: Path, target: Path = TARGET):
    """Swap `new` in for `target`, keeping the old file as .bak."""
    backup = target.with_suffix(target.suffix + ".bak")
    if target.exists():
        try:
            if backup.exists():
                backup.unlink()
            target.rename(backup)
        except PermissionError as e:
            raise PullError(f"{target} is in use -- stop the API (uvicorn) and try again") from e
    os.replace(new, target)


def check_database(path: Path):
    """The file opens and is a schema this code understands."""
    info = local_info(path)
    if info is None:
        raise PullError(f"{path.name} doesn't open as a database")
    if info.get("schema_version") != SCHEMA_VERSION:
        raise PullError(f"the database is schema {info.get('schema_version')}, this code expects "
                        f"{SCHEMA_VERSION} -- update the code (git pull) first")


def pull(check_only: bool = False, force: bool = False, target: Path = TARGET) -> int:
    release = latest_release()
    work = target.parent / ".download"
    work.mkdir(parents=True, exist_ok=True)
    manifest_path = work / "manifest.json"
    download(_asset_url(release, "manifest.json"), manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    have = local_info(target) or {}
    print(f"Installed: built {have.get('built_at', 'none')}, matches up to {have.get('latest_match_date', '-')}")
    print(f"Published: built {manifest['built_at']}, matches up to {manifest['latest_match']} "
          f"({manifest['asset']['bytes'] / 1e6:,.0f} MB download)")
    newer = have.get("built_at") != manifest["built_at"]
    if check_only:
        print("A newer build is available." if newer else "Up to date.")
        return 0
    if not newer and not force:
        print("Up to date.")
        return 0

    asset = work / manifest["asset"]["name"]
    download(_asset_url(release, manifest["asset"]["name"]), asset)
    if sha256(asset) != manifest["asset"]["sha256"]:
        raise PullError("download corrupted (sha256 mismatch) -- try again")
    fresh = work / "cricket.duckdb"
    print("Decompressing...")
    decompress(asset, fresh)
    if sha256(fresh) != manifest["database"]["sha256"]:
        raise PullError("decompressed database doesn't match the manifest -- try again")
    check_database(fresh)
    install(fresh, target)
    try:
        download(_asset_url(release, "DATA_NOTICE.md"), target.parent / "DATA_NOTICE.md")
    except PullError:
        pass
    asset.unlink(missing_ok=True)
    print(f"Installed {target} (previous kept as {target.name}.bak). {manifest['attribution']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Download the latest published cricket database.")
    parser.add_argument("--check", action="store_true", help="only report whether a newer build exists")
    parser.add_argument("--force", action="store_true", help="reinstall even if up to date")
    args = parser.parse_args(argv)
    try:
        return pull(check_only=args.check, force=args.force)
    except (PullError, urllib.error.URLError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
