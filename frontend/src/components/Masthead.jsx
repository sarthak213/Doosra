import { useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function formatCount(n) {
  if (n == null) return "—";
  return n.toLocaleString("en-US");
}

export default function Masthead() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE}/stats`)
      .then((r) => r.json())
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  return (
    <header className="masthead">
      <div className="masthead-title">
        <span className="masthead-mark">Doosra</span>
        <span className="masthead-sub">a cricket analytics agent</span>
      </div>
      <dl className="masthead-ticker">
        <div className="ticker-item">
          <dt>matches</dt>
          <dd>{formatCount(stats?.matches)}</dd>
        </div>
        <div className="ticker-item">
          <dt>deliveries</dt>
          <dd>{formatCount(stats?.deliveries)}</dd>
        </div>
        <div className="ticker-item">
          <dt>tournaments</dt>
          <dd>{formatCount(stats?.tournaments)}</dd>
        </div>
      </dl>
    </header>
  );
}
