import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { API_BASE } from "../api.js";

// Data attribution (required by Cricsheet's ODC-By licence) and how fresh
// the data is.
export default function Footer() {
  const [data, setData] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE}/stats`)
      .then((r) => r.json())
      .then((s) => setData(s.data || null))
      .catch(() => setData(null));
  }, []);

  return (
    <footer className="app-footer">
      <span>
        Data: <a href="https://cricsheet.org" target="_blank" rel="noreferrer">Cricsheet</a>, under the{" "}
        <a href="https://opendatacommons.org/licenses/by/1-0/" target="_blank" rel="noreferrer">ODC-By 1.0</a> licence
        {" · "}<Link to="/data">coverage</Link>
      </span>
      {data?.latest_match && (
        <span className="footer-build">
          Matches up to {data.latest_match}
          {data.built_at ? ` · built ${data.built_at.slice(0, 10)}` : ""}
        </span>
      )}
      <span>© 2026 sarthak213. All rights reserved.</span>
    </footer>
  );
}
