import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiGet } from "../api.js";
import { useCopilotContext } from "../copilot/CopilotProvider.jsx";
import DataTable from "../components/kit/DataTable.jsx";
import { CommitInput } from "../components/kit/Inputs.jsx";
import Panel, { ErrorNote, Loading, summarize } from "../components/kit/Panel.jsx";
import { useFetch } from "../hooks/useFetch.js";
import { useViewState } from "../hooks/useViewState.js";

// What the ball-by-ball data covers and what it's missing, from Cricsheet's
// coverage and missing-match pages (parsed into the database every build).

const DEFAULTS = { gender: "", missing: { format: "", competition: "", team: "", gender: "" } };

function Segmented({ value, options, onChange, label }) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map(([v, l]) => (
        <button type="button" key={v || "all"} role="radio" aria-checked={value === v} className={value === v ? "on" : ""}
          onClick={() => onChange(v)}>{l}</button>
      ))}
    </div>
  );
}

export default function DataCoverage() {
  const navigate = useNavigate();
  const [st, set] = useViewState(DEFAULTS);
  const [teamQuery, setTeamQuery] = useState("");
  const m = st.missing;

  const cov = useFetch((s) => apiGet("/api/coverage", { gender: st.gender }, s), `cov|${st.gender}`);
  const missing = useFetch((s) => apiGet("/api/coverage/missing", { ...m, limit: 500 }, s), `missing|${JSON.stringify(m)}`);

  const teams = useMemo(() => {
    const t = cov.data?.teams;
    if (!t) return null;
    const q = teamQuery.trim().toLowerCase();
    return q ? { ...t, rows: t.rows.filter((r) => String(r[0]).toLowerCase().includes(q)) } : t;
  }, [cov.data, teamQuery]);
  const compNames = useMemo(() => [...new Set((cov.data?.competitions?.rows || []).map((r) => r[0]))].sort(),
    [cov.data]);
  const setMissing = (patch) => set({ missing: { ...m, ...patch } });
  const w = cov.data?.withheld;

  useCopilotContext({
    view: "data coverage", settings: st,
    visible: { by_format: summarize(cov.data, 12), withheld: w, missing: summarize(missing.data, 10) },
  });

  return (
    <div className="view method">
      <header className="method-head">
        <p className="method-kicker">The data</p>
        <h1>Data coverage</h1>
        <p className="method-dek">
          What Doosra's ball-by-ball data covers, and what it doesn't. Everything comes
          from <a href="https://cricsheet.org" target="_blank" rel="noreferrer">Cricsheet</a>, which publishes the periods it
          covers and every match it knows it's missing; both are refreshed with each weekly data build. Where a
          player's totals fall short of the official record, this is usually why.
        </p>
      </header>

      {cov.loading && <Loading label="Checking the archive…" />}
      <ErrorNote error={cov.error} />

      {w?.matches && (
        <div className="coverage-callout" role="note">
          <strong>{w.matches.toLocaleString("en-US")} matches are withheld.</strong> {w.reason}{" "}
          Nothing involving Afghanistan is in the data -- not Afghanistan's internationals, and not their opponents'
          matches against them. <a href={w.article} target="_blank" rel="noreferrer">Cricsheet's explanation</a>.
        </div>
      )}

      {cov.data && (
        <Panel title="By format" subtitle="Matches in Doosra, when Cricsheet's data starts, and how much of each format since then is missing."
          actions={<Segmented label="Gender" value={st.gender} options={[["", "All"], ["male", "Men"], ["female", "Women"]]}
            onChange={(v) => set({ gender: v })} />}
          explain={{ data: summarize(cov.data, 12), question: "Summarise how complete this data is by format. Where are the biggest gaps?" }}>
          <DataTable table={{ ...cov.data, notes: undefined }} labels={cov.data.labels} maxHeight={420} />
          <ul className="coverage-notes">{(cov.data.notes || []).map((n) => <li key={n}>{n}</li>)}</ul>
        </Panel>
      )}

      {cov.data && (
        <div className="grid-2">
          <Panel title="By competition" subtitle="Cricsheet's figures: matches held of matches played. Lowest coverage first.">
            <DataTable table={cov.data.competitions} maxHeight={420}
              onRowClick={(row) => setMissing({ competition: row[0], format: "", gender: row[1] || "" })} />
          </Panel>
          <Panel title="By team" subtitle="Across every format Cricsheet covers for the team."
            actions={<input className="compact-input" value={teamQuery} placeholder="Find a team…" aria-label="Find a team"
              onChange={(e) => setTeamQuery(e.target.value)} />}>
            {teams && <DataTable table={teams} maxHeight={420}
              onRowClick={(row) => setMissing({ team: row[0], competition: "" })} />}
          </Panel>
        </div>
      )}

      <Panel title="Missing matches" subtitle={missing.data?.notes?.[0] || "Matches Cricsheet has no ball-by-ball data for (Tests, ODIs and the competitions it covers)."}
        explain={{ data: summarize(missing.data, 25), question: "What stands out about these missing matches?" }}>
        <div className="filter-bar" role="group" aria-label="Missing-match filters">
          <label className="filter-field">
            <span>Format</span>
            <select value={m.format} onChange={(e) => setMissing({ format: e.target.value, competition: "" })}>
              <option value="">any</option><option value="Test">Test</option><option value="ODI">ODI</option>
            </select>
          </label>
          <label className="filter-field">
            <span>Competition</span>
            <CommitInput value={m.competition} placeholder="any" options={compNames} ariaLabel="Competition"
              onCommit={(v) => setMissing({ competition: v || "", format: "" })} />
          </label>
          <label className="filter-field">
            <span>Team</span>
            <CommitInput value={m.team} placeholder="any" ariaLabel="Team" onCommit={(v) => setMissing({ team: v || "" })} />
          </label>
          <label className="filter-field">
            <span>Gender</span>
            <select value={m.gender} onChange={(e) => setMissing({ gender: e.target.value })}>
              <option value="">any</option><option value="male">male</option><option value="female">female</option>
            </select>
          </label>
          {(m.format || m.competition || m.team || m.gender) && (
            <button type="button" className="ghost-btn" onClick={() => set({ missing: DEFAULTS.missing })}>Clear</button>
          )}
        </div>
        {missing.loading ? <Loading /> : <DataTable table={missing.data && { ...missing.data, notes: undefined }} maxHeight={460}
          labels={["Date", "Team", "Opponent", "Format / competition", "Gender"]} />}
        <ErrorNote error={missing.error} />
        <p className="table-notes-inline">
          Source: <a href="https://cricsheet.org/missing/" target="_blank" rel="noreferrer">cricsheet.org/missing</a> and{" "}
          <a href="https://cricsheet.org/coverage/" target="_blank" rel="noreferrer">cricsheet.org/coverage</a>. Know where
          the data for a missing match is? Cricsheet would like to hear from you. Click a competition or team above to
          filter this list.
        </p>
      </Panel>

      <p className="table-notes-inline">
        Looking for a player? Their Player Hub page flags matches their teams played during their career that aren't in
        the data. <button type="button" className="link-btn" onClick={() => navigate("/players")}>Open the Player Hub</button>
      </p>
    </div>
  );
}
