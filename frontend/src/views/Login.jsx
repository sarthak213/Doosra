import { useSearchParams } from "react-router-dom";
import { API_BASE, apiGet } from "../api.js";
import Wordmark from "../components/Wordmark.jsx";
import { useFetch } from "../hooks/useFetch.js";

const MESSAGES = {
  not_invited: "That account hasn't been invited. Ask the owner to add your email, then try again.",
  failed: "Sign-in didn't complete. Please try again.",
};

// Shown to anyone without a session on a hosted instance. There are no passwords:
// you sign in with Google or GitHub, and only invited emails are let in.
export default function Login() {
  const [params] = useSearchParams();
  const { data, loading } = useFetch(() => apiGet("/auth/providers"), "providers");
  const error = MESSAGES[params.get("error")];
  return (
    <main className="login">
      <div className="login-card">
        <Wordmark />
        <h1>Sign in to Doosra</h1>
        <p className="muted">Doosra is invite-only. Use the Google or GitHub account whose email was invited.</p>
        {error && (
          <p className="resolution-note" role="alert">
            {error}
          </p>
        )}
        <div className="login-buttons">
          {(data?.providers || []).map((p) => (
            <a key={p.id} className="primary-btn" href={`${API_BASE}/auth/login/${p.id}`}>
              Continue with {p.label}
            </a>
          ))}
        </div>
        {!loading && data && data.providers.length === 0 && (
          <p className="empty-note">No sign-in provider is configured on this server.</p>
        )}
        <p className="note-meta">
          Your chats, notes and boards are private to your account. Questions you ask are sent to the language-model
          provider this server is set up with. Cricket data: Cricsheet, ODC-By 1.0.
        </p>
      </div>
    </main>
  );
}
