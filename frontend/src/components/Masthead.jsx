import { useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { API_BASE } from "../api.js";
import { useDesktop } from "../desktop/DesktopProvider.jsx";
import UserMenu from "./UserMenu.jsx";
import Wordmark from "./Wordmark.jsx";

function formatCount(n) {
  if (n == null) return "—";
  return n.toLocaleString("en-US");
}

const NAV = [
  ["/players", "Players"],
  ["/compare", "Compare"],
  ["/query", "Query"],
  ["/matrix", "Matrix"],
  ["/matches", "Matches"],
  ["/methodology/fibs", "FIBS"],
  ["/data", "Data"],
  ["/ask", "Ask"],
];

export default function Masthead() {
  const { desktop, update } = useDesktop();
  const [stats, setStats] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE}/stats`)
      .then((r) => r.json())
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  return (
    <header className="masthead">
      <Link to="/" className="masthead-title">
        <Wordmark />
        <span className="masthead-sub">cricket analytics</span>
      </Link>
      <nav className="masthead-nav" aria-label="Main">
        {NAV.map(([to, label]) => (
          <NavLink key={to} to={to} className={({ isActive }) => (isActive ? "active" : undefined)}>
            {label}
          </NavLink>
        ))}
      </nav>
      <dl className="masthead-ticker">
        <div className="ticker-item">
          <dt>matches</dt>
          <dd>{formatCount(stats?.matches)}</dd>
        </div>
        <div className="ticker-item">
          <dt>deliveries</dt>
          <dd>{formatCount(stats?.deliveries)}</dd>
        </div>
      </dl>
      <UserMenu />
      {desktop && (
        <NavLink to="/settings" className="masthead-settings" aria-label={update?.available ? "Settings: an update is available" : "Settings"}
          title={update?.available ? `Doosra ${update.latest.version} is available` : "Settings"}>
          ⚙{update?.available && <span className="masthead-dot" aria-hidden="true" />}
        </NavLink>
      )}
    </header>
  );
}
