import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { apiGet, apiSend } from "../api.js";
import { CHATS_CHANGED } from "../hooks/useAgentQuery.js";
import { newCardId } from "./CardEditor.jsx";

// The card a chat table or chart becomes. A table that came from a query,
// matrix or compare keeps that source, so the card is live; anything else
// (SQL, a drawn chart) is saved as a static snapshot of the numbers shown.
export function cardFrom({ table, chart }) {
  const now = new Date().toISOString();
  if (table) {
    const { id, source, ...rest } = table;
    return { id: newCardId(), title: table.title || "", note: "", chart: {},
      source: source || { kind: "snapshot", saved_at: now, table: { title: rest.title, columns: rest.columns, rows: rest.rows, filters: rest.filters, notes: rest.notes } } };
  }
  const series = (chart.series?.length ? chart.series : [{ name: chart.y_label || "value", values: chart.y || [] }]).map((s) => s.name);
  const rows = chart.x.map((label, i) => [label, ...(chart.series?.length ? chart.series : [{ values: chart.y }]).map((s) => s.values[i] ?? null)]);
  return { id: newCardId(), title: chart.title || "", note: "", chart: { type: chart.type === "line" ? "line" : "bar", x: "label", y: series },
    source: { kind: "snapshot", saved_at: now, table: { title: chart.title, columns: ["label", ...series], rows } } };
}

// "Add to board" under a chat table or chart: pick a board or start a new one.
export default function AddToBoard({ table, chart, projectId }) {
  const [open, setOpen] = useState(false);
  const [boards, setBoards] = useState([]);
  const [done, setDone] = useState(null);
  const [error, setError] = useState(null);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    apiGet("/api/boards").then((r) => setBoards(r.boards)).catch(() => setBoards([]));
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const add = async (board) => {
    setOpen(false);
    try {
      const card = cardFrom({ table, chart });
      const target = board || await apiSend("/api/boards", { name: card.title || "New board", project_id: projectId || null });
      await apiSend(`/api/boards/${target.id}`, { cards: [...(target.cards || []), card] }, "PATCH");
      window.dispatchEvent(new Event(CHATS_CHANGED));
      setDone({ id: target.id, name: target.name, live: card.source.kind !== "snapshot" });
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="add-to-board" ref={ref}>
      <button type="button" className="ghost-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>Add to board</button>
      {open && (
        <div className="sb-menu" role="menu">
          {boards.map((b) => <button type="button" role="menuitem" key={b.id} onClick={() => add(b)}>{b.name}</button>)}
          <button type="button" role="menuitem" onClick={() => add(null)}>+ New board</button>
        </div>
      )}
      {done && <span className="note-meta">Added to <Link to={`/boards/${done.id}`}>{done.name}</Link>{done.live ? "" : " as a static snapshot"}.</span>}
      {error && <span className="note-meta" role="alert">{error}</span>}
    </div>
  );
}
