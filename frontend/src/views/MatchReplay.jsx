import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiGet } from "../api.js";
import { useCopilotContext } from "../copilot/CopilotProvider.jsx";
import { WinProbChart } from "../components/kit/Charts.jsx";
import DataTable from "../components/kit/DataTable.jsx";
import Panel, { ErrorNote, Loading } from "../components/kit/Panel.jsx";
import { useFetch } from "../hooks/useFetch.js";

const pct = (p) => `${Math.round(p * 100)}%`;
// Cricsheet's match number is a number for league games and a stage name for knockouts ("Final").
export const stage = (n) => (!n ? "" : /^\d+$/.test(String(n)) ? `, match ${n}` : ` ${n}`);

// One match, ball by ball: the win-probability worm, the moments that swung it, and the scorecard.
export default function MatchReplay() {
  const { matchId } = useParams();
  const res = useFetch((s) => apiGet(`/api/matches/${encodeURIComponent(matchId)}/replay`, undefined, s), matchId);
  const [active, setActive] = useState(null);
  const r = res.data;
  const m = r?.match;

  const byOver = [];
  if (r) {
    const last = new Map();
    r.balls.forEach((b) => last.set(`${b.innings}-${b.over}`, b));
    last.forEach((b) => byOver.push({ innings: b.innings, over: b.over + 1, [m.team1]: Math.round(b.wp * 100) }));
  }
  const story = r && {
    match: `${m.team1} v ${m.team2}, ${m.event_name || m.match_type}, ${String(m.date).slice(0, 10)}`,
    result: m.summary, innings: r.innings,
    key_moments: r.moments.map((k) => ({ moment: k.text, score: k.score, helped: k.team,
      chance_before: pct(k.before), chance_after: pct(k.after) })),
    [`${m.team1}_win_chance_by_over`]: byOver,
  };
  useCopilotContext({ view: "match replay", visible: story || { loading: true } });

  if (res.loading) return <div className="view"><Loading label="Replaying the match…" /></div>;
  if (res.error) return <div className="view"><ErrorNote error={res.error} /></div>;

  const overs = m.overs_per_innings || (m.match_type === "ODI" || m.match_type === "ODM" ? 50 : 20);
  const card = (inn, key) => {
    const rows = r[key].filter((x) => x.innings === inn);
    return key === "batting"
      ? { title: "Batting", columns: ["player", "runs", "balls", "fours", "sixes", "out"],
          rows: rows.map((x) => [x.player, x.runs, x.balls, x.fours, x.sixes, x.out ? x.dismissal : "not out"]) }
      : { title: "Bowling", columns: ["player", "overs", "runs", "wickets", "dots"],
          rows: rows.map((x) => [x.player, `${Math.floor(x.balls / 6)}.${x.balls % 6}`, x.runs, x.wickets, x.dots]) };
  };

  return (
    <div className="view replay-view">
      <div className="view-head">
        <div>
          <p className="note-meta"><Link to="/matches">← Matches</Link></p>
          <h1>{m.team1} v {m.team2}</h1>
          <p className="muted">
            {m.event_name || m.match_type}{stage(m.match_number)} · {String(m.date).slice(0, 10)} ·{" "}
            {String(m.venue).split(",")[0]}
          </p>
        </div>
      </div>

      <div className="replay-summary">
        {r.innings.map((i) => (
          <div key={i.innings} className="replay-score">
            <span className="note-meta">{i.innings === 1 ? "Batted first" : `Chasing ${i.target}`}</span>
            <strong>{i.team} {i.score}/{i.wickets}</strong>
            <span className="note-meta">{i.overs} overs</span>
          </div>
        ))}
        <div className="replay-score result">
          <span className="note-meta">Result</span>
          <strong>{m.summary}</strong>
          {m.player_of_match && <span className="note-meta">Player of the match: {m.player_of_match}</span>}
        </div>
      </div>
      {r.rain && <p className="resolution-note">Rain-affected (DLS): the chase is measured against the revised target.</p>}
      {r.note && <p className="resolution-note">{r.note}</p>}

      {r.balls.length > 0 && (
        <Panel title="Win probability" subtitle={`${m.team1}'s chance of winning after every ball. Hover for the ball; the numbers are the key moments.`}
          explain={{ question: "Walk me through this match: where was it won and lost, and which moments mattered most?", data: story }}>
          <div className="replay-grid">
            <WinProbChart balls={r.balls} overs={overs} team1={m.team1} team2={m.team2} moments={r.moments}
              activeMoment={active} onMoment={setActive} />
            <ol className="moments">
              {r.moments.map((k, i) => (
                <li key={`${k.innings}-${k.seq}`} className={active === i + 1 ? "on" : undefined}>
                  <button type="button" onClick={() => setActive(active === i + 1 ? null : i + 1)}>
                    <span className="moment-no">{i + 1}</span>
                    <span className="grow">
                      <strong>{k.text}</strong>
                      <span className="note-meta">
                        {k.batting_team} {k.score} · {k.team} {pct(k.before)} → {pct(k.after)}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          </div>
          <p className="note-meta">
            From Doosra's win-probability model: trees and a logistic regression trained on {m.match_type === "ODI" || m.match_type === "ODM" ? "ODI" : "T20"} matches up to 2022,
            using the score, wickets, balls left, target, the ground's par and each side's Elo rating. Tested on 2025 onwards.
          </p>
        </Panel>
      )}

      <div className="replay-cards">
        {r.innings.map((i) => (
          <Panel key={i.innings} title={`${i.team} ${i.score}/${i.wickets}`} subtitle={`${i.overs} overs`}>
            <DataTable table={card(i.innings, "batting")} compact maxHeight={420} />
            <DataTable table={card(i.innings, "bowling")} compact maxHeight={320} />
          </Panel>
        ))}
      </div>
    </div>
  );
}
