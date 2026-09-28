"""
Writes the manifest.json published with each data release.

    python -m ingest.manifest --db data/cricket.duckdb --asset cricket.duckdb.zst --out manifest.json

The manifest says what the release contains and lets `python -m ingest.pull`
verify the download: sha256 of the compressed asset and of the database
inside it, row counts, source hashes, build date and the code version.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import duckdb

from ingest.build_db import sha256
from ingest.validate import row_counts

ATTRIBUTION = "Data: Cricsheet (cricsheet.org), Open Data Commons Attribution License (ODC-By) 1.0."


def make_manifest(db_path: Path, asset: Path, tag: str | None = None) -> dict:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        info = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM build_info").fetchall()}
        rows = row_counts(con)
    finally:
        con.close()
    return {
        "tag": tag,
        "schema_version": info.get("schema_version"),
        "built_at": info.get("built_at"),
        "latest_match": info.get("latest_match_date"),
        "git_sha": os.environ.get("GITHUB_SHA"),
        "sources": info.get("sources"),
        "rows": rows,
        "database": {"name": "cricket.duckdb", "sha256": sha256(db_path), "bytes": db_path.stat().st_size},
        "asset": {"name": asset.name, "sha256": sha256(asset), "bytes": asset.stat().st_size},
        "attribution": ATTRIBUTION,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Write a data release manifest.")
    parser.add_argument("--db", default="data/cricket.duckdb")
    parser.add_argument("--asset", required=True, help="the compressed database file being published")
    parser.add_argument("--tag", help="release tag, e.g. data-2026-09-28")
    parser.add_argument("--out", default="manifest.json")
    args = parser.parse_args(argv)
    doc = make_manifest(Path(args.db), Path(args.asset), args.tag)
    Path(args.out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
