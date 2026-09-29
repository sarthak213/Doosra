import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { apiGet, apiSend } from "../api.js";
import BoardCard from "../components/BoardCard.jsx";
import CardEditor from "../components/CardEditor.jsx";
import { ErrorNote, Loading } from "../components/kit/Panel.jsx";
import { useCopilotContext } from "../copilot/CopilotProvider.jsx";
import { CHATS_CHANGED } from "../hooks/useAgentQuery.js";
import { useFetch } from "../hooks/useFetch.js";

const announce = () => window.dispatchEvent(new Event(CHATS_CHANGED));

// A board: a named set of chart cards. Cards are live (they re-run their query
// on every open) unless they were saved from a chat as snapshots.
export default function BoardView() {
  const { boardId } = useParams();
  const navigate = useNavigate();
  const { data, error, loading } = useFetch((signal) => apiGet(`/api/boards/${boardId}`, undefined, signal), boardId);
  const projects = useFetch(() => apiGet("/api/projects"), "projects");
  const [board, setBoard] = useState(null);
  const [editing, setEditing] = useState(null);     // null | "new" | card id
  const [status, setStatus] = useState(null);

  useEffect(() => { if (data) setBoard(data); }, [data]);
  useCopilotContext({ view: "board", settings: { name: board?.name, cards: board?.cards.length }, visible: null });

  const save = (patch) =>
    apiSend(`/api/boards/${boardId}`, patch, "PATCH").then((b) => { setBoard(b); setStatus(null); announce(); }).catch((e) => setStatus(e.message));
  const saveCards = (cards) => save({ cards });
  const project = projects.data?.projects.find((p) => p.id === board?.project_id);

  const explain = (card) =>
    apiSend("/api/chats", { title: `Insights: ${card?.title || board.name}`, project_id: board.project_id, board_id: board.id })
      .then((chat) => { announce(); navigate(`/ask/${chat.id}?explain=${card ? card.id : "board"}`); })
      .catch((e) => setStatus(e.message));
  const remove = () => {
    if (!window.confirm(`Delete "${board.name}"? Its charts go with it; chats that explained it are kept.`)) return;
    apiSend(`/api/boards/${boardId}`, undefined, "DELETE").then(() => { announce(); navigate(board.project_id ? `/projects/${board.project_id}` : "/ask"); }).catch((e) => setStatus(e.message));
  };

  if (loading && !board) return <div className="view"><Loading label="Opening board" /></div>;
  if (error) return <div className="view"><ErrorNote error={error} /><Link to="/ask">Back to Ask</Link></div>;
  if (!board) return null;
  const editingCard = editing && editing !== "new" ? board.cards.find((c) => c.id === editing) : null;
  const upsert = (card) => {
    saveCards(editing === "new" ? [...board.cards, card] : board.cards.map((c) => (c.id === card.id ? card : c)));
    setEditing(null);
  };

  return (
    <div className="view board-view">
      <div className="view-head">
        <div className="grow">
          <textarea className="project-name" rows={1} defaultValue={board.name} key={board.updated} maxLength={120} aria-label="Board name"
            onBlur={(e) => e.target.value.trim() && e.target.value.trim() !== board.name && save({ name: e.target.value.trim() })}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); } }} />
          <p className="muted">
            {project ? <>In <Link to={`/projects/${project.id}`}>{project.name}</Link>. The AI also reads that project's notes when it explains this board.</> : "Not in a project. Move it into one so the AI can use the project's notes."}
          </p>
        </div>
        <div className="view-tools">
          <select aria-label="Project" value={board.project_id || ""}
            onChange={(e) => save({ project_id: e.target.value || null, move: true })}>
            <option value="">No project</option>
            {(projects.data?.projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button type="button" className="ghost-btn" onClick={() => setEditing("new")}>Add card</button>
          <button type="button" className="primary-btn" disabled={!board.cards.length} onClick={() => explain(null)}>Explain this view</button>
          <button type="button" className="ghost-btn danger" onClick={remove}>Delete</button>
        </div>
      </div>
      <textarea className="board-desc" rows={2} defaultValue={board.description} key={`d${board.updated}`} maxLength={2000}
        aria-label="Board description" placeholder="What is this board for? The AI reads this too."
        onBlur={(e) => e.target.value !== board.description && save({ description: e.target.value })} />
      {status && <p className="resolution-note" role="alert">{status}</p>}

      {editing && <CardEditor key={editing} card={editingCard} onSave={upsert} onCancel={() => setEditing(null)} />}

      {board.cards.length === 0 && !editing && (
        <div className="empty-note">
          <p>No charts yet. Add a card here, or use “Add to board” under any table or chart the AI shows you in a chat.</p>
          <button type="button" className="primary-btn" onClick={() => setEditing("new")}>Add your first card</button>
        </div>
      )}
      <div className="board-grid">
        {board.cards.map((c, i) => (
          <div key={c.id} className={["table", "scatter"].includes(c.chart?.type) || c.source?.kind === "matrix" ? "board-wide" : ""}>
            <BoardCard card={c}>
              <button type="button" className="ghost-btn" onClick={() => explain(c)}>Explain</button>
              <button type="button" className="ghost-btn" onClick={() => setEditing(c.id)}>Edit</button>
              <button type="button" className="ghost-btn" aria-label="Move earlier" disabled={i === 0}
                onClick={() => { const n = [...board.cards]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; saveCards(n); }}>↑</button>
              <button type="button" className="ghost-btn danger" onClick={() => window.confirm(`Remove "${c.title || "this card"}"?`) && saveCards(board.cards.filter((x) => x.id !== c.id))}>Remove</button>
            </BoardCard>
          </div>
        ))}
      </div>
    </div>
  );
}
