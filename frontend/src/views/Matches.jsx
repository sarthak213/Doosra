import { Link, useNavigate } from "react-router-dom";
import { apiGet } from "../api.js";
import { useCopilotContext } from "../copilot/CopilotProvider.jsx";
import { FilterBar } from "../components/kit/Inputs.jsx";
import Panel, { ErrorNote, Loading } from "../components/kit/Panel.jsx";
import { useFetch } from "../hooks/useFetch.js";
import { useViewState } from "../hooks/useViewState.js";
import { stage } from "./MatchReplay.jsx";

const DEFAULTS = { filters: { competition: "Indian Premier League", season: "2024" } };
const SHOWN = ["competition", "format", "gender", "team", "opposition", "venue", "season", "from_year", "to_year"];

// Finals worth replaying first.
const FEATURED = [
  ["1415755", "2024 T20 World Cup final", "India v South Africa"],
  ["1384439", "2023 ODI World Cup final", "India v Australia"],
  ["1512773", "2026 T20 World Cup final", "India v New Zealand"],
  ["1466428", "2025 Champions Trophy final", "New Zealand v India"],
  ["1535465", "IPL 2026 final", "Gujarat Titans v RCB"],
  ["1181768", "IPL 2019 final", "Mumbai Indians v CSK, won by 1 run"],
];

// Pick a limited-overs match to replay ball by ball with the win-probability model.
export default function Matches() {
  const navigate = useNavigate();
  const [st, set] = useViewState(DEFAULTS);
  const res = useFetch((s) => apiGet("/api/matches", st.filters, s), JSON.stringify(st.filters));
  const matches = res.data?.matches || [];
  useCopilotContext({ view: "matches", filters: st.filters, visible: { matches_listed: matches.length } });

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>Match Replay</h1>
          <p className="muted">
            Any T20 or ODI, ball by ball: how each side's chance of winning moved, and the moments that swung it.
            The chances come from Doosra's win-probability model, trained on earlier matches only.
          </p>
        </div>
      </div>

      <div className="featured-matches" role="list" aria-label="Famous finals">
        {FEATURED.map(([id, label, teams]) => (
          <Link key={id} to={`/matches/${id}`} className="featured-match" role="listitem">
            <strong>{label}</strong>
            <span className="note-meta">{teams}</span>
          </Link>
        ))}
      </div>

      <FilterBar filters={st.filters} onChange={(f) => set({ filters: f })} show={SHOWN} />

      <Panel title="Matches" subtitle={res.data ? `${matches.length}${res.data.more ? "+" : ""} matches, newest first` : undefined}>
        {res.loading && <Loading label="Finding matches…" />}
        {res.error && <ErrorNote error={res.error} />}
        {res.data && matches.length === 0 && <p className="empty-note">No T20 or ODI matches in these filters.</p>}
        {matches.length > 0 && (
          <div className="table-view"><div className="table-scroll" style={{ maxHeight: 640 }}>
          <table className="match-list">
            <thead>
              <tr><th>Date</th><th>Match</th><th>Competition</th><th>Result</th><th>Ground</th></tr>
            </thead>
            <tbody>
              {matches.map((m) => (
                <tr key={m.match_id} className="clickable" tabIndex={0}
                  onClick={() => navigate(`/matches/${m.match_id}`)}
                  onKeyDown={(e) => e.key === "Enter" && navigate(`/matches/${m.match_id}`)}>
                  <td className="num">{String(m.date).slice(0, 10)}</td>
                  <td>{m.team1} v {m.team2}</td>
                  <td>{m.event_name || m.match_type}{stage(m.match_number)}</td>
                  <td>{m.summary}</td>
                  <td>{m.venue}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div></div>
        )}
      </Panel>
    </div>
  );
}
