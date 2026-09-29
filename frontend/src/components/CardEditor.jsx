import { useMemo, useState } from "react";
import { apiGet } from "../api.js";
import { useFetch } from "../hooks/useFetch.js";
import BoardCard, { columnKinds } from "./BoardCard.jsx";
import { CommitInput, FilterBar, MetricPicker, PlayerPicker, RoleToggle, useMetrics } from "./kit/Inputs.jsx";
import { humanize } from "./kit/theme.js";

const SPLITS = ["", "season", "year", "format", "competition", "team", "opposition", "phase", "chase", "result"];
const ROLE_METRICS = {
  batting: { metrics: ["runs", "average", "strike_rate"], sort_by: "runs", x: "true_average", y: "true_sr" },
  bowling: { metrics: ["wickets", "average", "economy"], sort_by: "wickets", x: "true_economy", y: "true_wickets" },
};
const TYPES = [["bar", "Bar"], ["line", "Line"], ["scatter", "Scatter"], ["table", "Table"]];

export const newCardId = () => `c${Math.random().toString(36).slice(2, 10)}`;

const defaultSource = (kind, role = "batting") => {
  const d = ROLE_METRICS[role];
  if (kind === "matrix") return { kind, state: { role, x: d.x, y: d.y, min_balls: null, filters: { format: "T20I" } } };
  if (kind === "compare") return { kind, state: { players: [], role, metrics: d.metrics, filters: {} } };
  return { kind: "query", state: { role, metrics: d.metrics, sort_by: d.sort_by, split_by: null, min_balls: null, limit: 10, filters: {} } };
};

