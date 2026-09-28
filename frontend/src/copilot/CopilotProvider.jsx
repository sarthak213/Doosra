import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAgentQuery } from "../hooks/useAgentQuery.js";
import { stateUrl } from "../hooks/useViewState.js";

const CopilotContext = createContext(null);

// Where an open_in_app action goes: /<view>?state=..., or the player's hub.
export function urlForAction(event) {
  const routes = { query: "/query", matrix: "/matrix", compare: "/compare" };
  if (event.view === "player") {
    const { player, ...rest } = event.state || {};
    return stateUrl(`/players/${encodeURIComponent(player || "")}`, rest);
  }
  return routes[event.view] ? stateUrl(routes[event.view], event.state || {}) : null;
}

// The copilot is one conversation shared by every view. Views register what
// the user is looking at (view name, settings, visible data) so questions and
// "Explain" clicks are answered in context; the agent's open_in_app actions
// navigate the app.
export function CopilotProvider({ children }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const viewContext = useRef(null);

  const onUiAction = useCallback(
    (event) => {
      const url = urlForAction(event);
      if (url) navigate(url);
    },
    [navigate]
  );

  const agent = useAgentQuery({ onUiAction });

  const ask = useCallback(
    (question) => {
      setOpen(true);
      agent.ask(question, viewContext.current);
    },
    [agent]
  );

  const explain = useCallback(
    (question, data, panel) => {
      setOpen(true);
      agent.ask(question, { ...(viewContext.current || {}), focus: { panel, data } });
    },
    [agent]
  );

  const setViewContext = useCallback((ctx) => {
    viewContext.current = ctx;
  }, []);

  const value = useMemo(() => ({ open, setOpen, ask, explain, setViewContext, agent }),
    [open, ask, explain, setViewContext, agent]);
  return <CopilotContext.Provider value={value}>{children}</CopilotContext.Provider>;
}

export function useCopilot() {
  return useContext(CopilotContext);
}

// Views call this with what's on screen; kept current as it changes.
export function useCopilotContext(ctx) {
  const copilot = useCopilot();
  const key = JSON.stringify(ctx);
  useEffect(() => {
    copilot?.setViewContext(ctx);
    // key captures ctx
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, copilot]);
}
