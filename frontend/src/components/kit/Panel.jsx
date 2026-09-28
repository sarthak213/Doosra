import { useCopilot } from "../../copilot/CopilotProvider.jsx";

// A titled section. With `explain`, gets an "Explain" button that opens the
// copilot with this panel's data as context.
export default function Panel({ title, subtitle, explain, actions, children, className = "" }) {
  const copilot = useCopilot();
  return (
    <section className={`panel ${className}`}>
      {(title || actions || explain) && (
        <header className="panel-head">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p className="panel-sub">{subtitle}</p>}
          </div>
          <div className="panel-actions">
            {actions}
            {explain && (
              <button type="button" className="ghost-btn explain-btn"
                onClick={() => copilot.explain(explain.question || `Explain what "${title}" shows and what stands out.`, explain.data, title)}>
                Explain
              </button>
            )}
          </div>
        </header>
      )}
      {children}
    </section>
  );
}

export function Loading({ label = "Crunching the numbers…" }) {
  return (
    <div className="loading" role="status">
      <span className="thinking-dot" />
      <span className="thinking-dot" />
      <span className="thinking-dot" />
      <span className="loading-label">{label}</span>
    </div>
  );
}

export function ErrorNote({ error, onPick }) {
  if (!error) return null;
  const candidates = error.payload?.candidates || [];
  return (
    <div className="error-note" role="alert">
      <p>{error.message}</p>
      {candidates.length > 0 && onPick && (
        <div className="candidate-list">
          {candidates.slice(0, 6).map((c) => (
            <button type="button" key={c.name || c.team || c.venue || c.event_name} className="ghost-btn"
              onClick={() => onPick(c.name || c.team || c.venue || c.event_name)}>
              {c.name || c.team || c.venue || c.event_name}
              {c.teams && <small> · {c.teams.slice(0, 2).join(", ")}</small>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Compact data summary for copilot context: title, filters, notes and the
// first rows as records.
export function summarize(table, maxRows = 15) {
  if (!table?.columns) return table;
  return {
    title: table.title,
    filters: table.filters,
    notes: table.notes,
    highlights: table.highlights,
    rows: (table.rows || []).slice(0, maxRows).map((r) => Object.fromEntries(table.columns.map((c, i) => [c, r[i]]))),
    total_rows: table.rows?.length,
  };
}
