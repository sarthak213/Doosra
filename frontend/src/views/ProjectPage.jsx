import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { apiGet, apiSend } from "../api.js";
import Panel, { ErrorNote, Loading } from "../components/kit/Panel.jsx";
import { useCopilotContext } from "../copilot/CopilotProvider.jsx";
import { CHATS_CHANGED } from "../hooks/useAgentQuery.js";
import { useFetch } from "../hooks/useFetch.js";

const NOTE_CAP = 20000;   // matches the server's per-note limit
const BRIEF_CAP = 4000;
const TEXT_FILES = /\.(md|markdown|txt|csv|tsv)$/i;

const announce = () => window.dispatchEvent(new Event(CHATS_CHANGED));

function NoteRow({ note, onSave, onDelete }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(note.title);
  const [body, setBody] = useState(note.body);
  return (
    <li className={`note${note.enabled ? "" : " off"}`}>
      <div className="note-head">
        <label className="note-toggle" title="When off, the AI doesn't see this note">
          <input type="checkbox" checked={note.enabled} onChange={(e) => onSave({ enabled: e.target.checked })} />
          <span className="note-title">{note.title}</span>
        </label>
        <span className="note-meta">
          {note.source !== "typed" && <span className="note-source">{note.source.replace("file:", "")}</span>}
          {note.body.length.toLocaleString()} chars
        </span>
        <button type="button" className="ghost-btn" onClick={() => setEditing((v) => !v)}>{editing ? "Close" : "Edit"}</button>
        <button type="button" className="ghost-btn danger" onClick={() => window.confirm(`Delete "${note.title}"?`) && onDelete()}>Delete</button>
      </div>
      {editing ? (
        <div className="note-edit">
          <input value={title} maxLength={160} aria-label="Note title" onChange={(e) => setTitle(e.target.value)} />
          <textarea value={body} rows={8} maxLength={NOTE_CAP} aria-label="Note text" onChange={(e) => setBody(e.target.value)} />
          <div className="note-actions">
            <button type="button" className="primary-btn"
              onClick={() => { onSave({ title: title.trim() || note.title, body }); setEditing(false); }}>Save note</button>
            <button type="button" className="ghost-btn" onClick={() => { setTitle(note.title); setBody(note.body); setEditing(false); }}>Cancel</button>
          </div>
        </div>
      ) : (
        <p className="note-preview">{note.body.slice(0, 240)}{note.body.length > 240 ? "…" : ""}</p>
      )}
    </li>
  );
}

