import { useCallback, useEffect, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const STORAGE_KEY = "cricket-agent-turns";

// A "turn" is one question + everything the agent produced answering it.
// steps: the raw reasoning events (thought/tool_call/tool_result/self_correction/error)
// in the order they arrived, rendered inside the collapsible ReasoningTrace.
function makeTurn(question) {
  return {
    id: crypto.randomUUID(),
    question,
    steps: [],
    charts: [],
    tables: [],
    finalAnswer: null,
    chartData: null,
    tableData: null,
    error: null,
    isStreaming: true,
  };
}

function loadPersistedTurns() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map((t) => ({ ...t, isStreaming: false })) : [];
  } catch {
    return [];
  }
}

export function useAgentQuery() {
  const [turns, setTurns] = useState(loadPersistedTurns);
  const esRef = useRef(null);
  const requestIdRef = useRef(null);
  const turnsRef = useRef(turns);

  useEffect(() => {
    turnsRef.current = turns;
  }, [turns]);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(turns.slice(-20)));
    } catch {
      /* quota/private mode — fail silently */
    }
  }, [turns]);

  const updateLastTurn = useCallback((updater) => {
    setTurns((prev) => {
      if (prev.length === 0) return prev;
      const next = [...prev];
      next[next.length - 1] = updater(next[next.length - 1]);
      return next;
    });
  }, []);

  const ask = useCallback(
    (question) => {
      const trimmed = question.trim();
      if (!trimmed) return;

      // Close any in-flight stream before starting a new one.
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }

      const requestId = crypto.randomUUID();
      requestIdRef.current = requestId;

      // Only the last few turns: enough for follow-ups ("what about in
      // Tests?"), without bloating the URL or the model's context.
      const history = turnsRef.current.slice(-4).flatMap((t) => [
        { role: "user", content: t.question },
        ...(t.finalAnswer ? [{ role: "assistant", content: t.finalAnswer }] : []),
      ]);

      setTurns((prev) => [...prev, makeTurn(trimmed)]);

      const url = `${API_BASE}/query/stream?q=${encodeURIComponent(trimmed)}&history=${encodeURIComponent(JSON.stringify(history))}&request_id=${requestId}`;
      const es = new EventSource(url);
      esRef.current = es;

      es.onmessage = (evt) => {
        let event;
        try {
          event = JSON.parse(evt.data);
        } catch {
          return;
        }

        if (event.type === "final_answer") {
          updateLastTurn((t) => ({
            ...t,
            finalAnswer: event.content,
            chartData: event.chart_data || null,
            tableData: event.table_data || null,
          }));
        } else if (event.type === "chart") {
          // Charts render inline as soon as the agent calls plot_chart --
          // mid-reasoning, not just tacked onto the final answer. A turn
          // can have multiple (one per sub-comparison), rendered in order.
          updateLastTurn((t) => ({ ...t, charts: [...t.charts, event.chart_data] }));
        } else if (event.type === "table") {
          // Tool results with rows arrive as tables the moment they're
          // computed -- the numbers the user sees come straight from the
          // stats engine, not from the model's paraphrase.
          updateLastTurn((t) => ({
            ...t,
            tables: [...(t.tables || []), { id: event.table_id, ...event.table_data }],
          }));
        } else if (event.type === "error") {
          updateLastTurn((t) => ({ ...t, error: event.content }));
        } else if (event.type === "thought" && !event.content?.trim()) {
          // Skip empty/whitespace-only thought events some local models emit
          // alongside tool calls -- nothing worth showing in the trace.
        } else {
          updateLastTurn((t) => ({ ...t, steps: [...t.steps, event] }));
        }
      };

      es.addEventListener("done", () => {
        es.close();
        esRef.current = null;
        updateLastTurn((t) => ({ ...t, isStreaming: false }));
      });

      es.onerror = () => {
        es.close();
        esRef.current = null;
        updateLastTurn((t) =>
          t.isStreaming
            ? {
                ...t,
                isStreaming: false,
                error: t.error || "Lost connection to the agent server.",
              }
            : t
        );
      };
    },
    [updateLastTurn]
  );

  const cancel = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    if (requestIdRef.current) {
      fetch(`${API_BASE}/query/cancel/${requestIdRef.current}`, { method: "POST" }).catch(() => {});
    }
    updateLastTurn((t) => ({ ...t, isStreaming: false }));
  }, [updateLastTurn]);

  const clearHistory = useCallback(() => setTurns([]), []);

  const isStreaming = turns.length > 0 && turns[turns.length - 1].isStreaming;

  return { turns, ask, cancel, clearHistory, isStreaming };
}
