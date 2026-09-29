import { useMemo } from "react";
import { apiSend } from "../api.js";
import { useFetch } from "../hooks/useFetch.js";
import { BarChartKit, LineChartKit, ScatterMatrix } from "./kit/Charts.jsx";
import DataTable from "./kit/DataTable.jsx";
import { useMetrics } from "./kit/Inputs.jsx";
import { ErrorNote, Loading } from "./kit/Panel.jsx";
import { humanize } from "./kit/theme.js";

const SKIP = new Set(["rank"]);
const TIME_SPLITS = new Set(["season", "year"]);

// A result envelope ({columns, rows}) as records, plus which columns are numbers.
export function records(result) {
  const cols = result?.columns || [];
  return (result?.rows || []).map((r) => Object.fromEntries(cols.map((c, i) => [c, r[i]])));
}
export function columnKinds(result) {
  const rows = result?.rows || [];
  const numeric = [], text = [];
  (result?.columns || []).forEach((c, i) => {
    if (SKIP.has(c)) return;
    const vals = rows.map((r) => r[i]).filter((v) => v !== null && v !== undefined);
    (vals.length && vals.every((v) => typeof v === "number") ? numeric : text).push(c);
  });
  return { numeric, text };
}

// What to draw for a card: its saved choices where they still fit the result,
// otherwise sensible defaults (so changing the query never leaves a blank chart).
export function chartFor(card, result) {
  const { numeric, text } = columnKinds(result);
  const cfg = card.chart || {};
  const state = card.source?.state || {};
  const kind = card.source?.kind;
  let type = cfg.type;
  if (!type) type = kind === "matrix" ? "scatter" : TIME_SPLITS.has(state.split_by) ? "line" : "bar";
  if (type === "scatter" && kind !== "matrix" && numeric.length < 2) type = "bar";
  const x = text.includes(cfg.x) || numeric.includes(cfg.x) ? cfg.x
    : (state.split_by && text.includes(state.split_by) ? state.split_by : text.includes("player") ? "player" : text[0] || numeric[0]);
  let ys = (Array.isArray(cfg.y) ? cfg.y : cfg.y ? [cfg.y] : []).filter((c) => numeric.includes(c));
  if (!ys.length) {
    const preferred = kind === "query" ? [state.sort_by, ...(state.metrics || [])] : [];
    ys = [preferred.find((m) => numeric.includes(m)) || numeric.filter((c) => c !== x)[0]].filter(Boolean);
  }
  return { type, x, ys: ys.slice(0, 4), limit: cfg.limit || 15, sort: cfg.sort !== false };
}

function Drawn({ card, result }) {
  const metrics = useMetrics();
  const label = (id) => metrics.find((m) => m.id === id)?.label || humanize(id);
  const spec = useMemo(() => chartFor(card, result), [card, result]);
  const recs = useMemo(() => records(result), [result]);
  if (!result?.rows?.length) return <p className="empty-note">This card has no rows for its current filters.</p>;
  if (spec.type === "table") return <DataTable table={result} compact maxHeight={320} />;
  if (spec.type === "scatter") {
    const state = card.source?.state || {};
    const xk = spec.x && result.columns.includes(spec.x) ? spec.x : state.x;
    const yk = spec.ys[0] || state.y;
    if (!recs[0]?.player) return <DataTable table={result} compact maxHeight={320} />;
    return <ScatterMatrix points={recs} xKey={xk} yKey={yk} xLabel={label(xk)} yLabel={label(yk)} medians={result.medians}
      height={300} medianLabel="median" />;
  }
  if (!spec.x || !spec.ys.length) return <DataTable table={result} compact maxHeight={320} />;
  let data = recs;
  if (spec.type === "bar") {
    if (spec.sort) data = [...data].sort((a, b) => (b[spec.ys[0]] ?? -Infinity) - (a[spec.ys[0]] ?? -Infinity));
    data = data.slice(0, spec.limit);
  }
  const series = spec.ys.map((k) => ({ key: k, label: label(k) }));
  return spec.type === "line"
    ? <LineChartKit data={data} x={spec.x} series={series} height={260} xLabel={humanize(spec.x)} yLabel={series.length === 1 ? series[0].label : undefined} />
    : <BarChartKit data={data} x={spec.x} series={series} height={260} />;
}

// Runs the card's source and draws it. `children` (buttons) go in the header.
export default function BoardCard({ card, children, onResult }) {
  const key = JSON.stringify(card.source);
  const res = useFetch((signal) => apiSend("/api/boards/render-card", { source: card.source }, "POST", signal).then((r) => { onResult?.(r); return r; }), key);
  const snapshot = card.source?.kind === "snapshot";
  return (
    <article className="board-card">
      <header className="board-card-head">
        <div>
          <h3>{card.title || res.data?.title || "Untitled card"}</h3>
          {snapshot && <p className="note-meta">Static snapshot{card.source.saved_at ? `, saved ${card.source.saved_at.slice(0, 10)}` : ""}. It won't update with new data.</p>}
          {res.data?.filters && !snapshot && <p className="note-meta">{Object.entries(res.data.filters).map(([k, v]) => `${humanize(k)}: ${v}`).join(" · ")}</p>}
        </div>
        <div className="board-card-actions">{children}</div>
      </header>
      {res.loading && <Loading />}
      <ErrorNote error={res.error} />
      {res.data && <Drawn card={card} result={res.data} />}
      {card.note && <p className="board-card-note">{card.note}</p>}
    </article>
  );
}
