"""
Export Doosra's cricket database as a Hugging Face dataset: one Parquet file per table plus a
dataset card (README.md) with the schema, the rules for using it correctly, coverage, and the
Cricsheet attribution the ODC-By licence asks for.

    python ml/export_dataset.py                       # backend/data/cricket.duckdb -> ml/out/dataset
    python ml/export_dataset.py --db path.duckdb --out dir

DuckDB writes the Parquet itself (zstd), so this needs nothing beyond the backend's requirements.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import duckdb  # noqa: E402

from agent.tools import SQL_GUIDE  # noqa: E402

# table -> what it is (the card's table of contents, in this order)
TABLES = {
    "matches": "One row per match: format, competition (event_name), teams, venue, date, season, toss, result.",
    "deliveries": "One row per ball: innings, over and ball, batter, bowler, runs (batter, extras by type), wicket.",
    "deliveries_wickets": "Every dismissal on a ball (a ball can have two, for example a run out off a no-ball).",
    "players_matches": "Who played for which team in each match.",
    "people": "The Cricsheet register: one row per person, with their identifiers on other sites.",
    "batting_innings": "Derived: one row per batter per innings (runs, balls, boundaries, dismissal, batting "
                       "position, entry point, and expected runs/outs for the balls faced).",
    "bowling_innings": "Derived: one row per bowler per innings (legal balls, runs, wickets, dots, maidens, expected "
                       "runs/wickets).",
    "ball_expectation": "Derived: expected batter runs and dismissal chance, and expected bowler runs and wicket chance, "
                        "for every ball state (format group x gender x year x innings x over x wickets down).",
    "missing_matches": "Matches Cricsheet lists as missing or withheld from its data (a coverage gap, not an error).",
}


def build_info(con) -> dict:
    return {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM build_info").fetchall()}


def export(db: Path, out: Path) -> dict:
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)
    con = duckdb.connect(str(db), read_only=True)
    sizes = {}
    for table in TABLES:
        path = out / "data" / f"{table}.parquet"
        con.execute(f"COPY {table} TO '{path.as_posix()}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 250000)")
        rows = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        cols = con.execute(f"SELECT column_name, data_type FROM information_schema.columns "
                           f"WHERE table_name = '{table}' ORDER BY ordinal_position").fetchall()
        sizes[table] = {"rows": rows, "bytes": path.stat().st_size, "columns": cols}
        print(f"{table:20} {rows:>11,} rows  {path.stat().st_size / 1e6:8.1f} MB")
    stats = {
        "info": build_info(con),
        "by_format": con.execute("""
            SELECT match_type, gender, COUNT(*) AS matches, MIN(date) AS first, MAX(date) AS last
            FROM matches GROUP BY 1, 2 ORDER BY matches DESC""").fetchall(),
        "competitions": con.execute("SELECT COUNT(DISTINCT event_name) FROM matches").fetchone()[0],
    }
    con.close()
    (out / "README.md").write_text(card(sizes, stats), encoding="utf-8")
    return sizes


def card(sizes: dict, stats: dict) -> str:
    info = stats["info"]
    built = str(info.get("built_at", ""))[:10]
    latest = info.get("latest_match_date", "")
    configs = "\n".join(f"- config_name: {t}\n  data_files: data/{t}.parquet" for t in TABLES)
    toc = "\n".join(f"| `{t}` | {sizes[t]['rows']:,} | {TABLES[t]} |" for t in TABLES)
    formats = "\n".join(f"| {mt} | {g} | {n:,} | {a} | {b} |" for mt, g, n, a, b in stats["by_format"])
    schemas = "\n\n".join(
        f"<details><summary><code>{t}</code> ({len(sizes[t]['columns'])} columns)</summary>\n\n"
        + "\n".join(f"- `{c}` {ty}" for c, ty in sizes[t]["columns"]) + "\n\n</details>" for t in TABLES)
    return f"""---
license: odc-by
pretty_name: Doosra cricket ball-by-ball
language:
- en
tags:
- cricket
- sports
- sports-analytics
- tabular
size_categories:
- 10M<n<100M
configs:
{configs}
---

# Doosra: cricket, ball by ball

Every ball of {sum(r[2] for r in stats['by_format']):,} cricket matches (men's and women's internationals and the major
leagues and domestic competitions, {stats['competitions']:,} competitions), cleaned, validated and ready to query, with
per-innings batting and bowling tables derived from it. Built {built}; matches up to {latest}.

This is the database behind [Doosra](https://github.com/sarthak213/Doosra), a cricket analytics workbench (player
hubs, comparisons, context-adjusted metrics such as true strike rate, a local AI copilot). It is rebuilt every week
from [Cricsheet](https://cricsheet.org/).

## Tables

| Table | Rows | What it is |
|---|---:|---|
{toc}

```python
from datasets import load_dataset
deliveries = load_dataset("{{repo_id}}", "deliveries", split="train")
```

Or query the Parquet files directly, with no download step:

```python
import duckdb
duckdb.sql(\"\"\"
    SELECT d.batter, SUM(d.runs_batter) AS runs
    FROM 'hf://datasets/{{repo_id}}/data/deliveries.parquet' d
    JOIN 'hf://datasets/{{repo_id}}/data/matches.parquet' m USING (match_id)
    WHERE m.event_name = 'Indian Premier League' AND NOT d.is_super_over
    GROUP BY 1 ORDER BY runs DESC LIMIT 5
\"\"\").show()
```

## Using it correctly

Cricket data has traps that make hand-written queries silently wrong. The rules Doosra's own tools follow:

```text
{SQL_GUIDE.strip()}
```

## Coverage

| match_type | gender | matches | first | last |
|---|---|---:|---|---|
{formats}

Cricsheet doesn't have every match: `missing_matches` lists the ones it knows are missing or withheld. Some teams
are affected as a whole (for example, there are no Afghanistan matches), so check coverage before comparing teams.

## Schema

{schemas}

## Licence and attribution

The data is from [Cricsheet](https://cricsheet.org/) and is made available under the
[Open Data Commons Attribution License (ODC-By 1.0)](https://opendatacommons.org/licenses/by/1-0/). If you use it,
credit Cricsheet. The derived tables and this packaging are released under the same licence by the Doosra project.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "backend" / "data" / "cricket.duckdb")
    parser.add_argument("--out", type=Path, default=ROOT / "ml" / "out" / "dataset")
    args = parser.parse_args()
    sizes = export(args.db, args.out)
    print(f"\n{sum(s['bytes'] for s in sizes.values()) / 1e6:.0f} MB in {args.out}")


if __name__ == "__main__":
    main()
