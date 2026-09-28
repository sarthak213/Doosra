import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE } from "../api.js";

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

function loadPersistedTurns(storageKey) {
  if (!storageKey) return [];
  try {
    const parsed = JSON.parse(localStorage.getItem(storageKey) || "[]");
    return Array.isArray(parsed) ? parsed.map((t) => ({ ...t, isStreaming: false })) : [];
  } catch {
    return [];
  }
}

// Parses a text/event-stream body: calls onEvent(eventName, data) per event.
async function readSse(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let name = "message";
      const data = [];
      chunk.split("\n").forEach((line) => {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      });
      onEvent(name, data.join("\n"));
    }
  }
}

// storageKey: persist turns in localStorage (the Ask page does; the copilot
// drawer keeps a session-only history). onUiAction: called for the agent's
// open_in_app actions.
export function useAgentQuery({ storageKey = null, onUiAction } = {}) {
  const [turns, setTurns] = useState(() => loadPersistedTurns(storageKey));
  const abortRef = useRef(null);
  const requestIdRef = useRef(null);
  const turnsRef = useRef(turns);
  const uiActionRef = useRef(onUiAction);

  useEffect(() => {
    turnsRef.current = turns;
    if (!storageKey) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify(turns.slice(-20)));
    } catch {
      /* quota/private mode */
    }
  }, [turns, storageKey]);

  useEffect(() => {
    uiActionRef.current = onUiAction;
  }, [onUiAction]);

  const updateLastTurn = useCallback((updater) => {
    setTurns((prev) => {
      if (prev.length === 0) return prev;
      const next = [...prev];
      next[next.length - 1] = updater(next[next.length - 1]);
      return next;
    });
  }, []);

  const handleEvent = useCallback(
    (event) => {
      if (event.type === "final_answer") {
        updateLastTurn((t) => ({
          ...t,
          finalAnswer: event.content,
          chartData: event.chart_data || null,
          tableData: event.table_data || null,
        }));
      } else if (event.type === "chart") {
        updateLastTurn((t) => ({ ...t, charts: [...t.charts, event.chart_data] }));
      } else if (event.type === "table") {
        // Tables arrive the moment a tool returns: the numbers shown come
        // straight from the stats engine, not from the model's paraphrase.
        updateLastTurn((t) => ({ ...t, tables: [...(t.tables || []), { id: event.table_id, ...event.table_data }] }));
      } else if (event.type === "ui_action") {
        uiActionRef.current?.(event);
        updateLastTurn((t) => ({ ...t, steps: [...t.steps, event] }));
      } else if (event.type === "error") {
        updateLastTurn((t) => ({ ...t, error: event.content }));
      } else if (event.type === "thought" && !event.content?.trim()) {
        // skip empty thoughts some local models emit alongside tool calls
      } else {
        updateLastTurn((t) => ({ ...t, steps: [...t.steps, event] }));
      }
    },
    [updateLastTurn]
  );

  const ask = useCallback(
    async (question, context = null) => {
      const trimmed = question.trim();
      if (!trimmed) return;
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const requestId = crypto.randomUUID();
      requestIdRef.current = requestId;

      // Only the last few turns: enough for follow-ups, without bloating context.
      const history = turnsRef.current.slice(-4).flatMap((t) => [
        { role: "user", content: t.question },
        ...(t.finalAnswer ? [{ role: "assistant", content: t.finalAnswer }] : []),
      ]);
      setTurns((prev) => [...prev, makeTurn(trimmed)]);

      try {
        const res = await fetch(`${API_BASE}/api/chat/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: trimmed, history, request_id: requestId, context }),
          signal: ctrl.signal,
        });
        if (!res.ok || !res.body) throw new Error(`Server error (${res.status})`);
        await readSse(res.body, (name, data) => {
          if (name === "done") return;
          try {
            handleEvent(JSON.parse(data));
          } catch {
            /* ignore malformed event */
          }
        });
        updateLastTurn((t) => ({ ...t, isStreaming: false }));
      } catch (err) {
        if (err.name === "AbortError") return;
        updateLastTurn((t) => ({ ...t, isStreaming: false, error: t.error || "Lost connection to the agent server." }));
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null;
      }
    },
    [handleEvent, updateLastTurn]
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (requestIdRef.current) {
      fetch(`${API_BASE}/query/cancel/${requestIdRef.current}`, { method: "POST" }).catch(() => {});
    }
    updateLastTurn((t) => ({ ...t, isStreaming: false }));
  }, [updateLastTurn]);

  const clearHistory = useCallback(() => setTurns([]), []);
  const isStreaming = turns.length > 0 && turns[turns.length - 1].isStreaming;

  return { turns, ask, cancel, clearHistory, isStreaming };
}
