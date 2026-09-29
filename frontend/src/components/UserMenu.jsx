import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider.jsx";

// Hosted only: who you are, today's AI allowance, invites (admins) and sign out.
export default function UserMenu() {
  const { mode, user, quota, signOut } = useAuth();
  if (mode === "none" || !user) return null;
  const left = quota?.limit ? Math.max(quota.limit - quota.used, 0) : null;
  return (
    <details className="user-menu">
      <summary aria-label="Account menu">{user.name || user.email}</summary>
      <div className="user-menu-panel">
        <p className="note-meta">{user.email}</p>
        {quota?.limit > 0 && (
          <p className="note-meta">
            {left} of {quota.limit} AI questions left today
          </p>
        )}
        {user.role === "admin" && <Link to="/admin/invites">Invites</Link>}
        <button type="button" className="ghost-btn" onClick={signOut}>Sign out</button>
      </div>
    </details>
  );
}
