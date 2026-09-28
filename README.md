# Cricket Agent

An agentic AI assistant that answers natural-language cricket questions by
writing and executing SQL against ball-by-ball match data, with a live view
of its reasoning steps.

Data source: [Cricsheet](https://cricsheet.org/) (free, no auth required).

## Architecture

```text
backend/
├── ingest/
│   └── build_db.py             # parses Cricsheet JSON -> normalized DuckDB tables (batched)
├── agent/
│   ├── catalog.py              # name resolution: players, competitions, teams, venues, formats
│   ├── scope.py                # shared filters (competition/format/team/venue/season/phase...) -> SQL
│   ├── stats.py                # the analytics engine: player stats, splits, leaderboards, records,
│   │                           #   team/venue records, matchups -- every scoring rule encoded once
│   ├── tools.py                # run_sql (guarded), schema, autocomplete search, dataset counts
│   ├── prompts.py              # system prompt (routing guide + worked examples)
│   ├── cancellation.py         # in-flight request cancellation registry
│   └── graph.py                # hand-rolled tool-calling loop with grounding guards
├── tests/                      # pytest suite (both DB schemas) + eval questions
├── checkdb.py                  # quick sanity check on the built database
├── main.py                     # FastAPI app, SSE streaming endpoint
└── requirements.txt

frontend/                       # React app ("Doosra")
```

## How answers stay correct

The model never writes cricket SQL or guesses database spellings for normal
questions. It picks a tool and passes plain names; everything that decides
whether a number is right is deterministic, tested code:

- **Name resolution** (`catalog.py`). Cricsheet stores established players
  by initials ("RG Sharma", "SPD Smith"), so plain fuzzy matching picked
  obscure namesakes whose names are spelled out in full (the "Rohit Sharma"
  in the data is an 8-match domestic player). The resolver matches given
  names to initials and ranks namesakes by how much they've played. It
  merges competitions split across naming eras (T20 World Cup = "ICC World
  Twenty20" + "World T20" + "ICC Men's T20 World Cup"), renamed franchises
  (RCB Bangalore/Bengaluru), and venue spellings. Ambiguous names ("Smith")
  come back as candidates instead of a silent guess.
- **Scoring rules** (`stats.py`). Dismissals are counted by the dismissed
  player (non-striker run-outs), retired hurt isn't a dismissal, wides
  aren't balls faced, byes aren't the bowler's, and super overs are
  excluded. Every tool uses the same SQL building blocks, so they can't
  disagree with each other.
- **Grounded output** (`graph.py`). Tool results with rows go to the UI as
  tables straight away. Charts are drawn from those tables by id, so the
  model never copies numbers into them. An answer that quotes numbers with
  no data lookup behind it gets sent back once. A `final_answer` sent in the
  same batch as other tool calls is ignored, and running out of steps still
  produces an answer.
- **Stated assumptions**. Every result carries the filters it used and notes
  (what a name resolved to, qualification thresholds, "defaulted to men's
  cricket", data-coverage caveats), and the prompt asks the model to state
  them.

## Backend setup

```bash
cd backend
pip install -r requirements.txt

# 1. Point build_db.py at wherever your Cricsheet JSON files live (it globs
#    recursively, so one folder with everything in it, or subfolders per
#    tournament/format, both work fine)
python ingest/build_db.py --raw-dir /path/to/your/cricsheet/json --out data/cricket.duckdb

# 2. Copy .env.example to .env and fill in your LLM config
#    (Groq free tier: https://console.groq.com)
cp .env.example .env

# 3. Run the API
uvicorn main:app --reload --port 8000
```

Since the database now spans every tournament and format you have locally,
`matches.event_name` distinguishes competitions (IPL, Big Bash League, World
Cups, etc.) separately from `matches.match_type` (the format: Test/ODI/T20).
The agent is prompted to filter on `event_name` when a question names a
specific tournament.

Test it:

```bash
curl "http://localhost:8000/query/stream?q=who%20scored%20the%20most%20runs"
```

You should see a stream of `data: {...}` events — thoughts, tool calls, tool
results, and a final `final_answer` event.

## How the agent loop works

`agent/graph.py` implements a plain tool-calling loop (no LangGraph/CrewAI
dependency) so every step is transparent and emits an event:

1. Send the question + system prompt to the LLM with tool schemas attached
2. If the model calls a tool (`player_stats`, `compare_players`,
   `leaderboard`, `top_performances`, `team_stats`, `venue_stats`, `matchup`,
   `lookup`, `run_sql`), run it and emit the call and result as events.
   Results with rows also go out as `table` events (ids T1, T2...), and the
   model gets them as records
3. If a tool call errors (bad SQL, an ambiguous name with candidates), the
   error is fed back to the model so it can self-correct. This is emitted as
   a distinct `self_correction` event so the frontend can highlight it
4. `plot_chart` draws from a table id, and the chart goes out as a `chart`
   event
5. When the model calls `final_answer`, the loop ends and yields the answer

For a local model like Qwen3-14B, expect roughly 30–90s per question with
thinking on (`LLM_THINKING=on`, the default). Turning it off is faster, but
in testing it picked the wrong tool more often and misread results.

`main.py` wraps this generator in a Server-Sent Events response so the React
frontend can render each step as it happens.

## Security notes

`tools.run_sql` only permits `SELECT`/`WITH` statements and rejects any query
containing `INSERT`, `UPDATE`, `DELETE`, `DROP`, etc. (string literals are
stripped first, so a team or venue name containing a blocked word doesn't
false-positive). The DuckDB connection is also opened read-only **with
external access disabled**, so table functions like `read_csv`/`read_text`
can't be used to read files on the server from inside a SELECT. For public
deployments, note the remaining gaps: the in-memory rate limiter and the
request-id cancellation registry are per-process only (a reverse proxy or
Redis-backed limiter is needed across multiple workers).

## Multi-turn conversations, cancellation, and persistence

The frontend sends prior turns (`history`) with each `/query/stream` request,
so follow-up questions like "what about in Tests?" work. Each request also
carries a `request_id`; the Stop button hits `POST /query/cancel/<request_id>`
so the backend actually stops the agent loop rather than just dropping the
browser connection. Turns persist to `localStorage` (last 20) and a
"Clear history" button wipes them.

## Tests and eval

```bash
cd backend
pip install pytest
pytest                       # deterministic tests: scoring rules, SQL guard, agent pieces
```

The fixture tests build a small synthetic database through the real ingest
path and run every scoring-rule test against **both** database schemas (the
current one and the older one without `deliveries_wickets`), plus the agent
loop with a scripted fake LLM.

`tests/eval_fixtures/eval_questions.json` holds 40 natural-language
questions with expected values computed by independent SQL. The
deterministic checks run the stats tools directly against the built
database (skipped if `data/cricket.duckdb` is absent). To run the LLM-in-the-loop eval (spends
API credits, needs a key):

```bash
pytest -m llm tests/test_llm_eval.py
```

## Next steps

- Re-ingest `data/cricket.duckdb` with the current `build_db.py`: per-type
  extra columns and the `deliveries_wickets` table enable exact
  multi-extra/multi-wicket scoring and **fielding stats** (catches,
  stumpings, run-outs). Until then the tools fall back to the old-schema
  approximation, and fielding questions return an explanatory error
- Cricsheet `people.csv` registry, to tell apart two different players who
  share the same name
- Deploy: frontend on Vercel, backend on Render/Railway/HF Spaces (bring a
  shared rate limiter + `CORS_ORIGINS` env var)

## Frontend setup

The frontend is "Doosra" — a React app in `frontend/`, styled around a
cricket-pitch/scoreboard visual identity (deep pitch green, brass/seam
accents, Fraunces + IBM Plex type). It talks to the backend's SSE endpoint
and renders the agent's live reasoning trace, final answer, chart, and
table.

```bash
cd frontend
npm install
npm run dev
```

Opens at `http://localhost:5173` by default. It expects the backend running
at `http://localhost:8000` — override with a `.env` file containing
`VITE_API_BASE=http://your-backend-url` if needed.

Key files:

- `src/hooks/useAgentQuery.js` — manages the `EventSource` connection to
  `/query/stream` and assembles each turn's steps/answer/chart/table as
  events arrive
- `src/components/ReasoningTrace.jsx` — the collapsible live trace,
  auto-expanded while a turn is streaming
- `src/components/TableView.jsx` — renders each `table` event (title, the
  filters applied, caveat notes) as soon as the tool returns
- `src/components/ChartView.jsx` — renders `chart` events; several metrics
  are drawn as separate small charts, never on one shared axis

`npm run build` produces a static `dist/` you can deploy anywhere (Vercel,
Netlify, etc.) — just make sure `VITE_API_BASE` points at your deployed
backend.
