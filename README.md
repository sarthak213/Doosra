# Doosra

A cricket analytics workbench: player hubs, comparisons, a query builder and
a player matrix over ball-by-ball data, with context-adjusted metrics (true
strike rate, match factor, era factor...) and an AI copilot that can explain
any view or drive the app, plus an Ask workspace of saved chats, projects with
your own notes, and boards of charts the AI can explain. It installs as a
Windows app with its own AI model, runs from source, or runs as an invite-only
hosted container. Every analytics capability is also an MCP tool, so
any MCP-capable app (Claude Desktop and others) can use it directly.

Data: [Cricsheet](https://cricsheet.org/) ball-by-ball data and register,
under the [ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/) licence
(see [DATA_NOTICE.md](DATA_NOTICE.md)). Rebuilt weekly by GitHub Actions and
published as a release the app downloads.

## Install (Windows)

1. Download **DoosraSetup-&lt;version&gt;.exe** from the
   [latest release](https://github.com/sarthak213/Doosra/releases/latest) and run it. It installs
   for your user (no admin prompt) with Start menu and desktop shortcuts. The installer isn't
   code-signed yet, so SmartScreen may say it "protected your PC": click **More info**, then
   **Run anyway**.
2. The first launch opens a setup screen. It shows your PC's memory, GPU and free space,
   recommends a model, and downloads once:
   - the cricket database (about 430 MB);
   - an AI model: **Qwen3.5 4B Doosra** (2.8 GB, recommended: fine-tuned on Doosra's own tools, the
     most accurate in our tests and about 2.5x faster than the 9B; see [Models](#models)), or the
     general **Qwen3.5 9B** (5.6 GB; 16 GB+ of memory, faster with a GPU) or **Qwen3.5 4B** (2.7 GB).
     If LM Studio already has the same file, Doosra can use that copy instead (after checking its
     checksum).

   Downloads can be stopped and resumed. Then Doosra starts its built-in AI engine, checks it
   answers, and opens.

Everything runs on your PC; questions never leave it. The AI engine is
[llama.cpp](https://github.com/ggml-org/llama.cpp), shipped inside the app: it uses the GPU
through Vulkan (Intel Arc, AMD, NVIDIA) and falls back to the CPU. Settings (the ⚙ in the
masthead) switches models, GPU or CPU, and how much a conversation can hold, or uses a running
LM Studio instead. The cricket data updates itself once a day when a new weekly build is out,
and new versions of the app are offered in Settings (the ⚙ gets a red dot).

| | |
| --- | --- |
| Needs | Windows 10 (1809+) or 11, 64-bit; 8 GB of memory (16 GB+ for the 9B model); about 7 GB free |
| App | `%LOCALAPPDATA%\Programs\Doosra` |
| Your data | `%LOCALAPPDATA%\Doosra`: the database, models, chats and projects, settings, logs |
| Uninstall | Settings → Apps → Doosra. It asks whether to delete your data too (kept by default). |

## Architecture

```text
backend/
├── ingest/                 # the data pipeline
│   ├── build_db.py         #   Cricsheet zip/JSON + register -> DuckDB tables (batched)
│   ├── coverage.py         #   Cricsheet's coverage + missing-match pages -> tables
│   ├── validate.py         #   release gate: schema, counts, invariants, frozen careers
│   ├── manifest.py         #   release manifest (hashes, counts, build date)
│   ├── pull.py             #   download + verify + install the published database
│   └── update.py           #   rebuild locally from Cricsheet (no CI needed)
├── analytics/              # the analytics layer (no LLM involved)
│   ├── db.py               #   the one place that opens the database (read-only)
│   ├── catalog.py          #   name resolution: players, competitions, teams, venues, formats
│   ├── scope.py            #   shared filters -> SQL, and the per-ball scoring rules
│   ├── build.py            #   derived per-innings tables + ball-state expectations
│   ├── components.py       #   the per-ball outcome taxonomy (FIBS)
│   ├── fibs.py             #   the FIBS study: what's skill, what's luck
│   ├── coverage.py         #   what the data covers and what it's missing
│   ├── facets.py           #   dynamic filtering: which competitions/teams/venues/seasons exist in scope
│   ├── registry.py         #   the metric registry: every metric declared once
│   ├── insights.py         #   deterministic facts about a chart's data (for board explanations)
│   ├── engine.py           #   query builder: stats, splits, form, arcs, percentiles, matrix...
│   └── results.py          #   the result envelope + formatting/highlight helpers
├── mcp_server/             # every capability as an MCP tool (stdio or HTTP at /mcp)
├── agent/                  # the copilot
│   ├── graph.py            #   LangGraph state machine over the MCP tools, grounding guards
│   ├── prompts.py          #   system prompt (tool routing, filters, worked examples)
│   ├── stats.py            #   team/venue/matchup/records tools (ball-level SQL)
│   ├── tools.py            #   guarded run_sql, schema guide, autocomplete search
│   └── cancellation.py     #   in-flight request cancellation
├── api/                    # REST endpoints behind the UI views
│   ├── workspace.py        #   chats, projects, notes, boards, saved views (SQLite, or Postgres)
│   ├── migrations.py       #   the workspace schema and its forward-only migrations
│   ├── chat_persist.py     #   saving a chat turn as it streams; history rebuilt for the model
│   ├── board_context.py    #   what the AI is told when it explains a board
│   ├── auth.py             #   hosted sign-in gate, invites, per-user AI quotas
│   ├── auth_routes.py      #   Google/GitHub OAuth, sign-out, /api/me, invite admin
│   └── admin.py            #   `python -m api.admin invite|revoke|list`
├── ingest/refresh.py       # hosted: keeps the database on the latest published build
├── tests/                  # pytest suite (both DB schemas) + eval questions
├── docker-entrypoint.sh    # container start: fetch the database, then serve
└── main.py                 # FastAPI app: /api, /mcp, SSE chat streams, the built frontend when hosted

Dockerfile                  # the hosted image: API + built React app on one origin

backend/desktop_app.py      # the desktop app: settings, hardware check, model registry (models.json)
backend/local_llm.py        # the built-in AI engine: llama-server as a child process, GPU -> CPU fallback
backend/setup_job.py        # first-run setup: database, model, engine start, test answer (resumable)
backend/updates.py          # app updates from GitHub releases
backend/doosra_home.py      # where app data lives (backend/data in a checkout, %LOCALAPPDATA%\Doosra installed)
desktop/                    # the Windows app
├── launcher.py             #   Doosra.exe: the API in-process + a native window (pywebview, WebView2)
├── doosra.spec             #   PyInstaller build
├── installer.iss           #   Inno Setup installer (DoosraSetup-<version>.exe)
├── fetch_engine.py         #   the pinned llama.cpp release (sha256-checked)
├── make_icon.py            #   the icon and installer images, from frontend/public/favicon.svg
└── build.py                #   all of the above in one command

frontend/                   # React app: Player Hub, Compare, Query, Matrix, FIBS, Data, Ask workspace + copilot drawer
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
# (or run everything in one container instead -- see "Hosting" for the image;
#  locally, with LM Studio on the same machine:)
# docker run -p 8000:8000 -e AUTH_MODE=none -e SERVE_FRONTEND=1 -e LLM_PROVIDER=lmstudio #   -e LLM_BASE_URL=http://host.docker.internal:1234/v1 -e LLM_MODEL=qwen/qwen3.5-9b #   -v doosra-data:/app/backend/data doosra
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

## What the data covers

Cricsheet publishes, as web pages, the periods it covers, how many matches it
holds of each competition and team, and a list of every match it knows it's
missing (about 2,900: roughly 10% of men's Tests and ODIs since 2001, and more
of women's cricket). It also withholds every match involving Afghanistan.
`ingest/coverage.py` parses those pages into tables each build
(`coverage_periods`, `coverage_counts`, `missing_matches`, `coverage_info`);
the Data page, the player notes and the `data_coverage` MCP tool read them,
so the copilot can explain why a total is short of the official record.

## The data pipeline

```text
Cricsheet all_json.zip + register (people.csv, names.csv) + coverage/missing pages
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
- FIBS's expected outfield catches don't balance the observed ones;
- Cricsheet's coverage / missing-match pages were downloaded but parsed to
  nothing (the page layout changed) -- rather than ship an empty Data page;
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

Every view shares one filter bar, and its suggestions are **dynamic**: they list only what exists
within the filters already chosen. Pick ODI and the competitions are ODI tournaments; add the
Men's ODI World Cup and 2023/24, and the venues, teams and seasons narrow to that tournament.
Each list ignores its own choice, so you can still switch to another competition of the same
format (`/api/options/scoped`, `analytics/facets.py`).

- **Player Hub** (`/players/:name`) — batting/bowling summary with
  context-adjusted cards, a rolling-form chart against the career line (with
  peak and trough windows), percentiles against qualified peers, splits by
  format/season/opposition/phase/position/entry point/dismissal, an
  entry-point heatmap, and similar players. Watchlist toggle.
- **Comparison Studio** (`/compare`) — up to four players on the same
  filters: side-by-side table, percentile bars, career arcs aligned by
  innings number, and a **By phase** panel (powerplay, middle overs, death)
  for batters and bowlers: grouped bars for a chosen metric over a table that
  puts the players next to each other in each phase. With the batting-role
  filters (below) an opener can be compared with openers and a finisher with
  finishers.
- **Query Builder** (`/query`) — any registry metrics as columns, any
  filters, sort and qualification, optional split. Save/load queries, CSV
  export, click through to players.
- **Player Matrix** (`/matrix`) — every qualified player on two metrics,
  medians as quadrant lines, standouts labelled, watchlist in brass.
- **Match Replay** (`/matches`, `/matches/:id`) — any T20 or ODI ball by ball: each side's chance of
  winning after every ball (the win-probability model below), the key moments that swung it (single
  wickets and boundaries, and whole overs, largest swing first), and the scorecard. "Explain" hands
  the story to the copilot, which can also find a match itself (`match_replay`: "what was the
  turning point of the 2024 T20 World Cup final?").
- **FIBS** (`/methodology/fibs`) — the Fielding-Independent Bowling Statistics methodology page:
  what's skill and what's luck in a cricket record, with every number,
  chart and finding read live from the study (see below). Luckiest and
  unluckiest players for any competition and season.
- **Data coverage** (`/data`) — what the data covers and what it doesn't:
  matches per format since when, Cricsheet's coverage by competition and
  team, every known-missing match (filterable), and the matches Cricsheet
  withholds. Player Hub pages flag gaps during a player's career.
- **Ask** (`/ask`) — full-page chat.
- **Copilot drawer** (Ctrl+K, on every view) — knows what you're looking at;
  every panel has an **Explain** button, and it can open views with settings
  filled in ("show me the best death bowlers since 2022 in the query builder").

Every view keeps its settings in the URL, so any view is a shareable permalink.

Every view shares one set of filters: competition, format, gender, team,
opposition, venue, season, year range, phase and innings. Innings follows the
format: Tests and first-class matches have four (3rd and 4th are each side's
second innings), limited overs have two (setting, chasing). Season also takes
`latest` -- the most recent season of whatever else is filtered ("this IPL").
Competition, team and venue inputs suggest as you type.

**Match result** (won, lost, drawn, tied, no result) keeps only the matches
with that result from the player's side ("Kohli in ODI wins", "Sachin in lost
Tests"), for batting and bowling; splitting by result shows all of them side by
side, with draws and ties told apart from no-results.

Batting views add three filters on how the batter came in, for like-for-like
comparisons (a finisher against finishers, not against openers): **batting
position** (a number, a range such as `1-3` or `5+`, or openers / top order /
middle order / finishers / lower order), **came in during** (powerplay, middle,
death) and **wickets down at entry**. Stats can also be split by each of them.
They apply to per-innings batting figures only: bowling views hide them, and a
view built on ball-by-ball data refuses them with a reason rather than quietly
ignoring them. Within a phase filter, figures that only exist for a whole innings
(highest score, hundreds, match factor...) are left out, with a note saying so.

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
- **FIBS** (Fielding-Independent Bowling Statistics) — FIB economy, average and wickets,
  wicket luck and runs luck, and reliability-adjusted ("regressed") figures.
  See the next section.

These adjust for match situation, not opposition or pitch quality. Cricsheet
has no ball tracking or player attributes, so pace-vs-spin, handedness and
line/length analysis aren't available.

## FIBS: Fielding-Independent Bowling Statistics

FIBS is an attempt at a cricket framework analogous to baseball's DIPS
(Defense-Independent Pitching Statistics). DIPS (Voros McCracken, 2001)
showed that pitchers barely control whether a ball in play becomes a hit,
so part of every pitcher's record is fielding and luck. FIBS asks the same
question of cricket -- for bowlers, and for batters as the mirror image --
without assuming baseball's answer. `analytics/fibs.py` runs it as a study
over the whole database at every build:

- Every ball is broken into outcomes (`analytics/components.py`): dots,
  fours, sixes, wides, no-balls, scoring shots in play, bowled, lbw, caught
  by the keeper, caught in the field, caught and bowled, stumped, run out.
  Each has an expected rate for every ball state, so everything is measured
  *above expected*. Caught behind uses an inferred keeper (the XI member who
  made a stumping in the match, otherwise the one with most career
  stumpings).
- **Stability**: each player-season is split in two by match; the covariance
  of the halves across players estimates the true-talent spread, giving
  **K** -- the balls at which a rate is half skill, half noise
  (reliability over n balls = n / (n + K)). Year-to-year correlations are
  the cross-check.
- **Prediction**: out of sample, which of this season's figures best
  predicts next season's economy and wicket rate -- raw, regressed,
  DIPS-style (fielding-affected outcomes replaced by the league average) or
  luck-adjusted.

What it finds (men's T20, current data): dot % and economy settle within a
couple of hundred deliveries, but wicket rate needs about 1,700 -- a typical
season's wicket rate is roughly 90% noise. Unlike baseball, catches in the
field are about as repeatable as bowled and lbw, and the DIPS-style
replacement predicts next season *worse* than the raw figures. So Doosra's FIB and luck metrics
don't discard those outcomes: they shrink each player's own rate by its
measured K, and call what's left over luck.

The results live in `fibs_stability` and `fibs_prediction`; every innings
row carries the K for its format and gender. The copilot uses them through
the `fibs_report` and `luck_leaderboard` tools.

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

## The Ask workspace

The Ask tab is a workspace, with a sidebar like Claude's:

- **Saved chats.** Every conversation is stored on the server with the tables,
  charts and reasoning steps it showed, so reopening one looks exactly as it
  did. Rename, move, search (titles and message text) and delete from the
  sidebar. The server rebuilds each chat's history for the model, including a
  short note of the tables earlier answers showed, so "now split that by phase"
  has the numbers. (A conversation kept in the browser by v2.2 and earlier is
  imported once, as "Imported chat".)
- **Projects** group chats and boards and carry context the AI reads: a
  *standing brief*, and *notes*, typed or imported from a `.md`, `.txt` or
  `.csv` file (up to 20,000 characters each; switch a note off to hide it from
  the AI). The brief and enabled notes go into the system prompt as
  background, never as data: every number still comes from a tool, and if a
  note contradicts the data the AI says so with the numbers. Past about 8,000
  characters, notes are listed by title and read on demand. Export a project as
  JSON and import it elsewhere from the sidebar.
- **Boards** ("custom views") are named sets of chart cards. Build a card from
  a Query, Matrix or Compare (or start from a saved view), choose bar, line,
  scatter or table, and see the live preview. Cards are re-run against the
  current data every time the board opens. Under any table or chart the AI
  shows there is **Add to board**: if the answer came from a query, matrix or
  compare it becomes a live card, otherwise a static snapshot (labelled as such).
- **Explain.** "Explain this view" (or a card's Explain) opens a saved chat in
  the project. The *server* loads the board you own, re-runs every card and
  computes a digest from the numbers: leaders and laggards, spread, outliers,
  trend, correlation, thin samples, and, for raw rate metrics, how far each
  player's figure moves once regressed toward the league (the FIBS lens on skill
  versus luck). The model interprets that digest; numbers the browser sends
  about a board are ignored. There is also an Explain button under every chart
  and table in an answer, and follow-up questions keep the board attached.

Everything lives in `data/workspace.db` (SQLite) next to the cricket database,
scoped to a user id (`local` when running locally). Set `DATABASE_URL` to use
Postgres instead.

## The copilot

`agent/graph.py` is a LangGraph state machine (`agent` → `tools` → `agent`...,
with a `wrap_up` node at the step limit). It lists and calls tools through an
in-memory MCP client session against `mcp_server/server.py`, adds three
app-only tools (`plot_chart`, `open_in_app`, `final_answer`), and talks to
any OpenAI-compatible endpoint (LM Studio, Ollama, Groq — see `.env.example`).
Events stream to the UI over SSE (`POST /api/chat/stream`, which also carries
the current view as context; `GET /query/stream` for plain chat).

Answers stream in as the model writes them, and the model's reasoning shows
live while it thinks. With `LLM_THINKING=auto` (the default) a quick
explanation from the copilot drawer or a page's Explain button answers
directly, while a saved chat (the Ask tab, a project, a board explanation)
reasons first and says so, since that can take a few minutes on a local model.
`on` or `off` forces one mode. For per-request switching, leave reasoning
enabled in LM Studio: while it is switched off there, requests can't turn it on.
A quick answer is sent with `reasoning_effort: "none"`, which LM Studio honours
for Qwen3.5 (it ignores the chat-template `enable_thinking` switch and Qwen's
`/no_think`). On a laptop's integrated GPU with Qwen3.5-9B, the same question
took about 35 seconds as a quick answer and about 75 with reasoning. A saved
chat keeps answering if you leave the page, and the reopened chat picks the
answer up; its reasoning is kept as steps in the trace.

Answers put player and team names and key numbers in bold, use short bullet
lists, and use a small table when comparing three or more players.

With Qwen3.5 4B Doosra (below) the copilot runs in **compact mode**: a system prompt of
about 280 tokens instead of 2,100, tool descriptions cut to their first sentence (except
`run_sql`, which keeps its schema), and reasoning off. The model learned the rest in training.

Local models are slowest at reading the prompt, so the prompt is laid out to
be reused: the fixed rules and the tool definitions come first and never
change; project notes, page context and the question come last. After the first
question LM Studio reads only what's new. With Qwen3.5-9B (Q4_K_M) on a laptop's
integrated GPU a question takes about a minute. The app waits up to 10 minutes
for a local model (`LLM_TIMEOUT_SECONDS`) and never retries, since a retry
means reading the whole prompt again.

## Using the tools from other apps (MCP)

Any app that speaks the Model Context Protocol can use the same 21 tools. The
server offers two transports:

| Transport | Endpoint | Needs the API running? | Use when |
| --- | --- | --- | --- |
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

## Hosting (invite-only)

Locally nothing changes: no sign-in, one implicit user, MCP on. Setting
`AUTH_MODE=oauth` turns on the hosted behaviour:

- **Sign-in with Google or GitHub** (no passwords are ever handled). Only
  emails you invite can get in; `ADMIN_EMAILS` are always admitted and can
  manage invites from the account menu (or `python -m api.admin invite <email>`).
  The session is a signed, HttpOnly cookie; revoking an invite ends that
  person's session on their next request.
- **Everything is per user.** Chats, projects, notes, boards, saved views and
  the watchlist are scoped to the signed-in user in the storage layer, and the
  tests check every route against a second user.
- **AI is metered.** `AI_DAILY_QUESTIONS` per user (30 by default when hosted)
  and an optional `AI_GLOBAL_DAILY_QUESTIONS` ceiling for the whole instance,
  counted in the database so they hold across restarts and instances.
- **The MCP endpoint is not served** (it has no sign-in); the copilot's own
  in-process tool access is unaffected.
- **One container, one origin.** The `Dockerfile` builds the React app and
  serves it from the API, so there are no cross-site cookies or CORS. The
  cricket database is not in the image: on first start the entrypoint downloads
  the latest validated build from this repository's Releases into the data
  volume, and `DATA_REFRESH_HOURS` (24 by default) checks for a newer one and
  swaps it in without a restart.

```bash
docker build -t doosra .
docker run -p 8000:8000 --env-file backend/.env.hosted -v doosra-data:/app/backend/data doosra
```

To try the image on your own machine without setting up sign-in, run it in
local mode (one implicit user, no OAuth, the app served on port 8000; the MCP
endpoint is off in this mode):

```bash
docker run -p 8000:8000 -e AUTH_MODE=none -e SERVE_FRONTEND=1 -e GROQ_API_KEY=... -v doosra-data:/app/backend/data doosra
```

Every setting is documented in `backend/.env.hosted.example`. Chats and
projects live in SQLite inside the data volume by default (one instance, a
persistent disk) or in Postgres if `DATABASE_URL` is set (required for hosts
with an ephemeral disk or more than one instance). Budget about 2 GB of RAM:
the analytics queries run over a database of roughly 700 MB.

GitHub Pages can't host this: it serves static files only, and Doosra needs
the API and a database. (A landing page there is possible.) The CI `container`
job builds the image and smoke-tests it on every push.

## Models

Trained on the dataset itself, and published on Hugging Face with their data (`ml/`).

**Win probability** (`analytics/winprob.py`, trained by `ml/winprob_train.py`): the batting side's chance
of winning after every ball of a T20 or ODI. For each innings, gradient-boosted trees (LightGBM, with
monotone constraints: more wickets in hand or balls left can only help, more runs needed can only hurt) are
blended with a logistic regression on the log-odds scale; the trees capture the non-linear parts and the
regression keeps the curve smooth from ball to ball. Features: the score, wickets in hand, balls left,
target, runs needed and required rate, the ground's par (its last 20 first-innings totals before the
match), and each side's Elo rating from earlier results only. Trained on matches up to 2022, early-stopped on
2023-24, and tested on everything from 2025 on:

| Test (2025+) | Matches | Log loss | Brier | AUC | Calibration error | Logistic alone | Trees alone | Par heuristic |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T20 | 2,889 | **0.455** | 0.152 | 0.861 | 0.8% | 0.461 | 0.458 | 0.658 |
| ODI | 638 | **0.513** | 0.174 | 0.818 | 2.3% | 0.514 | 0.519 | 0.712 |

The app runs the exported models with NumPy (no LightGBM in the app); training checks that the NumPy
evaluation matches LightGBM's. Retrain with `pip install -r ml/requirements-ml.txt` then
`python ml/winprob_train.py`, which also writes a report with calibration and by-over accuracy plots.

**The dataset** (`ml/export_dataset.py`): every table as Parquet with a dataset card, for Hugging Face.

**Qwen3.5 4B Doosra** (`ml/toolcall/`; [model](https://huggingface.co/Sarthak213/doosra-qwen3.5-4b-toolcalls-v2),
[data](https://huggingface.co/datasets/Sarthak213/doosra-toolcalls)): Qwen3.5 4B fine-tuned (LoRA, rank 32, 2
epochs on a Colab A100) to call Doosra's 25 tools and answer from their results. The training conversations are
generated, not written: questions from templates over real players, teams, venues and competitions (46% reworded by
the local 9B), gold tool calls **executed against the database**, answers written from the results, and checks that
every answer's figures come from its results and that nothing overlaps the evaluation. On Doosra's 40 end-to-end
questions, through the real tool loop:

| Model | Correct of 40 | Calls per question | Median time | 90th percentile |
|---|---:|---:|---:|---:|
| Qwen3.5 4B (full prompt) | 29 | 3.1 | 30 s | 85 s |
| Qwen3.5 9B (full prompt) | 30 | 2.0 | 27 s | 73 s |
| Fine-tune v1 (best run) | 21 | 2.0 | 13 s | 26 s |
| **Qwen3.5 4B Doosra (v2)** | **33** | **1.3** | **11 s** | **17 s** |

The first version aced its own test set and lost end to end; evaluating it showed why (no SQL schema in its tool
list, answers built on a template's headline figure, no recovery from a failed call, a template bug that left team
answers without figures), and v2's data fixes each. Generate the data with `python ml/toolcall/generate.py`, check it
with `ml/toolcall/validate.py`, train with `ml/toolcall/train.ipynb` (one Colab A100 run, about 6 hours), and evaluate
with ToolEval (`ml/toolcall/export_eval.py` writes its dataset; `eval_mcp.py` serves Doosra's tools to it).

## Building the Windows app

```bash
pip install -r backend/requirements.txt pyinstaller pywebview
python desktop/build.py            # needs Node and Inno Setup 6 (winget install JRSoftware.InnoSetup)
```

It builds the frontend (served by the app itself), fetches the pinned llama.cpp builds, builds
`desktop/dist/Doosra/Doosra.exe` with PyInstaller, runs its `--self-test` (the API, the app page
and both engine builds, headless), then the installer `desktop/dist/DoosraSetup-<version>.exe`.
`python desktop/launcher.py` runs the app from source without building anything.

Releasing is part of merging. Bump `VERSION` in `backend/version.py` in your pull request; when it's
merged to `main`, the Desktop app workflow sees a version that hasn't been released yet, builds the installer
on Windows, runs the app's self-test, and publishes `app-v<version>` as a GitHub release, with notes that
list the pull requests since the last one. Merges that don't change the version don't release anything.

Installed copies see the release in Settings, download it (checked against the sha256 GitHub publishes)
and update in place: the app closes, the installer runs silently and opens the new version. A tag pushed
by hand (`git tag app-v2.5.0 && git push origin app-v2.5.0`) also releases, and a manual run of the
workflow builds the installer without releasing it.

## Security notes

`run_sql` only permits `SELECT`/`WITH` statements and rejects write keywords
(string literals are stripped first, so a venue named "Drop Zone" doesn't
false-positive). Every connection is read-only **with external access
disabled**, so `read_csv`/`read_text` can't read server files from a SELECT.
Filter values reaching SQL are resolved database values or validated
integers, inlined as escaped literals. Saved views and the watchlist live in
a separate SQLite file (`data/workspace.db`); every workspace query is scoped
to the owning user, so one user can't read or change another's chats, notes or
boards (there is one implicit user, `local`, unless hosted sign-in is on). Project
notes are the user's own text and are framed to the model as background that
cannot change the rules. The rate limiter and cancellation registry are
per-process; a public deployment needs shared ones.

## Tests and eval

```bash
cd backend
pytest                  # ~720 tests: ingest, release pipeline, coverage, scoring rules, FIBS, engine, API, workspace, auth, hosting, MCP, agent graph
pytest -m llm           # LLM-in-the-loop eval (needs a model endpoint)
```

The fixture tests build a small synthetic database through the real ingest
and analytics-build path, and run against **both** database schemas (the
current one and the older one without `deliveries_wickets`). Context metrics
are also checked against invariants that hold by construction (runs above
expected sum to zero across all batters). The FIBS estimator is checked on
simulated players with known true rates (it must recover K), and the coverage
parser on pages shaped like Cricsheet's. The agent graph is tested with a
scripted fake LLM against the real MCP tools.

`tests/eval_fixtures/eval_questions.json` holds 40 questions with expected
values computed by independent SQL, checked against the real database.

## Next steps

- Match Centre (worm, Manhattan, win-probability model, key moments, impact)
- Matchup grid + auto-written pre-match reports
- Venue, team and tournament dashboards; a scouting board with league-strength adjustment

## Licence

Copyright (c) 2026 sarthak213. All rights reserved.

Doosra is proprietary software, not open source. No licence is granted to
use, copy, modify, distribute or host it without the copyright holder's prior
written permission -- see [LICENSE](LICENSE).

Cricket data comes from [Cricsheet](https://cricsheet.org) and remains
under Cricsheet's ODC-By 1.0 licence, which requires attribution -- see
[DATA_NOTICE.md](DATA_NOTICE.md).
Third-party libraries and fonts keep their own licences.
