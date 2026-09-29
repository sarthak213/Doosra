import { useState } from "react";
import { apiGet, apiSend } from "../api.js";
import { useAuth } from "../auth/AuthProvider.jsx";
import Panel, { ErrorNote, Loading } from "../components/kit/Panel.jsx";
import { useFetch } from "../hooks/useFetch.js";

// Who may sign in. Admins are the emails in the server's ADMIN_EMAILS.
export default function Invites() {
  const { user } = useAuth();
  const [tick, setTick] = useState(0);
  const [email, setEmail] = useState("");
  const [error, setError] = useState(null);
  const { data, error: loadError, loading } = useFetch(() => apiGet("/api/admin/invites"), `invites${tick}`);
  const run = (fn) =>
    fn()
      .then(() => {
        setTick((t) => t + 1);
        setError(null);
      })
      .catch((e) => setError(e.message));

  if (user && user.role !== "admin") {
    return (
      <div className="view">
        <p className="empty-note">Only admins can manage invites.</p>
      </div>
    );
  }
  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>Invites</h1>
          <p className="muted">Only these emails can sign in. Revoking one signs that person out immediately.</p>
        </div>
      </div>
      <Panel title="Invite someone">
        <form
          className="controls-row"
          onSubmit={(e) => {
            e.preventDefault();
            if (email.trim()) run(() => apiSend("/api/admin/invites", { email: email.trim() })).then(() => setEmail(""));
          }}
        >
          <label className="filter-field grow">
            <span>Email</span>
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" />
          </label>
          <button type="submit" className="primary-btn">Invite</button>
        </form>
        {error && (
          <p className="resolution-note" role="alert">
            {error}
          </p>
        )}
      </Panel>
      <Panel title="Who can sign in">
        {loading && !data && <Loading />}
        <ErrorNote error={loadError} />
        <ul className="notes">
          {(data?.admins || []).map((a) => (
            <li className="note" key={`admin-${a}`}>
              <div className="note-head">
                <span className="note-title grow">{a}</span>
                <span className="note-meta">admin (server setting)</span>
              </div>
            </li>
          ))}
          {(data?.invites || []).map((i) => (
            <li className="note" key={i.email}>
              <div className="note-head">
                <span className="note-title grow">{i.email}</span>
                <span className="note-meta">
                  {i.accepted ? "has signed in" : "not yet"} · invited {i.created.slice(0, 10)}
                </span>
                <button
                  type="button"
                  className="ghost-btn danger"
                  onClick={() =>
                    window.confirm(`Revoke ${i.email}?`) &&
                    run(() => apiSend(`/api/admin/invites/${encodeURIComponent(i.email)}`, undefined, "DELETE"))
                  }
                >
                  Revoke
                </button>
              </div>
            </li>
          ))}
        </ul>
        {data && !data.invites.length && !data.admins.length && <p className="empty-note">No invites yet.</p>}
      </Panel>
    </div>
  );
}
