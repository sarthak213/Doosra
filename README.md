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
│   ├── tools.py                # get_schema, run_sql, search_player/tournament, get_stats
│   ├── stats.py                # batting/bowling/fielding/head-to-head/venue/trend/matchup tools
│   ├── prompts.py              # system prompt
│   ├── cancellation.py         # in-flight request cancellation registry
│   └── graph.py                # hand-rolled tool-calling reasoning loop
├── tests/                      # pytest suite + eval questions
├── checkdb.py                  # quick sanity check on the built database
├── main.py                     # FastAPI app, SSE streaming endpoint
└── requirements.txt

frontend/                       # React app ("Doosra")
```

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
2. If the model calls a tool (`get_schema`, `search_player`, `run_sql`), run
   it, emit the call and result as events, and feed the result back
3. If a tool call errors (e.g. bad SQL), the error is fed back to the model
   so it can self-correct — this is emitted as a distinct `self_correction`
   event so the frontend can highlight it
4. When the model calls `final_answer`, the loop ends and yields the summary
   plus optional chart/table data

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

`tests/eval_fixtures/eval_questions.json` holds ~35 natural-language
questions with expected values. The deterministic tests run the stats
functions directly against the built database (skipped if
`data/cricket.duckdb` is absent). To run the LLM-in-the-loop eval (spends
API credits, needs a key):

```bash
pytest -m llm tests/test_llm_eval.py
```

## Next steps

- Re-ingest `data/cricket.duckdb` with the new schema when convenient:
  per-type extra columns and the `deliveries_wickets` table enable exact
  multi-extra/multi-wicket scoring and fielding stats (until then, the stats
  tools transparently fall back to the old-schema approximation)
- Cricsheet `people.csv` registry for exact player-name resolution
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
- `src/components/ChartView.jsx` / `TableView.jsx` — render `chart_data` /
  `table_data` from the `final_answer` event

`npm run build` produces a static `dist/` you can deploy anywhere (Vercel,
Netlify, etc.) — just make sure `VITE_API_BASE` points at your deployed
backend.
