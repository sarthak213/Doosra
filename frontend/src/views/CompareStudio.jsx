import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { apiSend } from "../api.js";
import { useCopilotContext } from "../copilot/CopilotProvider.jsx";
import { LineChartKit, PercentileBars } from "../components/kit/Charts.jsx";
import DataTable from "../components/kit/DataTable.jsx";
import { FilterBar, MetricPicker, PlayerPicker, RoleToggle } from "../components/kit/Inputs.jsx";
import Panel, { ErrorNote, Loading, summarize } from "../components/kit/Panel.jsx";
import { SERIES } from "../components/kit/theme.js";
import { useFetch } from "../hooks/useFetch.js";
import { stateUrl, useViewState } from "../hooks/useViewState.js";

const METRICS = {
  batting: ["matches", "innings", "runs", "average", "strike_rate", "true_sr", "true_average", "match_factor", "hundreds", "fifties", "boundary_pct", "dot_pct"],
  bowling: ["matches", "wickets", "average", "economy", "strike_rate", "true_economy", "true_wickets", "match_factor", "dot_pct", "best"],
};
const ARC = { batting: ["average", "strike_rate", "runs", "true_sr"], bowling: ["wickets", "average", "economy", "strike_rate"] };
const DEFAULTS = { players: [], role: "batting", filters: {}, metrics: null, arc: "average" };

export default function CompareStudio() {
  const navigate = useNavigate();
  const [st, set] = useViewState(DEFAULTS);
  const metrics = st.metrics || METRICS[st.role];
  const arcMetric = ARC[st.role].includes(st.arc) ? st.arc : ARC[st.role][0];
  const key = JSON.stringify([st.players, st.role, st.filters, metrics, arcMetric]);
  const res = useFetch(st.players.length ? (s) => apiSend("/api/compare", {
    players: st.players, role: st.role, metrics, filters: st.filters, arc_metric: arcMetric,
  }, "POST", s) : null, key);

  const resolved = useMemo(() => res.data?.table?.rows?.map((r) => r[0]) || [], [res.data]);
  const pct = useMemo(() => {
    const t = res.data?.percentiles;
    if (!t?.rows) return null;
    const values = {};
    const ms = [];
    t.rows.forEach(([m, p, value, percentile]) => {
      if (!ms.includes(m)) ms.push(m);
      values[`${m}|${p}`] = { value, percentile };
    });
    return { metrics: ms, values, labels: t.labels };
  }, [res.data]);
  const arc = useMemo(() => {
    const t = res.data?.arc;
    if (!t?.rows) return null;
    return { data: t.rows.map((r) => Object.fromEntries(t.columns.map((c, i) => [c, r[i]]))), players: t.columns.slice(1) };
  }, [res.data]);

  useCopilotContext({
    view: "comparison studio", settings: st,
    visible: { table: summarize(res.data?.table), highlights: res.data?.table?.highlights, arc_highlights: res.data?.arc?.highlights },
  });

  const add = (n) => set({ players: [...st.players.filter((p) => p !== n), n].slice(0, 4) });
  const remove = (n) => set({ players: st.players.filter((p) => p !== n) });

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>Comparison Studio</h1>
          <p className="muted">Up to four players on the same filters: side-by-side figures, percentiles against peers, and careers aligned by innings number.</p>
        </div>
      </div>
      <div className="controls-row">
        <div className="compare-players">
          {st.players.map((p, i) => (
            <span className="player-chip" key={p} style={{ borderColor: SERIES[i] }}>
              <span className="legend-swatch" style={{ background: SERIES[i] }} aria-hidden="true" />
              {resolved[i] || p}
              <button type="button" onClick={() => remove(p)} aria-label={`Remove ${p}`}>×</button>
            </span>
          ))}
          {st.players.length < 4 && <PlayerPicker onPick={add} placeholder={st.players.length ? "Add another…" : "Add a player…"} />}
        </div>
        <RoleToggle value={st.role} onChange={(r) => set({ role: r, metrics: null })} />
      </div>
      <div className="controls-row">
        <FilterBar filters={st.filters} onChange={(f) => set({ filters: f })} />
      </div>
      <div className="controls-row">
        <MetricPicker role={st.role} multiple value={metrics} onChange={(m) => set({ metrics: m })} label="Metrics" />
      </div>

      {!st.players.length && (
        <div className="empty-note">
          Add players to compare. Try{" "}
          {[["Babar Azam", "Mohammad Rizwan"], ["Joe Root", "Steve Smith", "Kane Williamson", "Virat Kohli"]].map((ps) => (
            <button type="button" key={ps.join()} className="link-btn" onClick={() => set({ players: ps, filters: ps.length === 2 ? { format: "T20I" } : { format: "Test" } })}>
              {ps.join(" vs ")}
            </button>
          ))}
        </div>
      )}
      {res.loading && <Loading />}
      <ErrorNote error={res.error} onPick={add} />

      {res.data && (
        <>
          <Panel title="Side by side" explain={{ data: summarize(res.data.table), question: "Compare these players. Who's better at what?" }}>
            <DataTable table={res.data.table} onRowClick={(row) => navigate(stateUrl(`/players/${encodeURIComponent(row[0])}`, { role: st.role, filters: st.filters }))} />
            {res.data.table.highlights && (
              <ul className="highlight-list">
                {Object.entries(res.data.table.highlights).map(([k, v]) => <li key={k}><span>{k.replace(/_/g, " ")}</span> {v}</li>)}
              </ul>
            )}
          </Panel>
          <div className="grid-2">
            <Panel title="Percentile vs peers" subtitle={res.data.percentiles?.notes?.slice(-1)[0]}
              explain={{ data: res.data.percentiles?.rows, question: "Using the percentiles, where does each player stand out?" }}>
              {pct && <PercentileBars metrics={pct.metrics} players={resolved} values={pct.values} labels={pct.labels} />}
            </Panel>
            <Panel title="Career arc" subtitle="Career-to-date figure after each innings, aligned by innings number."
              explain={{ data: res.data.arc?.highlights, question: "How do these careers compare at the same stage?" }}
              actions={
                <select aria-label="Career arc metric" value={arcMetric} onChange={(e) => set({ arc: e.target.value })}>
                  {ARC[st.role].map((m) => <option key={m} value={m}>{m.replace("_", " ")}</option>)}
                </select>
              }>
              {arc && <LineChartKit data={arc.data} x="innings_no" series={arc.players.map((p) => ({ key: p, label: p }))} xLabel="Innings" height={300} />}
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}