// A project: the standing brief and notes the AI reads, plus its chats and boards.
export default function ProjectPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const [version, setVersion] = useState(0);
  const reload = () => { setVersion((v) => v + 1); announce(); };
  const { data: project, error, loading } = useFetch((signal) => apiGet(`/api/projects/${projectId}`, undefined, signal), `${projectId}:${version}`);
  const [brief, setBrief] = useState("");
  const [name, setName] = useState("");
  const [draft, setDraft] = useState({ title: "", body: "" });
  const [status, setStatus] = useState(null);
  const fileRef = useRef(null);

  useCopilotContext({ view: "project", settings: { name: project?.name }, visible: null });
  useEffect(() => { if (project) { setBrief(project.instructions || ""); setName(project.name); } }, [project?.id, project?.updated]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = (fn, ok) => fn().then(() => { setStatus(ok || null); reload(); }).catch((e) => setStatus(e.message));

  const saveBrief = () => { if (brief !== project.instructions) run(() => apiSend(`/api/projects/${projectId}`, { instructions: brief }, "PATCH"), "Brief saved."); };
  const rename = () => { if (name.trim() && name.trim() !== project.name) run(() => apiSend(`/api/projects/${projectId}`, { name: name.trim() }, "PATCH")); };
  const addNote = () => {
    if (!draft.title.trim() || !draft.body.trim()) return;
    run(() => apiSend(`/api/projects/${projectId}/notes`, { title: draft.title.trim(), body: draft.body }), "Note added.");
    setDraft({ title: "", body: "" });
  };
  const importFile = async (file) => {
    if (!file) return;
    if (!TEXT_FILES.test(file.name)) { setStatus("Import a .md, .txt or .csv file."); return; }
    const text = await file.text();
    const cut = text.length > NOTE_CAP;
    run(() => apiSend(`/api/projects/${projectId}/notes`, { title: file.name, body: text.slice(0, NOTE_CAP), source: `file:${file.name}` }),
      cut ? `Imported ${file.name}; only the first ${NOTE_CAP.toLocaleString()} characters were kept.` : `Imported ${file.name}.`);
    if (fileRef.current) fileRef.current.value = "";
  };
  const removeProject = () => {
    if (!window.confirm(`Delete "${project.name}", its notes and boards? Its chats are kept.`)) return;
    apiSend(`/api/projects/${projectId}`, undefined, "DELETE").then(() => { announce(); navigate("/ask"); }).catch((e) => setStatus(e.message));
  };
  const exportProject = () => apiGet(`/api/projects/${projectId}/export`).then((data) => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `${project.name.replace(/[^\w-]+/g, "_")}.doosra.json` });
    a.click();
    URL.revokeObjectURL(url);
  }).catch((e) => setStatus(e.message));
  const newBoard = () => apiSend("/api/boards", { name: "Untitled board", project_id: projectId })
    .then((b) => { announce(); navigate(`/boards/${b.id}`); }).catch((e) => setStatus(e.message));

  if (loading && !project) return <div className="view"><Loading label="Opening project" /></div>;
  if (error) return <div className="view"><ErrorNote error={error} /><Link to="/ask">Back to Ask</Link></div>;
  const active = project.notes.filter((n) => n.enabled).length;

  return (
    <div className="view project-view">
      <div className="view-head">
        <div className="grow">
          <textarea className="project-name" rows={1} value={name} maxLength={120} aria-label="Project name"
            onChange={(e) => setName(e.target.value.replace(/\n/g, " "))} onBlur={rename}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); } }} />
          <p className="muted">The AI reads this project's brief and notes in every chat inside it.</p>
        </div>
        <div className="view-tools">
          <Link className="primary-btn" to={`/ask?project=${projectId}`}>New chat</Link>
          <button type="button" className="ghost-btn" onClick={exportProject}>Export</button>
          <button type="button" className="ghost-btn danger" onClick={removeProject}>Delete project</button>
        </div>
      </div>
      {status && <p className="resolution-note" role="status">{status}</p>}

      <Panel title="Standing brief" subtitle="What you're trying to do, in a few lines. It is sent with every question.">
        <textarea className="project-brief" value={brief} rows={4} maxLength={BRIEF_CAP} aria-label="Standing brief"
          placeholder="e.g. I'm scouting death-over bowlers for an IPL auction. Focus on 2022 onwards and separate skill from luck."
          onChange={(e) => setBrief(e.target.value)} onBlur={saveBrief} />
        <p className="note-meta">{brief.length}/{BRIEF_CAP}</p>
      </Panel>

      <Panel title={`Notes (${active} of ${project.notes.length} in use)`}
        subtitle="Team goals, opposition notes, a selection brief. Turn a note off to hide it from the AI."
        actions={<>
          <input ref={fileRef} type="file" accept=".md,.markdown,.txt,.csv,.tsv,text/*" hidden onChange={(e) => importFile(e.target.files?.[0])} />
          <button type="button" className="ghost-btn" onClick={() => fileRef.current?.click()}>Import .md / .txt / .csv</button>
        </>}>
        {project.notes.length === 0 && <p className="empty-note">No notes yet. Add one below or import a file.</p>}
        <ul className="notes">
          {project.notes.map((n) => (
            <NoteRow key={`${n.id}:${n.updated}`} note={n}
              onSave={(patch) => run(() => apiSend(`/api/notes/${n.id}`, patch, "PATCH"))}
              onDelete={() => run(() => apiSend(`/api/notes/${n.id}`, undefined, "DELETE"))} />
          ))}
        </ul>
        <div className="note-edit note-new">
          <input value={draft.title} maxLength={160} placeholder="Note title" aria-label="New note title"
            onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
          <textarea value={draft.body} rows={4} maxLength={NOTE_CAP} placeholder="Write or paste a note" aria-label="New note text"
            onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
          <div className="note-actions">
            <button type="button" className="primary-btn" disabled={!draft.title.trim() || !draft.body.trim()} onClick={addNote}>Add note</button>
            <span className="note-meta">{draft.body.length.toLocaleString()}/{NOTE_CAP.toLocaleString()}</span>
          </div>
        </div>
      </Panel>

      <div className="grid-2">
        <Panel title="Boards" actions={<button type="button" className="ghost-btn" onClick={newBoard}>New board</button>}>
          {project.boards.length === 0 && <p className="empty-note">Boards are sets of charts you build once and ask the AI to explain.</p>}
          <ul className="plain-list">
            {project.boards.map((b) => (
              <li key={b.id}><Link to={`/boards/${b.id}`}>{b.name}</Link> <span className="note-meta">{b.cards} card{b.cards === 1 ? "" : "s"}</span></li>
            ))}
          </ul>
        </Panel>
        <Panel title="Chats">
          {project.chats.length === 0 && <p className="empty-note">No chats in this project yet.</p>}
          <ul className="plain-list">
            {project.chats.map((c) => (
              <li key={c.id}><Link to={`/ask/${c.id}`}>{c.title}</Link> <span className="note-meta">{new Date(c.updated).toLocaleDateString()}</span></li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
