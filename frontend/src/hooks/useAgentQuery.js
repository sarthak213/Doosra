import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, UNAUTHORIZED, apiGet, apiSend } from "../api.js";

// A "turn" is one question + everything the agent produced answering it.
// steps: the raw reasoning events (thought/tool_call/tool_result/self_correction/error)
// in the order they arrived, rendered inside the collapsible ReasoningTrace.
function makeTurn(question, streaming = true) {
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
    isStreaming: streaming,
  };
}

// One stream event applied to a turn. Live streaming and reopening a saved
// chat both go through this, so a restored answer looks exactly like the live one.
export function applyEvent(t, event) {
  switch (event.type) {
    case "final_answer":
      return { ...t, finalAnswer: event.content, chartData: event.chart_data || null, tableData: event.table_data || null };
    case "chart":
      return { ...t, charts: [...t.charts, event.chart_data] };
    case "table":
      // Tables arrive the moment a tool returns: the numbers shown come
      // straight from the stats engine, not from the model's paraphrase.
      return { ...t, tables: [...(t.tables || []), { id: event.table_id, source: event.source || null, ...event.table_data }] };
    case "error":
      return { ...t, error: event.content };
    case "thought":
      // skip empty thoughts some local models emit alongside tool calls
      return event.content?.trim() ? { ...t, steps: [...t.steps, event] } : t;
    default: // tool_call, tool_result, self_correction, ui_action
      return { ...t, steps: [...t.steps, event] };
  }
}

// Saved messages -> turns: each user message opens a turn, the assistant
// message that follows fills it from its stored events.
export function turnsFromMessages(messages) {
  const turns = [];
  for (const m of messages || []) {
    if (m.role === "user") {
      turns.push({ ...makeTurn(m.content, false), error: "No answer was saved for this question (it was interrupted)." });
    } else if (turns.length) {
      let t = { ...turns[turns.length - 1], error: null };
      (m.events?.events || []).forEach((e) => { t = applyEvent(t, e); });
      const fin = m.events?.final || {};
      turns[turns.length - 1] = {
        ...t,
        finalAnswer: m.content || t.finalAnswer,
        chartData: fin.chart_data || null,
        tableData: fin.table_data || null,
      };
    }
  }
  return turns;
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

export const CHATS_CHANGED = "doosra:chats-changed";
const announceChats = () => window.dispatchEvent(new Event(CHATS_CHANGED));

// saved: turns live on the server (the Ask page). chatId picks the chat; with
// none, the first question creates one (in projectId, if given) and
// onChatCreated(id) lets the page move to its URL. Without `saved` (the
// copilot drawer) the history is session-only and sent with each request.
// onUiAction: called for the agent's open_in_app actions.
export function useAgentQuery({ saved = false, chatId = null, projectId = null, onUiAction, onChatCreated } = {}) {
  const [turns, setTurns] = useState([]);
  const [chat, setChat] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const abortRef = useRef(null);
  const requestIdRef = useRef(null);
  const turnsRef = useRef(turns);
  const uiActionRef = useRef(onUiAction);
  const createdRef = useRef(null);       // a chat this hook just made: its turns are already live

  useEffect(() => { turnsRef.current = turns; }, [turns]);
  useEffect(() => { uiActionRef.current = onUiAction; }, [onUiAction]);

  useEffect(() => {
    if (!saved) return undefined;
    if (createdRef.current && createdRef.current !== chatId) createdRef.current = null;
    if (!chatId) {
      abortRef.current?.abort();
      setTurns([]); setChat(null); setLoadError(null);
      return undefined;
    }
    if (createdRef.current === chatId) return undefined;
    abortRef.current?.abort();
    let cancelled = false;
    let timer = null;
    // A chat reopened while its answer is still being written (the server keeps going after you
    // leave): show it as in progress and check back until the answer is saved.
    const load = (first) =>
      apiGet(`/api/chats/${chatId}`)
        .then((c) => {
          if (cancelled) return;
          const { messages, ...meta } = c;
          setChat(meta);
          const loaded = turnsFromMessages(messages);
          if (meta.answering && loaded.length) {
            loaded[loaded.length - 1] = { ...loaded[loaded.length - 1], error: null, isStreaming: true };
            timer = setTimeout(() => load(false), 3000);
          } else if (!first) {
            announceChats();
          }
          setTurns(loaded);
        })
        .catch((e) => { if (!cancelled && first) { setTurns([]); setChat(null); setLoadError(e.message || "Couldn't open this chat."); } })
        .finally(() => { if (!cancelled && first) setLoading(false); });
    setLoading(true); setLoadError(null);
    load(true);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [saved, chatId]);

  // A rename or move (or the auto-title after a first answer) changes the chat's header.
  useEffect(() => {
    if (!saved || !chatId) return undefined;
    const refreshMeta = () =>
      apiGet(`/api/chats/${chatId}`).then(({ messages, ...meta }) => setChat(meta)).catch(() => {});
    window.addEventListener(CHATS_CHANGED, refreshMeta);
    return () => window.removeEventListener(CHATS_CHANGED, refreshMeta);
  }, [saved, chatId]);

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
      if (event.type === "ui_action") uiActionRef.current?.(event);
      updateLastTurn((t) => applyEvent(t, event));
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

      // Drawer mode: only the last few turns, enough for follow-ups. Saved
      // chats send nothing: the server loads their history.
      const history = saved ? [] : turnsRef.current.slice(-4).flatMap((t) => [
        { role: "user", content: t.question },
        ...(t.finalAnswer ? [{ role: "assistant", content: t.finalAnswer }] : []),
      ]);
      setTurns((prev) => [...prev, makeTurn(trimmed)]);

      try {
        let id = chatId;
        if (saved && !id) {
          const created = await apiSend("/api/chats", { project_id: projectId || null }, "POST", ctrl.signal);
          id = created.id;
          createdRef.current = id;
          setChat(created);
          onChatCreated?.(id);
        }
        const res = await fetch(`${API_BASE}/api/chat/stream`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: trimmed, history, request_id: requestId, context, chat_id: saved ? id : null }),
          signal: ctrl.signal,
        });
        if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED));
        if (!res.ok || !res.body) {
          // e.g. 429 "You've used today's AI questions": show the server's reason, not a generic failure.
          const detail = (await res.json().catch(() => ({}))).detail;
          throw Object.assign(new Error(`Server error (${res.status})`), { detail: typeof detail === "string" ? detail : null });
        }
        await readSse(res.body, (name, data) => {
          if (name === "done") return;
          try {
            handleEvent(JSON.parse(data));
          } catch {
            /* ignore malformed event */
          }
        });
        updateLastTurn((t) => ({ ...t, isStreaming: false }));
        if (saved) announceChats();
      } catch (err) {
        if (err.name === "AbortError") return;
        updateLastTurn((t) => ({ ...t, isStreaming: false, error: t.error || err.detail || "Lost connection to the agent server." }));
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null;
      }
    },
    [saved, chatId, projectId, onChatCreated, handleEvent, updateLastTurn]
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (requestIdRef.current) {
      fetch(`${API_BASE}/query/cancel/${requestIdRef.current}`, { method: "POST", credentials: "include" }).catch(() => {});
    }
    updateLastTurn((t) => ({ ...t, isStreaming: false }));
    if (saved) setTimeout(announceChats, 400);
  }, [saved, updateLastTurn]);

  const clearHistory = useCallback(() => setTurns([]), []);
  const isStreaming = turns.length > 0 && turns[turns.length - 1].isStreaming;

  return { turns, ask, cancel, clearHistory, isStreaming, chat, loading, loadError };
}
