import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { apiGet, apiSend, playerPath } from "../api.js";
import { useCopilotContext } from "../copilot/CopilotProvider.jsx";
import { LineChartKit, Heatmap, PercentileBars } from "../components/kit/Charts.jsx";
import DataTable from "../components/kit/DataTable.jsx";
import { FilterBar, PlayerPicker, RoleToggle, filtersFor, useMetrics } from "../components/kit/Inputs.jsx";
import Panel, { ErrorNote, Loading, summarize } from "../components/kit/Panel.jsx";
import { formatValue } from "../components/kit/theme.js";
import { useFetch } from "../hooks/useFetch.js";
import { stateUrl, useViewState } from "../hooks/useViewState.js";

const DEFAULTS = { role: "batting", filters: {}, window: 10, split: "format", formMetric: "average" };
const SPLITS = ["format", "season", "competition", "opposition", "venue", "phase", "position", "entry_phase", "entry_wickets", "chase", "result", "dismissal"];
const SPLIT_METRICS = {
  batting: "innings,runs,average,strike_rate,true_sr,hundreds,fifties,boundary_pct",
  bowling: "innings,wickets,average,economy,strike_rate,true_economy,dot_pct",
};
const CARDS = {
  batting: [["matches", "innings", "runs", "average", "strike_rate", "highest", "hundreds", "fifties"],
    ["true_sr", "true_average", "match_factor", "era_factor", "first5_sr", "conversion_pct", "dot_pct", "boundary_pct"],
    ["fib_average", "regressed_average", "regressed_sr", "dismissal_luck", "runs_luck"]],
  bowling: [["matches", "innings", "wickets", "average", "economy", "strike_rate", "best", "five_wkt_hauls"],
    ["true_economy", "true_wickets", "match_factor", "dot_pct", "boundary_pct"],
    ["fib_economy", "fib_average", "regressed_economy", "regressed_average", "wicket_luck", "runs_luck"]],
};

