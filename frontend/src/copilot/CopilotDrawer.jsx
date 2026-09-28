import { useEffect, useRef } from "react";
import AssistantTurn from "../components/AssistantTurn.jsx";
import CricketBall from "../components/CricketBall.jsx";
import InputBar from "../components/InputBar.jsx";
import MessageBubble from "../components/MessageBubble.jsx";
import Wordmark from "../components/Wordmark.jsx";
import { useCopilot } from "./CopilotProvider.jsx";

const STARTERS = [
  "What stands out on this page?",
  "Who are the best death bowlers in the IPL since 2022?",
  "Which T20I batters are both consistent and fast?",
];

export default function CopilotDrawer() {
  const { open, setOpen, ask, agent } = useCopilot();
  const { turns, cancel, clearHistory, isStreaming } = agent;
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setOpen]);

  return (
    <>
      {!open && (
        <button type="button" className="copilot-fab" onClick={() => setOpen(true)}
          aria-label="Ask Doosra -- open the copilot (Ctrl+K)" title="Ask Doosra (Ctrl+K)">
          <CricketBall motion size="58px" />
        </button>
      )}
      <aside className={`copilot-drawer${open ? " open" : ""}`} aria-label="Doosra copilot" aria-hidden={!open}>
        <header className="copilot-head">
          <div>
            <Wordmark small />
            <span className="copilot-sub">copilot · sees what you're looking at</span>
          </div>
          <div className="copilot-actions">
            {turns.length > 0 && (
              <button type="button" className="ghost-btn" onClick={clearHistory}>Clear</button>
            )}
            <button type="button" className="ghost-btn" onClick={() => setOpen(false)} aria-label="Close copilot">✕</button>
          </div>
        </header>
        <div className="copilot-feed">
          {turns.length === 0 ? (
            <div className="copilot-empty">
              <p>Ask about anything on screen, or anything in the data. I can also set up views for you.</p>
              {STARTERS.map((q) => (
                <button type="button" key={q} className="suggestion-chip" onClick={() => ask(q)}>{q}</button>
              ))}
            </div>
          ) : (
            turns.map((turn) => (
              <div className="turn" key={turn.id}>
                <MessageBubble text={turn.question} />
                <AssistantTurn turn={turn} />
              </div>
            ))
          )}
          <div ref={endRef} />
        </div>
        <InputBar onAsk={ask} onCancel={cancel} disabled={isStreaming} placeholder="Ask, or 'explain this'…" />
      </aside>
    </>
  );
}
