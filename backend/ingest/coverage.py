"""
What the data covers and what it's missing, from Cricsheet's own pages:

    https://cricsheet.org/coverage/   periods covered + matches held / played,
                                      by match type, competition and team
    https://cricsheet.org/missing/    every match known to be missing, by
                                      match type and competition
    https://cricsheet.org/matches/    the note on withheld matches

Cricsheet publishes these as web pages only, so the data pipeline downloads
them (FILES, next to people.csv) and this module parses them into tables:

    coverage_periods  gender, kind (match type / competition), name,
                      earliest_checked, earliest_provided ('YYYY-MM')
    coverage_counts   gender (NULL for teams), kind (competition / team),
                      name, have, total, pct
    missing_matches   kind (match type / competition), name, gender, date,
                      team1, team2
    coverage_info     key, value: withheld match count and the source pages

    python -m ingest.coverage --dir data/raw            # parse and summarise
    python -m ingest.coverage --dir data/raw --fetch    # download first
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

BASE = "https://cricsheet.org"
FILES = {
    "cricsheet_coverage.html": f"{BASE}/coverage/",
    "cricsheet_missing.html": f"{BASE}/missing/",
    "cricsheet_matches.html": f"{BASE}/matches/",
}
TABLES = ("coverage_periods", "coverage_counts", "missing_matches", "coverage_info")
MATCH_TYPES = {"test": "Test Matches", "odi": "One-day Internationals"}


class _Events(HTMLParser):
    """Flattens a page into the events the parsers need, in document order:
    ("h", level, text), ("row", [cells]), ("dt", text), ("dd", text)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events: list[tuple] = []
        self._tag = None
        self._text: list[str] = []
        self._cells: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd"):
            self._tag, self._text = tag, []
        elif tag == "tr":
            self._cells = []
        elif tag in ("td", "th") and self._cells is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == self._tag:
            text = " ".join("".join(self._text).split())
            if tag.startswith("h"):
                self.events.append(("h", int(tag[1]), text))
            else:
                self.events.append((tag, text))
            self._tag = None
        elif tag in ("td", "th") and self._cell is not None and self._cells is not None:
            self._cells.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._cells is not None:
            self.events.append(("row", self._cells))
            self._cells = None

    def handle_data(self, data):
        if self._tag:
            self._text.append(data)
        if self._cell is not None:
            self._cell.append(data)


def _events(html: str) -> list[tuple]:
    p = _Events()
    p.feed(html)
    p.close()
    return p.events


def _gender(text: str) -> str | None:
    t = text.lower()
    if "women" in t or "female" in t:
        return "female"
    if "men" in t or "male" in t:
        return "male"
    return None


