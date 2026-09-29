import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { apiGet } from "../api.js";

const DesktopContext = createContext({ loading: false, desktop: false, status: null, refresh: () => {} });
export const useDesktop = () => useContext(DesktopContext);

// Whether this is the installed desktop app, and its setup state (engine, model, data). In a
// browser against a normal server /api/desktop/status just says {desktop: false}.
export function DesktopProvider({ children }) {
  const [state, setState] = useState({ loading: true, desktop: false, status: null });
  const refresh = useCallback(
    () =>
      apiGet("/api/desktop/status")
        .then((s) => setState({ loading: false, desktop: !!s.desktop, status: s.desktop ? s : null }))
        .catch(() => setState({ loading: false, desktop: false, status: null })),
    []
  );
  useEffect(() => { refresh(); }, [refresh]);
  return <DesktopContext.Provider value={{ ...state, refresh }}>{children}</DesktopContext.Provider>;
}

export const formatBytes = (n) => {
  if (n == null) return "";
  if (n >= 1e9) return `${(n / 1e9).toFixed(n >= 1e10 ? 0 : 1)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  return `${Math.round(n / 1e3)} KB`;
};
