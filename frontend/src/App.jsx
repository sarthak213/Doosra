import { useEffect, useRef } from "react";
import Masthead from "./components/Masthead.jsx";
import MessageBubble from "./components/MessageBubble.jsx";
import AssistantTurn from "./components/AssistantTurn.jsx";
import SuggestedQuestions from "./components/SuggestedQuestions.jsx";
import InputBar from "./components/InputBar.jsx";
import { useAgentQuery } from "./hooks/useAgentQuery.js";
import "./App.css";

export default function App() {
  const { turns, ask, cancel, clearHistory, isStreaming } = useAgentQuery();
  const feedEndRef = useRef(null);

  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  return (
    <div className="app">
      <Masthead />

      <main className="feed">
        {turns.length > 0 && (
          <div className="feed-tools">
            <button type="button" className="clear-history" onClick={clearHistory}>
              Clear history
            </button>
          </div>
        )}
        {turns.length === 0 ? (
          <SuggestedQuestions onPick={ask} />
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

      <InputBar onAsk={ask} onCancel={cancel} disabled={isStreaming} />
    </div>
  );
}
