import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { API_BASE, UNAUTHORIZED, apiGet } from "../api.js";
import { CHATS_CHANGED } from "../hooks/useAgentQuery.js";

const AuthContext = createContext({ loading: false, mode: "none", user: null, quota: null });
export const useAuth = () => useContext(AuthContext);

// Who is signed in. Running locally (mode "none") there is nothing to sign in to;
// hosted, the app shows the login page until /api/me returns a user.
export function AuthProvider({ children }) {
  const [state, setState] = useState({ loading: true, mode: "none", user: null, quota: null });

  const refresh = useCallback(
    () =>
      apiGet("/api/me")
        .then((me) => setState({ loading: false, mode: me.mode, user: me.user, quota: me.quota || null }))
        .catch(() => setState((s) => ({ ...s, loading: false }))),
    []
  );

  useEffect(() => {
    refresh();
    const signedOut = () => setState((s) => (s.mode === "none" ? s : { ...s, user: null, quota: null }));
    window.addEventListener(UNAUTHORIZED, signedOut);
    window.addEventListener(CHATS_CHANGED, refresh); // an answer just used some of today's allowance
    return () => {
      window.removeEventListener(UNAUTHORIZED, signedOut);
      window.removeEventListener(CHATS_CHANGED, refresh);
    };
  }, [refresh]);

  const signOut = useCallback(
    () =>
      fetch(`${API_BASE}/auth/logout`, { method: "POST", credentials: "include" }).finally(() =>
        setState((s) => ({ ...s, user: null, quota: null }))
      ),
    []
  );

  return <AuthContext.Provider value={{ ...state, refresh, signOut }}>{children}</AuthContext.Provider>;
}
