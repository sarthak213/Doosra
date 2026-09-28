import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

// A view's settings live in the URL (?state=<json>) so every view is a
// permalink: shareable, bookmarkable, and settable by the copilot's
// open_in_app action.
export function useViewState(defaults) {
  const [params, setParams] = useSearchParams();
  const raw = params.get("state");

  const state = useMemo(() => {
    if (!raw) return defaults;
    try {
      return { ...defaults, ...JSON.parse(raw) };
    } catch {
      return defaults;
    }
    // defaults is a module-level constant in each view
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [raw]);

  const update = useCallback(
    (patch) => {
      const next = typeof patch === "function" ? patch(state) : { ...state, ...patch };
      setParams({ state: JSON.stringify(next) }, { replace: true });
    },
    [state, setParams]
  );

  return [state, update];
}

export function stateUrl(route, state) {
  return `${route}?state=${encodeURIComponent(JSON.stringify(state))}`;
}
