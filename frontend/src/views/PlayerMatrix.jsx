import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiGet, apiSend } from "../api.js";
import { useCopilotContext } from "../copilot/CopilotProvider.jsx";
import { ScatterMatrix } from "../components/kit/Charts.jsx";
import DataTable from "../components/kit/DataTable.jsx";
import { FilterBar, MetricPicker, RoleToggle, filtersFor } from "../components/kit/Inputs.jsx";
import Panel, { ErrorNote, Loading } from "../components/kit/Panel.jsx";
import { formatValue } from "../components/kit/theme.js";
import { useFetch } from "../hooks/useFetch.js";
import { stateUrl, useViewState } from "../hooks/useViewState.js";

const DEFAULTS = { role: "batting", x: "true_average", y: "true_sr", min_balls: null, filters: { format: "T20I" } };
const ROLE_DEFAULTS = { batting: { x: "true_average", y: "true_sr" }, bowling: { x: "true_economy", y: "true_wickets" } };

export default function PlayerMatrix() {
  const navigate = useNavigate();
  const [st, set] = useViewState(DEFAULTS);
  const [selected, setSelected] = useState(null);
  const wl = useFetch(() => apiGet("/api/watchlist"), "watchlist");
  const watch = wl.data?.players || [];
  const body = { role: st.role, x: st.x, y: st.y, min_balls: st.min_balls, filters: filtersFor(st.role, st.filters) };
  const res = useFetch((s) => apiSend("/api/matrix", body, "POST", s), JSON.stringify(body));

  const points = useMemo(() => (res.data?.rows || []).map((r) => Object.fromEntries(res.data.columns.map((c, i) => [c, r[i]]))), [res.data]);
  const ax = res.data?.axes;
  const sel = points.find((p) => p.player === selected);

  useCopilotContext({
    view: "player matrix", settings: body,
    visible: { axes: ax && { x: ax.x.label, y: ax.y.label }, medians: res.data?.medians, standouts: res.data?.highlights,
      players_plotted: points.length, selected: sel },
  });

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>Player Matrix</h1>
          <p className="muted">Every qualified player on two metrics. Dashed lines are medians; the top-right corner is better on both when both axes point "better" that way.</p>
        </div>
      </div>
      <div className="controls-row">
        <RoleToggle value={st.role} onChange={(r) => set({ role: r, ...ROLE_DEFAULTS[r] })} />
        <MetricPicker role={st.role} value={st.x} onChange={(x) => set({ x })} label="X axis" />
        <MetricPicker role={st.role} value={st.y} onChange={(y) => set({ y })} label="Y axis" />
        <label className="filter-field">
          <span>Min balls</span>
          <input type="number" min="0" value={st.min_balls ?? ""} placeholder="auto"
            onChange={(e) => set({ min_balls: e.target.value === "" ? null : Number(e.target.value) })} />
        </label>
      </div>
      <div className="controls-row">
        <FilterBar filters={st.filters} role={st.role} onChange={(f) => set({ filters: f })} />
      </div>

      <div className="matrix-layout">
        <Panel title={res.data?.title || "Player matrix"} subtitle={res.data?.notes?.slice(-1)[0]}
          explain={{ data: { axes: ax && { x: ax.x.label, y: ax.y.label }, medians: res.data?.medians, standouts: res.data?.highlights },
            question: "Who stands out on this matrix, and what kinds of players sit in each corner?" }}>
          {res.loading && <Loading />}
          <ErrorNote error={res.error} />
          {ax && (
            <ScatterMatrix points={points} xKey={st.x} yKey={st.y} xLabel={ax.x.label} yLabel={ax.y.label} medians={res.data.medians}
              highlighted={watch} selected={selected} onSelect={setSelected} standouts={res.data.standouts || []}
              xBetterHigh={ax.x.higher_is_better} yBetterHigh={ax.y.higher_is_better} />
          )}
          {res.data?.highlights?.best_on_both_axes && (
            <p className="standouts"><span>Best on both axes:</span> {res.data.highlights.best_on_both_axes}</p>
          )}
        </Panel>
        <aside className="matrix-side">
          {sel ? (
            <Panel title={sel.player} subtitle={sel.team}>
              <dl className="stat-cards">
                <div className="stat-card"><dt>{ax?.x.label}</dt><dd>{formatValue(sel[st.x], st.x)}</dd></div>
                <div className="stat-card"><dt>{ax?.y.label}</dt><dd>{formatValue(sel[st.y], st.y)}</dd></div>
                <div className="stat-card"><dt>Balls</dt><dd>{formatValue(sel.balls)}</dd></div>
                <div className="stat-card"><dt>Innings</dt><dd>{formatValue(sel.innings)}</dd></div>
              </dl>
              <button type="button" className="primary-btn" onClick={() => navigate(stateUrl(`/players/${encodeURIComponent(sel.player)}`, { role: st.role, filters: st.filters }))}>
                Open in Player Hub
              </button>
            </Panel>
          ) : (
            <Panel title="Pick a point">
              <p className="muted">Click a player to see their numbers. Watchlisted players are labelled in brass.</p>
            </Panel>
          )}
          {ax && (
            <Panel title="Axes">
              <p className="axis-def"><strong>{ax.x.label}:</strong> {ax.x.definition}</p>
              <p className="axis-def"><strong>{ax.y.label}:</strong> {ax.y.definition}</p>
            </Panel>
          )}
        </aside>
      </div>
      {res.data && (
        <Panel title="All plotted players">
          <DataTable table={{ ...res.data, title: undefined, notes: undefined }} maxHeight={360}
            onRowClick={(row) => setSelected(row[0])} highlight={watch} />
        </Panel>
      )}
    </div>
  );
}