function StatCards({ row, ids, metrics, role, context }) {
  const byId = Object.fromEntries(metrics.filter((m) => m.role === role).map((m) => [m.id, m]));
  return (
    <dl className={`stat-cards${context ? " context" : ""}`}>
      {ids.filter((id) => id in row).map((id) => (
        <div className="stat-card" key={id} title={byId[id]?.definition}>
          <dt>{byId[id]?.label || id}</dt>
          <dd>{formatValue(row[id], id)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Landing() {
  const navigate = useNavigate();
  return (
    <div className="landing">
      <h1>Player Hub</h1>
      <p>Career figures, form, context-adjusted numbers, entry points and lookalikes for any player in the data.</p>
      <PlayerPicker autoFocus onPick={(name) => navigate(`/players/${encodeURIComponent(name)}`)} />
      <div className="quick-picks">
        {["Virat Kohli", "Jasprit Bumrah", "Smriti Mandhana", "Joe Root", "Rashid Khan", "Heinrich Klaasen"].map((n) => (
          <button type="button" key={n} className="suggestion-chip" onClick={() => navigate(`/players/${encodeURIComponent(n)}`)}>{n}</button>
        ))}
      </div>
    </div>
  );
}

export default function PlayerHub() {
  const { name } = useParams();
  const navigate = useNavigate();
  const [st, set] = useViewState(DEFAULTS);
  const metrics = useMetrics();
  const [watchlist, setWatchlist] = useState(null);
  const role = st.role;
  const filters = filtersFor(role, st.filters);   // bowling views don't send the batting-only filters
  const fkey = JSON.stringify(filters);

  const profile = useFetch(name ? (s) => apiGet(`${playerPath(name)}/profile`, filters, s) : null, `${name}|${fkey}`);
  const hasRole = profile.data?.[role];
  const form = useFetch(name && hasRole ? (s) => apiGet(`${playerPath(name)}/form`, { ...filters, role, window: st.window }, s) : null,
    `${name}|${fkey}|${role}|${st.window}|${!!hasRole}`);
  const splits = useFetch(name && hasRole ? (s) => apiGet(`${playerPath(name)}/splits`,
    { ...filters, role, split_by: st.split, metrics: SPLIT_METRICS[role] }, s) : null, `${name}|${fkey}|${role}|${st.split}|${!!hasRole}`);
  const pct = useFetch(name && hasRole ? (s) => apiGet(`${playerPath(name)}/percentiles`, { ...filters, role }, s) : null,
    `${name}|${fkey}|${role}|${!!hasRole}`);
  const entry = useFetch(name && hasRole && role === "batting" ? (s) => apiGet(`${playerPath(name)}/entry-heatmap`, filters, s) : null,
    `${name}|${fkey}|${role}|${!!hasRole}`);
  const similar = useFetch(name && hasRole ? (s) => apiGet(`${playerPath(name)}/similar`, { ...filters, role, limit: 8 }, s) : null,
    `${name}|${fkey}|${role}|${!!hasRole}`);
  const wl = useFetch(() => apiGet("/api/watchlist"), "watchlist");
  const onList = (watchlist ?? wl.data?.players ?? []);

  const summaryRow = useMemo(() => {
    const t = profile.data?.[role]?.summary;
    return t?.rows?.length ? Object.fromEntries(t.columns.map((c, i) => [c, t.rows[0][i]])) : null;
  }, [profile.data, role]);

  const formData = useMemo(() => (form.data?.rows || []).map((r) => Object.fromEntries(form.data.columns.map((c, i) => [c, r[i]]))), [form.data]);
  const formSeries = role === "batting"
    ? (st.formMetric === "strike_rate"
      ? [{ key: "rolling_strike_rate", label: `Last ${st.window} innings` }, { key: "career_strike_rate", label: "Career to date" }]
      : [{ key: "rolling_average", label: `Last ${st.window} innings` }, { key: "career_average", label: "Career to date" }])
    : (st.formMetric === "average"
      ? [{ key: "rolling_average", label: `Last ${st.window} innings` }, { key: "career_average", label: "Career to date" }]
      : [{ key: "rolling_economy", label: `Last ${st.window} innings` }, { key: "career_economy", label: "Career to date" }]);

  const heat = useMemo(() => {
    const rows = entry.data?.rows || [];
    const cells = {};
    const phases = [];
    const wkts = [];
    rows.forEach(([ph, wk, innings, runs, balls, average, strike_rate, true_sr]) => {
      if (!phases.includes(ph)) phases.push(ph);
      if (!wkts.includes(wk)) wkts.push(wk);
      cells[`${ph}|${wk}`] = { innings, runs, balls, average, strike_rate, true_sr };
    });
    wkts.sort((a, b) => (a === "5+" ? 99 : +a) - (b === "5+" ? 99 : +b));
    return { phases, wkts, cells };
  }, [entry.data]);

  const pctView = useMemo(() => {
    const t = pct.data;
    if (!t?.rows) return null;
    const values = {};
    const ms = [];
    t.rows.forEach(([m, p, value, percentile]) => {
      if (!ms.includes(m)) ms.push(m);
      values[`${m}|${p}`] = { value, percentile };
    });
    return { metrics: ms, player: t.rows[0]?.[1], values, labels: t.labels };
  }, [pct.data]);

  useCopilotContext(name ? {
    view: "player hub", player: profile.data?.player || name, settings: st,
    visible: { summary: summaryRow, form_highlights: form.data?.highlights, splits: summarize(splits.data, 12),
      percentiles: pct.data?.rows?.slice(0, 12), similar: similar.data?.rows?.slice(0, 6) },
  } : { view: "player hub (search)" });

  if (!name) return <Landing />;

  async function toggleWatch() {
    const p = profile.data?.player;
    if (!p) return;
    const next = onList.includes(p) ? onList.filter((x) => x !== p) : [...onList, p];
    setWatchlist((await apiSend("/api/watchlist", { players: next }, "PUT")).players);
  }

  const p = profile.data;
  return (
    <div className="view">
      <div className="view-head">
        <div className="player-title">
          {p ? (
            <>
              <h1>{p.player}</h1>
              <p className="muted">
                {p.teams?.join(" · ")} · {p.span} · {p.matches_all_cricket} matches in the data · {p.primary_role}
                {p.gender === "female" ? " · women's cricket" : ""}
              </p>
              {p.note && <p className="resolution-note">{p.note}</p>}
              {p.coverage_notes?.map((n) => (
                <p className="coverage-note" key={n}>{n} <Link to="/data">What's missing</Link></p>
              ))}
            </>
          ) : <h1>{name}</h1>}
        </div>
        <div className="view-tools">
          <PlayerPicker onPick={(n) => navigate(stateUrl(`/players/${encodeURIComponent(n)}`, st))} placeholder="Switch player…" />
          {p && (
            <button type="button" className={`ghost-btn watch-btn${onList.includes(p.player) ? " on" : ""}`} onClick={toggleWatch}
              aria-pressed={onList.includes(p.player)}>
              {onList.includes(p.player) ? "★ Watchlisted" : "☆ Watchlist"}
            </button>
          )}
          {p && <button type="button" className="ghost-btn" onClick={() => navigate(stateUrl("/compare", { players: [p.player], role, filters: st.filters }))}>Compare…</button>}
        </div>
      </div>

      <div className="controls-row">
        <RoleToggle value={role} onChange={(r) => set({ role: r, formMetric: r === "batting" ? "average" : "economy" })} />
        <FilterBar filters={st.filters} role={role} onChange={(f) => set({ filters: f })} />
      </div>

      {profile.loading && <Loading />}
      <ErrorNote error={profile.error} onPick={(n) => navigate(`/players/${encodeURIComponent(n)}`)} />

      {p && !hasRole && !profile.loading && (
        <p className="empty-note">No {role} records for {p.player} with these filters.</p>
      )}

      {summaryRow && (
        <Panel title={`${role === "batting" ? "Batting" : "Bowling"} summary`} subtitle="Hover a figure for its definition."
          explain={{ data: { summary: summaryRow }, question: `Summarise ${p.player}'s ${role} profile. What do the context-adjusted numbers say?` }}>
          <StatCards row={summaryRow} ids={CARDS[role][0]} metrics={metrics} role={role} />
          <p className="section-label">Context-adjusted</p>
          <StatCards row={summaryRow} ids={CARDS[role][1]} metrics={metrics} role={role} context />
          <p className="section-label">
            Skill vs luck <Link to="/methodology/fibs" className="section-link">how this works</Link>
          </p>
          <StatCards row={summaryRow} ids={CARDS[role][2]} metrics={metrics} role={role} context />
        </Panel>
      )}

      {hasRole && (
        <div className="grid-2">
          <Panel title="Form" subtitle={form.data?.highlights?.[`current_${formSeries[0].key}`] ? `Current: ${form.data.highlights[`current_${formSeries[0].key}`]}` : undefined}
            explain={{ data: { highlights: form.data?.highlights, recent: formData.slice(-15) }, question: `Is ${p?.player} in form? Use the rolling figures.` }}
            actions={
              <>
                <select aria-label="Form metric" value={st.formMetric} onChange={(e) => set({ formMetric: e.target.value })}>
                  {role === "batting"
                    ? [["average", "Average"], ["strike_rate", "Strike rate"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)
                    : [["economy", "Economy"], ["average", "Average"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
                <select aria-label="Rolling window" value={st.window} onChange={(e) => set({ window: Number(e.target.value) })}>
                  {[5, 10, 20, 30].map((w) => <option key={w} value={w}>Last {w}</option>)}
                </select>
              </>
            }>
            {form.loading ? <Loading /> : (
              <>
                <LineChartKit data={formData} x="innings_no" series={formSeries} xLabel="Innings" height={260} />
                {form.data?.highlights && (
                  <ul className="highlight-list">
                    {Object.entries(form.data.highlights).filter(([k]) => !k.startsWith("current")).map(([k, v]) => (
                      <li key={k}><span>{k.replace(/_/g, " ")}</span> {v}</li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </Panel>
          <Panel title="Percentile vs peers" subtitle="Against every qualified player in the same filters. 100 = best."
            explain={{ data: pct.data?.rows, question: `Where does ${p?.player} rank among peers, and on which metrics do they stand out?` }}>
            {pct.loading ? <Loading /> : pctView && (
              <PercentileBars metrics={pctView.metrics} players={[pctView.player]} values={pctView.values} labels={pctView.labels} />
            )}
            {pct.data?.notes && <p className="table-notes-inline">{pct.data.notes[pct.data.notes.length - 1]}</p>}
          </Panel>
        </div>
      )}

      {hasRole && (
        <Panel title="Splits" explain={{ data: summarize(splits.data), question: "What stands out in these splits?" }}
          actions={
            <select aria-label="Split by" value={st.split} onChange={(e) => set({ split: e.target.value })}>
              {SPLITS.filter((s) => role === "batting" || !["position", "entry_phase", "entry_wickets", "dismissal"].includes(s)).map((s) => (
                <option key={s} value={s}>by {s.replace("_", " ")}</option>
              ))}
            </select>
          }>
          {splits.loading ? <Loading /> : <DataTable table={splits.data} maxHeight={380} />}
          <ErrorNote error={splits.error} />
        </Panel>
      )}

      {hasRole && role === "batting" && heat.phases.length > 0 && (
        <Panel title="Entry points" subtitle="Average by the phase they walked in and wickets already down (shade = average)."
          explain={{ data: summarize(entry.data, 20), question: "What does this entry-point map say about this batter's role?" }}>
          <Heatmap rows={heat.phases} cols={heat.wkts} cells={heat.cells} valueKey="average" subKey="innings" labelKey="entry ↓  /  wickets down →"
            format={(c) => `${c.innings} innings · ${c.runs} runs · avg ${formatValue(c.average, "average")} · SR ${formatValue(c.strike_rate, "strike_rate")}`} />
          <p className="heatmap-note">Cells with fewer than 5 innings aren't shaded. Pick a format to separate T20/ODI phases from Test over bands.</p>
        </Panel>
      )}

      {hasRole && similar.data && (
        <Panel title="Similar players" subtitle={similar.data.notes?.[similar.data.notes.length - 1]}
          explain={{ data: summarize(similar.data), question: `Why are these players similar to ${p?.player}?` }}>
          <DataTable table={{ ...similar.data, notes: undefined, filters: undefined }} maxHeight={340}
            onRowClick={(row) => navigate(stateUrl(`/players/${encodeURIComponent(row[0])}`, st))} />
        </Panel>
      )}
    </div>
  );
}
