import { useEffect, useState } from "react";

// Runs `load(signal)` whenever `key` changes, cancelling the previous request.
// Returns {data, error, loading}.
export function useFetch(load, key) {
  const [res, setRes] = useState({ data: null, error: null, loading: true });

  useEffect(() => {
    if (!load) {
      setRes({ data: null, error: null, loading: false });
      return undefined;
    }
    const ctrl = new AbortController();
    setRes((r) => ({ ...r, loading: true, error: null }));
    load(ctrl.signal)
      .then((data) => setRes({ data, error: null, loading: false }))
      .catch((error) => {
        if (error.name !== "AbortError") setRes({ data: null, error, loading: false });
      });
    return () => ctrl.abort();
    // `key` captures everything load depends on
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return res;
}
