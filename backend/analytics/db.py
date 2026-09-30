"""
The one place that knows where the cricket database lives and how to open it.

Every reader (the agent's tools, the analytics engine, the APIs) goes
through connect(), so tests can point the whole app at a fixture database
by setting `analytics.db.DB_PATH`.
"""

from pathlib import Path

import duckdb

import doosra_home

# backend/data in a checkout; %LOCALAPPDATA%\Doosra in the desktop app; DOOSRA_HOME overrides.
DB_PATH = doosra_home.data_dir() / "cricket.duckdb"


def connect():
    """Read-only connection with external file/network access disabled, so
    table functions like read_csv()/read_text() can't be used to read or
    exfiltrate files from inside a SELECT, and nothing can mutate the data."""
    return duckdb.connect(str(DB_PATH), read_only=True, config={"enable_external_access": False})


def connect_writable(path=None):
    """Read-write connection -- only for offline build steps (ingest, derived
    tables), never for request handling."""
    return duckdb.connect(str(path or DB_PATH))


def query(sql: str, params=None) -> list[dict]:
    con = connect()
    try:
        cur = con.execute(sql, params or [])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()
