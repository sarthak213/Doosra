import { useCallback, useEffect, useRef } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { apiSend } from "../api.js";
import AssistantTurn from "../components/AssistantTurn.jsx";
import InputBar from "../components/InputBar.jsx";
import MessageBubble from "../components/MessageBubble.jsx";
import { summarize } from "../components/kit/Panel.jsx";
import SuggestedQuestions from "../components/SuggestedQuestions.jsx";
import { EXPLAIN_BOARD, EXPLAIN_CARD, explainItem } from "../explainPrompts.js";
import { urlForAction, useCopilot } from "../copilot/CopilotProvider.jsx";
import { CHATS_CHANGED, useAgentQuery } from "../hooks/useAgentQuery.js";

const LEGACY_KEY = "cricket-agent-turns"; // the pre-v2.3 browser-stored conversation

// Moves the old single conversation into a saved chat, once.
async function importLegacyTurns() {
  try {
    const turns = JSON.parse(localStorage.getItem(LEGACY_KEY) || "[]");
    if (!Array.isArray(turns) || !turns.length) return false;
    await apiSend("/api/chats/import", { title: "Imported chat", turns });
    localStorage.removeItem(LEGACY_KEY);
    return true;
  } catch {
    return false;
  }
}

// Full-page chat: a saved conversation with a sidebar of chats and projects.
// (The copilot drawer keeps a separate, session-only conversation that follows
// you across views.)
export default function AskView() {
  const navigate = useNavigate();
  const { chatId } = useParams();
  const [params] = useSearchParams();
  const copilot = useCopilot();
  const onChatCreated = useCallback((id) => navigate(`/ask/${id}`, { replace: true }), [navigate]);
  const { turns, ask, cancel, isStreaming, chat, loading, loadError } = useAgentQuery({
    saved: true,
    chatId: chatId || null,
    projectId: params.get("project"),
    onChatCreated,
    onUiAction: (e) => {
      const url = urlForAction(e);
      if (url) navigate(url);
    },
  });
  const feedEndRef = useRef(null);
  const autoExplained = useRef(false);

  // "Explain this view" on a board opens a fresh chat here with ?explain=board (or a card id): ask once, then tidy the URL.
  const explainParam = params.get("explain");
  useEffect(() => {
    if (!explainParam || !chatId || !chat || loading || turns.length || autoExplained.current) return;
    autoExplained.current = true;
    ask(explainParam === "board" ? EXPLAIN_BOARD : EXPLAIN_CARD, explainParam === "board" ? null : { card_id: explainParam });
    navigate(`/ask/${chatId}`, { replace: true });
  }, [explainParam, chatId, chat, loading, turns.length, ask, navigate]);

  // The Explain button under a chart or table in an answer: ask about that excerpt in this chat.
  const explainAnswer = useCallback((kind, item) => {
    const data = kind === "table" ? summarize(item, 15) : { type: item.type, x: item.x, series: item.series || [{ name: item.y_label, values: item.y }] };
    ask(explainItem(kind, item.title), { view: "ask", focus: { panel: item.title || kind, data } });
  }, [ask]);

  useEffect(() => {
    copilot?.setOpen(false);
    importLegacyTurns().then((moved) => { if (moved) window.dispatchEvent(new Event(CHATS_CHANGED)); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    copilot?.setViewContext({ view: "ask (full-page chat)" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatId]);

  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  return (
    <div className="ask-view">
        <header className="ask-head">
          <h1 className="ask-title">{chat?.title || "New chat"}</h1>
          {chat?.board_id && <Link className="ghost-btn" to={`/boards/${chat.board_id}`}>Open board</Link>}
        </header>
        <main className="feed">
          {loadError && <p className="empty-note" role="alert">{loadError}</p>}
          {loading && <p className="muted">Opening chat…</p>}
          {!loading && !loadError && turns.length === 0 && <SuggestedQuestions onPick={(q) => ask(q)} />}
          {turns.map((turn) => (
            <div className="turn" key={turn.id}>
              <MessageBubble text={turn.question} />
              <AssistantTurn turn={turn} projectId={chat?.project_id || params.get("project")} onExplain={isStreaming ? undefined : explainAnswer} />
            </div>
          ))}
          <div ref={feedEndRef} />
        </main>
        <InputBar onAsk={(q) => ask(q)} onCancel={cancel} disabled={isStreaming || loading} />
    </div>
  );
}