def _month(text: str) -> str | None:
    try:
        return dt.datetime.strptime(text.strip(), "%b %Y").strftime("%Y-%m")
    except ValueError:
        return None


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_coverage(html: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The /coverage/ page -> (periods, counts)."""
    periods, counts = [], []
    section = gender = None
    for ev in _events(html):
        if ev[0] == "h":
            level, text = ev[1], ev[2].lower()
            if level == 3:
                section = "periods" if "period" in text else "numbers" if "number" in text else None
                gender = None
            elif level == 4:
                # "Women's Matches" / "Men's Matches" (periods); "By Competition" / "By Team" (numbers)
                gender = _gender(text) if section == "periods" else None
                if section == "numbers":
                    section = "teams" if "team" in text else "numbers"
            elif level == 6 and section == "numbers":
                gender = _gender(text)
            continue
        if ev[0] != "row" or len(ev[1]) < 3:
            continue
        name, a, b = ev[1][:3]
        if name in ("Match Type", "Competition", "Team"):
            continue
        if section == "periods" and gender:
            kind = "match type" if name in _PERIOD_TYPES else "competition"
            periods.append({"gender": gender, "kind": kind, "name": name,
                            "earliest_checked": _month(a), "earliest_provided": _month(b)})
        elif section in ("numbers", "teams"):
            if section == "numbers" and not gender:
                continue  # the combined table repeats the per-gender ones
            m = re.fullmatch(r"([\d,]+) of ([\d,]+)", a)
            if not m:
                continue
            have, total = (int(x.replace(",", "")) for x in m.groups())
            counts.append({"gender": gender if section == "numbers" else None,
                           "kind": "competition" if section == "numbers" else "team", "name": name,
                           "have": have, "total": total, "pct": float(b) if b else None})
    return (pd.DataFrame(periods, columns=["gender", "kind", "name", "earliest_checked", "earliest_provided"]),
            pd.DataFrame(counts, columns=["gender", "kind", "name", "have", "total", "pct"]))


_PERIOD_TYPES = {"Test Matches", "Multi-day Matches", "One-day Internationals", "One-day matches",
                 "T20 Internationals", "International T20s"}


def parse_missing(html: str, competitions: list[str] = ()) -> pd.DataFrame:
    """The /missing/ page -> one row per missing match. Competition headings
    there are lower-cased ("One day cup ( australia) Matches"), so they're
    mapped back to the proper names from the coverage page where possible."""
    proper = {_key(c): c for c in competitions}
    rows = []
    kind = name = gender = date = None
    for ev in _events(html):
        if ev[0] == "h":
            level, text = ev[1], ev[2]
            if level == 4:
                low = text.lower()
                kind = "match type" if "match type" in low else "competition" if "competition" in low else None
            elif level == 5:
                raw = re.sub(r"\s*matches\s*$", "", text, flags=re.I).strip()
                if kind == "match type":
                    name = MATCH_TYPES.get(raw.lower(), raw)
                else:
                    name = proper.get(_key(raw), raw)
                gender = date = None
            elif level == 6:
                gender = _gender(text)
            continue
        if ev[0] == "dt":
            date = ev[1]
        elif ev[0] == "dd" and kind and name and date:
            teams = [t.strip() for t in ev[1].split(" vs ", 1)]
            rows.append({"kind": kind, "name": name, "gender": gender, "date": date,
                         "team1": teams[0], "team2": teams[1] if len(teams) > 1 else None})
    return pd.DataFrame(rows, columns=["kind", "name", "gender", "date", "team1", "team2"])


def parse_withheld(html: str) -> dict:
    """The /matches/ page's note on withheld matches."""
    text = " ".join(re.sub(r"<[^>]+>", " ", html).split())
    m = re.search(r"([\d,]+) matches are currently being withheld", text)
    out = {"withheld_matches": int(m.group(1).replace(",", "")) if m else None}
    m = re.search(r"These matches (.+?)\.", text)
    if m:
        out["withheld_reason"] = f"These matches {m.group(1)}."
    return out


def fetch(raw_dir: Path):
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        req = urllib.request.Request(url, headers={"User-Agent": "doosra-update"})
        with urllib.request.urlopen(req, timeout=60) as r:
            (raw_dir / name).write_bytes(r.read())


def load(con, raw_dir: Path) -> dict:
    """Parse the downloaded pages (whichever exist) into the coverage tables."""
    paths = {n: raw_dir / n for n in FILES}
    if not all(p.exists() for p in paths.values()):
        return {}
    periods, counts = parse_coverage(paths["cricsheet_coverage.html"].read_text(encoding="utf-8"))
    comps = sorted(set(periods.loc[periods["kind"] == "competition", "name"]) | set(
        counts.loc[counts["kind"] == "competition", "name"]))
    missing = parse_missing(paths["cricsheet_missing.html"].read_text(encoding="utf-8"), comps)
    info = parse_withheld(paths["cricsheet_matches.html"].read_text(encoding="utf-8"))
    info.update({f"source_{n.split('_')[1].split('.')[0]}": u for n, u in FILES.items()})
    info_df = pd.DataFrame([{"key": k, "value": None if v is None else str(v)} for k, v in info.items()],
                           columns=["key", "value"])
    for table, df in (("coverage_periods", periods), ("coverage_counts", counts),
                      ("missing_matches", missing), ("coverage_info", info_df)):
        con.register("_cov_df", df)
        try:
            con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM _cov_df")
        finally:
            con.unregister("_cov_df")
    return {"coverage_periods": len(periods), "coverage_counts": len(counts), "missing_matches": len(missing)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Parse Cricsheet's coverage and missing-match pages.")
    parser.add_argument("--dir", default="data/raw", help="folder holding (or to download) the pages")
    parser.add_argument("--fetch", action="store_true", help="download the pages first")
    args = parser.parse_args(argv)
    raw = Path(args.dir)
    if args.fetch:
        fetch(raw)
    import duckdb

    con = duckdb.connect()
    counts = load(con, raw)
    if not counts:
        print(f"error: pages not found in {raw} (run with --fetch)", file=sys.stderr)
        return 1
    print(counts)
    print(con.execute("SELECT kind, name, gender, COUNT(*) FROM missing_matches GROUP BY ALL ORDER BY 4 DESC").df())
    print(con.execute("SELECT * FROM coverage_info").df())
    return 0


if __name__ == "__main__":
    sys.exit(main())
