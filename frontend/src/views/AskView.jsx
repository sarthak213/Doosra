import { useEffect, useRef } from "react";
import AssistantTurn from "../components/AssistantTurn.jsx";
import InputBar from "../components/InputBar.jsx";
import MessageBubble from "../components/MessageBubble.jsx";
import SuggestedQuestions from "../components/SuggestedQuestions.jsx";
import { useNavigate } from "react-router-dom";
import { urlForAction, useCopilot } from "../copilot/CopilotProvider.jsx";
import { useAgentQuery } from "../hooks/useAgentQuery.js";

// Full-page chat. Its own persisted conversation (the copilot drawer has a
// separate, session-only one that follows you across views).
export default function AskView() {
  const navigate = useNavigate();
  const copilot = useCopilot();
  const { turns, ask, cancel, clearHistory, isStreaming } = useAgentQuery({
    storageKey: "cricket-agent-turns",
    onUiAction: (e) => {
      const url = urlForAction(e);
      if (url) navigate(url);
    },
  });
  const feedEndRef = useRef(null);

  useEffect(() => {
    copilot?.setOpen(false);
    copilot?.setViewContext({ view: "ask (full-page chat)" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  return (
    <div className="ask-view">
      <main className="feed">
        {turns.length > 0 && (
          <div className="feed-tools">
            <button type="button" className="clear-history" onClick={clearHistory}>Clear history</button>
          </div>
        )}
        {turns.length === 0 ? (
          <SuggestedQuestions onPick={(q) => ask(q)} />
        ) : (
          turns.map((turn) => (
            <div className="turn" key={turn.id}>
              <MessageBubble text={turn.question} />
              <AssistantTurn turn={turn} />
            </div>
          ))
        )}
        <div ref={feedEndRef} />
      </main>
      <InputBar onAsk={(q) => ask(q)} onCancel={cancel} disabled={isStreaming} />
    </div>
  );
}
