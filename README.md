# Doosra

A cricket analytics workbench: player hubs, comparisons, a query builder and
a player matrix over ball-by-ball data, with context-adjusted metrics (true
strike rate, match factor, era factor...) and an AI copilot that can explain
any view or drive the app. Every analytics capability is also an MCP tool, so
any MCP-capable app (Claude Desktop and others) can use it directly.

Data: [Cricsheet](https://cricsheet.org/) ball-by-ball data and register,
under the [ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/) licence
(see [DATA_NOTICE.md](DATA_NOTICE.md)). Rebuilt weekly by GitHub Actions and
published as a release the app downloads.

## Architecture

```text
backend/
├── ingest/                 # the data pipeline
│   ├── build_db.py         #   Cricsheet zip/JSON + register -> DuckDB tables (batched)
│   ├── validate.py         #   release gate: schema, counts, invariants, frozen careers
│   ├── manifest.py         #   release manifest (hashes, counts, build date)
│   ├── pull.py             #   download + verify + install the published database
│   └── update.py           #   rebuild locally from Cricsheet (no CI needed)
├── analytics/              # the analytics layer (no LLM involved)
│   ├── db.py               #   the one place that opens the database (read-only)
│   ├── catalog.py          #   name resolution: players, competitions, teams, venues, formats
│   ├── scope.py            #   shared filters -> SQL, and the per-ball scoring rules
│   ├── build.py            #   derived per-innings tables + ball-state expectations
│   ├── registry.py         #   the metric registry: every metric declared once
│   ├── engine.py           #   query builder: stats, splits, form, arcs, percentiles, matrix...
│   └── results.py          #   the result envelope + formatting/highlight helpers
├── mcp_server/             # every capability as an MCP tool (stdio or HTTP at /mcp)
├── agent/                  # the copilot
│   ├── graph.py            #   LangGraph state machine over the MCP tools, grounding guards
│   ├── prompts.py          #   system prompt (tool routing, filters, worked examples)
│   ├── stats.py            #   team/venue/matchup/records tools (ball-level SQL)
│   ├── tools.py            #   guarded run_sql, schema guide, autocomplete search
│   └── cancellation.py     #   in-flight request cancellation
├── api/                    # REST endpoints behind the UI views + saved views/watchlist
├── tests/                  # pytest suite (both DB schemas) + eval questions
└── main.py                 # FastAPI app: /api, /mcp, SSE chat streams

frontend/                   # React app: Player Hub, Compare, Query, Matrix, Ask + copilot drawer
```

The UI, the copilot and external MCP clients all go through the same engine,
so a number on screen and a number the copilot quotes are the same number.

## Setup

```bash
cd backend
pip install -r requirements.txt

# 1. Get the database: download the latest validated weekly build
python -m ingest.pull
#    ...or build it yourself from Cricsheet (~3 minutes, same steps as CI)
python -m ingest.update

# 2. LLM config: copy and edit (LM Studio / Ollama locally, or Groq)
cp .env.example .env

# 3. Run the API
uvicorn main:app --reload --port 8000
```

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173 (expects the API on :8000; override with VITE_API_BASE)
```

Stop the API before `ingest.pull` or `ingest.update`: they swap the database
file in place (the previous one is kept as `data/cricket.duckdb.bak`).
`python -m ingest.pull --check` says whether a newer build is out; the app
footer shows which build you're running.

## The data pipeline

```text
Cricsheet all_json.zip + register (people.csv, names.csv)
  -> ingest/build_db.py        raw tables: matches, deliveries, wickets, XIs, people
  -> python -m analytics.build derived per-innings tables and expectations
  -> ingest/validate.py        the release gate (below)
  -> zstd + manifest.json      published as GitHub Releases data-YYYY-MM-DD and data-latest
  -> python -m ingest.pull     downloaded, sha256-checked, swapped in
```

`.github/workflows/data.yml` runs this on `main` every Monday at 03:00 UTC
(and on demand from the Actions tab), keeping the last 8 dated releases.
`.github/workflows/ci.yml` runs the tests and the frontend build on every push.

The validation gate fails the release if:

- a table or column is missing, or a derived table is empty;
- any table shrank against the previous release;
- runs in the per-innings table don't match the ball-by-ball runs exactly,
  super-over innings leak in, or runs above expected doesn't sum to ~0;
- a retired player's career changed. `ingest/frozen_careers.json` holds
  totals for Tendulkar, Dravid, Kumble, Kallis, Sangakkara, Jayawardene,
  Muralitharan, Malinga, Steyn, de Villiers, McCullum, Hafeez, Mithali Raj
  and Charlotte Edwards, computed by SQL straight over the raw tables; the
  derived tables must agree with that SQL too. If Cricsheet back-fills an old
  match, review it and refresh with `python -m ingest.validate --freeze`.

Manual pieces, for development:

```bash
python ingest/build_db.py --zip data/raw/all_json.zip --register-dir data/raw --out data/cricket.duckdb
python ingest/build_db.py --zip recently_added_7_json.zip --incremental   # upsert new matches
python -m analytics.build
python -m ingest.validate
```

## The views

- **Player Hub** (`/players/:name`) — batting/bowling summary with
  context-adjusted cards, a rolling-form chart against the career line (with
  peak and trough windows), percentiles against qualified peers, splits by
  format/season/opposition/phase/position/entry point/dismissal, an
  entry-point heatmap, and similar players. Watchlist toggle.
- **Comparison Studio** (`/compare`) — up to four players on the same
  filters: side-by-side table, percentile bars, and career arcs aligned by
  innings number.
- **Query Builder** (`/query`) — any registry metrics as columns, any
  filters, sort and qualification, optional split. Save/load queries, CSV
  export, click through to players.
- **Player Matrix** (`/matrix`) — every qualified player on two metrics,
  medians as quadrant lines, standouts labelled, watchlist in brass.
- **Ask** (`/ask`) — full-page chat.
- **Copilot drawer** (Ctrl+K, on every view) — knows what you're looking at;
  every panel has an **Explain** button, and it can open views with settings
  filled in ("show me the best death bowlers since 2022 in the query builder").

Every view keeps its settings in the URL, so any view is a shareable permalink.

## Metrics

`analytics/registry.py` declares every metric once: SQL over the per-innings
tables, which direction is better, whether it needs a minimum sample, and a
plain-English definition (shown in the UI and given to the model). Highlights:

- **True strike rate / true average / runs above expected** — performance
  against what an average player would have done facing the same situations
  (format, year, gender, innings, over, wickets down). Bowling equivalents:
  true economy, true wickets, runs saved.
- **Match factor** — average divided by the average of every other top-7
  batter in the same matches (1.0 = par for the conditions). Bowling version too.
- **Era factor** — average against the same batting position, format and era.
- **Form** — rolling last-N averages/strike rates/economies with career-to-date lines.
- Scoring profile (dot %, boundary %, balls per boundary...), reliability
  (conversion rate, 30+ rate, median, variability), role (position, entry point).
- Fielding: catches, stumpings and run-outs, per player and as leaderboards.

These adjust for match situation, not opposition or pitch quality. Cricsheet
has no ball tracking or player attributes, so pace-vs-spin, handedness and
line/length analysis aren't available.

## How answers stay correct

The model never writes cricket SQL or guesses database spellings for normal
questions. It picks a tool and passes plain names; everything that decides
whether a number is right is deterministic, tested code:

- **Player identity** (Cricsheet register). Names aren't unique: there are
  five different "Rashid Khan"s. Every player is identified by their register
  id and shown by the register's unique name ("Rashid Khan (2)"), so
  namesakes' records never merge.
- **Name resolution** (`analytics/catalog.py`). Cricsheet stores established
  players by initials ("RG Sharma", "SPD Smith"); the register's alternate
  names ("Rohit Sharma") resolve exactly, and otherwise the resolver matches
  given names to initials and ranks namesakes by how much they've played. It
  merges tournament naming eras (T20 World Cup = "ICC World Twenty20" + "World
  T20" + "ICC Men's T20 World Cup"), renamed franchises and venue spellings.
  Ambiguous names come back as candidates, not silent guesses.
- **Scoring rules** (`analytics/scope.py`, `build.py`). Dismissals are counted
  by the dismissed player (non-striker run-outs), retired hurt isn't a
  dismissal, wides aren't balls faced, byes aren't the bowler's, a "4" that
  was run isn't a boundary, and super overs are excluded (as in official
  records).
- **Grounded output** (`agent/graph.py`). Tool results go to the UI as tables
  straight away; charts are drawn from those tables by id; an answer quoting
  numbers with no data behind it (a lookup or the on-screen view) is sent
  back once; a premature `final_answer` is ignored; hitting the step limit
  still produces an answer.
- **Stated assumptions.** Every result carries the filters it used and notes
  (what a name resolved to, qualification thresholds, defaults, coverage).

## The copilot

`agent/graph.py` is a LangGraph state machine (`agent` → `tools` → `agent`...,
with a `wrap_up` node at the step limit). It lists and calls tools through an
in-memory MCP client session against `mcp_server/server.py`, adds three
app-only tools (`plot_chart`, `open_in_app`, `final_answer`), and talks to
any OpenAI-compatible endpoint (LM Studio, Ollama, Groq — see `.env.example`).
Events stream to the UI over SSE (`POST /api/chat/stream`, which also carries
the current view as context; `GET /query/stream` for plain chat).

With a local Qwen3-14B, expect roughly 30–120s per question with thinking on
(`LLM_THINKING=on`, the default); turning it off is faster but it picks the
wrong tool more often.

## Using the tools from other apps (MCP)

Any app that speaks the Model Context Protocol can use the same 18 tools. The
server offers two transports:

| Transport | Endpoint | Needs the API running? | Use when |
|---|---|---|---|
| **stdio** | the client launches `python -m mcp_server` itself | No | Simplest; the client manages the process |
| **HTTP** (streamable) | `http://localhost:8000/mcp` | Yes (`uvicorn main:app --port 8000`) | You already run the app, or several clients should share one server |

Neither transport needs the LLM: the tools are pure analytics. For any MCP
client, point it at one of:

- **stdio:** command `C:/path/to/Doosra/backend/.venv/Scripts/python.exe`,
  arguments `-m mcp_server`, environment `PYTHONPATH=C:/path/to/Doosra/backend`
- **HTTP:** `http://localhost:8000/mcp` (streamable HTTP)

In the examples below, replace `C:/path/to/Doosra` with your checkout (forward
slashes work on Windows). The database must exist and the analytics tables
must be built (`python -m analytics.build`).

### Example: Claude Desktop

Open **Settings → Developer → Edit Config** to open
`claude_desktop_config.json` (on Windows it's in `%APPDATA%\Claude\`), add one
of the entries below, then fully quit and restart Claude Desktop. The tools
appear under the tools (slider) icon in the chat box.

**stdio:**

```json
{
  "mcpServers": {
    "doosra": {
      "command": "C:/path/to/Doosra/backend/.venv/Scripts/python.exe",
      "args": ["-m", "mcp_server"],
      "env": { "PYTHONPATH": "C:/path/to/Doosra/backend" }
    }
  }
}
```

**HTTP:** the config file only launches local processes, and Desktop's
**Settings → Connectors → Add custom connector** only accepts publicly
reachable `https` URLs, not `localhost`. So for the local HTTP server, bridge it
with [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) (needs Node.js;
`npx` fetches it on first run). Start the API first, then add:

```json
{
  "mcpServers": {
    "doosra": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:8000/mcp"]
    }
  }
}
```

If you deploy the API somewhere public over `https`, add
`https://your-host/mcp` directly as a custom connector instead.

### Checking the server yourself

The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) lists
the tools and lets you call them by hand:

```bash
npx @modelcontextprotocol/inspector
```

Pick **Streamable HTTP** with URL `http://localhost:8000/mcp`, or **STDIO**
with command `C:/path/to/Doosra/backend/.venv/Scripts/python.exe`, arguments
`-m mcp_server` and a `PYTHONPATH` environment variable set to the `backend`
folder.

### Troubleshooting

- **HTTP returns 421 "Invalid Host header"**: the server only accepts
  `localhost` host names (DNS-rebinding protection). Use
  `http://localhost:8000/mcp`, not a LAN IP or custom host name.
- **"No module named mcp_server"** (stdio): `PYTHONPATH` isn't pointing at
  the `backend` folder.
- **Tools report the analytics tables aren't built**: run
  `python -m analytics.build` from `backend/`.

## Security notes

`run_sql` only permits `SELECT`/`WITH` statements and rejects write keywords
(string literals are stripped first, so a venue named "Drop Zone" doesn't
false-positive). Every connection is read-only **with external access
disabled**, so `read_csv`/`read_text` can't read server files from a SELECT.
Filter values reaching SQL are resolved database values or validated
integers, inlined as escaped literals. Saved views and the watchlist live in
a separate SQLite file (`data/workspace.db`). The rate limiter and
cancellation registry are per-process; a public deployment needs shared ones.

## Tests and eval

```bash
cd backend
pytest                  # ~440 tests: ingest, release pipeline, scoring rules, engine, API, MCP, agent graph
pytest -m llm           # LLM-in-the-loop eval (needs a model endpoint)
```

The fixture tests build a small synthetic database through the real ingest
and analytics-build path, and run against **both** database schemas (the
current one and the older one without `deliveries_wickets`). Context metrics
are also checked against invariants that hold by construction (runs above
expected sum to zero across all batters). The agent graph is tested with a
scripted fake LLM against the real MCP tools.

`tests/eval_fixtures/eval_questions.json` holds 40 questions with expected
values computed by independent SQL, checked against the real database.

## Next steps

- Match Centre (worm, Manhattan, win-probability model, key moments, impact)
- Matchup grid + auto-written pre-match reports
- Venue, team and tournament dashboards; a scouting board with league-strength adjustment
- Tool-use fine-tune of a small local model for speed
- Cricket DIPS: which parts of a record are skill and which are luck, with a live methodology page
- Single-installer desktop app

## Licence

Copyright (c) 2026 sarthak213. All rights reserved.

Doosra is proprietary software, not open source. No licence is granted to
use, copy, modify, distribute or host it without the copyright holder's prior
written permission -- see [LICENSE](LICENSE).

Cricket data comes from [Cricsheet](https://cricsheet.org) and remains
under Cricsheet's ODC-By 1.0 licence, which requires attribution -- see
[DATA_NOTICE.md](DATA_NOTICE.md).
Third-party libraries and fonts keep their own licences.
