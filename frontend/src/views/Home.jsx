import { Link, useNavigate } from "react-router-dom";
import { PlayerPicker } from "../components/kit/Inputs.jsx";
import { useCopilot, useCopilotContext } from "../copilot/CopilotProvider.jsx";
import { stateUrl } from "../hooks/useViewState.js";

const MODULES = [
  { to: "/players", title: "Player Hub", body: "Career, form, context-adjusted numbers, entry points and lookalikes." },
  { to: "/compare", title: "Comparison Studio", body: "Up to four players: side by side, percentiles, careers aligned by innings." },
  { to: "/query", title: "Query Builder", body: "Any metric, any scope. Leaderboards, splits, saved queries, CSV." },
  { to: "/matrix", title: "Player Matrix", body: "Every qualified player on two metrics. Find who's both X and Y." },
];

const RECIPES = [
  ["Best death bowlers in the IPL since 2022", "/query", { role: "bowling", metrics: ["balls", "economy", "true_economy", "dot_pct", "wickets"], sort_by: "economy", min_balls: 300, filters: { competition: "IPL", phase: "death", from_year: 2022 } }],
  ["Test batting by match factor (min 5,000 balls)", "/query", { metrics: ["innings", "average", "match_factor", "era_factor", "true_average"], sort_by: "match_factor", min_balls: 5000, filters: { format: "Test" } }],
  ["T20I batters: true average vs true strike rate", "/matrix", { role: "batting", x: "true_average", y: "true_sr", filters: { format: "T20I" } }],
  ["Root vs Smith vs Williamson vs Kohli in Tests", "/compare", { players: ["Joe Root", "Steve Smith", "Kane Williamson", "Virat Kohli"], filters: { format: "Test" } }],
];

export default function Home() {
  const navigate = useNavigate();
  const copilot = useCopilot();
  useCopilotContext({ view: "home" });
  return (
    <div className="view home">
      <section className="hero">
        <h1>Cricket analytics, with context.</h1>
        <p>Ball-by-ball data for men's and women's internationals and the major leagues — with true strike rates, match factors and form curves, and a copilot that can explain any of it.</p>
        <PlayerPicker autoFocus onPick={(n) => navigate(`/players/${encodeURIComponent(n)}`)} placeholder="Jump to a player…" />
      </section>
      <section className="module-grid">
        {MODULES.map((m) => (
          <Link key={m.to} to={m.to} className="module-card">
            <h2>{m.title}</h2>
            <p>{m.body}</p>
          </Link>
        ))}
      </section>
      <section className="recipes">
        <h2 className="section-label">Start from a recipe</h2>
        <div className="recipe-list">
          {RECIPES.map(([label, route, state]) => (
            <button type="button" key={label} className="suggestion-chip" onClick={() => navigate(stateUrl(route, state))}>{label}</button>
          ))}
          <button type="button" className="suggestion-chip" onClick={() => copilot.ask("Who has improved the most in T20Is over the last two years?")}>
            Ask: who has improved most in T20Is lately?
          </button>
        </div>
      </section>
    </div>
  );
}
