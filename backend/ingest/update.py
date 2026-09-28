"""
Rebuilds the database on this machine straight from Cricsheet -- the same
steps the weekly GitHub Actions job runs, without needing a release.

    python -m ingest.update               # download, build, validate, install
    python -m ingest.update --no-download # rebuild from the files already in data/raw

Downloads all_json.zip, people.csv, names.csv and Cricsheet's coverage /
missing-match pages into data/raw, builds into
data/.build/cricket.duckdb, builds the derived tables, runs the validation
gate, and only then swaps the result in for data/cricket.duckdb (the previous
file is kept as cricket.duckdb.bak). Stop the API first.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

from analytics import build as analytics_build
from ingest import build_db, coverage, validate
from ingest.pull import TARGET, PullError, install

SOURCES = {
    "all_json.zip": "https://cricsheet.org/downloads/all_json.zip",
    "people.csv": "https://cricsheet.org/register/people.csv",
    "names.csv": "https://cricsheet.org/register/names.csv",
    **coverage.FILES,
}


def fetch(raw_dir: Path):
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        print(f"Downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "doosra-update"})
        tmp = raw_dir / (name + ".part")
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as fh:
            for chunk in iter(lambda: r.read(1 << 20), b""):
                fh.write(chunk)
        tmp.replace(raw_dir / name)


def update(download: bool = True, target: Path = TARGET) -> int:
    raw_dir = target.parent / "raw"
    if download:
        fetch(raw_dir)
    zpath = raw_dir / "all_json.zip"
    if not zpath.exists():
        print(f"error: {zpath} not found", file=sys.stderr)
        return 1

    work = target.parent / ".build"
    work.mkdir(parents=True, exist_ok=True)
    fresh = work / "cricket.duckdb"
    fresh.unlink(missing_ok=True)
    build_db.build([zpath], fresh, register_dir=raw_dir)
    analytics_build.build(fresh)

    rep = validate.validate(fresh)
    for w in rep.warnings:
        print(f"WARN  {w}")
    for f in rep.failures:
        print(f"FAIL  {f}")
    if rep.failures:
        print(f"Validation failed; the new build is left at {fresh} and nothing was installed.")
        return 1
    try:
        install(fresh, target)
    except PullError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Installed {target} (previous kept as {target.name}.bak).")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the cricket database from Cricsheet on this machine.")
    parser.add_argument("--no-download", action="store_true", help="use the files already in data/raw")
    args = parser.parse_args(argv)
    return update(download=not args.no_download)


if __name__ == "__main__":
    sys.exit(main())
