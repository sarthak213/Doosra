import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { apiGet, apiSend } from "../api.js";
import { CHATS_CHANGED } from "../hooks/useAgentQuery.js";

// The Ask page's left rail: new chat, search, projects (with their chats and
// boards) and recent chats. Rename, move and delete live in each row's menu.
export default function AskSidebar({ activeChatId, open, onClose }) {
  const navigate = useNavigate();
  const [chats, setChats] = useState([]);
  const [projects, setProjects] = useState([]);
  const [boards, setBoards] = useState([]);
  const [expanded, setExpanded] = useState(() => new Set());
  const [search, setSearch] = useState("");
  const [found, setFound] = useState(null);
  const [menuFor, setMenuFor] = useState(null);
  const [renaming, setRenaming] = useState(null);
  const [naming, setNaming] = useState(null); // new project name draft
  const [error, setError] = useState(null);
  const importRef = useRef(null);

  const refresh = useCallback(() => {
    Promise.all([apiGet("/api/chats"), apiGet("/api/projects"), apiGet("/api/boards")])
      .then(([c, p, b]) => { setChats(c.chats); setProjects(p.projects); setBoards(b.boards); setError(null); })
      .catch(() => setError("Couldn't load your chats."));
  }, []);

  useEffect(() => {
    refresh();
    window.addEventListener(CHATS_CHANGED, refresh);
    return () => window.removeEventListener(CHATS_CHANGED, refresh);
  }, [refresh]);

  useEffect(() => {
    const q = search.trim();
    if (!q) { setFound(null); return undefined; }
    const t = setTimeout(() => apiGet("/api/chats", { q }).then((r) => setFound(r.chats)).catch(() => setFound([])), 250);
    return () => clearTimeout(t);
  }, [search]);

  // The active chat's project opens by itself.
  useEffect(() => {
    const active = chats.find((c) => c.id === activeChatId);
    if (active?.project_id) setExpanded((s) => (s.has(active.project_id) ? s : new Set(s).add(active.project_id)));
  }, [activeChatId, chats]);

  const toggle = (id) => setExpanded((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const act = (fn) => fn().then(refresh).catch((e) => setError(e.message));

  const rename = (chat, title) => {
    setRenaming(null);
    if (title.trim() && title.trim() !== chat.title) act(() => apiSend(`/api/chats/${chat.id}`, { title: title.trim() }, "PATCH"));
  };
  const remove = (chat) => {
    setMenuFor(null);
    if (!window.confirm(`Delete "${chat.title}"? This can't be undone.`)) return;
    act(() => apiSend(`/api/chats/${chat.id}`, undefined, "DELETE"));
    if (chat.id === activeChatId) navigate("/ask");
  };
  const move = (chat, projectId) => {
    setMenuFor(null);
    act(() => apiSend(`/api/chats/${chat.id}`, { project_id: projectId || null, move: true }, "PATCH"));
  };
  const createProject = (name) => {
    setNaming(null);
    if (!name.trim()) return;
    apiSend("/api/projects", { name: name.trim() }).then((p) => { refresh(); navigate(`/projects/${p.id}`); })
      .catch((e) => setError(e.message));
  };

  const importProject = async (file) => {
    if (importRef.current) importRef.current.value = "";
    if (!file) return;
    try {
      const p = await apiSend("/api/projects/import", JSON.parse(await file.text()));
      refresh();
      navigate(`/projects/${p.id}`);
    } catch (e) {
      setError(e instanceof SyntaxError ? "That file isn't a Doosra project export." : e.message);
    }
  };

  const row = (chat) => (
    <li key={chat.id} className={`sb-row${chat.id === activeChatId ? " active" : ""}`}>
      {renaming === chat.id ? (
        <input className="sb-rename" autoFocus defaultValue={chat.title} aria-label="Chat title"
          onBlur={(e) => rename(chat, e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") setRenaming(null); }} />
      ) : (
        <NavLink to={`/ask/${chat.id}`} className="sb-link" title={chat.title} onClick={onClose}>
          {chat.board_id && <span className="sb-tag" title="Explains a board">◧</span>}{chat.title}
        </NavLink>
      )}
      <button type="button" className="sb-more" aria-label={`Options for ${chat.title}`}
        onClick={() => setMenuFor(menuFor === chat.id ? null : chat.id)}>⋯</button>
      {menuFor === chat.id && (
        <div className="sb-menu" role="menu">
          <button type="button" role="menuitem" onClick={() => { setMenuFor(null); setRenaming(chat.id); }}>Rename</button>
          <label className="sb-move">Move to
            <select value={chat.project_id || ""} onChange={(e) => move(chat, e.target.value)}>
              <option value="">No project</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <button type="button" role="menuitem" className="danger" onClick={() => remove(chat)}>Delete</button>
        </div>
      )}
    </li>
  );

  const loose = chats.filter((c) => !c.project_id);
  return (
    <aside className={`ask-sidebar${open ? " open" : ""}`} aria-label="Chats and projects">
      <div className="sb-top">
        <NavLink to="/ask" end className="primary-btn sb-new" onClick={onClose}>+ New chat</NavLink>
        <input className="sb-search" type="search" placeholder="Search chats" aria-label="Search chats"
          value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>
      {error && <p className="sb-error" role="alert">{error}</p>}
      <div className="sb-scroll">
        {found ? (
          <>
            <h3 className="sb-head">Results</h3>
            {found.length ? <ul>{found.map(row)}</ul> : <p className="sb-empty">No chats match “{search.trim()}”.</p>}
          </>
        ) : (
          <>
            <h3 className="sb-head">
              Projects
              <span>
                <input ref={importRef} type="file" accept="application/json,.json" hidden onChange={(e) => importProject(e.target.files?.[0])} />
                <button type="button" className="sb-add" aria-label="Import a project file" title="Import a project (.json)"
                  onClick={() => importRef.current?.click()}>⤒</button>
                <button type="button" className="sb-add" aria-label="New project" onClick={() => setNaming("")}>+</button>
              </span>
            </h3>
            {naming !== null && (
              <input className="sb-rename" autoFocus placeholder="Project name" aria-label="Project name" value={naming}
                onChange={(e) => setNaming(e.target.value)} onBlur={(e) => createProject(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") setNaming(null); }} />
            )}
            {projects.length === 0 && naming === null && (
              <p className="sb-empty">Projects group chats, boards and notes the AI can use.</p>
            )}
            <ul>
              {projects.map((p) => (
                <li key={p.id} className="sb-project">
                  <div className="sb-prow">
                    <button type="button" className="sb-chev" aria-label={expanded.has(p.id) ? "Collapse" : "Expand"}
                      aria-expanded={expanded.has(p.id)} onClick={() => toggle(p.id)}>{expanded.has(p.id) ? "▾" : "▸"}</button>
                    <NavLink to={`/projects/${p.id}`} className="sb-link sb-pname" onClick={onClose}>{p.name}</NavLink>
                    <NavLink to={`/ask?project=${p.id}`} className="sb-more" aria-label={`New chat in ${p.name}`}
                      title="New chat in this project" onClick={onClose}>+</NavLink>
                  </div>
                  {expanded.has(p.id) && (
                    <ul className="sb-nested">
                      {boards.filter((b) => b.project_id === p.id).map((b) => (
                        <li key={b.id} className="sb-row">
                          <NavLink to={`/boards/${b.id}`} className="sb-link" onClick={onClose}><span className="sb-tag">▦</span>{b.name}</NavLink>
                        </li>
                      ))}
                      {chats.filter((c) => c.project_id === p.id).map(row)}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
            <h3 className="sb-head">Recents</h3>
            {loose.length ? <ul>{loose.map(row)}</ul> : <p className="sb-empty">Your conversations will appear here.</p>}
          </>
        )}
      </div>
    </aside>
  );
}
