import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiGet, apiSend } from "../api.js";
import { useCopilotContext } from "../copilot/CopilotProvider.jsx";
import DataTable from "../components/kit/DataTable.jsx";
import { FilterBar, MetricPicker, RoleToggle, useMetrics, filtersFor } from "../components/kit/Inputs.jsx";
import Panel, { ErrorNote, Loading, summarize } from "../components/kit/Panel.jsx";
import { useFetch } from "../hooks/useFetch.js";
import { stateUrl, useViewState } from "../hooks/useViewState.js";

const DEFAULTS = {
  role: "batting",
  metrics: ["runs", "average", "strike_rate", "true_sr", "hundreds", "fifties"],
  sort_by: "runs",
  ascending: null,
  split_by: "",
  min_balls: null,
  limit: 50,
  filters: {},
};
const ROLE_DEFAULTS = {
  batting: { metrics: DEFAULTS.metrics, sort_by: "runs" },
  bowling: { metrics: ["wickets", "average", "economy", "strike_rate", "true_economy", "best"], sort_by: "wickets" },
};
const SPLITS = ["", "season", "year", "format", "competition", "team", "opposition", "phase", "chase", "result"];

// State can arrive from a permalink or the copilot's open_in_app, so make it
// consistent: columns valid for the role, a singular `metric` folded in.
function sanitize(st, allMetrics) {
  const valid = new Set(allMetrics.filter((m) => m.role === st.role).map((m) => m.id));
  let metrics = Array.isArray(st.metrics) ? st.metrics : [];
  if (st.metric && !metrics.includes(st.metric)) metrics = [st.metric, ...metrics];
  if (st.sort_by && !metrics.includes(st.sort_by)) metrics = [st.sort_by, ...metrics];
  if (valid.size) metrics = metrics.filter((m) => valid.has(m));
  if (!metrics.length) metrics = ROLE_DEFAULTS[st.role].metrics;
  return { ...st, metrics };
}

export default function QueryBuilder() {
  const navigate = useNavigate();
  const [raw, set] = useViewState(DEFAULTS);
  const allMetrics = useMetrics();
  const st = sanitize(raw, allMetrics);
  const [saveName, setSaveName] = useState("");
  const [savedTick, setSavedTick] = useState(0);
  const sortBy = st.metrics.includes(st.sort_by) ? st.sort_by : st.metrics[0];
  const body = { role: st.role, metrics: st.metrics, sort_by: sortBy, ascending: st.ascending,
    split_by: st.split_by || null, min_balls: st.min_balls, limit: st.limit, filters: filtersFor(st.role, st.filters) };
  // Wait for the metric registry so incoming state is sanitized before querying.
  const res = useFetch(allMetrics.length ? (s) => apiSend("/api/query", body, "POST", s) : null,
    `${allMetrics.length > 0}|${JSON.stringify(body)}`);
  const saved = useFetch(() => apiGet("/api/views", { kind: "query" }), `views${savedTick}`);
  const sortMeta = allMetrics.find((m) => m.role === st.role && m.id === sortBy);
  const bestFirst = st.ascending === null || st.ascending === undefined;

  useCopilotContext({ view: "query builder", settings: body, visible: summarize(res.data, 15) });

  async function save() {
    if (!saveName.trim()) return;
    await apiSend("/api/views", { name: saveName.trim(), kind: "query", state: st });
    setSaveName("");
    setSavedTick((t) => t + 1);
  }

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>Query Builder</h1>
          <p className="muted">Any metric, any scope. Pick columns, filter, sort; rate metrics get a sensible qualification automatically.</p>
        </div>
        <div className="view-tools saved-views">
          <select aria-label="Load a saved query" value="" onChange={(e) => {
            const v = saved.data?.views?.find((x) => x.id === e.target.value);
            if (v) navigate(stateUrl("/query", v.state));
          }}>
            <option value="">Saved queries ({saved.data?.views?.length || 0})</option>
            {saved.data?.views?.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
          </select>
          <input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder="Name this query" aria-label="Query name"
            onKeyDown={(e) => { if (e.key === "Enter") save(); }} />
          <button type="button" className="ghost-btn" onClick={save} disabled={!saveName.trim()}>Save</button>
        </div>
      </div>

      <div className="controls-row">
        <RoleToggle value={st.role} onChange={(r) => set({ role: r, ...ROLE_DEFAULTS[r], split_by: "" })} />
        <MetricPicker role={st.role} multiple value={st.metrics} onChange={(m) => set({ metrics: m })} label="Columns" />
      </div>
      <div className="controls-row">
        <label className="filter-field">
          <span>Sort by</span>
          <select value={sortBy} onChange={(e) => set({ sort_by: e.target.value })}>
            {st.metrics.map((m) => <option key={m} value={m}>{allMetrics.find((x) => x.role === st.role && x.id === m)?.label || m}</option>)}
          </select>
        </label>
        <label className="filter-field">
          <span>Order</span>
          <select value={bestFirst ? "best" : st.ascending ? "asc" : "desc"}
            onChange={(e) => set({ ascending: e.target.value === "best" ? null : e.target.value === "asc" })}>
            <option value="best">best first{sortMeta ? ` (${sortMeta.higher_is_better ? "high" : "low"})` : ""}</option>
            <option value="desc">highest first</option>
            <option value="asc">lowest first</option>
          </select>
        </label>
        <label className="filter-field">
          <span>Split by</span>
          <select value={st.split_by || ""} onChange={(e) => set({ split_by: e.target.value })}>
            {SPLITS.map((s) => <option key={s} value={s}>{s ? s.replace("_", " ") : "none"}</option>)}
          </select>
        </label>
        <label className="filter-field">
          <span>Min balls</span>
          <input type="number" min="0" value={st.min_balls ?? ""} placeholder="auto"
            onChange={(e) => set({ min_balls: e.target.value === "" ? null : Number(e.target.value) })} />
        </label>
        <label className="filter-field">
          <span>Rows</span>
          <select value={st.limit} onChange={(e) => set({ limit: Number(e.target.value) })}>
            {[10, 25, 50, 100, 200].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      </div>
      <div className="controls-row">
        <FilterBar filters={st.filters} role={st.role} onChange={(f) => set({ filters: f })} />
      </div>

      <Panel explain={{ data: summarize(res.data, 20), question: "What does this table tell us? Point out anything surprising." }}
        title={res.data?.title || "Results"}>
        {res.loading && <Loading />}
        <ErrorNote error={res.error} />
        {res.data && (
          <DataTable table={{ ...res.data, title: undefined }} labels={res.data.labels} maxHeight={620}
            onRowClick={(row, cols) => {
              const name = row[cols.indexOf("player")];
              if (name) navigate(stateUrl(`/players/${encodeURIComponent(name)}`, { role: st.role, filters: st.filters }));
            }} />
        )}
      </Panel>
    </div>
  );
}