// Builds one card: pick a source (Query / Matrix / Compare, or a saved view),
// then how to draw it. The preview is the real card, running the real query.
export default function CardEditor({ card, onSave, onCancel }) {
  const [draft, setDraft] = useState(() => card || { id: newCardId(), title: "", note: "", source: defaultSource("query"), chart: {} });
  const [result, setResult] = useState(null);
  const allMetrics = useMetrics();
  const saved = useFetch(() => apiGet("/api/views"), "views");
  const src = draft.source;
  const st = src.state || {};
  const snapshot = src.kind === "snapshot";

  const patch = (p) => setDraft((d) => ({ ...d, ...p }));
  const setState = (p) => setDraft((d) => ({ ...d, source: { ...d.source, state: { ...d.source.state, ...p } } }));
  const setChart = (p) => setDraft((d) => ({ ...d, chart: { ...d.chart, ...p } }));
  const roleChanged = (role) => setDraft((d) => ({ ...d, chart: {}, source: { ...defaultSource(d.source.kind, role), state: { ...defaultSource(d.source.kind, role).state, filters: d.source.state.filters } } }));
  const usable = useMemo(() => (saved.data?.views || []).filter((v) => ["query", "matrix", "compare"].includes(v.kind)), [saved.data]);
  const { numeric, text } = useMemo(() => columnKinds(result), [result]);
  const metricLabel = (id) => allMetrics.find((m) => m.id === id)?.label || humanize(id);

  const ys = Array.isArray(draft.chart.y) ? draft.chart.y : draft.chart.y ? [draft.chart.y] : [];
  const toggleY = (c) => setChart({ y: ys.includes(c) ? ys.filter((v) => v !== c) : [...ys, c].slice(0, 4) });
  const valid = snapshot || (src.kind === "compare" ? (st.players || []).length > 0 : true);

  return (
    <section className="panel card-editor" aria-label="Card editor">
      <header className="panel-head"><div><h2>{card ? "Edit card" : "New card"}</h2></div></header>
      <div className="controls-row">
        <label className="filter-field grow"><span>Title</span>
          <input value={draft.title} maxLength={160} placeholder="e.g. Death-over economy, IPL 2022 onwards" onChange={(e) => patch({ title: e.target.value })} />
        </label>
        {!snapshot && (
          <label className="filter-field"><span>Start from a saved view</span>
            <select value="" onChange={(e) => {
              const v = usable.find((x) => x.id === e.target.value);
              if (v) setDraft((d) => ({ ...d, chart: {}, title: d.title || v.name, source: { kind: v.kind, state: v.state } }));
            }}>
              <option value="">{usable.length ? "Choose…" : "None saved yet"}</option>
              {usable.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.kind})</option>)}
            </select>
          </label>
        )}
      </div>

      {snapshot ? (
        <p className="muted">This card is a static snapshot saved from a chat, so its data can't be changed here. You can still change how it's drawn.</p>
      ) : (
        <>
          <div className="controls-row">
            <div className="segmented" role="radiogroup" aria-label="Data source">
              {[["query", "Query"], ["matrix", "Matrix"], ["compare", "Compare"]].map(([k, l]) => (
                <button type="button" key={k} role="radio" aria-checked={src.kind === k} className={src.kind === k ? "on" : ""}
                  onClick={() => src.kind !== k && setDraft((d) => ({ ...d, chart: {}, source: defaultSource(k, d.source.state?.role) }))}>{l}</button>
              ))}
            </div>
            <RoleToggle value={st.role || "batting"} onChange={roleChanged} />
          </div>

          {src.kind === "query" && (
            <div className="controls-row">
              <MetricPicker role={st.role || "batting"} multiple value={st.metrics || []} label="Columns"
                onChange={(m) => setState({ metrics: m, sort_by: m.includes(st.sort_by) ? st.sort_by : m[0] })} />
              <label className="filter-field"><span>Sort by</span>
                <select value={st.sort_by || ""} onChange={(e) => setState({ sort_by: e.target.value })}>
                  {(st.metrics || []).map((m) => <option key={m} value={m}>{metricLabel(m)}</option>)}
                </select>
              </label>
              <label className="filter-field"><span>Split by</span>
                <select value={st.split_by || ""} onChange={(e) => setState({ split_by: e.target.value || null })}>
                  {SPLITS.map((s) => <option key={s} value={s}>{s ? s.replace("_", " ") : "none"}</option>)}
                </select>
              </label>
              <label className="filter-field"><span>Rows</span>
                <CommitInput type="number" value={st.limit} placeholder="10" onCommit={(v) => setState({ limit: Math.min(Math.max(v || 10, 1), 200) })} />
              </label>
            </div>
          )}
          {src.kind === "matrix" && (
            <div className="controls-row">
              <MetricPicker role={st.role || "batting"} value={st.x} label="X axis" onChange={(x) => setState({ x })} />
              <MetricPicker role={st.role || "batting"} value={st.y} label="Y axis" onChange={(y) => setState({ y })} />
              <label className="filter-field"><span>Min balls</span>
                <CommitInput type="number" value={st.min_balls} placeholder="auto" onCommit={(v) => setState({ min_balls: v })} />
              </label>
            </div>
          )}
          {src.kind === "compare" && (
            <div className="controls-row">
              <div className="filter-field grow"><span>Players</span>
                <div className="chips">
                  {(st.players || []).map((p) => (
                    <button type="button" key={p} className="chip" title="Remove" onClick={() => setState({ players: st.players.filter((x) => x !== p) })}>{p} ✕</button>
                  ))}
                  {(st.players || []).length < 4 && <PlayerPicker placeholder="Add a player…" onPick={(p) => setState({ players: [...(st.players || []), p.name || p] })} />}
                </div>
              </div>
              <MetricPicker role={st.role || "batting"} multiple value={st.metrics || []} label="Metrics" onChange={(m) => setState({ metrics: m })} />
            </div>
          )}
          <FilterBar filters={st.filters || {}} role={st.role} onChange={(f) => setState({ filters: f })} />
        </>
      )}

      <div className="controls-row">
        <label className="filter-field"><span>Chart</span>
          <select value={draft.chart.type || ""} onChange={(e) => setChart({ type: e.target.value || undefined })}>
            <option value="">Automatic</option>
            {TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        {draft.chart.type !== "table" && result && (
          <label className="filter-field"><span>{draft.chart.type === "scatter" || src.kind === "matrix" ? "X" : "Category"}</span>
            <select value={draft.chart.x || ""} onChange={(e) => setChart({ x: e.target.value || undefined })}>
              <option value="">Automatic</option>
              {[...text, ...numeric].map((c) => <option key={c} value={c}>{humanize(c)}</option>)}
            </select>
          </label>
        )}
        {draft.chart.type !== "table" && result && (
          <div className="filter-field"><span>{draft.chart.type === "scatter" || src.kind === "matrix" ? "Y" : "Values (up to 4)"}</span>
            <div className="chips">
              {numeric.map((c) => (
                <button type="button" key={c} className={`chip${ys.includes(c) ? " on" : ""}`} aria-pressed={ys.includes(c)}
                  onClick={() => (draft.chart.type === "scatter" || src.kind === "matrix" ? setChart({ y: [c] }) : toggleY(c))}>{humanize(c)}</button>
              ))}
            </div>
          </div>
        )}
      </div>

      <label className="filter-field"><span>Note (optional, shown under the chart and read by the AI)</span>
        <textarea rows={2} value={draft.note} maxLength={2000} onChange={(e) => patch({ note: e.target.value })}
          placeholder="Why this chart matters, e.g. 'Bowlers under 8.5 economy are our shortlist.'" />
      </label>

      <BoardCard card={draft} onResult={setResult} />

      <div className="note-actions">
        <button type="button" className="primary-btn" disabled={!valid} onClick={() => onSave({ ...draft, title: draft.title.trim() })}>
          {card ? "Save card" : "Add card"}
        </button>
        <button type="button" className="ghost-btn" onClick={onCancel}>Cancel</button>
        {!valid && <span className="note-meta">Add at least one player.</span>}
      </div>
    </section>
  );
}
